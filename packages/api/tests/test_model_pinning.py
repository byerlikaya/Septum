from __future__ import annotations

"""Model weight pinning across the API: loaders, coded refusals, CLI.

No test touches the network: every fetch goes through a mocked
``ensure_*`` function or never starts because the spec is refused first.
"""

import ast
import json
import logging
import os
import pickle
from pathlib import Path
from typing import Any

import fitz
import pytest
import test_chat_security_hardening as chat_security
from fastapi import BackgroundTasks
from httpx import AsyncClient
from septum_core import model_integrity
from septum_core.model_integrity import (
    ModelIntegrityError,
    ModelRefusal,
    UnpinnedModelError,
)
from septum_core.model_pins import MODEL_PINS
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from septum_api import main as main_module
from septum_api.cli import models as cli_models
from septum_api.errors import MODEL_REFUSED_DETAIL
from septum_api.models import Base
from septum_api.models.document import Document
from septum_api.routers import chat as chat_module
from septum_api.routers import documents as documents_router
from septum_api.services import vector_store as vector_store_module
from septum_api.services import whisper_models
from septum_api.services.ingestion import pdf_ingester as pdf_ingester_module
from septum_api.services.ingestion.ocr import paddle_provider
from septum_api.services.ingestion.pdf_ingester import PdfIngester

_PACKAGES = Path(__file__).resolve().parents[2]
UNPINNED = "someone/unpinned-ner-probe"
INTEGRITY_REASON = "file 'model.safetensors' does not match its pinned hash"

chat_env = chat_security.chat_env

# --- single loading path (static guard) -----------------------------------

_LOADER_NAMES = {"pipeline", "SentenceTransformer", "from_pretrained", "PaddleOCR"}
_PINNED_LOADERS = {
    ("core/septum_core/ner_model_registry.py", "get_pipelines"),
    ("api/septum_api/services/vector_store.py", "get_shared_sentence_transformer"),
    ("api/septum_api/services/ingestion/ocr/paddle_provider.py", "_paddle_ocr_predict"),
}
_HUB_FETCH_NAMES = {"snapshot_download", "hf_hub_download"}


def _called_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _calls_by_function(path: Path) -> list[tuple[str, str]]:
    """Return ``(called name, enclosing function)`` for every call in ``path``."""
    found: list[tuple[str, str]] = []

    def _walk(node: ast.AST, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            inner = (
                child.name
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                else scope
            )
            if isinstance(child, ast.Call):
                name = _called_name(child)
                if name:
                    found.append((name, scope))
            _walk(child, inner)

    _walk(ast.parse(path.read_text(encoding="utf-8")), "<module>")
    return found


def _source_files() -> list[Path]:
    roots = [_PACKAGES / "core" / "septum_core", _PACKAGES / "api" / "septum_api"]
    return sorted(p for root in roots for p in root.rglob("*.py"))


def test_model_constructors_appear_only_in_pinned_loaders() -> None:
    offenders = []
    for path in _source_files():
        rel = path.relative_to(_PACKAGES).as_posix()
        for name, scope in _calls_by_function(path):
            if name in _LOADER_NAMES and (rel, scope) not in _PINNED_LOADERS:
                offenders.append(f"{rel}:{scope} calls {name}(")

    assert offenders == []


def test_every_pinned_loader_still_constructs_its_model() -> None:
    for rel, scope in _PINNED_LOADERS:
        names = {n for n, s in _calls_by_function(_PACKAGES / rel) if s == scope}
        assert names & _LOADER_NAMES, f"{rel}:{scope} no longer loads a model"


def test_hub_downloads_happen_only_in_model_integrity() -> None:
    offenders = [
        f"{path.relative_to(_PACKAGES).as_posix()}:{scope}"
        for path in _source_files()
        if path.name != "model_integrity.py"
        for name, scope in _calls_by_function(path)
        if name in _HUB_FETCH_NAMES
    ]

    assert offenders == []


# --- sentence-transformers ------------------------------------------------


@pytest.fixture
def st_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[tuple, dict]]:
    import sentence_transformers

    calls: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(vector_store_module, "_shared_st_model", None)
    monkeypatch.setattr(
        sentence_transformers,
        "SentenceTransformer",
        lambda *a, **k: calls.append((a, k)) or object(),
    )
    return calls


