from __future__ import annotations

"""KVKK-specific Presidio recognizers.

Entity types align with 6698 sayılı KVKK Madde 3(d) (kişisel veri: kimliği belirli
veya belirlenebilir gerçek kişiye ilişkin her türlü bilgi) and Madde 6 (özel nitelikli
kişisel veriler: ırk, etnik köken, siyasi düşünce, din, sağlık, cinsel hayat, biyometrik,
genetik vb.). Kurum rehberi örnekleri: ad, soyad, ana/baba adı, adres, doğum tarihi,
telefon, plaka, SGK, pasaport. See backend/docs/REGULATION_ENTITY_SOURCES.md.
"""

from typing import List

from presidio_analyzer import EntityRecognizer

from ..base_recognizer import RegexPatternConfig, ValidatedPatternRecognizer


def _validated_national_id_recognizer() -> EntityRecognizer:
    """11-digit national ID recognizer for KVKK compliance.

    Detects 11-digit numeric IDs starting with 1-9.
    Checksum validation handled separately by ValidatedNationalIDRecognizer.
    """
    return ValidatedPatternRecognizer(
        entity_type="NATIONAL_ID",
        config=RegexPatternConfig(
            name="kvkk_national_id_11digit",
            pattern=r"\b[1-9][0-9]{10}\b",
            score=0.7,
        ),
    )


def _generic_national_id_with_context() -> EntityRecognizer:
    """Generic NATIONAL_ID recognizer using numeric pattern plus context words.

    This recognizer is more permissive than the algorithmic national ID
    validator and is intended to catch obvious ID-like numbers even when
    stricter checksum-based validators do not fire.
    It looks for 8-12 digit sequences that appear close to generic
    ID-related context terms as defined in its regex pattern.
    """
    return ValidatedPatternRecognizer(
        entity_type="NATIONAL_ID",
        config=RegexPatternConfig(
            name="kvkk_national_id_context",
            pattern=(
                r"(?i)(?:id|national|citizen|passport|identity|identification|no)"
                r"[^\d]{0,16}\d{8,12}"
            ),
            score=0.6,
        ),
    )


def _email_recognizer() -> EntityRecognizer:
    return ValidatedPatternRecognizer(
        entity_type="EMAIL_ADDRESS",
        config=RegexPatternConfig(
            name="kvkk_email",
            pattern=r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}\b",
            score=0.8,
        ),
    )


def _phone_recognizer() -> EntityRecognizer:
    """Phone number recognizer for KVKK compliance.

    Detects phone numbers with optional country code prefix.
    Pattern: optional +XX prefix followed by 10 digits.
    """
    return ValidatedPatternRecognizer(
        entity_type="PHONE_NUMBER",
        config=RegexPatternConfig(
            name="kvkk_phone",
            pattern=r"\b\+?[0-9]{2}[0-9]{10}\b",
            score=0.75,
        ),
    )


def get_recognizers() -> List[EntityRecognizer]:
    """Return all KVKK-specific recognizers."""
    recognizers: List[EntityRecognizer] = []
    recognizers.append(_validated_national_id_recognizer())
    recognizers.append(_email_recognizer())
    recognizers.append(_phone_recognizer())
    recognizers.append(_generic_national_id_with_context())
    return recognizers

