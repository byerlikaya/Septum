from __future__ import annotations

"""Malformed retry tokens and oversized placeholder indexes fail closed."""

import hashlib
import json

import pytest
from septum_api.routers.chat import _build_unified_placeholder_space
from septum_api.services import retry_numbering
from septum_api.services.anonymization_map import PLACEHOLDER_PATTERN, AnonymizationMap
from septum_api.services.pii_gate import (
    MAX_TYPED_PLACEHOLDER_INDEX,
    reserve_placeholder_counters,
)
from septum_api.services.retry_numbering import (
    MAX_NUMBERING_TOKEN_LENGTH,
    numbering_fingerprint,
    sign_numbering,
    verify_numbering,
)

FINGERPRINT = b"\x01" * 32


def test_issued_token_verifies() -> None:
    assert verify_numbering(sign_numbering(FINGERPRINT, 7), FINGERPRINT, 7) is True


@pytest.mark.parametrize(
    "token",
    [
        "v1.\ud800",
        "v1.\udcff" + "A" * 10,
        "v1.çok-güzel",
        "v1." + "٣" * 20,
        "v1." + "A" * MAX_NUMBERING_TOKEN_LENGTH,
    ],
    ids=["lone-high-surrogate", "lone-low-surrogate", "non-ascii", "unicode-digits", "too-long"],
)
def test_malformed_token_is_invalid_not_an_error(token: str) -> None:
    assert verify_numbering(token, FINGERPRINT, 7) is False


def test_huge_placeholder_index_is_ignored_without_raising() -> None:
    amap = AnonymizationMap(document_id=1, language="en")

    reserve_placeholder_counters(amap, ["[PERSON_" + "9" * 5000 + "] and [EMAIL_12]"])

    assert amap.token_counter == {"EMAIL": 12}


def test_typed_index_at_the_cap_is_still_reserved() -> None:
    amap = AnonymizationMap(document_id=1, language="en")

    reserve_placeholder_counters(
        amap, [f"[PERSON_{MAX_TYPED_PLACEHOLDER_INDEX}]", "[PHONE_1234567890]"]
    )

    assert amap.token_counter == {"PERSON": MAX_TYPED_PLACEHOLDER_INDEX}


def test_typed_index_above_the_cap_does_not_push_the_counter() -> None:
    amap = AnonymizationMap(document_id=1, language="en")

    reserve_placeholder_counters(amap, ["[ORGANIZATION_999999999]"])
    minted = amap.add_entity("Acme", "ORGANIZATION")

    assert PLACEHOLDER_PATTERN.fullmatch(minted) is not None
    assert minted == "[ORGANIZATION_1]"


def test_typed_small_index_is_still_reserved() -> None:
    amap = AnonymizationMap(document_id=1, language="en")

    reserve_placeholder_counters(amap, ["[ORGANIZATION_7]"])

    assert amap.add_entity("Acme", "ORGANIZATION") == "[ORGANIZATION_8]"


def test_large_index_the_map_already_holds_still_raises_the_counter() -> None:
    amap = AnonymizationMap(document_id=1, language="en")
    amap.placeholder_lookup["[PERSON_NAME_5000000]"] = "Jane Roe"

    reserve_placeholder_counters(amap)

    assert amap.token_counter == {"PERSON_NAME": 5000000}
    assert amap.add_entity("John Doe", "PERSON_NAME") == "[PERSON_NAME_5000001]"


def _maps() -> tuple[AnonymizationMap, dict[int, AnonymizationMap]]:
    query = AnonymizationMap(document_id=0, language="tr")
    query.add_entity("Ali", "PERSON_NAME")
    docs = {}
    for doc_id, names in ((1, ("Ali Yılmaz", "Ali")), (2, ("Ali Kaya", "Ali"))):
        docs[doc_id] = AnonymizationMap(document_id=doc_id, language="tr")
        for name in names:
            docs[doc_id].add_entity(name, "PERSON_NAME")
    return query, docs


def _fingerprint_before_the_algorithm_was_pinned(
    query: AnonymizationMap, docs: dict[int, AnonymizationMap], seed: int | None
) -> bytes:
    """The fingerprint as issued before ``NUMBERING_ALGORITHM`` joined the digest."""
    payload = json.dumps(
        {
            "documents": sorted(docs),
            "seed": seed,
            "query": retry_numbering._map_digest(query),
            "maps": {str(d): retry_numbering._map_digest(docs[d]) for d in sorted(docs)},
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).digest()


def test_token_issued_under_the_previous_numbering_fails_closed() -> None:
    query, docs = _maps()
    old_token = sign_numbering(_fingerprint_before_the_algorithm_was_pinned(query, docs, 1), 7)

    assert verify_numbering(old_token, numbering_fingerprint(query, docs, 1), 7) is False


def test_retry_on_the_same_inputs_verifies_and_rebuilds_the_same_numbering() -> None:
    query, docs = _maps()
    token = sign_numbering(numbering_fingerprint(query, docs, None), 7)
    first = _build_unified_placeholder_space({-1: query, **docs})

    retry_query, retry_docs = _maps()
    second = _build_unified_placeholder_space({-1: retry_query, **retry_docs})

    assert verify_numbering(token, numbering_fingerprint(retry_query, retry_docs, None), 7) is True
    assert first[0] == second[0]
    assert first[1].placeholder_lookup == second[1].placeholder_lookup
    assert first[1].token_to_placeholder == second[1].token_to_placeholder


def test_trusted_text_reserves_an_index_above_the_cap() -> None:
    amap = AnonymizationMap(document_id=1, language="en")

    reserve_placeholder_counters(amap, trusted_texts=["[EMAIL_1500000]"])

    assert amap.add_entity("a@example.com", "EMAIL") == "[EMAIL_1500001]"


async def test_working_copy_reserves_stored_chunk_indexes_above_the_cap() -> None:
    from types import SimpleNamespace

    from septum_api.services import pii_gate

    class _Rows:
        def scalars(self):
            return self

        def all(self):
            return ["[EMAIL_1500000] wrote to [EMAIL_3]"]

    class _Db:
        async def execute(self, _statement):
            return _Rows()

    document = SimpleNamespace(id=5, language_override=None, detected_language="en")

    working = await pii_gate._working_copy(_Db(), document, None, ["[EMAIL_2000000]"])

    assert working.token_counter == {"EMAIL": 1500000}
    assert working.add_entity("a@example.com", "EMAIL") == "[EMAIL_1500001]"