def test_embedding_model_loads_from_the_verified_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, st_calls: list
) -> None:
    pins: list[str] = []
    monkeypatch.setattr(
        model_integrity, "ensure_pinned_snapshot", lambda pin: pins.append(pin.repo_id) or tmp_path
    )

    vector_store_module.get_shared_sentence_transformer()

    assert pins == [vector_store_module._DEFAULT_ST_MODEL]
    [(args, kwargs)] = st_calls
    assert args == (str(tmp_path),)
    assert kwargs["local_files_only"] is True
    assert kwargs["trust_remote_code"] is False
    assert kwargs["model_kwargs"] == {"use_safetensors": True}


def test_unpinned_embedding_model_is_refused_without_fallback(st_calls: list) -> None:
    with pytest.raises(ModelIntegrityError) as exc:
        vector_store_module.get_shared_sentence_transformer(UNPINNED)

    assert exc.value.code == "model_integrity_failed"
    assert st_calls == []


def test_embedding_integrity_failure_is_not_retried_unpinned(
    monkeypatch: pytest.MonkeyPatch, st_calls: list
) -> None:
    def _refuse(pin):
        raise ModelIntegrityError(pin.repo_id, INTEGRITY_REASON)

    monkeypatch.setattr(model_integrity, "ensure_pinned_snapshot", _refuse)

    with pytest.raises(ModelIntegrityError):
        vector_store_module.get_shared_sentence_transformer()

    assert st_calls == []
    assert vector_store_module._shared_st_model is None


# --- PaddleOCR --------------------------------------------------------------


def test_every_ocr_pipeline_model_is_in_the_manifest() -> None:
    names = paddle_provider.PaddleOcrProvider._MODEL_NAMES.values()

    assert [n for n in names if f"PaddlePaddle/{n}" not in MODEL_PINS] == []


def test_engine_kwargs_point_every_model_at_its_verified_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        model_integrity, "ensure_pinned_snapshot", lambda pin: tmp_path / pin.repo_id
    )

    kwargs = paddle_provider._pinned_engine_kwargs({"text_detection": "PP-OCRv5_mobile_det"})

    assert kwargs == {
        "text_detection_model_name": "PP-OCRv5_mobile_det",
        "text_detection_model_dir": str(tmp_path / "PaddlePaddle/PP-OCRv5_mobile_det"),
    }


def test_engine_kwargs_refuse_an_unpinned_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        model_integrity, "ensure_pinned_snapshot", lambda pin: pytest.fail("must not fetch")
    )

    with pytest.raises(ModelIntegrityError, match="no pin"):
        paddle_provider._pinned_engine_kwargs({"text_detection": "Unknown_det"})


def test_worker_raises_the_integrity_error_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    def _refuse(model_names):
        raise ModelIntegrityError("PaddlePaddle/UVDoc", INTEGRITY_REASON)

    monkeypatch.setattr(paddle_provider, "_worker_engine", None)
    monkeypatch.setattr(paddle_provider, "_pinned_engine_kwargs", _refuse)

    with pytest.raises(ModelIntegrityError) as exc:
        paddle_provider._paddle_ocr_predict(b"\x00", (1,), "uint8", {})

    assert exc.value.model_id == "PaddlePaddle/UVDoc"


_REFUSALS = [
    ModelIntegrityError("PaddlePaddle/UVDoc", INTEGRITY_REASON),
    UnpinnedModelError(UNPINNED),
    whisper_models.WhisperModelNotAllowed("Whisper model is not in the supported model registry"),
]
_REFUSAL_IDS = ["integrity", "unpinned", "whisper"]


@pytest.mark.parametrize("exc", _REFUSALS, ids=_REFUSAL_IDS)
def test_every_refusal_is_a_model_refusal_that_survives_pickling(exc: ModelRefusal) -> None:
    clone = pickle.loads(pickle.dumps(exc))

    assert isinstance(exc, ModelRefusal)
    assert type(clone) is type(exc)
    assert (clone.code, str(clone), vars(clone)) == (exc.code, str(exc), vars(exc))


def test_integrity_error_crosses_a_real_process_pool_unchanged() -> None:
    import multiprocessing as mp

    from septum_core.model_integrity import require_pin

    with mp.get_context("spawn").Pool(1) as pool:
        with pytest.raises(ModelIntegrityError) as exc:
            pool.apply(require_pin, (UNPINNED,))

    assert (exc.value.model_id, exc.value.reason) == (UNPINNED, "model has no pin")


def test_worker_integrity_failure_reaches_the_caller_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import numpy as np

    sent = ModelIntegrityError("PaddlePaddle/UVDoc", INTEGRITY_REASON)

    class _Pool:
        def apply(self, fn, args):
            raise pickle.loads(pickle.dumps(sent))

    monkeypatch.setattr(paddle_provider, "_get_ocr_pool", lambda: _Pool())

    provider = paddle_provider.PaddleOcrProvider()
    image = np.zeros((2, 2, 3), np.uint8)

    with pytest.raises(ModelIntegrityError) as exc:
        provider._predict_in_subprocess(image)

    assert (exc.value.code, exc.value.model_id) == ("model_integrity_failed", "PaddlePaddle/UVDoc")


