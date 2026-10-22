"""``detect_language``: canonical codes, script guard, and the evaluation-set counts."""

from __future__ import annotations

from collections import Counter

import pytest
from langdetect import DetectorFactory
from septum_core.language_resolution import language_table_codes

from septum_api.utils.db_helpers import detect_language
from tests.benchmark_retrieval import fill_document, fill_query
from tests.retrieval_eval import load_docs, load_queries


@pytest.fixture(autouse=True)
def _seed_langdetect():
    DetectorFactory.seed = 42


def test_chinese_document_text_detects_as_base_code():
    assert detect_language("合同双方应当按照约定全面履行自己的义务，不得擅自变更或者解除合同。") == "zh"


def test_han_only_short_query_is_never_korean():
    assert detect_language("血压计使用什么电池？") == "zh"
    assert detect_language("准妈妈能被要求值夜班吗？", context_language="en") == "zh"


def test_hangul_query_detects_as_korean():
    assert detect_language("혈압계는 어떤 배터리를 사용합니까?") == "ko"


def test_low_confidence_query_uses_document_language():
    assert detect_language("Hello, how are you today?") == "en"
    assert detect_language("Hello, how are you today?", context_language="tr") == "tr"
    assert detect_language("软件系统的质保期有多长？", context_language="zh-cn") == "zh"


def test_english_and_turkish_unchanged():
    assert detect_language("How much deposit did the tenant pay?") == "en"
    assert detect_language("Gecikmiş ödemelere hangi faiz uygulanır?") == "tr"


def test_cross_lingual_query_keeps_its_language():
    assert detect_language("How many days of marriage leave are there?", context_language="zh") == "en"
    assert detect_language("Babalık izni kaç gündür?", context_language="zh") == "tr"


def test_featureless_text_uses_context_then_fallback():
    assert detect_language("12345", context_language="tr") == "tr"
    assert detect_language("", fallback="de") == "de"


def test_evaluation_set_detection_counts():
    """Docs alone and queries with their document as context, the way chat calls it."""
    docs: dict[str, tuple[int, str, dict]] = {}
    doc_hits: Counter[str] = Counter()
    for key, meta in enumerate(load_docs(), start=1):
        filled, values = fill_document(key, meta.lang, meta.read_text())
        docs[meta.doc_id] = (key, meta.lang, values)
        doc_hits[meta.lang] += detect_language(filled) == meta.lang

    totals: Counter[str] = Counter()
    hits: Counter[str] = Counter()
    for query in load_queries():
        doc = docs.get(query.doc_id) if query.doc_id else None
        doc_key, doc_lang, values = doc if doc else (None, None, {})
        text = fill_query(query.text, doc_key, doc_lang or query.lang, values)
        totals[query.lang] += 1
        hits[query.lang] += detect_language(text, context_language=doc_lang) == query.lang

    assert doc_hits == Counter({"en": 10, "tr": 10, "zh": 10})
    assert hits["en"] == totals["en"]
    assert hits["tr"] == totals["tr"]
    assert totals["zh"] == 46 and hits["zh"] >= 45


def test_threshold_boundary_via_detect_language(monkeypatch):
    from types import SimpleNamespace

    from septum_api.utils import db_helpers

    def _guesses(top: float):
        return lambda _text: [
            SimpleNamespace(lang="es", prob=top),
            SimpleNamespace(lang="pt", prob=round(1 - top, 2)),
        ]

    monkeypatch.setattr(db_helpers, "detect_langs", _guesses(0.8))
    assert detect_language("Gracias por todo", context_language="it") == "es"
    monkeypatch.setattr(db_helpers, "detect_langs", _guesses(0.79))
    assert detect_language("Gracias por todo", context_language="it") == "it"


def test_audio_language_from_whisper_is_canonicalized():
    from types import SimpleNamespace

    from septum_api.routers.documents import _detect_document_language

    result = SimpleNamespace(text="ignored", metadata={"detected_language": " ZH-CN "})
    assert _detect_document_language("audio", result) == "zh"


def test_audio_without_whisper_language_falls_back_to_text_detection():
    from types import SimpleNamespace

    from septum_api.routers.documents import _detect_document_language

    result = SimpleNamespace(text="Gecikmiş ödemelere hangi faiz uygulanır?", metadata={"detected_language": "  "})
    assert _detect_document_language("audio", result) == "tr"
    assert _detect_document_language("pdf", SimpleNamespace(text="血压计使用什么电池？", metadata=None)) == "zh"


def test_url_padded_chinese_document_detects_as_chinese():
    padding = " ".join(f"https://example.com/archive/page/{n}" for n in range(400))
    text = padding + "\n合同双方应当按照约定全面履行自己的义务，不得擅自变更或者解除合同。"
    assert detect_language(text) == "zh"


def test_query_language_uses_the_target_document_language():
    from types import SimpleNamespace

    from septum_api.routers.chat import query_language

    stored_region_tag = SimpleNamespace(language_override=None, detected_language="zh-cn")
    overridden = SimpleNamespace(language_override="tr", detected_language="en")
    assert query_language("准妈妈能被要求值夜班吗？", stored_region_tag) == "zh"
    assert query_language("Hello, how are you today?", overridden) == "tr"
    assert query_language("Hello, how are you today?", None) == "en"
    assert query_language("How much deposit did the tenant pay?", stored_region_tag) == "en"


async def test_analyze_query_detects_with_the_requested_document_language(monkeypatch):
    from types import SimpleNamespace

    from septum_api.routers import chat as chat_module

    document = SimpleNamespace(id=7, language_override=None, detected_language="tr")
    seen: dict = {}

    async def _settings(_db):
        return SimpleNamespace()

    async def _owned(_db, _model, pk, _user, _detail):
        seen["document_id"] = pk
        return document

    async def _sanitize(_message, language, _settings, _anon_map, _db):
        seen["language"] = language

    monkeypatch.setattr(chat_module, "load_settings", _settings)
    monkeypatch.setattr(chat_module, "get_owned_or_404", _owned)
    monkeypatch.setattr(chat_module, "_sanitize_query", _sanitize)
    response = await chat_module.chat_analyze_query(
        chat_module.AnalyzeQueryRequest(message="Hello, how are you today?", document_ids=[7]),
        db=None,
        user=SimpleNamespace(id=1, role="editor"),
    )
    assert seen == {"document_id": 7, "language": "tr"}
    assert response.reason == "no_entity_in_query"

    seen.clear()
    await chat_module.chat_analyze_query(
        chat_module.AnalyzeQueryRequest(message="Hello, how are you today?"),
        db=None,
        user=SimpleNamespace(id=1, role="editor"),
    )
    assert seen == {"language": "en"}


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _FakeDb:
    def __init__(self, row):
        self._row = row
        self.lookups = 0

    async def execute(self, _statement):
        self.lookups += 1
        return _FakeResult(self._row)


@pytest.mark.parametrize("row_owner", [None, 999])
async def test_analyze_query_returns_404_for_missing_or_foreign_document(monkeypatch, row_owner):
    from types import SimpleNamespace

    from fastapi import HTTPException

    from septum_api.routers import chat as chat_module

    async def _settings(_db):
        return SimpleNamespace()

    async def _unreachable(*_args, **_kwargs):
        raise AssertionError("query must not be sanitized for a document the caller cannot see")

    monkeypatch.setattr(chat_module, "load_settings", _settings)
    monkeypatch.setattr(chat_module, "_sanitize_query", _unreachable)
    row = None if row_owner is None else SimpleNamespace(
        id=7, user_id=row_owner, language_override=None, detected_language="tr"
    )
    db = _FakeDb(row)
    with pytest.raises(HTTPException) as caught:
        await chat_module.chat_analyze_query(
            chat_module.AnalyzeQueryRequest(message="hello", document_ids=[7]),
            db=db,
            user=SimpleNamespace(id=1, role="editor"),
        )
    assert caught.value.status_code == 404
    assert caught.value.detail == "Document not found."


async def test_analyze_query_checks_only_the_first_document(monkeypatch):
    from types import SimpleNamespace

    from septum_api.routers import chat as chat_module

    async def _settings(_db):
        return SimpleNamespace()

    async def _sanitize(*_args, **_kwargs):
        return None

    monkeypatch.setattr(chat_module, "load_settings", _settings)
    monkeypatch.setattr(chat_module, "_sanitize_query", _sanitize)
    db = _FakeDb(SimpleNamespace(id=7, user_id=1, language_override=None, detected_language="tr"))
    await chat_module.chat_analyze_query(
        chat_module.AnalyzeQueryRequest(message="hello", document_ids=[7, 8]),
        db=db,
        user=SimpleNamespace(id=1, role="editor"),
    )
    assert db.lookups == 1


