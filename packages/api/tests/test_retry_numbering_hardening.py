from __future__ import annotations

"""Malformed retry tokens and oversized placeholder indexes fail closed."""

import pytest
from septum_api.services.anonymization_map import AnonymizationMap
from septum_api.services.pii_gate import reserve_placeholder_counters
from septum_api.services.retry_numbering import (
    MAX_NUMBERING_TOKEN_LENGTH,
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


def test_nine_digit_placeholder_index_is_still_reserved() -> None:
    amap = AnonymizationMap(document_id=1, language="en")

    reserve_placeholder_counters(amap, ["[PERSON_123456789]", "[PHONE_1234567890]"])

    assert amap.token_counter == {"PERSON": 123456789}