def test_other_worker_failures_still_yield_no_results(monkeypatch: pytest.MonkeyPatch) -> None:
    import numpy as np

    class _Pool:
        def apply(self, fn, args):
            raise RuntimeError("worker crashed")

    monkeypatch.setattr(paddle_provider, "_get_ocr_pool", lambda: _Pool())

    result = paddle_provider.PaddleOcrProvider()._predict_in_subprocess(
        np.zeros((2, 2, 3), np.uint8)
    )

    assert result == []


def _pdf_with_image() -> tuple[fitz.Document, fitz.Page]:
    doc = fitz.open()
    page = doc.new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 200), False)
    pix.clear_with(200)
    page.insert_image(fitz.Rect(0, 0, 200, 200), pixmap=pix)
    return doc, page


@pytest.mark.parametrize("exc", _REFUSALS, ids=_REFUSAL_IDS)
def test_pdf_image_ocr_does_not_swallow_a_model_refusal(
    monkeypatch: pytest.MonkeyPatch, exc: ModelRefusal
) -> None:
    def _refuse(*args, **kwargs):
        raise exc

    monkeypatch.setattr(pdf_ingester_module, "run_ocr", _refuse)
    doc, page = _pdf_with_image()

    ingester = PdfIngester()

    with pytest.raises(type(exc)):
        ingester._ocr_page_images(page, doc)


def test_pdf_image_ocr_still_skips_ordinary_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail(*args, **kwargs):
        raise RuntimeError("unreadable image")

    monkeypatch.setattr(pdf_ingester_module, "run_ocr", _fail)
    doc, page = _pdf_with_image()

    assert PdfIngester()._ocr_page_images(page, doc) == ([], [])


# --- settings ---------------------------------------------------------------

_WHISPER_REGISTRY = {"base": f"https://example.invalid/models/{'c' * 64}/base.pt"}


