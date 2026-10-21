"""Integrity checks for the hand-authored retrieval evaluation fixtures."""

from __future__ import annotations

import re
from collections import Counter

import pytest

from tests.retrieval_eval import (
    ALLOWED_SLOT_TYPES,
    SLOT_PATTERN,
    fixture_digest,
    fixture_files,
    list_slots,
    load_doc_slots,
    load_docs,
    load_queries,
    resolve_gold_spans,
)

LANGS = ("en", "tr", "zh")
QUERY_TYPE_SPLIT = {
    "lexical": 15,
    "paraphrase": 15,
    "entity": 8,
    "cross_lingual": 5,
    "negative": 2,
}
MIN_DOC_CHARS = 4000
MAX_DOC_CHARS = 10000

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
LONG_DIGITS_RE = re.compile(r"\d{7,}")
PHONE_RE = re.compile(r"\+\d[\d ()-]{7,}\d|\(?\d{3}\)?[ -]\d{3}[ -]\d{4}")
IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,}")


@pytest.fixture(scope="module")
def docs():
    return load_docs()


@pytest.fixture(scope="module")
def queries():
    return load_queries()


@pytest.fixture(scope="module")
def doc_texts(docs):
    return {d.doc_id: d.read_text() for d in docs}


def test_gold_quotes_occur_exactly_once(queries, doc_texts):
    for q in queries:
        if q.doc_id is None:
            continue
        assert q.doc_id in doc_texts, f"{q.query_id}: unknown doc {q.doc_id}"
        assert q.gold_quotes, f"{q.query_id}: answerable query without gold"
        spans = resolve_gold_spans(q, doc_texts[q.doc_id])
        for (start, end), quote in zip(spans, q.gold_quotes, strict=True):
            assert doc_texts[q.doc_id][start:end] == quote
            assert "{{" not in quote and "}}" not in quote


def test_every_doc_listed_has_a_file(docs):
    for d in docs:
        assert d.abs_path.is_file(), d.path
    on_disk = {p.stem for p in fixture_files() if p.suffix == ".txt"}
    assert on_disk == {d.doc_id for d in docs}


@pytest.mark.parametrize("lang", LANGS)
def test_doc_counts_and_structure_split(docs, lang):
    lang_docs = [d for d in docs if d.lang == lang]
    assert len(lang_docs) == 10
    assert Counter(d.structure for d in lang_docs) == {"structured": 4, "plain": 6}
    for d in lang_docs:
        expected = "pdf" if d.structure == "structured" else "txt"
        assert d.file_format == expected, d.doc_id
        assert d.doc_id.startswith(f"{lang}_")
        assert d.path == f"corpus/{lang}/{d.doc_id}.txt"


def test_domains_are_spread(docs):
    assert {d.domain for d in docs} == {
        "contract",
        "hr_policy",
        "clinical_report",
        "meeting_minutes",
        "product_manual",
    }
    for lang in LANGS:
        assert len({d.domain for d in docs if d.lang == lang}) == 5


@pytest.mark.parametrize("lang", LANGS)
def test_query_counts_and_type_split(queries, lang):
    lang_queries = [q for q in queries if q.lang == lang and q.type != "chitchat"]
    assert len(lang_queries) == 45
    assert Counter(q.type for q in lang_queries) == QUERY_TYPE_SPLIT


def test_chitchat_negatives(queries):
    chat = [q for q in queries if q.type == "chitchat"]
    assert len(chat) == 5
    assert len({q.query_id for q in queries}) == len(queries)


def test_negatives_have_no_doc_or_gold(queries):
    negatives = [q for q in queries if q.type in ("negative", "chitchat")]
    assert len(negatives) == 3 * 2 + 5
    for q in negatives:
        assert q.doc_id is None, q.query_id
        assert q.gold_quotes == (), q.query_id


def test_cross_lingual_queries_target_other_language(queries, docs):
    doc_lang = {d.doc_id: d.lang for d in docs}
    for q in queries:
        if q.type == "cross_lingual":
            assert doc_lang[q.doc_id] != q.lang, q.query_id
        elif q.doc_id is not None:
            assert doc_lang[q.doc_id] == q.lang, q.query_id


def test_doc_length_within_bounds(docs, doc_texts):
    for d in docs:
        n = len(doc_texts[d.doc_id])
        assert MIN_DOC_CHARS <= n <= MAX_DOC_CHARS, f"{d.doc_id}: {n} chars"


def test_no_literal_pii_in_fixtures():
    for path in fixture_files():
        text = SLOT_PATTERN.sub("", path.read_text(encoding="utf-8"))
        for name, pattern in (
            ("email", EMAIL_RE),
            ("long digit run", LONG_DIGITS_RE),
            ("phone", PHONE_RE),
            ("iban", IBAN_RE),
        ):
            match = pattern.search(text)
            assert match is None, f"{path.name}: literal {name} {match.group(0)!r}"


def test_slots_use_allowed_types_only(docs, doc_texts, queries):
    texts = list(doc_texts.values()) + [q.text for q in queries]
    seen = set()
    for text in texts:
        seen.update(t for t, _ in list_slots(text))
        leftover = SLOT_PATTERN.sub("", text)
        assert "{{" not in leftover and "}}" not in leftover, leftover[:80]
    assert seen
    assert seen <= ALLOWED_SLOT_TYPES, seen - ALLOWED_SLOT_TYPES


def test_entity_ids_are_contiguous_per_doc_and_type():
    for doc_id, slots in load_doc_slots().items():
        by_type: dict[str, list[int]] = {}
        for slot_type, index in slots:
            by_type.setdefault(slot_type, []).append(index)
        for slot_type, indexes in by_type.items():
            assert sorted(indexes) == list(range(1, len(indexes) + 1)), (doc_id, slot_type)


def test_query_slots_exist_in_their_doc(queries):
    doc_slots = {k: set(v) for k, v in load_doc_slots().items()}
    for q in queries:
        slots = list_slots(q.text)
        if not slots:
            continue
        assert q.doc_id is not None, f"{q.query_id}: slot in query without doc_id"
        missing = set(slots) - doc_slots[q.doc_id]
        assert not missing, f"{q.query_id}: {missing} not in {q.doc_id}"
    assert any(q.type == "entity" and list_slots(q.text) for q in queries)
    for q in queries:
        if q.type == "entity":
            assert list_slots(q.text), q.query_id


def test_digest_is_stable_across_loads():
    first = fixture_digest()
    second = fixture_digest()
    assert first == second
    assert re.fullmatch(r"[0-9a-f]{64}", first)