async def test_chat_ask_detects_with_the_resolved_document(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from starlette.requests import Request

    from septum_api.routers import chat as chat_module

    document = SimpleNamespace(id=7, language_override=None, detected_language="zh-cn")
    seen: dict = {}

    async def _owned(_db, _model, pk, _user, _detail):
        return document

    async def _settings(_db):
        return SimpleNamespace()

    async def _regulations(_db):
        return []

    def _language(message, doc):
        seen["args"] = (message, doc)
        raise asyncio.CancelledError

    monkeypatch.setattr(chat_module, "get_owned_or_404", _owned)
    monkeypatch.setattr(chat_module, "load_settings", _settings)
    monkeypatch.setattr(chat_module, "_active_regulations", _regulations)
    monkeypatch.setattr(chat_module, "query_language", _language)
    http_request = Request({"type": "http", "method": "POST", "path": "/api/chat/ask", "headers": []})
    response = await chat_module.chat_ask(
        chat_module.ChatRequest(message="押金是多少？", document_ids=[7]),
        http_request,
        db=None,
        current_user=SimpleNamespace(id=1, role="editor"),
    )
    with pytest.raises(asyncio.CancelledError):
        async for _ in response.body_iterator:
            pass
    assert seen["args"] == ("押金是多少？", document)


def test_detection_cost_is_bounded_by_the_sample():
    from septum_core.language_resolution import DETECTION_SAMPLE_CHARS, detection_sample

    body = "合同双方应当按照约定全面履行自己的义务。"
    small, large = body * 600, body * 600_000
    assert detection_sample(large) == detection_sample(small)[:DETECTION_SAMPLE_CHARS]
    assert detect_language(large) == "zh"


def test_prefix_language_wins_over_a_body_beyond_the_sample():
    prefix = "Gecikmiş ödemelere hangi faiz uygulanır ve sözleşme ne zaman biter? " * 160
    assert detect_language(prefix + "合同双方应当按照约定全面履行自己的义务。" * 20_000) == "tr"


def test_detector_receives_at_most_the_sample(monkeypatch):
    from septum_core.language_resolution import DETECTION_SAMPLE_CHARS

    from septum_api.utils import db_helpers

    received: list[int] = []

    def _spy(text):
        received.append(len(text))
        return []

    monkeypatch.setattr(db_helpers, "detect_langs", _spy)
    detect_language("合同双方应当按照约定全面履行自己的义务。" * 600_000)
    detect_language("https://example.com/a " * 50_000 + "合同双方应当")
    assert received and max(received) <= DETECTION_SAMPLE_CHARS


def test_document_call_sites_run_detection_in_a_worker_thread():
    import inspect
    import re

    from septum_api.routers import documents

    source = inspect.getsource(documents)
    threaded = re.findall(r"asyncio\.to_thread\(\s*_detect_document_language\b", source)
    assert len(threaded) == 2
    assert source.count("_detect_document_language(") == 1


def test_cold_langdetect_factory_survives_concurrent_first_calls(monkeypatch):
    import threading

    from langdetect import detector_factory

    texts = {
        "tr": "Ahmet Yılmaz İstanbul'da yaşıyor ve Ankara'daki şirkette çalışıyor. " * 5,
        "de": "Hans Müller wohnt in Berlin und arbeitet bei einer Firma in München. " * 5,
        "en": "The tenant paid the deposit before moving into the warehouse hall. " * 5,
        "zh": "合同双方应当按照约定全面履行自己的义务，不得擅自变更或者解除合同。" * 3,
    }
    jobs = list(texts.items()) * 4
    for _ in range(3):
        monkeypatch.setattr(detector_factory, "_factory", None)
        barrier = threading.Barrier(len(jobs))
        results: list = []
        errors: list = []

        def _work(expected: str, text: str) -> None:
            barrier.wait()
            try:
                results.append((expected, detect_language(text)))
            except Exception as exc:  # recorded and asserted below
                errors.append(exc)

        threads = [threading.Thread(target=_work, args=job) for job in jobs]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []
        assert all(expected == got for expected, got in results), results
        assert len(results) == len(jobs)


def test_langdetect_errors_other_than_no_features_propagate(monkeypatch):
    from langdetect.lang_detect_exception import ErrorCode, LangDetectException

    from septum_api.utils import db_helpers

    def _broken(_text):
        raise LangDetectException(ErrorCode.NeedLoadProfileError, "Need to load profiles.")

    monkeypatch.setattr(db_helpers, "detect_langs", _broken)
    with pytest.raises(LangDetectException):
        detect_language("Gecikmiş ödemelere hangi faiz uygulanır?")


def test_link_dump_longer_than_the_raw_head_still_finds_the_body():
    from septum_core.language_resolution import DETECTION_RAW_HEAD_CHARS

    links = " ".join(f"https://example.com/archive/page/{n}" for n in range(3_000))
    assert len(links) > DETECTION_RAW_HEAD_CHARS
    text = links + "\n合同双方应当按照约定全面履行自己的义务，不得擅自变更或者解除合同。"
    assert detect_language(text) == "zh"


def test_cold_factory_is_never_seen_half_loaded(monkeypatch):
    """A slow profile load widens the window deterministically: without the lock
    a second thread sees the published but still empty factory and fails."""
    import threading
    import time

    from langdetect import detector_factory

    real_load = detector_factory.DetectorFactory.load_profile

    def _slow_load(self, profile_directory):
        time.sleep(0.2)
        real_load(self, profile_directory)

    monkeypatch.setattr(detector_factory.DetectorFactory, "load_profile", _slow_load)
    monkeypatch.setattr(detector_factory, "_factory", None)
    barrier = threading.Barrier(8)
    results: list[str] = []
    errors: list[Exception] = []

    def _work():
        barrier.wait()
        try:
            results.append(
                detect_language("The tenant paid the deposit before moving into the warehouse hall.")
            )
        except Exception as exc:  # recorded and asserted below
            errors.append(exc)

    threads = [threading.Thread(target=_work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert results == ["en"] * 8


def test_warm_factory_skips_the_lock_and_init(monkeypatch):
    from langdetect import detector_factory

    from septum_api.utils import db_helpers

    db_helpers.ensure_language_profiles_loaded()
    monkeypatch.setattr(
        detector_factory.DetectorFactory,
        "load_profile",
        lambda *_a: pytest.fail("warm factory re-initialised"),
    )
    db_helpers.ensure_language_profiles_loaded()


async def test_lifespan_loads_language_profiles_off_the_event_loop(monkeypatch):
    import threading

    from septum_api import main

    seen: list[int] = []
    monkeypatch.setattr(
        main, "ensure_language_profiles_loaded", lambda: seen.append(threading.get_ident())
    )
    monkeypatch.setattr(main.bootstrap, "needs_setup", lambda: True)
    async with main.lifespan(main.app):
        assert len(seen) == 1
        assert seen[0] != threading.get_ident()


def test_partial_profile_load_fails_loudly_and_is_not_published(monkeypatch):
    import json
    import os

    from langdetect import detector_factory
    from langdetect.lang_detect_exception import LangDetectException
    from langdetect.utils.lang_profile import LangProfile

    from septum_api.utils import db_helpers

    real_load = detector_factory.DetectorFactory.load_profile

    def _short_load(self, directory):
        names = sorted(n for n in os.listdir(directory) if not n.startswith("."))[:3]
        for index, name in enumerate(names):
            with open(os.path.join(directory, name), encoding="utf-8") as handle:
                self.add_profile(LangProfile(**json.load(handle)), index, len(names))

    monkeypatch.setattr(detector_factory, "_factory", None)
    monkeypatch.setattr(db_helpers, "_loaded_language_factory", None)
    monkeypatch.setattr(detector_factory.DetectorFactory, "load_profile", _short_load)
    for _ in range(2):
        with pytest.raises(LangDetectException, match="loaded 3 of"):
            db_helpers.ensure_language_profiles_loaded()
        assert db_helpers._loaded_language_factory is None
        assert detector_factory._factory is None
    with pytest.raises(LangDetectException):
        detect_language("Gecikmiş ödemelere hangi faiz uygulanır?")

    monkeypatch.setattr(detector_factory.DetectorFactory, "load_profile", real_load)
    assert detect_language("Gecikmiş ödemelere hangi faiz uygulanır?") == "tr"


def test_no_features_fallback_on_lettered_text_warns_without_content(monkeypatch, caplog):
    import logging

    from langdetect.lang_detect_exception import ErrorCode, LangDetectException

    from septum_api.utils import db_helpers

    def _no_features(_text):
        raise LangDetectException(ErrorCode.CantDetectError, "No features in text.")

    secret = "Ahmet Yılmaz TCKN 10000000146"
    monkeypatch.setattr(db_helpers, "detect_langs", _no_features)
    with caplog.at_level(logging.WARNING, logger=db_helpers.__name__):
        assert detect_language(secret, context_language="tr") == "tr"
    records = [r for r in caplog.records if r.name == db_helpers.__name__]
    assert len(records) == 1
    rendered = records[0].getMessage()
    assert "fell back to tr" in rendered
    for fragment in ("Ahmet", "Yılmaz", "10000000146"):
        assert fragment not in rendered
        assert all(fragment not in str(arg) for arg in records[0].args or ())

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=db_helpers.__name__):
        assert detect_language("12345") == "en"
    assert not [r for r in caplog.records if r.name == db_helpers.__name__]


def test_profile_check_counts_only_regular_non_hidden_files(monkeypatch, tmp_path):
    import shutil
    from pathlib import Path

    from langdetect import detector_factory
    from langdetect.lang_detect_exception import LangDetectException

    from septum_api.utils import db_helpers

    class _Factory:
        def __init__(self, count):
            self._count = count

        def get_lang_list(self):
            return ["x"] * self._count

    real = sorted(Path(detector_factory.PROFILES_DIRECTORY).iterdir())
    for source in [p for p in real if not p.name.startswith(".")][:3]:
        shutil.copy(source, tmp_path / source.name)
    (tmp_path / ".hidden").write_text("{}")
    (tmp_path / "subdir").mkdir()
    monkeypatch.setattr(detector_factory, "PROFILES_DIRECTORY", str(tmp_path))

    db_helpers._require_complete_profiles(_Factory(3))
    monkeypatch.setattr(detector_factory, "_factory", object())
    with pytest.raises(LangDetectException, match="loaded 4 of 3"):
        db_helpers._require_complete_profiles(_Factory(4))
    assert detector_factory._factory is None


def test_profile_check_rejects_an_empty_profile_directory(monkeypatch, tmp_path):
    from langdetect import detector_factory
    from langdetect.lang_detect_exception import LangDetectException

    from septum_api.utils import db_helpers

    monkeypatch.setattr(detector_factory, "PROFILES_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(detector_factory, "_factory", object())
    with pytest.raises(LangDetectException, match="of 0"):
        db_helpers._require_complete_profiles(None)
    assert detector_factory._factory is None


@pytest.mark.parametrize(
    "bad",
    ["evil\nFORGED", "en US", "tr ", "x" * 36, "e", "english-language-name-that-is-long", "zh-", ""],
)
async def test_language_override_payload_rejects_malformed_tags_with_422(bad):
    from types import SimpleNamespace

    from httpx import ASGITransport, AsyncClient

    from septum_api.database import get_db
    from septum_api.main import app
    from septum_api.utils.auth_dependency import get_current_user

    async def _no_db():
        yield None

    app.dependency_overrides[get_db] = _no_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, role="editor")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.patch("/api/documents/1/language", json={"language": bad})
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"][0]["loc"] == ["body", "language"]
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


async def test_language_override_is_stored_as_its_base_code(monkeypatch):
    from types import SimpleNamespace

    from septum_api.models.document import Document
    from septum_api.routers import documents as documents_router

    document = Document(
        id=1, language_override=None, detected_language="en", transcription_text=None,
        ingestion_status="processing", sanitized_policy_digest="d",
    )

    async def _owned(_db, _model, _pk, _user, _detail, **_kwargs):
        return document

    async def _response(_db, doc):
        return doc.language_override

    async def _noop(*_args, **_kwargs):
        return None

    added: list = []
    async def _execute(statement):
        selected = statement.selected_columns[0].key
        value = {"transcription_text": None, "id": 3}[selected]
        return SimpleNamespace(scalar_one_or_none=lambda: value)

    db = SimpleNamespace(
        commit=_noop, refresh=_noop, flush=_noop, add=added.append, execute=_execute
    )
    monkeypatch.setattr(documents_router, "get_owned_or_404", _owned)
    monkeypatch.setattr(documents_router, "_single_document_response", _response)
    for tag, stored in (("zh-CN", "zh"), ("pt_BR", "pt"), ("TR", "tr"), ("az", "az")):
        payload = documents_router.LanguageUpdatePayload(language=tag)
        result = await documents_router.update_document_language(
            1, payload, db=db, user=SimpleNamespace(id=3)
        )
        assert (result, document.language_override) == (stored, stored)
    assert document.sanitized_policy_digest == "d"
    assert [event.extra["override"] for event in added] == ["zh", "pt", "tr", "az"]


def test_stored_junk_override_is_ignored_and_never_logged(monkeypatch, caplog):
    import logging
    from types import SimpleNamespace

    from langdetect.lang_detect_exception import ErrorCode, LangDetectException

    from septum_api.routers.chat import query_language
    from septum_api.utils import db_helpers

    junk = SimpleNamespace(language_override="evil\nFORGED", detected_language="tr")
    junk_only = SimpleNamespace(language_override="evil\nFORGED", detected_language=None)
    assert db_helpers.document_language(junk) == "tr"
    assert db_helpers.document_language(junk_only) is None

    def _no_features(_text):
        raise LangDetectException(ErrorCode.CantDetectError, "No features in text.")

    monkeypatch.setattr(db_helpers, "detect_langs", _no_features)
    with caplog.at_level(logging.WARNING, logger=db_helpers.__name__):
        assert query_language("Merhaba", junk) == "tr"
        assert query_language("Merhaba", junk_only) == "en"
        assert detect_language("Merhaba", context_language="evil\nFORGED") == "en"
    rendered = " ".join(r.getMessage() for r in caplog.records)
    assert "evil" not in rendered and "FORGED" not in rendered
    assert "fell back to tr" in rendered and "fell back to en" in rendered


class _ChunkRows:
    def scalars(self):
        return self

    def all(self):
        return []


class _ChunkDb:
    async def execute(self, _statement):
        return _ChunkRows()


@pytest.mark.parametrize(
    ("override", "detected", "expected"),
    [
        ("tr", "en", "tr"),
        (None, "zh-cn", "zh"),
        ("not a language!", "de", "de"),
        ("../etc", "junk value", "en"),
        (None, None, "en"),
    ],
)
async def test_pii_gate_fresh_map_uses_the_document_language(override, detected, expected):
    from types import SimpleNamespace

    from septum_api.services import pii_gate

    document = SimpleNamespace(id=5, language_override=override, detected_language=detected)
    working = await pii_gate._working_copy(_ChunkDb(), document, None, ["text"])
    assert working.language == expected


async def test_unsupported_language_override_is_rejected_with_422():
    from types import SimpleNamespace

    from httpx import ASGITransport, AsyncClient

    from septum_api.database import get_db
    from septum_api.main import app
    from septum_api.utils.auth_dependency import get_current_user

    async def _no_db():
        yield None

    app.dependency_overrides[get_db] = _no_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, role="editor")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            for tag in ("tur", "zho", "xx", "tlh-Latn"):
                resp = await client.patch("/api/documents/1/language", json={"language": tag})
                assert resp.status_code == 422, (tag, resp.text)
                assert "language not supported" in resp.json()["detail"][0]["msg"]
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


def test_supported_codes_come_from_the_profile_files(monkeypatch, tmp_path):
    from langdetect import detector_factory

    from septum_api.utils import db_helpers

    for name in ("en", "zh-cn", "zh-tw", ".hidden", "README.md"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    (tmp_path / "xx-dir").mkdir()
    monkeypatch.setattr(detector_factory, "PROFILES_DIRECTORY", str(tmp_path))
    assert db_helpers.supported_language_codes() == frozenset({"en", "zh"}) | language_table_codes()
    assert db_helpers.supported_language_code("zh-TW") == "zh"
    assert db_helpers.supported_language_code("tr") == "tr"
    assert db_helpers.supported_language_code("sw") is None


def test_legacy_unsupported_override_is_ignored_on_read():
    from types import SimpleNamespace

    from septum_api.utils.db_helpers import document_language

    assert document_language(SimpleNamespace(language_override="tur", detected_language="tr")) == "tr"
    assert document_language(SimpleNamespace(language_override="zho", detected_language=None)) is None
    assert document_language(SimpleNamespace(language_override="TR", detected_language="en")) == "tr"


async def test_ingestion_pipeline_masks_in_the_override_language(monkeypatch):
    from types import SimpleNamespace

    from septum_api.services import document_pipeline

    seen: dict = {}

    class _Stop(Exception):
        pass

    class _Map:
        def __init__(self, document_id, language):
            seen["map_language"] = language

    class _Composer:
        async def compose_with_snapshot(self, _db, _settings):
            return None, None

    class _Sanitizer:
        def sanitize(self, _text, language, _anon_map):
            seen["detector_language"] = language
            raise _Stop

    async def _sanitizer(*_args, **_kwargs):
        return _Sanitizer()

    class _Normalizer:
        async def normalize(self, _db, text):
            return text

    async def _chunks(_format, text):
        return [SimpleNamespace(text=text, index=0)]

    monkeypatch.setattr(document_pipeline, "AnonymizationMap", _Map)
    monkeypatch.setattr(document_pipeline, "PolicyComposer", _Composer)
    monkeypatch.setattr(document_pipeline, "create_sanitizer", _sanitizer)
    monkeypatch.setattr(document_pipeline, "TextNormalizer", _Normalizer)
    pipeline = document_pipeline.DocumentPipeline(SimpleNamespace())
    monkeypatch.setattr(pipeline, "_build_chunks", _chunks)
    document = SimpleNamespace(id=9, language_override="tr", detected_language="en")
    with pytest.raises(_Stop):
        await pipeline.run(None, document, "pdf", "Ahmet Yılmaz", None)
    assert seen == {"map_language": "tr", "detector_language": "tr"}

    seen.clear()
    legacy = SimpleNamespace(id=9, language_override="tur", detected_language="en")
    with pytest.raises(_Stop):
        await pipeline.run(None, legacy, "pdf", "Ahmet Yılmaz", None)
    assert seen == {"map_language": "en", "detector_language": "en"}


def test_whisper_language_falls_back_to_text_detection_when_invalid_or_unsupported():
    from types import SimpleNamespace

    from septum_api.routers.documents import _detect_document_language

    text = "Gecikmiş ödemelere hangi faiz uygulanır?"
    for whisper in ("haw", "yue", "evil\nFORGED", "x" * 40, 3):
        result = SimpleNamespace(text=text, metadata={"detected_language": whisper})
        assert _detect_document_language("audio", result) == "tr"
    supported = SimpleNamespace(text=text, metadata={"detected_language": "de"})
    assert _detect_document_language("audio", supported) == "de"


async def test_reprocess_keeps_the_override_and_masks_in_it(env, monkeypatch):
    from types import SimpleNamespace

    from septum_api.routers import documents as documents_router
    from septum_api.services import document_pipeline
    from septum_api.utils.db_helpers import document_language

    doc_id, _ = await env.add_document()
    resp = await env.client.patch(f"/api/documents/{doc_id}/language", json={"language": "TR"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["language_override"] == "tr"

    async def _ingest(**_kwargs):
        text = "The tenant paid the deposit before moving into the warehouse hall."
        return SimpleNamespace(text=text, confidence=None, metadata=None)

    seen: dict = {}

    async def _run(self, db, document, *_args, **_kwargs):
        seen["stored"] = (document.language_override, document.detected_language)
        seen["masking"] = document_language(document)

    monkeypatch.setattr(documents_router._INGESTION_ROUTER, "ingest", _ingest)
    monkeypatch.setattr(document_pipeline.DocumentPipeline, "run", _run)
    resp = await env.client.post(f"/api/documents/{doc_id}/reprocess")
    assert resp.status_code == 200, resp.text
    assert seen == {"stored": ("tr", "en"), "masking": "tr"}
    stored = await env.document(doc_id)
    assert stored.language_override == "tr"


async def test_ingestion_audit_event_records_the_override_language(monkeypatch):
    import contextlib
    from types import SimpleNamespace

    from septum_api.services import document_pipeline

    audit: dict = {}

    class _Stop(Exception):
        pass

    class _Composer:
        async def compose_with_snapshot(self, _db, _settings):
            return None, None

    class _Sanitizer:
        def sanitize(self, text, _language, _anon_map):
            return SimpleNamespace(
                entity_count=1,
                entity_type_counts={"PERSON_NAME": 1},
                detected_spans=[],
                sanitized_text=text,
            )

    added: list = []
    stored: dict = {}

    class _Db:
        async def commit(self):
            return None

        async def refresh(self, _row):
            return None

        async def scalar(self, _statement):
            return 1

        async def execute(self, _statement):
            return SimpleNamespace(rowcount=1, scalar_one_or_none=lambda: stored["override"])

        def add(self, row):
            added.append(row)

    class _Lock:
        async def ensure_held(self):
            return None

    @contextlib.asynccontextmanager
    async def _map_lock(_document_id):
        yield _Lock()

    async def _noop(*_args, **_kwargs):
        return None

    async def _sanitizer(*_args, **_kwargs):
        return _Sanitizer()

    async def _persist(*_args, **_kwargs):
        return []

    async def _log(_db, **kwargs):
        audit.update(kwargs["extra"])
        raise _Stop

    class _Normalizer:
        async def normalize(self, _db, text):
            return text

    async def _chunks(_format, text):
        return [SimpleNamespace(text=text, index=0, source_page=None, section_title=None)]

    monkeypatch.setattr(document_pipeline, "PolicyComposer", _Composer)
    monkeypatch.setattr(document_pipeline, "create_sanitizer", _sanitizer)
    monkeypatch.setattr(document_pipeline, "TextNormalizer", _Normalizer)
    monkeypatch.setattr(document_pipeline, "document_map_lock", _map_lock)
    monkeypatch.setattr(document_pipeline, "set_document_map", _noop)
    monkeypatch.setattr(document_pipeline, "replace_index_for_document", _noop)
    monkeypatch.setattr(document_pipeline, "recompute_relationships_for_document", _noop)
    monkeypatch.setattr(document_pipeline, "log_pii_detected", _log)
    pipeline = document_pipeline.DocumentPipeline(SimpleNamespace())
    monkeypatch.setattr(pipeline, "_build_chunks", _chunks)
    monkeypatch.setattr(pipeline, "_persist_chunks", _persist)

    def _document(override):
        return SimpleNamespace(
            id=9, language_override=override, detected_language="en",
            active_regulation_ids=[], original_filename="a.pdf",
        )

    for override, expected in (("tr", "tr"), ("tur", "en"), (None, "en")):
        audit.clear()
        with pytest.raises(_Stop):
            await pipeline.run(_Db(), _document(override), "pdf", "Ahmet Yılmaz", None)
        source = "override" if override == expected else "detected"
        assert audit == {
            "source": "document_ingestion", "language": expected, "language_source": source,
        }


def test_supported_codes_cache_is_keyed_by_directory(monkeypatch, tmp_path):
    from langdetect import detector_factory

    from septum_api.utils import db_helpers

    real_dir = detector_factory.PROFILES_DIRECTORY
    real = db_helpers.supported_language_codes()
    other = tmp_path / "other"
    other.mkdir()
    (other / "xx").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(detector_factory, "PROFILES_DIRECTORY", str(other))
    assert db_helpers.supported_language_codes() == frozenset({"xx"}) | language_table_codes()
    monkeypatch.setattr(detector_factory, "PROFILES_DIRECTORY", real_dir)
    assert db_helpers.supported_language_codes() == real
    assert {"en", "tr", "zh", "ko", "ja"} <= real


async def _set_digest(test_env, doc_id, digest):
    from septum_api.models.document import Document

    async with test_env.session_maker() as session:
        row = await session.get(Document, doc_id)
        row.sanitized_policy_digest = digest
        await session.commit()


async def _record_language(test_env, doc_id, *, language, transcription="The tenant paid.", status=None):
    from septum_api.models.document import Document

    async with test_env.session_maker() as session:
        row = await session.get(Document, doc_id)
        row.sanitized_language = language
        row.transcription_text = transcription
        if status is not None:
            row.ingestion_status = status
        await session.commit()


async def _needs_reprocessing(test_env, doc_id):
    resp = await test_env.client.get(f"/api/documents/{doc_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()["needs_reprocessing"]


async def test_staleness_follows_the_recorded_masking_language(env):
    from sqlalchemy import select

    from septum_api.models.audit_event import AuditEvent
    from septum_api.services.policy_snapshot import current_policy_snapshot

    doc_id, _ = await env.add_document()
    async with env.session_maker() as session:
        snapshot = await current_policy_snapshot(session)
    await _set_digest(env, doc_id, snapshot.digest)
    async with env.session_maker() as session:
        from septum_api.models.document import Document

        row = await session.get(Document, doc_id)
        row.sanitized_entity_types = snapshot.entity_types
        await session.commit()

    await _record_language(env, doc_id, language=None)
    assert await _needs_reprocessing(env, doc_id) is True

    await _record_language(env, doc_id, language="en")
    assert await _needs_reprocessing(env, doc_id) is False

    resp = await env.client.patch(f"/api/documents/{doc_id}/language", json={"language": "en"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["needs_reprocessing"] is False

    resp = await env.client.patch(f"/api/documents/{doc_id}/language", json={"language": "de"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["needs_reprocessing"] is True
    stored = await env.document(doc_id)
    assert stored.sanitized_policy_digest == snapshot.digest

    await _record_language(env, doc_id, language="de")
    assert await _needs_reprocessing(env, doc_id) is False

    async with env.session_maker() as session:
        events = (
            await session.execute(
                select(AuditEvent)
                .where(AuditEvent.event_type == "document_language_changed")
                .order_by(AuditEvent.id)
            )
        ).scalars().all()
    assert [e.extra for e in events] == [
        {"user_id": env.user.id, "previous_override": None, "override": "en"},
        {"user_id": env.user.id, "previous_override": "en", "override": "de"},
    ]
    assert all(e.document_id == doc_id for e in events)


async def test_override_set_during_processing_or_before_pickup_leaves_the_document_stale(env):
    from septum_api.services.policy_snapshot import current_policy_snapshot

    doc_id, _ = await env.add_document(status="processing")
    resp = await env.client.patch(f"/api/documents/{doc_id}/language", json={"language": "de"})
    assert resp.status_code == 200, resp.text

    # The run that was already in flight finishes with the language it started with.
    async with env.session_maker() as session:
        snapshot = await current_policy_snapshot(session)
    await _set_digest(env, doc_id, snapshot.digest)
    async with env.session_maker() as session:
        from septum_api.models.document import Document

        row = await session.get(Document, doc_id)
        row.sanitized_entity_types = snapshot.entity_types
        await session.commit()
    await _record_language(env, doc_id, language="en", status="completed")
    assert await _needs_reprocessing(env, doc_id) is True

    # A reprocess that has been queued but not picked up yet changes nothing either.
    resp = await env.client.patch(f"/api/documents/{doc_id}/language", json={"language": "tr"})
    assert resp.status_code == 200, resp.text
    assert await _needs_reprocessing(env, doc_id) is True
    await _record_language(env, doc_id, language="tr")
    assert await _needs_reprocessing(env, doc_id) is False


async def test_language_patch_and_its_audit_row_commit_together(env, monkeypatch):
    from sqlalchemy import func, select

    from septum_api.models.audit_event import AuditEvent
    from septum_api.routers import documents as documents_router

    doc_id, _ = await env.add_document()

    def _broken_event(*_args, **_kwargs):
        raise RuntimeError("audit row could not be built")

    monkeypatch.setattr(documents_router, "language_override_event", _broken_event)
    with pytest.raises(RuntimeError):
        await env.client.patch(f"/api/documents/{doc_id}/language", json={"language": "de"})
    assert (await env.document(doc_id)).language_override is None

    monkeypatch.undo()
    resp = await env.client.patch(f"/api/documents/{doc_id}/language", json={"language": "de"})
    assert resp.status_code == 200, resp.text
    async with env.session_maker() as session:
        count = (
            await session.execute(
                select(func.count()).select_from(AuditEvent)
                .where(AuditEvent.event_type == "document_language_changed")
            )
        ).scalar_one()
    assert ((await env.document(doc_id)).language_override, count) == ("de", 1)


async def test_remask_after_a_correction_uses_the_new_language(monkeypatch):
    import contextlib
    from types import SimpleNamespace

    from septum_api.services import pii_gate

    seen: list = []
    working = SimpleNamespace(language="en", entity_map={})

    async def _noop(*_args, **_kwargs):
        return None

    async def _load(_document_id):
        return working

    async def _working(*_args):
        return working

    async def _gate(texts, _map, _db, _settings, language=None, sanitizer=None):
        seen.append(language)
        return list(texts)

    class _Lock:
        async def ensure_held(self):
            return None

    @contextlib.asynccontextmanager
    async def _map_lock(_document_id):
        yield _Lock()

    monkeypatch.setattr(pii_gate, "_require_row", _noop)
    monkeypatch.setattr(pii_gate, "load_persisted_document_map", _load)
    monkeypatch.setattr(pii_gate, "_working_copy", _working)
    monkeypatch.setattr(pii_gate, "gate_untrusted_texts", _gate)
    monkeypatch.setattr(pii_gate, "document_map_lock", _map_lock)
    monkeypatch.setattr(pii_gate, "_same_map", lambda a, b: True)

    corrected = SimpleNamespace(
        id=4, language_override="de", detected_language="en",
        transcription_text="Hans Müller wohnt in Berlin.",
    )
    contradicting = SimpleNamespace(
        id=4, language_override="en", detected_language="zh-cn",
        transcription_text="李明负责交付合同。",
    )
    legacy = SimpleNamespace(
        id=4, language_override=None, detected_language="tur", transcription_text="Some text",
    )
    await pii_gate.remask_document_texts(None, None, corrected, ["Hans Müller wohnt in Berlin."], object())
    await pii_gate.remask_document_texts(None, None, contradicting, ["李明负责交付合同。"], object())
    await pii_gate.remask_document_texts(None, None, legacy, ["Some text"], object())
    # The read path trusts the stored override: a script-contradicting one is
    # refused at PATCH time and cleared by the pipeline, never re-judged here.
    assert seen == ["de", "en", "en"]


@pytest.mark.parametrize(
    ("text", "override", "detected", "expected", "source"),
    [
        ("合同双方应当按照约定全面履行自己的义务，李明负责交付。", "en", "zh-cn", "zh", "detected"),
        ("Ahmet Yılmaz İstanbul'da yaşıyor ve Ankara'da çalışıyor.", "az", "tr", "az", "override"),
        ("The tenant paid the deposit before moving in.", "de", "en", "de", "override"),
    ],
)
async def test_ingestion_ignores_an_override_that_contradicts_the_script(
    monkeypatch, caplog, text, override, detected, expected, source
):
    import logging
    from types import SimpleNamespace

    from septum_api.services import document_pipeline
    from septum_api.utils import db_helpers

    seen: dict = {}

    class _Stop(Exception):
        pass

    class _Map:
        def __init__(self, document_id, language):
            seen["map_language"] = language

    class _Composer:
        async def compose_with_snapshot(self, _db, _settings):
            return None, None

    class _Sanitizer:
        def sanitize(self, _text, language, _anon_map):
            seen["detector_language"] = language
            raise _Stop

    async def _sanitizer(*_args, **_kwargs):
        return _Sanitizer()

    class _Normalizer:
        async def normalize(self, _db, value):
            return value

    async def _chunks(_format, value):
        return [SimpleNamespace(text=value, index=0)]

    monkeypatch.setattr(document_pipeline, "AnonymizationMap", _Map)
    monkeypatch.setattr(document_pipeline, "PolicyComposer", _Composer)
    monkeypatch.setattr(document_pipeline, "create_sanitizer", _sanitizer)
    monkeypatch.setattr(document_pipeline, "TextNormalizer", _Normalizer)
    pipeline = document_pipeline.DocumentPipeline(SimpleNamespace())
    monkeypatch.setattr(pipeline, "_build_chunks", _chunks)
    document = SimpleNamespace(id=9, language_override=override, detected_language=detected)
    with caplog.at_level(logging.WARNING, logger=db_helpers.__name__):
        with pytest.raises(_Stop):
            await pipeline.run(None, document, "pdf", text, None)
    assert seen == {"map_language": expected, "detector_language": expected}
    assert db_helpers.masking_language(document, text) == (expected, source)
    records = [r.getMessage() for r in caplog.records if r.name == db_helpers.__name__]
    if source == "detected":
        assert records and all(f"override {override}" in m for m in records)
    else:
        assert records == []
    for message in records:
        assert not any(word in message for word in text.split()[:3])
        assert "李明" not in message and "合同" not in message


async def test_language_change_leaves_the_cache_alone_and_the_key_carries_the_language(
    env, monkeypatch
):
    from septum_api.services.anonymization_map import AnonymizationMap
    from septum_api.services.remask_cache import RemaskCache, remask_cache

    forgotten: list[int] = []
    monkeypatch.setattr(remask_cache, "forget_document", lambda doc_id: forgotten.append(doc_id))
    doc_id, _ = await env.add_document()
    for language in ("de", "DE", "tr"):
        resp = await env.client.patch(f"/api/documents/{doc_id}/language", json={"language": language})
        assert resp.status_code == 200, resp.text
    assert forgotten == []

    keys = {RemaskCache.key(doc_id, 1, "p", "text", language) for language in ("en", "de", "en")}
    assert len(keys) == 2
    cache = RemaskCache()
    cache.put(RemaskCache.key(doc_id, 1, "p", "text", "en"), "[PERSON_NAME_1] signed.")
    known = AnonymizationMap(document_id=doc_id, language="de")
    known.add_entity("Ahmet", "PERSON_NAME")
    assert cache.get(RemaskCache.key(doc_id, 1, "p", "text", "de"), known) is None
    assert cache.get(RemaskCache.key(doc_id, 1, "p", "text", "en"), known) == "[PERSON_NAME_1] signed."


@pytest.mark.parametrize(
    ("override", "detected", "expected"),
    [
        (None, "tr", ("tr", "detected")),
        (None, None, ("en", "detected")),
        (None, "junk!", ("en", "detected")),
        (None, "zz-unsupported", ("en", "detected")),
        ("de", "tr", ("de", "override")),
        ("junk!", "tr", ("tr", "detected")),
        ("junk!", None, ("en", "detected")),
        ("TR-tr", "en", ("tr", "override")),
    ],
)
def test_masking_language_fallback_chain(override, detected, expected):
    from types import SimpleNamespace

    from septum_api.utils.db_helpers import masking_language

    document = SimpleNamespace(language_override=override, detected_language=detected)
    assert masking_language(document, "Gecikmiş ödemelere hangi faiz uygulanır?") == expected


def test_masking_language_script_contradicted_override_falls_back_to_detected_or_en():
    from types import SimpleNamespace

    from septum_api.utils.db_helpers import masking_language

    han = "合同双方应当按照约定全面履行自己的义务。"
    detected = SimpleNamespace(language_override="en", detected_language="zh-cn")
    assert masking_language(detected, han) == ("zh", "detected")
    undetected = SimpleNamespace(language_override="en", detected_language=None)
    assert masking_language(undetected, han) == ("en", "detected")


async def test_deleting_a_user_unlinks_it_from_audit_events(router_client):
    from sqlalchemy import select

    from septum_api.database import get_db
    from septum_api.main import app
    from septum_api.models.audit_event import AuditEvent
    from septum_api.services.audit_logger import build_document_event
    from tests.test_users_router import _auth, _bootstrap_admin, _create_user

    admin_token = await _bootstrap_admin(router_client)
    leaving = await _create_user(router_client, admin_token, "leaving@example.com")
    staying = await _create_user(router_client, admin_token, "staying@example.com")

    async def _session_run(work):
        session_gen = app.dependency_overrides[get_db]()
        session = await session_gen.__anext__()
        try:
            return await work(session)
        finally:
            await session_gen.aclose()

    async def _seed(session):
        for user_id in (leaving["id"], staying["id"]):
            session.add(build_document_event(
                document_id=1, event_type="document_language_changed",
                extra={"user_id": user_id, "previous_override": None, "override": "de"},
            ))
        session.add(build_document_event(
            document_id=1, event_type="document_uploaded", extra={"user_id": leaving["id"]},
        ))
        await session.commit()

    await _session_run(_seed)
    resp = await router_client.delete(f"/api/users/{leaving['id']}", headers=_auth(admin_token))
    assert resp.status_code == 204, resp.text

    async def _read(session):
        rows = await session.execute(select(AuditEvent).order_by(AuditEvent.id))
        return [(e.event_type, e.extra) for e in rows.scalars()]

    assert await _session_run(_read) == [
        ("document_language_changed", {"user_id": None, "previous_override": None, "override": "de"}),
        ("document_language_changed", {"user_id": staying["id"], "previous_override": None, "override": "de"}),
        ("document_uploaded", {"user_id": leaving["id"]}),
    ]


async def test_remask_writes_the_effective_language_into_the_persisted_map(monkeypatch):
    import contextlib
    from types import SimpleNamespace

    from septum_api.services import pii_gate
    from septum_api.services.anonymization_map import AnonymizationMap

    stored = AnonymizationMap(document_id=4, language="en")
    stored.add_entity("Hans Müller", "PERSON_NAME")
    written: list = []

    async def _noop(*_args, **_kwargs):
        return None

    async def _load(_document_id):
        return stored

    async def _gate(texts, _map, _db, _settings, language=None, sanitizer=None):
        return list(texts)

    async def _write(_document_id, anon_map):
        written.append(anon_map.language)

    class _Lock:
        async def ensure_held(self):
            return None

    @contextlib.asynccontextmanager
    async def _map_lock(_document_id):
        yield _Lock()

    monkeypatch.setattr(pii_gate, "_require_row", _noop)
    monkeypatch.setattr(pii_gate, "load_persisted_document_map", _load)
    monkeypatch.setattr(pii_gate, "gate_untrusted_texts", _gate)
    monkeypatch.setattr(pii_gate, "document_map_lock", _map_lock)
    monkeypatch.setattr(pii_gate, "set_document_map", _write)

    corrected = SimpleNamespace(
        id=4, language_override="de", detected_language="en",
        transcription_text="Hans Müller wohnt in Berlin.",
    )
    _, changed = await pii_gate.remask_document_texts(None, None, corrected, ["[PERSON_NAME_1] wohnt."], object())
    assert (changed, written) == (True, ["de"])

    written.clear()
    same = SimpleNamespace(
        id=4, language_override=None, detected_language="en", transcription_text="Hans lives here.",
    )
    _, changed = await pii_gate.remask_document_texts(None, None, same, ["[PERSON_NAME_1] lives."], object())
    assert (changed, written) == (False, [])


@pytest.mark.parametrize(
    ("override", "text", "honoured"),
    [
        ("zh", "[PERSON_NAME_1]和[PERSON_NAME_2]签署了[ORGANIZATION_NAME_1]的合同，[PERSON_NAME_3]见证。", True),
        ("en", "[PERSON_NAME_1]和[PERSON_NAME_2]签署了[ORGANIZATION_NAME_1]的合同，[PERSON_NAME_3]见证。", False),
        ("zh", "The contract was signed by 王伟 and 李娜 in the presence of the lessor.", True),
        ("ru", "وقع أحمد العقد مع الشركة في الرياض", False),
        ("ar", "The tenant paid the deposit before moving in.", False),
        ("de", "Арендатор внёс залог до въезда в помещение.", False),
        ("en", "合同双方应当按照约定全面履行自己的义务，李明负责交付。", False),
        ("en", "Gecikmiş ödemelere hangi faiz uygulanır?", True),
    ],
)
def test_override_script_guard(override, text, honoured):
    from types import SimpleNamespace

    from septum_api.utils.db_helpers import masking_language

    document = SimpleNamespace(language_override=override, detected_language="tr")
    expected = (override, "override") if honoured else ("tr", "detected")
    assert masking_language(document, text) == expected


def test_every_detectable_language_has_a_script_rule():
    from langdetect import detector_factory
    from septum_core.language_resolution import _REQUIRED_SCRIPTS, iso_language_code

    from septum_api.utils.db_helpers import _profile_names

    codes = {iso_language_code(name) for name in _profile_names(detector_factory.PROFILES_DIRECTORY)}
    assert codes - set(_REQUIRED_SCRIPTS) == set()


async def test_pipeline_records_the_masking_language_it_used(monkeypatch):
    import contextlib
    from types import SimpleNamespace

    from septum_api.services import document_pipeline

    seen: list[str] = []

    class _Composer:
        async def compose_with_snapshot(self, _db, _settings):
            return None, SimpleNamespace(entity_types=["X"], digest="d")

    class _Sanitizer:
        def sanitize(self, text, language, _anon_map):
            seen.append(language)
            return SimpleNamespace(
                entity_count=0, entity_type_counts={}, detected_spans=[], sanitized_text=text
            )

    added: list = []
    stored: dict = {}

    class _Db:
        async def commit(self):
            return None

        async def refresh(self, _row):
            return None

        async def scalar(self, _statement):
            return 1

        async def execute(self, _statement):
            return SimpleNamespace(rowcount=1, scalar_one_or_none=lambda: stored["override"])

        def add(self, row):
            added.append(row)

    class _Lock:
        async def ensure_held(self):
            return None

    @contextlib.asynccontextmanager
    async def _map_lock(_document_id):
        yield _Lock()

    async def _noop(*_args, **_kwargs):
        return None

    async def _sanitizer(*_args, **_kwargs):
        return _Sanitizer()

    async def _persist(*_args, **_kwargs):
        return []

    class _Normalizer:
        async def normalize(self, _db, text):
            return text

    async def _chunks(_format, text):
        return [SimpleNamespace(text=text, index=0, source_page=None, section_title=None)]

    monkeypatch.setattr(document_pipeline, "PolicyComposer", _Composer)
    monkeypatch.setattr(document_pipeline, "create_sanitizer", _sanitizer)
    monkeypatch.setattr(document_pipeline, "TextNormalizer", _Normalizer)
    monkeypatch.setattr(document_pipeline, "document_map_lock", _map_lock)
    monkeypatch.setattr(document_pipeline, "set_document_map", _noop)
    monkeypatch.setattr(document_pipeline, "replace_index_for_document", _noop)
    monkeypatch.setattr(document_pipeline, "recompute_relationships_for_document", _noop)
    pipeline = document_pipeline.DocumentPipeline(SimpleNamespace())
    monkeypatch.setattr(pipeline, "_build_chunks", _chunks)
    monkeypatch.setattr(pipeline, "_persist_chunks", _persist)

    han = "合同双方应当按照约定全面履行自己的义务。"
    cases = (
        ("tr", "en", "Gecikmiş ödemelere hangi faiz uygulanır?", "tr"),
        (None, "de", "Hello there", "de"),
        ("en", "zh", han, "zh"),
        (None, None, "Hello there", "en"),
    )
    for override, detected, text, expected in cases:
        seen.clear()
        document = SimpleNamespace(
            id=9, language_override=override, detected_language=detected,
            active_regulation_ids=[], original_filename="a.pdf",
            sanitized_language=None, ingestion_status="processing",
        )
        added.clear()
        stored["override"] = override
        await pipeline.run(_Db(), document, "pdf", text, None)
        assert seen == [expected]
        assert document.sanitized_language == expected
        assert document.ingestion_status == "completed"
        refused = override is not None and expected != override
        assert document.language_override == (None if refused else override)
        assert [row.extra for row in added] == (
            [{"user_id": None, "previous_override": override, "override": None,
              "reason": "script_mismatch"}]
            if refused else []
        )


async def test_patch_refuses_an_override_the_document_script_contradicts(env):
    from sqlalchemy import func, select

    from septum_api.models.audit_event import AuditEvent

    doc_id, _ = await env.add_document()
    await _record_language(env, doc_id, language="zh", transcription="合同双方应当按照约定全面履行自己的义务，李明负责交付。" * 3)
    resp = await env.client.patch(f"/api/documents/{doc_id}/language", json={"language": "ru"})
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == "language does not match the document's script"
    resp = await env.client.patch(f"/api/documents/{doc_id}/language", json={"language": "ja"})
    assert resp.status_code == 422, resp.text
    assert (await env.document(doc_id)).language_override is None

    resp = await env.client.patch(f"/api/documents/{doc_id}/language", json={"language": "zh-TW"})
    assert resp.status_code == 200, resp.text
    async with env.session_maker() as session:
        count = (
            await session.execute(
                select(func.count()).select_from(AuditEvent)
                .where(AuditEvent.event_type == "document_language_changed")
            )
        ).scalar_one()
    assert count == 1


async def test_patch_before_the_text_exists_is_accepted(env):
    doc_id, _ = await env.add_document(status="processing")
    resp = await env.client.patch(f"/api/documents/{doc_id}/language", json={"language": "ru"})
    assert resp.status_code == 200, resp.text
    assert (await env.document(doc_id)).language_override == "ru"


async def test_patch_by_a_user_deleted_meanwhile_writes_nothing(env):
    from sqlalchemy import delete as sa_delete
    from sqlalchemy import func, select

    from septum_api.main import app
    from septum_api.models.audit_event import AuditEvent
    from septum_api.models.user import User
    from septum_api.utils.auth_dependency import get_current_user

    doc_id, _ = await env.add_document()
    async with env.session_maker() as session:
        actor = User(email="admin@example.com", hashed_password="x", role="admin")
        session.add(actor)
        await session.commit()
        await session.execute(sa_delete(User).where(User.id == actor.id))
        await session.commit()
    # The request authenticated before the deletion, so only the
    # in-transaction user check can stop it.
    app.dependency_overrides[get_current_user] = lambda: actor
    resp = await env.client.patch(f"/api/documents/{doc_id}/language", json={"language": "de"})
    app.dependency_overrides[get_current_user] = lambda: env.user
    assert resp.status_code == 401, resp.text
    async with env.session_maker() as session:
        count = (
            await session.execute(select(func.count()).select_from(AuditEvent))
        ).scalar_one()
    assert ((await env.document(doc_id)).language_override, count) == (None, 0)


def test_effective_masking_language_reads_no_text():
    from types import SimpleNamespace

    from septum_api.utils.db_helpers import effective_masking_language

    class _Untouchable(SimpleNamespace):
        @property
        def transcription_text(self):
            raise AssertionError("the read path must not scan the text")

    assert effective_masking_language(_Untouchable(language_override="de", detected_language="tr")) == "de"
    assert effective_masking_language(_Untouchable(language_override="tur", detected_language="tr")) == "tr"
    assert effective_masking_language(_Untouchable(language_override=None, detected_language=None)) == "en"


def test_override_check_reads_only_the_capped_head():
    from septum_core.language_resolution import DETECTION_RAW_CAP_CHARS

    from septum_api.utils.db_helpers import override_fits_text

    beyond = "[PERSON_NAME_1] " * (DETECTION_RAW_CAP_CHARS // 16 + 1) + "合同双方应当按照约定全面履行自己的义务"
    assert override_fits_text("zh", beyond)
    latin_head = "x " * (DETECTION_RAW_CAP_CHARS // 2) + "合同双方应当按照约定全面履行自己的义务"
    assert not override_fits_text("zh", latin_head)


async def _refused_override_row(test_env, doc_id, override):
    from septum_api.models.document import Document

    async with test_env.session_maker() as session:
        row = await session.get(Document, doc_id)
        row.language_override = override
        await session.commit()


async def _language_events(test_env):
    from sqlalchemy import select

    from septum_api.models.audit_event import AuditEvent

    async with test_env.session_maker() as session:
        rows = await session.execute(
            select(AuditEvent.extra).where(AuditEvent.event_type == "document_language_changed")
        )
        return list(rows.scalars())


async def test_auto_clear_does_not_overwrite_a_newer_override(env):
    from septum_api.models.document import Document
    from septum_api.services.document_pipeline import DocumentPipeline

    doc_id, _ = await env.add_document()
    await _refused_override_row(env, doc_id, "ru")
    async with env.session_maker() as session:
        judged = await session.get(Document, doc_id)
    assert judged.language_override == "ru"

    await _refused_override_row(env, doc_id, "de")
    async with env.session_maker() as session:
        session.add(judged)
        await DocumentPipeline._clear_refused_override(session, judged, "The tenant paid the deposit before moving in.")
        await session.commit()

    assert (await env.document(doc_id)).language_override == "de"
    assert await _language_events(env) == []


async def test_auto_clear_and_its_audit_row_commit_or_roll_back_together(env):
    from septum_api.models.document import Document
    from septum_api.services.document_pipeline import DocumentPipeline

    doc_id, _ = await env.add_document()
    await _refused_override_row(env, doc_id, "ru")

    async with env.session_maker() as session:
        document = await session.get(Document, doc_id)
        await DocumentPipeline._clear_refused_override(session, document, "The tenant paid the deposit before moving in.")
        await session.rollback()
    assert (await env.document(doc_id)).language_override == "ru"
    assert await _language_events(env) == []

    async with env.session_maker() as session:
        document = await session.get(Document, doc_id)
        await DocumentPipeline._clear_refused_override(session, document, "The tenant paid the deposit before moving in.")
        await session.commit()
    assert (await env.document(doc_id)).language_override is None
    assert await _language_events(env) == [
        {"user_id": None, "previous_override": "ru", "override": None, "reason": "script_mismatch"}
    ]


async def test_auto_clear_ignores_an_override_that_was_honoured(env):
    from septum_api.models.document import Document
    from septum_api.services.document_pipeline import DocumentPipeline

    doc_id, _ = await env.add_document()
    await _refused_override_row(env, doc_id, "ru")
    async with env.session_maker() as session:
        document = await session.get(Document, doc_id)
        await DocumentPipeline._clear_refused_override(session, document, "Арендатор внёс залог до въезда.")
        await session.commit()
    assert (await env.document(doc_id)).language_override == "ru"
    assert await _language_events(env) == []


async def test_forget_user_counts_and_keeps_other_keys(env):
    from sqlalchemy import select

    from septum_api.models.audit_event import AuditEvent
    from septum_api.services.audit_logger import (
        build_document_event,
        forget_user_in_audit_events,
    )

    async with env.session_maker() as session:
        for user_id in (7, 8, 7):
            session.add(build_document_event(
                document_id=1, event_type="document_language_changed",
                extra={"user_id": user_id, "override": "de"},
            ))
        await session.commit()
        assert await forget_user_in_audit_events(session, 7) == 2
        await session.commit()
        assert await forget_user_in_audit_events(session, 7) == 0
        rows = await session.execute(select(AuditEvent.extra).order_by(AuditEvent.id))
        assert list(rows.scalars()) == [
            {"user_id": None, "override": "de"},
            {"user_id": 8, "override": "de"},
            {"user_id": None, "override": "de"},
        ]


async def test_override_patched_after_the_language_decision_is_judged_at_completion(env):
    from septum_api.models.document import Document
    from septum_api.services.document_pipeline import DocumentPipeline
    from septum_api.services.policy_snapshot import current_policy_snapshot

    final_text = "The tenant paid the deposit before moving into the warehouse hall."
    doc_id, _ = await env.add_document(status="processing")
    async with env.session_maker() as session:
        judged = await session.get(Document, doc_id)
    assert judged.language_override is None

    # A PATCH accepted while the document had no text yet lands after the run decided.
    resp = await env.client.patch(f"/api/documents/{doc_id}/language", json={"language": "ru"})
    assert resp.status_code == 200, resp.text
    async with env.session_maker() as session:
        session.add(judged)
        await DocumentPipeline._clear_refused_override(session, judged, final_text)
        await session.commit()
    assert (await env.document(doc_id)).language_override is None
    assert (await _language_events(env))[-1] == {
        "user_id": None, "previous_override": "ru", "override": None, "reason": "script_mismatch",
    }

    resp = await env.client.patch(f"/api/documents/{doc_id}/language", json={"language": "de"})
    assert resp.status_code == 200, resp.text
    async with env.session_maker() as session:
        judged = await session.get(Document, doc_id)
        await DocumentPipeline._clear_refused_override(session, judged, final_text)
        snapshot = await current_policy_snapshot(session)
        judged.sanitized_entity_types = snapshot.entity_types
        judged.sanitized_policy_digest = snapshot.digest
        judged.sanitized_language = "en"
        judged.transcription_text = final_text
        judged.ingestion_status = "completed"
        await session.commit()
    stored = await env.document(doc_id)
    assert stored.language_override == "de"
    assert await _needs_reprocessing(env, doc_id) is True


async def test_document_response_reports_the_masked_language_and_a_refused_override(env):
    from septum_api.models.document import Document
    from septum_api.services.document_pipeline import DocumentPipeline

    doc_id, _ = await env.add_document()
    await _record_language(env, doc_id, language="en")
    await _refused_override_row(env, doc_id, "ru")
    async with env.session_maker() as session:
        document = await session.get(Document, doc_id)
        await DocumentPipeline._clear_refused_override(session, document, "The tenant paid.")
        await session.commit()

    body = (await env.client.get(f"/api/documents/{doc_id}")).json()
    assert (body["sanitized_language"], body["override_refused_language"], body["override_refused_reason"]) == (
        "en", "ru", "script_mismatch",
    )
    listed = (await env.client.get("/api/documents")).json()["items"]
    assert [(d["id"], d["override_refused_language"]) for d in listed] == [(doc_id, "ru")]

    resp = await env.client.patch(f"/api/documents/{doc_id}/language", json={"language": "de"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["language_override"], body["override_refused_language"], body["override_refused_reason"]) == (
        "de", None, None,
    )


async def test_override_refusals_use_one_query_and_report_only_the_latest_event(env):
    from sqlalchemy import event

    from septum_api.models.audit_event import AuditEvent
    from septum_api.routers.documents import _override_refusals
    from septum_api.services.audit_logger import build_document_event

    ids = [(await env.add_document())[0] for _ in range(4)]
    refused, repatched, plain, untouched = ids

    def _refusal(document_id, language):
        return build_document_event(
            document_id=document_id, event_type="document_language_changed",
            extra={"user_id": None, "previous_override": language, "override": None,
                   "reason": "script_mismatch"},
        )

    def _patch(document_id, language):
        return build_document_event(
            document_id=document_id, event_type="document_language_changed",
            extra={"user_id": 1, "previous_override": None, "override": language},
        )

    async with env.session_maker() as session:
        session.add_all([
            _refusal(refused, "ru"),
            _refusal(repatched, "ru"), _patch(repatched, "de"),
            _patch(plain, "tr"),
            build_document_event(document_id=untouched, event_type="document_uploaded"),
        ])
        await session.commit()

        statements: list[str] = []

        def _count(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        sync_engine = session.bind.sync_engine
        event.listen(sync_engine, "before_cursor_execute", _count)
        try:
            result = await _override_refusals(session, ids)
            empty = await _override_refusals(session, [])
        finally:
            event.remove(sync_engine, "before_cursor_execute", _count)

    assert result == {refused: ("ru", "script_mismatch")}
    assert empty == {}
    assert len(statements) == 1
    assert AuditEvent.__tablename__ in statements[0]


async def test_document_without_audit_events_reports_no_refusal(env):
    doc_id, _ = await env.add_document()
    body = (await env.client.get(f"/api/documents/{doc_id}")).json()
    assert (body["override_refused_language"], body["override_refused_reason"]) == (None, None)
    listed = (await env.client.get("/api/documents")).json()["items"]
    assert [(d["override_refused_language"], d["override_refused_reason"]) for d in listed] == [(None, None)]


async def test_refusal_resets_after_a_later_patch_back_to_the_same_code(env):
    from septum_api.models.document import Document
    from septum_api.services.document_pipeline import DocumentPipeline

    doc_id, _ = await env.add_document()
    await _refused_override_row(env, doc_id, "ru")
    async with env.session_maker() as session:
        document = await session.get(Document, doc_id)
        await DocumentPipeline._clear_refused_override(session, document, "The tenant paid.")
        await session.commit()
    body = (await env.client.get(f"/api/documents/{doc_id}")).json()
    assert body["override_refused_language"] == "ru"

    resp = await env.client.patch(f"/api/documents/{doc_id}/language", json={"language": "ru"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["language_override"] == "ru"
    assert resp.json()["override_refused_language"] is None


async def test_patch_judges_text_committed_between_its_read_and_its_write(env, monkeypatch):
    from sqlalchemy import func, select
    from sqlalchemy import inspect as sa_inspect

    from septum_api.models.audit_event import AuditEvent
    from septum_api.models.document import Document
    from septum_api.routers import documents as documents_router

    doc_id, _ = await env.add_document(status="processing")
    real_lookup = documents_router.get_owned_or_404

    async def _text_lands_after_the_read(*args, **kwargs):
        document = await real_lookup(*args, **kwargs)
        assert "transcription_text" in sa_inspect(document).unloaded
        async with env.session_maker() as other:
            row = await other.get(Document, doc_id)
            row.transcription_text = "合同双方应当按照约定全面履行自己的义务，李明负责交付。" * 3
            await other.commit()
        return document

    monkeypatch.setattr(documents_router, "get_owned_or_404", _text_lands_after_the_read)
    resp = await env.client.patch(f"/api/documents/{doc_id}/language", json={"language": "ru"})
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == "language does not match the document's script"
    async with env.session_maker() as session:
        events = (
            await session.execute(select(func.count()).select_from(AuditEvent))
        ).scalar_one()
    assert ((await env.document(doc_id)).language_override, events) == (None, 0)


async def test_unknown_refusal_reason_is_not_reported(env):
    from septum_api.routers.documents import _override_refusals
    from septum_api.services.audit_logger import build_document_event

    known, unknown = [(await env.add_document())[0] for _ in range(2)]
    async with env.session_maker() as session:
        for document_id, reason in ((known, "script_mismatch"), (unknown, "<b>free text</b>")):
            session.add(build_document_event(
                document_id=document_id, event_type="document_language_changed",
                extra={"user_id": None, "previous_override": "ru", "override": None, "reason": reason},
            ))
        await session.commit()
        assert await _override_refusals(session, [known, unknown]) == {known: ("ru", "script_mismatch")}

    body = (await env.client.get(f"/api/documents/{unknown}")).json()
    assert (body["override_refused_language"], body["override_refused_reason"]) == (None, None)


async def test_list_refusals_are_scoped_by_a_subquery_not_bound_ids(env):
    from sqlalchemy import event, select

    from septum_api.models.document import Document
    from septum_api.routers.documents import _override_refusals, _scope_to_owner
    from septum_api.services.audit_logger import build_document_event

    mine, *_others = [(await env.add_document())[0] for _ in range(5)]
    async with env.session_maker() as session:
        session.add(build_document_event(
            document_id=mine, event_type="document_language_changed",
            extra={"user_id": None, "previous_override": "ru", "override": None,
                   "reason": "script_mismatch"},
        ))
        session.add(build_document_event(
            document_id=mine + 1000, event_type="document_language_changed",
            extra={"user_id": None, "previous_override": "de", "override": None,
                   "reason": "script_mismatch"},
        ))
        await session.commit()

        seen: list = []

        def _params(conn, cursor, statement, parameters, context, executemany):
            seen.append(parameters)

        sync_engine = session.bind.sync_engine
        event.listen(sync_engine, "before_cursor_execute", _params)
        try:
            result = await _override_refusals(
                session, _scope_to_owner(select(Document.id), env.user)
            )
        finally:
            event.remove(sync_engine, "before_cursor_execute", _params)

    assert result == {mine: ("ru", "script_mismatch")}
    assert [tuple(params) for params in seen] == [("document_language_changed", env.user.id)]


async def test_list_refusals_are_scoped_to_the_caller_unless_admin(env):
    from septum_api.main import app
    from septum_api.models.document import Document
    from septum_api.models.user import User
    from septum_api.services.audit_logger import build_document_event
    from septum_api.utils.auth_dependency import get_current_user

    mine, _ = await env.add_document()
    theirs, _ = await env.add_document()
    async with env.session_maker() as session:
        other = User(email="other@example.com", hashed_password="x", role="editor")
        admin = User(email="admin@example.com", hashed_password="x", role="admin")
        session.add_all([other, admin])
        await session.flush()
        row = await session.get(Document, theirs)
        row.user_id = other.id
        for document_id, language in ((mine, "ru"), (theirs, "ko")):
            session.add(build_document_event(
                document_id=document_id, event_type="document_language_changed",
                extra={"user_id": None, "previous_override": language, "override": None,
                       "reason": "script_mismatch"},
            ))
        await session.commit()
        admin_user = admin

    async def _listed():
        items = (await env.client.get("/api/documents")).json()["items"]
        return {d["id"]: d["override_refused_language"] for d in items}

    assert await _listed() == {mine: "ru"}
    app.dependency_overrides[get_current_user] = lambda: admin_user
    try:
        assert await _listed() == {mine: "ru", theirs: "ko"}
    finally:
        app.dependency_overrides[get_current_user] = lambda: env.user


async def test_patch_reads_the_acting_user_before_it_writes_the_document(env):
    from sqlalchemy import event

    from septum_api.database import get_db
    from septum_api.main import app

    doc_id, _ = await env.add_document()
    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(statement.split()).lower())

    session_gen = app.dependency_overrides[get_db]()
    session = await session_gen.__anext__()
    sync_engine = session.bind.sync_engine
    await session_gen.aclose()
    event.listen(sync_engine, "before_cursor_execute", _record)
    try:
        resp = await env.client.patch(f"/api/documents/{doc_id}/language", json={"language": "de"})
    finally:
        event.remove(sync_engine, "before_cursor_execute", _record)
    assert resp.status_code == 200, resp.text

    def _first(prefix):
        return next(i for i, sql in enumerate(statements) if sql.startswith(prefix))

    user_read = _first("select users.id from users")
    document_write = _first("update documents set language_override")
    assert user_read < document_write
    assert not any(
        "for update" in sql and "transcription_text" in sql for sql in statements
    )


async def test_malformed_refusal_events_report_null_instead_of_failing(env):
    from septum_api.routers.documents import _override_refusals
    from septum_api.services.audit_logger import build_document_event

    listed, scalar, nested, good = [(await env.add_document())[0] for _ in range(4)]
    async with env.session_maker() as session:
        for document_id, extra in (
            (listed, {"reason": ["script_mismatch"], "previous_override": "ru"}),
            (scalar, {"reason": "script_mismatch", "previous_override": ["ru"]}),
            (good, {"reason": "script_mismatch", "previous_override": "ru"}),
        ):
            session.add(build_document_event(
                document_id=document_id, event_type="document_language_changed", extra=extra,
            ))
        event_row = build_document_event(
            document_id=nested, event_type="document_language_changed", extra={},
        )
        event_row.extra = ["script_mismatch", "ru"]
        session.add(event_row)
        await session.commit()
        assert await _override_refusals(session, [listed, scalar, nested, good]) == {
            good: ("ru", "script_mismatch")
        }

    resp = await env.client.get("/api/documents")
    assert resp.status_code == 200, resp.text
    refused = {d["id"]: d["override_refused_reason"] for d in resp.json()["items"]}
    assert refused == {listed: None, scalar: None, nested: None, good: "script_mismatch"}


async def test_patch_locks_follow_the_postgres_order_and_the_user_is_rechecked_before_commit(env, monkeypatch):
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.ext.asyncio import AsyncSession

    doc_id, _ = await env.add_document()
    compiled: list[str] = []
    real_execute = AsyncSession.execute

    async def _spy(self, statement, *args, **kwargs):
        if hasattr(statement, "compile"):
            compiled.append(" ".join(str(statement.compile(dialect=postgresql.dialect())).split()).lower())
        return await real_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "execute", _spy)
    resp = await env.client.patch(f"/api/documents/{doc_id}/language", json={"language": "de"})
    assert resp.status_code == 200, resp.text

    user_reads = [i for i, sql in enumerate(compiled) if sql.startswith("select users.id")]
    assert len(user_reads) == 2
    assert all(compiled[i].endswith("for share") for i in user_reads)
    text_reads = [i for i, sql in enumerate(compiled) if "documents.transcription_text" in sql
                  and sql.startswith("select documents.transcription_text")]
    assert len(text_reads) == 1
    assert "for update" not in compiled[text_reads[0]]
    assert user_reads[0] < text_reads[0] < user_reads[1]


def test_sample_stripping_uses_the_same_patterns_as_langdetect():
    from langdetect.detector import Detector as LangDetector
    from septum_core.language_resolution import _MAIL_RE, _URL_RE

    assert _URL_RE.pattern == LangDetector.URL_RE.pattern
    assert _MAIL_RE.pattern == LangDetector.MAIL_RE.pattern


async def test_patch_response_carries_the_same_transcription_text(env):
    text = "The tenant paid the deposit before moving into the warehouse hall."
    doc_id, _ = await env.add_document()
    await _record_language(env, doc_id, language="en", transcription=text)
    resp = await env.client.patch(f"/api/documents/{doc_id}/language", json={"language": "de"})
    assert resp.status_code == 200, resp.text
    fetched = (await env.client.get(f"/api/documents/{doc_id}")).json()
    assert resp.json()["transcription_text"] == fetched["transcription_text"] == text


async def test_patch_response_text_is_null_when_the_document_has_none_yet(env):
    doc_id, _ = await env.add_document(status="processing")
    resp = await env.client.patch(f"/api/documents/{doc_id}/language", json={"language": "de"})
    assert resp.status_code == 200, resp.text
    fetched = (await env.client.get(f"/api/documents/{doc_id}")).json()
    assert resp.json()["transcription_text"] is None and fetched["transcription_text"] is None
    assert resp.json()["language_override"] == fetched["language_override"] == "de"