async def _admin_headers(client: AsyncClient) -> dict[str, str]:
    resp = await client.post(
        "/api/auth/register",
        json={"email": "root@example.com", "password": "password-12345"},
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def test_unpinned_ner_override_is_refused_with_code_and_no_echo(
    router_client: AsyncClient,
) -> None:
    headers = await _admin_headers(router_client)
    before = (await router_client.get("/api/settings", headers=headers)).json()

    resp = await router_client.patch(
        "/api/settings",
        headers=headers,
        json={"ner_model_overrides": {"en": f"{next(iter(MODEL_PINS))}, {UNPINNED}"}},
    )

    assert resp.status_code == 400
    assert resp.json()["code"] == "ner_model_unpinned"
    assert UNPINNED not in resp.text
    after = (await router_client.get("/api/settings", headers=headers)).json()
    assert after["ner_model_overrides"] == before["ner_model_overrides"]


@pytest.mark.parametrize(
    "override",
    [next(iter(MODEL_PINS)), f"{UNPINNED}@{'d' * 40}"],
    ids=["manifest-id", "commit-pinned"],
)
async def test_pinned_ner_override_is_accepted(router_client: AsyncClient, override: str) -> None:
    headers = await _admin_headers(router_client)

    resp = await router_client.patch(
        "/api/settings", headers=headers, json={"ner_model_overrides": {"en": override}}
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["ner_model_overrides"] == {"en": override}


async def test_unregistered_whisper_model_is_refused_with_code_and_no_echo(
    router_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(whisper_models, "registry_models", lambda: _WHISPER_REGISTRY)
    headers = await _admin_headers(router_client)

    resp = await router_client.patch(
        "/api/settings", headers=headers, json={"whisper_model": "/tmp/probe-checkpoint.pt"}
    )

    assert resp.status_code == 400
    assert resp.json()["code"] == "whisper_model_not_allowed"
    assert "probe-checkpoint" not in resp.text


async def test_registered_whisper_model_is_accepted(
    router_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(whisper_models, "registry_models", lambda: _WHISPER_REGISTRY)
    headers = await _admin_headers(router_client)

    resp = await router_client.patch("/api/settings", headers=headers, json={"whisper_model": "base"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["whisper_model"] == "base"


async def test_require_registry_model_http_answers_400_with_the_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from septum_api.errors import CodedHTTPError

    monkeypatch.setattr(whisper_models, "registry_models", lambda: _WHISPER_REGISTRY)

    with pytest.raises(CodedHTTPError) as exc:
        await whisper_models.require_registry_model_http("/tmp/probe-checkpoint.pt")

    assert (exc.value.status_code, exc.value.code) == (400, "whisper_model_not_allowed")
    assert "probe-checkpoint" not in str(exc.value.detail)
    assert await whisper_models.require_registry_model_http("base") == "base"


def test_ensure_whisper_model_refuses_a_local_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(whisper_models, "registry_models", lambda: _WHISPER_REGISTRY)
    monkeypatch.setattr(
        whisper_models, "install_whisper_file", lambda *a, **k: pytest.fail("must not install")
    )

    with pytest.raises(whisper_models.WhisperModelNotAllowed):
        whisper_models.ensure_whisper_model("/tmp/any-checkpoint.pt")


# --- documents --------------------------------------------------------------


def _document(**overrides: Any) -> Document:
    fields: dict[str, Any] = dict(
        filename="stored-a.pdf",
        original_filename="a.pdf",
        file_type="application/pdf",
        file_format="pdf",
        detected_language="en",
        encrypted_path="/nonexistent",
        file_size_bytes=10,
        ocr_confidence=0.0,
        active_regulation_ids=[],
        ingestion_status="processing",
    )
    fields.update(overrides)
    return Document(**fields)


class _FutureRefusal(ModelRefusal):
    """A refusal type the documents router has never heard of."""

    code = "future_refusal"


@pytest.fixture
async def doc_session(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'docs.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield maker
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (ModelIntegrityError("acme/m", INTEGRITY_REASON), "model_integrity_failed"),
        (UnpinnedModelError(UNPINNED), "ner_model_unpinned"),
        (whisper_models.WhisperModelNotAllowed("refused"), "whisper_model_not_allowed"),
        (_FutureRefusal("refused"), "future_refusal"),
        (RuntimeError("anything else"), None),
    ],
    ids=["integrity", "unpinned", "whisper", "any-model-refusal", "other"],
)
async def test_failed_ingestion_records_the_refusal_code(
    doc_session, exc: BaseException, code: str | None
) -> None:
    async with doc_session() as session:
        doc = _document()
        session.add(doc)
        await session.commit()
        doc_id = doc.id

    async with doc_session() as session:
        await documents_router._record_background_failure(session, doc_id, exc)

    async with doc_session() as session:
        row = (await session.execute(select(Document).where(Document.id == doc_id))).scalar_one()
    assert row.ingestion_status == "failed"
    assert row.ingestion_error_code == code


async def test_reprocess_clears_the_stored_error_code(
    router_client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from septum_api.database import get_db
    from septum_api.main import app

    monkeypatch.setattr(BackgroundTasks, "add_task", lambda self, *a, **k: None)
    headers = await _admin_headers(router_client)
    source = tmp_path / "source.enc"
    source.write_bytes(b"x")
    session_gen = app.dependency_overrides[get_db]()
    session = await session_gen.__anext__()
    try:
        doc = _document(
            encrypted_path=str(source),
            ingestion_status="failed",
            ingestion_error="refused",
            ingestion_error_code="model_integrity_failed",
        )
        session.add(doc)
        await session.commit()
        doc_id = doc.id
    finally:
        await session_gen.aclose()

    resp = await router_client.post(f"/api/documents/{doc_id}/reprocess", headers=headers)

    assert resp.status_code == 200, resp.text
    assert resp.json()["ingestion_error_code"] is None
    listed = await router_client.get(f"/api/documents/{doc_id}", headers=headers)
    assert listed.json()["ingestion_error_code"] is None


# --- chat -------------------------------------------------------------------


@pytest.mark.parametrize("exc", _REFUSALS, ids=_REFUSAL_IDS)
async def test_chat_stream_reports_a_refused_model_with_its_code(
    chat_env: chat_security._ChatEnv, monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    async def _refuse(*args, **kwargs):
        raise exc

    monkeypatch.setattr(chat_module, "_sanitize_query", _refuse)
    chat_env.act_as("alice")

    _, events, _ = await chat_env.ask({"message": "hello"})

    errors = [e for e in events if e["type"] == "error"]
    assert errors == [{"type": "error", "code": exc.code, "message": MODEL_REFUSED_DETAIL}]
    assert chat_env.prompts == []
    assert UNPINNED not in json.dumps(events)
    assert INTEGRITY_REASON not in json.dumps(events)


async def test_analyze_query_answers_503_with_the_refusal_code(
    chat_env: chat_security._ChatEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _refuse(*args, **kwargs):
        raise UnpinnedModelError(UNPINNED)

    monkeypatch.setattr(chat_module, "_sanitize_query", _refuse)
    chat_env.act_as("alice")

    resp = await chat_env.client.post("/api/chat/analyze_query", json={"message": "hello"})

    assert resp.status_code == 503
    assert resp.json()["code"] == "ner_model_unpinned"
    assert UNPINNED not in resp.text


# --- warmup -----------------------------------------------------------------


def test_warmup_logs_an_integrity_refusal_as_a_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from septum_core import detector

    from septum_api.services import ner_model_registry as api_ner_registry
    from septum_api.services import sanitizer

    def _refuse():
        raise ModelIntegrityError("acme/m", INTEGRITY_REASON)

    for module in (sanitizer, detector):
        if hasattr(module, "_get_shared_nlp_engine"):
            monkeypatch.setattr(module, "_get_shared_nlp_engine", lambda: None)
    monkeypatch.setattr(api_ner_registry, "get_shared_ner_registry", _refuse)

    with caplog.at_level(logging.DEBUG, logger=main_module.logger.name):
        main_module._preload_models()

    [record] = [r for r in caplog.records if r.name == main_module.logger.name]
    assert record.levelno == logging.WARNING
    assert "model_integrity_failed" in record.getMessage()


# --- CLI --------------------------------------------------------------------

_CLI_REPO = next(iter(MODEL_PINS))


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Mock the pinned fetch so no CLI test reaches the Hub; record every call."""
    from huggingface_hub import constants

    state: dict[str, Any] = {"calls": [], "offline_seen": [], "fetched": False}
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(tmp_path / "hub"))
    monkeypatch.setattr(whisper_models, "registry_models", lambda: _WHISPER_REGISTRY)

    def _fetch(pin, cache_dir=None):
        state["calls"].append(pin.repo_id)
        state["offline_seen"].append(os.environ.get("HF_HUB_OFFLINE"))
        return tmp_path, state["fetched"]

    monkeypatch.setattr(model_integrity, "fetch_pinned_snapshot", _fetch)
    return state


def test_cli_reports_ok_for_a_verified_cache(cli_env, capsys: pytest.CaptureFixture) -> None:
    code = cli_models.main(["fetch", "--only", _CLI_REPO])

    assert code == 0
    assert cli_env["calls"] == [_CLI_REPO]
    assert capsys.readouterr().out.splitlines()[-1].startswith("ok ")


def test_cli_reports_fetched_when_files_were_downloaded(
    cli_env, capsys: pytest.CaptureFixture
) -> None:
    cli_env["fetched"] = True

    code = cli_models.main(["fetch", "--only", _CLI_REPO])

    assert code == 0
    assert capsys.readouterr().out.splitlines()[-1].startswith("fetched ")


def test_cli_exits_1_with_the_code_when_a_fetch_fails(
    cli_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    def _refuse(pin, cache_dir=None):
        raise ModelIntegrityError(pin.repo_id, INTEGRITY_REASON)

    monkeypatch.setattr(model_integrity, "fetch_pinned_snapshot", _refuse)

    code = cli_models.main(["fetch", "--only", _CLI_REPO])

    out = capsys.readouterr().out
    assert code == 1
    assert "failed" in out
    assert "model_integrity_failed" in out


def test_cli_offline_check_fetches_with_offline_mode_on(cli_env) -> None:
    code = cli_models.main(["fetch", "--offline-check", "--only", _CLI_REPO])

    assert code == 0
    assert cli_env["offline_seen"] == ["1"]


def test_cli_offline_check_still_runs_when_hub_offline_is_already_set(
    cli_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")

    code = cli_models.main(["fetch", "--offline-check", "--only", _CLI_REPO])

    assert code == 0
    assert cli_env["offline_seen"] == ["1"]


@pytest.mark.parametrize("value", ["1", "true", "ON", "yes"])
def test_cli_plain_fetch_is_refused_up_front_when_hub_offline_is_set(
    cli_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, value: str
) -> None:
    monkeypatch.setenv("HF_HUB_OFFLINE", value)

    code = cli_models.main(["fetch", "--only", _CLI_REPO])

    captured = capsys.readouterr()
    assert code == 2
    assert cli_env["calls"] == []
    assert captured.out == ""
    assert captured.err.strip() == cli_models.OFFLINE_REFUSAL
    assert "HF_HUB_OFFLINE=0" in captured.err


def test_cli_offline_check_exits_1_on_a_missing_model(
    cli_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _missing(pin, cache_dir=None):
        raise ModelIntegrityError(pin.repo_id, "pinned files are not in the local model cache")

    monkeypatch.setattr(model_integrity, "fetch_pinned_snapshot", _missing)

    assert cli_models.main(["fetch", "--offline-check", "--only", _CLI_REPO]) == 1


def test_cli_exits_1_for_an_unregistered_whisper_model(cli_env) -> None:
    assert cli_models.main(["fetch", "--only", _CLI_REPO, "--whisper", "not-a-model"]) == 1


@pytest.mark.parametrize(
    "argv",
    [["fetch", "--only", UNPINNED], [], ["fetch", "--bogus"]],
    ids=["unknown-repo", "no-command", "bad-flag"],
)
def test_cli_exits_2_on_bad_usage(cli_env, argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli_models.main(argv)

    assert exc.value.code == 2
    assert cli_env["calls"] == []


_OFFLINE_PROBE = """
import sys
from septum_api.cli import models

def _probe(args):
    from huggingface_hub import constants
    print("offline=" + str(constants.is_offline_mode()))
    return 0

models._run_fetch = _probe
sys.exit(models.main(["fetch", "--offline-check", "--only", sys.argv[1]]))
"""


def test_cli_offline_check_is_in_force_before_the_hub_is_imported() -> None:
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if k != "HF_HUB_OFFLINE"}

    result = subprocess.run(
        [sys.executable, "-c", _OFFLINE_PROBE, _CLI_REPO],
        capture_output=True,
        text=True,
        env=env,
        cwd=_PACKAGES / "api",
        timeout=120,
    )

    assert result.returncode == 0, result.stderr[-2000:]
    assert "offline=True" in result.stdout


async def test_successful_pipeline_run_clears_a_previous_error_code(
    doc_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from contextlib import asynccontextmanager

    from septum_api.database import build_default_app_settings
    from septum_api.services import document_pipeline as dp

    class _Lock:
        async def ensure_held(self) -> None:
            return None

    @asynccontextmanager
    async def _map_lock(document_id):
        yield _Lock()

    async def _noop(*args, **kwargs):
        return None

    async def _no_chunks(self, file_format, text):
        return []

    async def _persist(self, db, document_id, chunks, raw_texts):
        return []

    monkeypatch.setattr(dp, "create_sanitizer", _noop)
    monkeypatch.setattr(dp.DocumentPipeline, "_build_chunks", _no_chunks)
    monkeypatch.setattr(dp.DocumentPipeline, "_persist_chunks", _persist)
    monkeypatch.setattr(dp, "document_map_lock", _map_lock)
    monkeypatch.setattr(dp, "set_document_map", _noop)
    monkeypatch.setattr(dp, "replace_index_for_document", _noop)
    monkeypatch.setattr(dp, "recompute_relationships_for_document", _noop)
    async with doc_session() as session:
        doc = _document(ingestion_error="refused", ingestion_error_code="model_integrity_failed")
        session.add(doc)
        await session.commit()

        await dp.DocumentPipeline(settings=build_default_app_settings()).run(
            db=session,
            document=doc,
            file_format="pdf",
            ingested_text="",
            ingestion_confidence=None,
        )

    async with doc_session() as session:
        row = (await session.execute(select(Document).where(Document.id == doc.id))).scalar_one()
    assert row.ingestion_status == "completed"
    assert row.ingestion_error_code is None


async def test_sqlite_startup_adds_error_code_to_an_older_documents_table(
    tmp_path: Path,
) -> None:
    from sqlalchemy import text

    from septum_api.database import _sqlite_ensure_columns

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("ALTER TABLE documents DROP COLUMN ingestion_error_code"))

    await _sqlite_ensure_columns(engine)
    await _sqlite_ensure_columns(engine)

    async with engine.connect() as conn:
        rows = (await conn.execute(text("PRAGMA table_info(documents)"))).all()
    await engine.dispose()
    column = {row[1]: row for row in rows}["ingestion_error_code"]
    assert column[2] == "VARCHAR(64)"
    assert column[3] == 0


# --- app-level refusal handler ----------------------------------------------


async def test_refused_model_on_a_sanitizing_endpoint_answers_503_with_its_code(
    router_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from septum_api.database import get_db
    from septum_api.main import app
    from septum_api.models.document import Chunk
    from septum_api.routers import chunks as chunks_router

    async def _refuse(*args, **kwargs):
        raise ModelIntegrityError("acme/m", INTEGRITY_REASON)

    monkeypatch.setattr(chunks_router, "gate_document_texts", _refuse)
    headers = await _admin_headers(router_client)
    session_gen = app.dependency_overrides[get_db]()
    session = await session_gen.__anext__()
    try:
        doc = _document(ingestion_status="completed")
        session.add(doc)
        await session.flush()
        chunk = Chunk(document_id=doc.id, index=0, sanitized_text="old", char_count=3)
        session.add(chunk)
        await session.commit()
        chunk_id = chunk.id
    finally:
        await session_gen.aclose()

    resp = await router_client.patch(
        f"/api/chunks/{chunk_id}", headers=headers, json={"sanitized_text": "edited text"}
    )

    assert resp.status_code == 503
    assert resp.json() == {"detail": MODEL_REFUSED_DETAIL, "code": "model_integrity_failed"}
    assert INTEGRITY_REASON not in resp.text


async def test_patching_whisper_model_to_null_is_not_refused(
    router_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        whisper_models, "registry_models", lambda: pytest.fail("null must not be looked up")
    )
    headers = await _admin_headers(router_client)

    resp = await router_client.patch("/api/settings", headers=headers, json={"whisper_model": None})

    assert resp.status_code == 200, resp.text


# --- chunking ---------------------------------------------------------------


@pytest.mark.parametrize(
    "method", ["_split_large_section_semantic", "_fallback_semantic_chunking"]
)
def test_chunking_does_not_fall_back_past_a_model_refusal(
    monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    from septum_api.services.chunking_strategy import StructuredDocumentChunker

    def _refuse(self):
        raise ModelIntegrityError(vector_store_module._DEFAULT_ST_MODEL, INTEGRITY_REASON)

    monkeypatch.setattr(StructuredDocumentChunker, "_get_semantic_splitter", _refuse)
    chunker = StructuredDocumentChunker()
    args = ("para one\n\npara two", None) if method.startswith("_split") else ("para one\n\npara two",)
    chunk = getattr(chunker, method)

    with pytest.raises(ModelIntegrityError):
        chunk(*args)


@pytest.mark.parametrize(
    "method", ["_split_large_section_semantic", "_fallback_semantic_chunking"]
)
def test_chunking_still_falls_back_on_ordinary_errors(
    monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    from septum_api.services.chunking_strategy import StructuredDocumentChunker

    def _fail(self):
        raise RuntimeError("splitter broke")

    monkeypatch.setattr(StructuredDocumentChunker, "_get_semantic_splitter", _fail)
    chunker = StructuredDocumentChunker()
    args = ("para one\n\npara two", None) if method.startswith("_split") else ("para one\n\npara two",)

    assert getattr(chunker, method)(*args)


# --- warmup settings check --------------------------------------------------


def test_warmup_warns_once_with_codes_for_refused_stored_settings(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from types import SimpleNamespace

    from septum_core import detector

    from septum_api.services import ner_model_registry as api_ner_registry

    monkeypatch.setattr(whisper_models, "registry_models", lambda: _WHISPER_REGISTRY)
    monkeypatch.setattr(detector, "_get_shared_nlp_engine", lambda: None)
    monkeypatch.setattr(
        api_ner_registry,
        "get_shared_ner_registry",
        lambda: SimpleNamespace(get_pipeline=lambda lang: None),
    )
    monkeypatch.setattr(
        vector_store_module,
        "get_shared_sentence_transformer",
        lambda: SimpleNamespace(encode=lambda *a, **k: None),
    )

    with caplog.at_level(logging.DEBUG, logger=main_module.logger.name):
        main_module._preload_models(
            ner_overrides={"en": f"{next(iter(MODEL_PINS))}, {UNPINNED}"},
            whisper_model="/tmp/probe-checkpoint.pt",
        )

    [record] = [r for r in caplog.records if r.name == main_module.logger.name]
    message = record.getMessage()
    assert record.levelno == logging.WARNING
    assert "ner_model_unpinned" in message
    assert "whisper_model_not_allowed" in message
    assert UNPINNED not in message
    assert "probe-checkpoint" not in message


def test_warmup_is_silent_for_accepted_stored_settings(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(whisper_models, "registry_models", lambda: _WHISPER_REGISTRY)

    with caplog.at_level(logging.DEBUG, logger=main_module.logger.name):
        main_module._warn_on_refused_model_settings({"en": next(iter(MODEL_PINS))}, "base")

    assert [r for r in caplog.records if r.name == main_module.logger.name] == []


# --- PaddleX never resolves a model on its own ---------------------------


def _build_engine_offline_by_construction(
    model_names: dict[str, str],
) -> tuple[list[str], list[str]]:
    """Worker body: build PaddleOCR from pinned dirs while recording any outbound connect.

    The pinned dirs are fetched first (a no-op on a warm cache). Only then is
    PaddleX's own model lookup disabled and every non-loopback connect
    recorded and refused, so the record covers importing PaddleOCR and
    building the engine and nothing else.
    """
    import ipaddress
    import socket

    kwargs = paddle_provider._pinned_engine_kwargs(model_names)
    attempts: list[str] = []
    real_connect = socket.socket.connect

    def _recording_connect(self, address):
        host = address[0] if isinstance(address, tuple) else None
        try:
            local = host is None or ipaddress.ip_address(str(host).split("%")[0]).is_loopback
        except ValueError:
            local = host == "localhost"
        if not local:
            attempts.append(repr(address))
            raise ConnectionRefusedError(f"blocked {address!r}")
        return real_connect(self, address)

    socket.socket.connect = _recording_connect
    from paddlex.inference.utils import official_models

    def _forbidden(self, model_name, **kw):
        raise AssertionError(f"PaddleX resolved '{model_name}' itself")

    official_models._ModelManager.get_model_path = _forbidden
    from paddleocr import PaddleOCR

    PaddleOCR(**kwargs)
    return sorted(kwargs), attempts


@pytest.mark.real_network
def test_ocr_engine_is_built_without_paddlex_lookup_or_network() -> None:
    import multiprocessing as mp

    names = dict(paddle_provider.PaddleOcrProvider._MODEL_NAMES)

    with mp.get_context("spawn").Pool(1) as pool:
        built, attempts = pool.apply(_build_engine_offline_by_construction, (names,))

    assert built == sorted(
        f"{prefix}_model_{part}" for prefix in names for part in ("dir", "name")
    )
    assert attempts == []


# --- CLI --user-pin -----------------------------------------------------------


@pytest.fixture
def user_pin_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Mock the user-pinned fetch and record ``(repo_id, revision)`` per call."""
    calls: list[tuple[str, str]] = []
    monkeypatch.setenv("SEPTUM_USER_PINS_PATH", str(tmp_path / "septum-user-pins.json"))

    def _fetch(repo_id, revision, cache_dir=None):
        calls.append((repo_id, revision))
        return tmp_path, True

    monkeypatch.setattr(model_integrity, "fetch_user_pinned_snapshot", _fetch)
    return calls


def test_cli_user_pin_fetches_only_that_model(
    cli_env, user_pin_calls, capsys: pytest.CaptureFixture
) -> None:
    spec = f"{UNPINNED}@{'d' * 40}"

    code = cli_models.main(["fetch", "--user-pin", spec])

    out = capsys.readouterr().out
    assert code == 0
    assert user_pin_calls == [(UNPINNED, "d" * 40)]
    assert cli_env["calls"] == []
    assert out.splitlines()[-1].startswith("fetched ")


def test_cli_user_pin_without_a_commit_exits_1(
    cli_env, user_pin_calls, capsys: pytest.CaptureFixture
) -> None:
    code = cli_models.main(["fetch", "--user-pin", UNPINNED])

    assert code == 1
    assert user_pin_calls == []
    assert "ner_model_unpinned" in capsys.readouterr().out


def test_cli_user_pin_failure_exits_1_with_the_code(
    cli_env, user_pin_calls, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    def _refuse(repo_id, revision, cache_dir=None):
        raise ModelIntegrityError(repo_id, "file 'config.json' does not match the Hub's metadata")

    monkeypatch.setattr(model_integrity, "fetch_user_pinned_snapshot", _refuse)

    code = cli_models.main(["fetch", "--user-pin", f"{UNPINNED}@{'d' * 40}"])

    assert code == 1
    assert "model_integrity_failed" in capsys.readouterr().out


def test_cli_user_pin_is_refused_up_front_when_hub_offline_is_set(
    cli_env, user_pin_calls, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")

    code = cli_models.main(["fetch", "--user-pin", f"{UNPINNED}@{'d' * 40}"])

    assert code == 2
    assert user_pin_calls == []
    assert "HF_HUB_OFFLINE=0" in capsys.readouterr().err


def test_warmup_skips_the_whisper_check_when_whisper_is_not_installed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import importlib.util

    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *a, **k: None if name == "whisper" else real_find_spec(name, *a, **k),
    )
    monkeypatch.setattr(
        whisper_models, "registry_models", lambda: pytest.fail("registry must not be read")
    )

    with caplog.at_level(logging.DEBUG, logger=main_module.logger.name):
        main_module._warn_on_refused_model_settings({"en": UNPINNED}, "/tmp/probe-checkpoint.pt")

    [record] = [r for r in caplog.records if r.name == main_module.logger.name]
    assert "ner_model_unpinned" in record.getMessage()
    assert "whisper_model_not_allowed" not in record.getMessage()
