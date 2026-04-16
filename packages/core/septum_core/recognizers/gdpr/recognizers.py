from __future__ import annotations

"""Recognizer pack for the gdpr regulation.

Adds format-based national-ID, tax-ID and social-security detectors
for the main EU jurisdictions alongside the common email / IPv4 /
phone patterns. Every detector uses a context-keyword preamble and
``narrow_to_group=1`` so only the identifier value itself lands in
the reported span. Entity-type coverage and legal basis are
documented in ``packages/api/docs/REGULATION_ENTITY_SOURCES.md``.
"""

from typing import List

from presidio_analyzer import EntityRecognizer

from ..base_recognizer import RegexPatternConfig, ValidatedPatternRecognizer

# FUTURE: move every context-keyword tuple below into a DB-backed
# per-regulation vocabulary table so administrators can extend
# preamble coverage without editing production code. The alternation
# strings that these tuples feed into are deliberately minimal and
# ASCII-insensitive where possible.
_LETTER_PREFIX_8DIGIT_ID_CONTEXT = (
    r"personalausweis(?:\s*[\-\s]?\s*nr\.?)?",
    r"ausweis\s*[\-\s]?\s*nr\.?",
    r"id\s*[\-\s]?\s*karte",
    r"identity\s*card",
)
_ELEVEN_DIGIT_TAX_ID_CONTEXT = (
    r"steuer\s*[\-\s]?\s*id",
    r"steuerliche\s+id",
    r"steueridentifikationsnummer",
    r"identifikationsnummer",
)
_PENSION_INSURANCE_CONTEXT = (
    r"rentenversicherungs?\s*[\-\s]?\s*nr\.?",
    r"sozialversicherungs?\s*[\-\s]?\s*nr\.?",
    r"rv\s*[\-\s]?\s*nr",
    r"sv\s*[\-\s]?\s*nr",
)
_DOTTED_8DIGIT_LETTER_ID_CONTEXT = (
    r"\bdni\b",
    r"documento\s+nacional",
)
_FOREIGNER_ID_CONTEXT = (
    r"\bnie\b",
    r"n[úu]mero\s+de\s+identificaci[óo]n\s+de\s+extranjero",
)
_TWELVE_DIGIT_SOCIAL_SECURITY_CONTEXT = (
    r"seguridad\s+social",
    r"n\.?\s*seguridad\s+social",
    r"\bnss\b",
)
_FIFTEEN_DIGIT_SOCIAL_SECURITY_CONTEXT = (
    r"s[eé]curit[eé]\s+sociale",
    r"\bnir\b",
    r"num[ée]ro\s+de\s+s[eé]curit[eé]\s+sociale",
)
_NINE_DIGIT_BUSINESS_REGISTRATION_CONTEXT = (
    r"\bsiren\b",
    r"\bsiret\b",
    r"n°?\s*siren",
)


def _join_context(*groups: tuple[str, ...]) -> str:
    """Flatten nested context-keyword tuples into a single alternation."""
    flat: list[str] = []
    for group in groups:
        flat.extend(group)
    return "|".join(flat)


def _email_recognizer() -> EntityRecognizer:
    return ValidatedPatternRecognizer(
        entity_type="EMAIL_ADDRESS",
        config=RegexPatternConfig(
            name="gdpr_email",
            pattern=r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}\b",
            score=0.8,
        ),
    )


def _ip_address_recognizer() -> EntityRecognizer:
    return ValidatedPatternRecognizer(
        entity_type="IP_ADDRESS",
        config=RegexPatternConfig(
            name="gdpr_ipv4",
            pattern=(
                r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}"
                r"(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\b"
            ),
            score=0.8,
        ),
    )


def _letter_prefix_8digit_national_id_recognizer() -> EntityRecognizer:
    """Letter + 8-digit national ID preceded by an ID-card context keyword.

    Targets the German Personalausweis format (for example
    ``T22000129``) but remains intentionally format-descriptive so
    other identity-card schemes with the same shape can reuse it.
    """
    ctx = _join_context(_LETTER_PREFIX_8DIGIT_ID_CONTEXT)
    return ValidatedPatternRecognizer(
        entity_type="NATIONAL_ID",
        config=RegexPatternConfig(
            name="gdpr_letter_prefix_8digit_national_id",
            pattern=rf"(?i)(?:{ctx})[^\w]{{0,16}}([A-Z]\d{{8}})",
            score=0.75,
            narrow_to_group=1,
        ),
    )


def _eleven_digit_tax_identifier_recognizer() -> EntityRecognizer:
    """11-digit tax identifier preceded by a Steuer-ID style preamble.

    Accepts compact ``77523164890`` and space-grouped
    ``77 523 164 890`` variants. Covers the German Steuer-ID and any
    jurisdictional 11-digit identifier that shares the context.
    """
    ctx = _join_context(_ELEVEN_DIGIT_TAX_ID_CONTEXT)
    return ValidatedPatternRecognizer(
        entity_type="TAX_ID",
        config=RegexPatternConfig(
            name="gdpr_11digit_tax_identifier",
            pattern=(
                rf"(?i)(?:{ctx})[^\w]{{0,16}}"
                r"(\d{2}\s?\d{3}\s?\d{3}\s?\d{3}|\d{11})"
            ),
            score=0.8,
            narrow_to_group=1,
        ),
    )


