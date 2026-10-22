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


def test_whisper_language_falls_back_to_text_detection_when_invalid_or_unsupported():
    from types import SimpleNamespace

    from septum_api.routers.documents import _detect_document_language

    text = "Gecikmiş ödemelere hangi faiz uygulanır?"
    for whisper in ("haw", "yue", "evil\nFORGED", "x" * 40, 3):
        result = SimpleNamespace(text=text, metadata={"detected_language": whisper})
        assert _detect_document_language("audio", result) == "tr"
    supported = SimpleNamespace(text=text, metadata={"detected_language": "de"})
    assert _detect_document_language("audio", supported) == "de"


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


def test_every_detectable_language_has_a_script_rule():
    from langdetect import detector_factory
    from septum_core.language_resolution import _REQUIRED_SCRIPTS, iso_language_code

    from septum_api.utils.db_helpers import _profile_names

    codes = {iso_language_code(name) for name in _profile_names(detector_factory.PROFILES_DIRECTORY)}
    assert codes - set(_REQUIRED_SCRIPTS) == set()


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


def test_sample_stripping_uses_the_same_patterns_as_langdetect():
    from langdetect.detector import Detector as LangDetector
    from septum_core.language_resolution import _MAIL_RE, _URL_RE

    assert _URL_RE.pattern == LangDetector.URL_RE.pattern
    assert _MAIL_RE.pattern == LangDetector.MAIL_RE.pattern
