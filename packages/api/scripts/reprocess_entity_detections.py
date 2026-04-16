"""One-shot script: refresh entity_detections for every stored document.

Runs the current sanitizer against the chunks already in the database,
rewrites the `entity_detections` table, overwrites the encrypted
`anon_maps/{id}.enc` payload, and updates `documents.entity_count`.
PDF text extraction and chunking are not re-run — only detection.

Respects each document's own `active_regulation_ids` and the app's
current `use_presidio_layer` / `use_ner_layer` flags. Ollama is kept
disabled to match the ingestion pipeline (document_pipeline.py).
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

# Make `app.*` imports resolve when the script is launched from backend/
SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
os.chdir(BACKEND_DIR)
sys.path.insert(0, str(BACKEND_DIR))

from septum_api.models.settings import AppSettings  # noqa: E402
from septum_api.seeds.regulations import builtin_regulations  # noqa: E402
from septum_api.services.anonymization_map import AnonymizationMap  # noqa: E402
from septum_api.services.document_anon_store import _ANON_MAP_DIR, _serialize  # noqa: E402
from septum_api.services.policy_composer import PolicyComposer  # noqa: E402
from septum_api.services.sanitizer import PIISanitizer  # noqa: E402
from septum_api.utils.crypto import encrypt  # noqa: E402


DB_PATH = BACKEND_DIR / "septum.db"


def _load_app_settings(conn: sqlite3.Connection) -> AppSettings:
    """Build an AppSettings dataclass from the single row in app_settings."""
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM app_settings LIMIT 1").fetchone()
    if row is None:
        raise SystemExit("app_settings row missing — backend not initialised")

    def _coerce(value, default):
        return default if value is None else value

    raw_image_langs = _coerce(row["image_ocr_languages"], '["en"]')
    try:
        image_ocr_languages = json.loads(raw_image_langs)
    except (TypeError, json.JSONDecodeError):
        image_ocr_languages = ["en"]

    raw_provider_opts = row["ocr_provider_options"] if "ocr_provider_options" in row.keys() else None
    try:
        ocr_provider_options = json.loads(raw_provider_opts) if raw_provider_opts else None
    except (TypeError, json.JSONDecodeError):
        ocr_provider_options = None

    return AppSettings(
        id=int(row["id"]),
        llm_provider=str(row["llm_provider"]),
        llm_model=str(row["llm_model"]),
        ollama_base_url=str(_coerce(row["ollama_base_url"], "http://localhost:11434")),
        ollama_chat_model=str(_coerce(row["ollama_chat_model"], "llama3.2:3b")),
        ollama_deanon_model=str(_coerce(row["ollama_deanon_model"], "llama3.2:3b")),
        deanon_enabled=bool(_coerce(row["deanon_enabled"], 1)),
        deanon_strategy=str(_coerce(row["deanon_strategy"], "simple")),
        require_approval=bool(_coerce(row["require_approval"], 0)),
        show_json_output=bool(_coerce(row["show_json_output"], 0)),
        use_presidio_layer=bool(_coerce(row["use_presidio_layer"], 1)),
        use_ner_layer=bool(_coerce(row["use_ner_layer"], 1)),
        use_ollama_validation_layer=False,  # ingestion never uses Ollama
        use_ollama_layer=False,
        chunk_size=int(_coerce(row["chunk_size"], 800)),
        chunk_overlap=int(_coerce(row["chunk_overlap"], 200)),
        top_k_retrieval=int(_coerce(row["top_k_retrieval"], 5)),
        pdf_chunk_size=int(_coerce(row["pdf_chunk_size"], 1200)),
        audio_chunk_size=int(_coerce(row["audio_chunk_size"], 60)),
        spreadsheet_chunk_size=int(_coerce(row["spreadsheet_chunk_size"], 200)),
        whisper_model=str(_coerce(row["whisper_model"], "base")),
        image_ocr_languages=image_ocr_languages,
        ocr_provider=str(_coerce(row["ocr_provider"], "paddleocr")),
        ocr_provider_options=ocr_provider_options,
        extract_embedded_images=bool(_coerce(row["extract_embedded_images"], 1)),
        recursive_email_attachments=bool(_coerce(row["recursive_email_attachments"], 1)),
        default_active_regulations=[],
    )


def _build_sanitizer_for(active_reg_ids: list[str], settings: AppSettings) -> PIISanitizer:
    """Build a PIISanitizer pinned to the given regulation IDs."""
    regs = builtin_regulations()
    for r in regs:
        r.is_active = r.id in set(active_reg_ids)
    active = [r for r in regs if r.is_active]
    policy = PolicyComposer().compose_from_data(active, [], [])
    return PIISanitizer(settings=settings, policy=policy)


def _reprocess_document(
    conn: sqlite3.Connection,
    sanitizer: PIISanitizer,
    doc_id: int,
    filename: str,
    language: str,
) -> tuple[int, int, dict[str, int]]:
    """Re-sanitize a single document. Returns (old_count, new_count, type_counts)."""
    old_count = conn.execute(
        "SELECT COUNT(*) FROM entity_detections WHERE document_id=?", (doc_id,)
    ).fetchone()[0]

    chunks = conn.execute(
        'SELECT id, "index", sanitized_text FROM chunks WHERE document_id=? ORDER BY "index"',
        (doc_id,),
    ).fetchall()

    anon_map = AnonymizationMap(document_id=doc_id, language=language)
    per_chunk_spans: list[tuple[int, object]] = []
    total_entities = 0
    type_counts: dict[str, int] = defaultdict(int)

    for chunk_row in chunks:
        chunk_id = chunk_row["id"]
        text = chunk_row["sanitized_text"] or ""
        if not text:
            continue
        result = sanitizer.sanitize(text=text, language=language, anon_map=anon_map)
        for sp in result.detected_spans:
            per_chunk_spans.append((chunk_id, sp))
            type_counts[sp.entity_type] += 1
        total_entities += result.entity_count

    conn.execute("DELETE FROM entity_detections WHERE document_id=?", (doc_id,))
    for chunk_id, sp in per_chunk_spans:
        conn.execute(
            "INSERT INTO entity_detections "
            "(document_id, chunk_id, entity_type, placeholder, start_offset, end_offset, score) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                doc_id,
                chunk_id,
                sp.entity_type,
                sp.placeholder,
                sp.start,
                sp.end,
                float(sp.score),
            ),
        )
    conn.execute(
        "UPDATE documents SET entity_count=? WHERE id=?",
        (total_entities, doc_id),
    )

    _ANON_MAP_DIR.mkdir(parents=True, exist_ok=True)
    serialized = _serialize(anon_map)
    encrypted_bytes = encrypt(serialized, associated_data=str(doc_id).encode("utf-8"))
    (_ANON_MAP_DIR / f"{doc_id}.enc").write_bytes(encrypted_bytes)

    return old_count, total_entities, dict(type_counts)


def main() -> None:
    if not DB_PATH.exists():
        raise SystemExit(f"Database not found at {DB_PATH}")

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    settings = _load_app_settings(conn)
    print(
        f"Settings loaded: presidio={settings.use_presidio_layer} "
        f"ner={settings.use_ner_layer} ollama=False (ingestion mode)"
    )

    docs = conn.execute(
        "SELECT id, original_filename, detected_language, active_regulation_ids "
        "FROM documents ORDER BY id"
    ).fetchall()

    sanitizer_cache: dict[tuple[str, ...], PIISanitizer] = {}

    print()
    print(f"{'ID':<3} {'FILENAME':<45} {'LANG':<5} {'OLD':<5} {'NEW':<5} {'Δ':<6} TYPES")
    print("-" * 110)

    grand_old = 0
    grand_new = 0
    for row in docs:
        doc_id = row["id"]
        filename = row["original_filename"]
        language = row["detected_language"]
        try:
            reg_ids = tuple(sorted(json.loads(row["active_regulation_ids"] or "[]")))
        except (TypeError, json.JSONDecodeError):
            reg_ids = tuple()

        if reg_ids not in sanitizer_cache:
            sanitizer_cache[reg_ids] = _build_sanitizer_for(list(reg_ids), settings)
        sanitizer = sanitizer_cache[reg_ids]

        old_count, new_count, type_counts = _reprocess_document(
            conn, sanitizer, doc_id, filename, language
        )
        grand_old += old_count
        grand_new += new_count
        delta = new_count - old_count
        mark = "+" if delta > 0 else (" " if delta == 0 else "-")
        types_str = ", ".join(
            f"{k}:{v}" for k, v in sorted(type_counts.items(), key=lambda kv: -kv[1])
        )
        print(
            f"{doc_id:<3} {filename[:44]:<45} {language:<5} "
            f"{old_count:<5} {new_count:<5} {mark}{abs(delta):<5} {types_str[:40]}"
        )

    conn.commit()
    conn.close()

    print("-" * 110)
    total_delta = grand_new - grand_old
    total_mark = "+" if total_delta >= 0 else ""
    print(
        f"{'TOTAL':<55} {grand_old:<5} {grand_new:<5} "
        f"{total_mark}{total_delta}"
    )
    print()
    print("Done. entity_detections, documents.entity_count and anon_maps/*.enc refreshed.")


if __name__ == "__main__":
    main()