def _pension_insurance_number_recognizer() -> EntityRecognizer:
    """Pension/social-insurance number preceded by an RV-Nr keyword.

    The German format is ``NN NNNNNN L NNN`` (two digits, six digits,
    one letter, three digits). Spaces are optional.
    """
    ctx = _join_context(_PENSION_INSURANCE_CONTEXT)
    return ValidatedPatternRecognizer(
        entity_type="NATIONAL_ID",
        config=RegexPatternConfig(
            name="gdpr_pension_insurance_number",
            pattern=(
                rf"(?i)(?:{ctx})[^\w]{{0,16}}"
                r"(\d{2}\s?\d{6}\s?[A-Z]\s?\d{3})"
            ),
            score=0.8,
            narrow_to_group=1,
        ),
    )


def _eight_digit_plus_letter_national_id_recognizer() -> EntityRecognizer:
    """8 digits + check letter preceded by a DNI-style keyword.

    Matches both the compact ``12345678Z`` form and the dotted
    ``12.345.678-Z`` form. Covers the Spanish DNI format.
    """
    ctx = _join_context(_DOTTED_8DIGIT_LETTER_ID_CONTEXT)
    return ValidatedPatternRecognizer(
        entity_type="NATIONAL_ID",
        config=RegexPatternConfig(
            name="gdpr_8digit_letter_national_id",
            pattern=(
                rf"(?i)(?:{ctx})[^\w]{{0,16}}"
                r"(\d{1,3}(?:\.\d{3}){2}[\-\s]?[A-Z]|\d{8}[\-\s]?[A-Z])"
            ),
            score=0.8,
            narrow_to_group=1,
        ),
    )


def _foreigner_identification_recognizer() -> EntityRecognizer:
    """``[XYZ]`` + 7 digits + check letter preceded by an NIE keyword."""
    ctx = _join_context(_FOREIGNER_ID_CONTEXT)
    return ValidatedPatternRecognizer(
        entity_type="NATIONAL_ID",
        config=RegexPatternConfig(
            name="gdpr_foreigner_id",
            pattern=rf"(?i)(?:{ctx})[^\w]{{0,16}}([XYZxyz]\d{{7}}[A-Z])",
            score=0.8,
            narrow_to_group=1,
        ),
    )


def _twelve_digit_social_security_recognizer() -> EntityRecognizer:
    """11-12 digit social-security number with flexible grouping.

    Matches the Spanish Seguridad Social family — commonly grouped
    as ``NN NN NNNNNN NN`` but also seen as ``NN NN NNNNN NN`` in
    print where the middle group is compressed. Requires a
    social-security context keyword preamble.
    """
    ctx = _join_context(_TWELVE_DIGIT_SOCIAL_SECURITY_CONTEXT)
    return ValidatedPatternRecognizer(
        entity_type="SOCIAL_SECURITY_NUMBER",
        config=RegexPatternConfig(
            name="gdpr_12digit_social_security",
            pattern=(
                rf"(?i)(?:{ctx})[^\w]{{0,16}}"
                r"(\d{2}[\s\-/]?\d{2}[\s\-/]?\d{4,8}[\s\-/]?\d{1,2})"
            ),
            score=0.8,
            narrow_to_group=1,
        ),
    )


def _fifteen_digit_social_security_recognizer() -> EntityRecognizer:
    """15-digit social-security number for NIR-style identifiers.

    Pattern: ``S YY MM DD NNN NNN KK`` (optional spaces). Covers the
    French NIR format.
    """
    ctx = _join_context(_FIFTEEN_DIGIT_SOCIAL_SECURITY_CONTEXT)
    return ValidatedPatternRecognizer(
        entity_type="SOCIAL_SECURITY_NUMBER",
        config=RegexPatternConfig(
            name="gdpr_15digit_social_security",
            pattern=(
                rf"(?i)(?:{ctx})[^\w]{{0,16}}"
                r"(\d\s?\d{2}\s?\d{2}\s?\d{2}\s?\d{3}\s?\d{3}(?:\s?\d{2})?)"
            ),
            score=0.85,
            narrow_to_group=1,
        ),
    )


def _nine_digit_business_registration_recognizer() -> EntityRecognizer:
    """9-digit business-registration identifier (3-3-3 grouped).

    Matches the French SIREN format (``834 291 076``) when preceded
    by a SIREN / SIRET context keyword.
    """
    ctx = _join_context(_NINE_DIGIT_BUSINESS_REGISTRATION_CONTEXT)
    return ValidatedPatternRecognizer(
        entity_type="TAX_ID",
        config=RegexPatternConfig(
            name="gdpr_9digit_business_registration",
            pattern=rf"(?i)(?:{ctx})[^\w]{{0,16}}(\d{{3}}\s?\d{{3}}\s?\d{{3}})",
            score=0.75,
            narrow_to_group=1,
        ),
    )


def get_recognizers() -> List[EntityRecognizer]:
    """Return all gdpr-specific recognizers."""
    recognizers: List[EntityRecognizer] = []
    recognizers.append(_email_recognizer())
    recognizers.append(_ip_address_recognizer())
    recognizers.append(_letter_prefix_8digit_national_id_recognizer())
    recognizers.append(_eleven_digit_tax_identifier_recognizer())
    recognizers.append(_pension_insurance_number_recognizer())
    recognizers.append(_eight_digit_plus_letter_national_id_recognizer())
    recognizers.append(_foreigner_identification_recognizer())
    recognizers.append(_twelve_digit_social_security_recognizer())
    recognizers.append(_fifteen_digit_social_security_recognizer())
    recognizers.append(_nine_digit_business_registration_recognizer())
    return recognizers
