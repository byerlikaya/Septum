from __future__ import annotations

"""Tests for country-specific national ID validators."""

from septum_api.services.national_ids import (
    AadhaarValidator,
    CPFValidator,
    IBANValidator,
    SSNValidator,
    TCKNValidator,
)


def test_tckn_valid_and_invalid() -> None:
    validator = TCKNValidator()

    # Known-valid TCKN based on checksum rules.
    assert validator.validate("10000000146")

    # Altering the last checksum digit should invalidate the number.
    assert not validator.validate("10000000145")


def test_cpf_valid_and_invalid() -> None:
    validator = CPFValidator()

    # Commonly used example of a valid CPF.
    assert validator.validate("529.982.247-25")

    # Change the last digit to break the checksum.
    assert not validator.validate("529.982.247-24")


def test_aadhaar_verhoeff_valid() -> None:
    validator = AadhaarValidator()

    # Construct a valid Aadhaar-like number using the internal Verhoeff check digit.
    base = "12345678901"
    check_digit = validator.compute_check_digit(base)
    candidate = f"{base}{check_digit}"

    assert validator.validate(candidate)


def test_iban_multiple_countries() -> None:
    validator = IBANValidator()

    # Sample valid IBANs for different countries (from official/standard examples).
    tr_iban = "TR330006100519786457841326"
    de_iban = "DE89370400440532013000"
    gb_iban = "GB82WEST12345698765432"
    fr_iban = "FR1420041010050500013M02606"

    assert validator.validate(tr_iban)
    assert validator.validate(de_iban)
    assert validator.validate(gb_iban)
    assert validator.validate(fr_iban)


def test_ssn_rejects_blocked_area_code() -> None:
    validator = SSNValidator()

    # Area 000 is explicitly invalid.
    assert not validator.validate("000-12-3456")

