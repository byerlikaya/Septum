from __future__ import annotations

"""LGPD recognizer pack package.

``ENTITY_TYPES`` is the union of PII categories this regulation
requires to be masked. Source of truth for both the API seed
and the standalone core/MCP engine.
"""

ENTITY_TYPES: tuple[str, ...] = (
    "PERSON_NAME",
    "CPF",
    "NATIONAL_ID",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "POSTAL_ADDRESS",
    "DATE_OF_BIRTH",
    "LOCATION",
    "IP_ADDRESS",
    "COOKIE_ID",
    "CREDIT_CARD_NUMBER",
    "BANK_ACCOUNT_NUMBER",
    "HEALTH_INSURANCE_ID",
    "DIAGNOSIS",
    "BIOMETRIC_ID",
    "POLITICAL_OPINION",
    "RELIGION",
    "ETHNICITY",
    "SEXUAL_ORIENTATION",
)
