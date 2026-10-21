"""Hand-authored retrieval evaluation fixtures and their loaders.

The corpus holds PII slots (``{{PII:TYPE:N}}``, N a 1-based entity id scoped per document) instead of literal values; the
benchmark harness fills them at run time. Gold quotes are slot-free substrings
of a document, resolved to character spans on the slotted template text.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent

SLOT_SYNTAX = r"\{\{PII:([A-Z_]+):([1-9][0-9]*)\}\}"
SLOT_PATTERN = re.compile(SLOT_SYNTAX)

ALLOWED_SLOT_TYPES = frozenset(
    {
        "PERSON_NAME",
        "EMAIL_ADDRESS",
        "PHONE_NUMBER",
        "IBAN",
        "ORGANIZATION_NAME",
        "LOCATION",
        "DATE_OF_BIRTH",
        "NATIONAL_ID",
        "CREDIT_CARD_NUMBER",
    }
)


@dataclass(frozen=True)
class EvalDoc:
    doc_id: str
    lang: str
    path: str
    structure: str
    domain: str
    file_format: str

    @property
    def abs_path(self) -> Path:
        return EVAL_DIR / self.path

    def read_text(self) -> str:
        return self.abs_path.read_text(encoding="utf-8")


@dataclass(frozen=True)
class EvalQuery:
    query_id: str
    lang: str
    type: str
    text: str
    doc_id: str | None
    gold_quotes: tuple[str, ...] = field(default_factory=tuple)


def _read_jsonl(name: str) -> list[dict]:
    lines = (EVAL_DIR / name).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def load_docs() -> list[EvalDoc]:
    """Return all corpus documents in file order."""
    return [EvalDoc(**row) for row in _read_jsonl("docs.jsonl")]


def load_queries() -> list[EvalQuery]:
    """Return all queries in file order."""
    return [
        EvalQuery(
            query_id=row["query_id"],
            lang=row["lang"],
            type=row["type"],
            text=row["text"],
            doc_id=row["doc_id"],
            gold_quotes=tuple(row["gold_quotes"]),
        )
        for row in _read_jsonl("queries.jsonl")
    ]


def resolve_gold_spans(query: EvalQuery, doc_text: str) -> list[tuple[int, int]]:
    """Resolve each gold quote to a ``(start, end)`` span in ``doc_text``.

    Raises ``ValueError`` when a quote is missing or occurs more than once.
    """
    spans: list[tuple[int, int]] = []
    for quote in query.gold_quotes:
        count = doc_text.count(quote)
        if count != 1:
            raise ValueError(
                f"{query.query_id}: gold quote occurs {count} times in "
                f"{query.doc_id}: {quote[:60]!r}"
            )
        start = doc_text.index(quote)
        spans.append((start, start + len(quote)))
    return spans


def load_gold_spans() -> dict[str, list[tuple[int, int]]]:
    """Map ``query_id`` to its gold spans for every answerable query."""
    docs = {d.doc_id: d.read_text() for d in load_docs()}
    return {
        q.query_id: resolve_gold_spans(q, docs[q.doc_id])
        for q in load_queries()
        if q.doc_id is not None
    }


def list_slots(text: str) -> list[tuple[str, int]]:
    """Distinct ``(type, N)`` slots of ``text`` in order of first appearance."""
    seen: dict[tuple[str, int], None] = {}
    for slot_type, index in SLOT_PATTERN.findall(text):
        seen.setdefault((slot_type, int(index)), None)
    return list(seen)


def load_doc_slots() -> dict[str, list[tuple[str, int]]]:
    """Map ``doc_id`` to the ``(type, N)`` slots its text contains."""
    return {d.doc_id: list_slots(d.read_text()) for d in load_docs()}


def fixture_files() -> list[Path]:
    """Fixture files covered by the digest, in stable sorted order."""
    files = [EVAL_DIR / "docs.jsonl", EVAL_DIR / "queries.jsonl"]
    files.extend((EVAL_DIR / "corpus").rglob("*.txt"))
    return sorted(files, key=lambda p: p.relative_to(EVAL_DIR).as_posix())


def fixture_digest() -> str:
    """SHA-256 over relative path and bytes of every fixture file."""
    h = hashlib.sha256()
    for path in fixture_files():
        h.update(path.relative_to(EVAL_DIR).as_posix().encode("utf-8"))
        h.update(b"\x00")
        h.update(path.read_bytes())
        h.update(b"\x00")
    return h.hexdigest()
