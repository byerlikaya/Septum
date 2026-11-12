"""``masking_segments``: the segment helper behind ingestion, and its reset on reprocess."""

from __future__ import annotations

import pytest
from langdetect import DetectorFactory
from septum_core.language_segments import Segment

from septum_api.utils import db_helpers
from septum_api.utils.db_helpers import detect_language, langdetect_candidates, masking_segments

EN = (
    "The quarterly report describes the ongoing negotiations between both parties and the "
    "obligations each of them accepts under the agreement. "
) * 4
ZH = "本合同由双方根据平等自愿的原则订立，双方应当严格履行合同约定的各项义务，不得擅自变更。" * 3


@pytest.fixture(autouse=True)
def _seed_langdetect():
    DetectorFactory.seed = 42


def _boundaries(*parts: str) -> list[int]:
    starts, position = [], 0
    for part in parts:
        starts.append(position)
        position += len(part) + 2
    return starts


def test_user_chosen_language_returns_no_segments(monkeypatch):
    def fail(sample):
        raise AssertionError("an override must not run language detection")

    monkeypatch.setattr(db_helpers, "langdetect_candidates", fail)
    text = f"{EN}\n\n{ZH}"

    assert masking_segments(text, _boundaries(EN, ZH), "en", "override") is None


def test_single_language_text_is_one_dominant_segment():
    text = f"{EN}\n\n{EN}"

    assert masking_segments(text, _boundaries(EN, EN), "en", "detected") == (
        Segment(0, len(text), "en"),
    )


def test_mixed_text_is_cut_where_the_language_changes():
    text = f"{EN}\n\n{ZH}\n\n{EN}"
    zh_start = len(EN) + 2

    segments = masking_segments(text, _boundaries(EN, ZH, EN), "en", "detected")

    assert [(s.start, s.language) for s in segments] == [
        (0, "en"),
        (zh_start, "zh"),
        (zh_start + len(ZH) + 2, "en"),
    ]
    assert segments[-1].end == len(text)


def test_segments_use_the_same_langdetect_call_as_detect_language(monkeypatch):
    calls: list[str] = []

    def candidates(sample):
        calls.append(sample)
        return [("fr", 0.99)]

    monkeypatch.setattr(db_helpers, "langdetect_candidates", candidates)

    assert detect_language(EN) == "fr"
    assert [s.language for s in masking_segments(EN, [0], "en", "detected")] == ["fr"]
    assert len(calls) == 2


def test_candidates_of_text_without_features_are_empty():
    assert langdetect_candidates("12345 6789") == []


def test_a_langdetect_error_other_than_no_features_propagates(monkeypatch):
    from langdetect.lang_detect_exception import ErrorCode, LangDetectException

    def broken(sample):
        raise LangDetectException(ErrorCode.NeedLoadProfileError, "profiles missing")

    monkeypatch.setattr(db_helpers, "detect_langs", broken)

    with pytest.raises(LangDetectException):
        langdetect_candidates(EN)
    with pytest.raises(LangDetectException):
        masking_segments(EN, [0], "en", "detected")


def test_candidates_are_ranked_langdetect_guesses():
    (code, probability), *_ = langdetect_candidates(EN)

    assert code == "en" and probability > 0.9


def test_dominant_language_that_is_not_a_language_code_raises():
    with pytest.raises(ValueError):
        masking_segments(EN, [0], "not a code", "detected")


async def test_claiming_a_document_for_rebuild_clears_the_masked_language_list(tmp_path):
    from septum_api.models import Base
    from septum_api.models.document import Document
    from septum_api.routers.documents import claim_for_reingest
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'claim.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with maker() as session:
            document = Document(
                filename="f", original_filename="f.pdf", file_type="application/pdf",
                file_format="pdf", detected_language="en", encrypted_path="/x",
                file_size_bytes=1, ingestion_status="completed",
                sanitized_language="en", sanitized_languages=["en", "zh"],
            )
            session.add(document)
            await session.commit()

            assert await claim_for_reingest(session, document.id)

        async with maker() as session:
            stored = await session.get(Document, document.id)
            assert stored.ingestion_status == "processing"
            assert stored.sanitized_language is None
            assert stored.sanitized_languages is None
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    ("language", "applied", "expected"),
    [
        ("en", None, ["en"]),
        ("en", (), ["en"]),
        ("en", ("tr", "zh"), ["en", "tr", "zh"]),
        ("en", ("zh", "tr"), ["en", "zh", "tr"]),
        ("zh", ("zh-cn", "en", "en"), ["zh", "en"]),
        ("en", ("EN", "tr-TR"), ["en", "tr"]),
        ("en", ("not a code", "t", "", "tr"), ["en", "tr"]),
    ],
)
def test_masked_languages_are_the_dominant_one_then_the_validated_applied_ones(
    language, applied, expected
):
    from septum_api.services.document_pipeline import _masked_languages

    assert _masked_languages(language, applied) == expected
