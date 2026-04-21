from __future__ import annotations

"""NZPA recognizer pack package.

``ENTITY_TYPES`` is the union of PII categories this regulation
requires to be masked. Source of truth for both the API seed
and the standalone core/MCP engine.
"""

ENTITY_TYPES: tuple[str, ...] = (
    "PERSON_NAME",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "IP_ADDRESS",
    "POSTAL_ADDRESS",
    "DATE_OF_BIRTH",
    "NATIONAL_ID",
    "BIOMETRIC_ID",
    "HEALTH_INSURANCE_ID",
    "DIAGNOSIS",
    "COOKIE_ID",
    "DEVICE_ID",
    "LOCATION",
    "FINANCIAL_ACCOUNT",
    "CREDIT_CARD_NUMBER",
    "BANK_ACCOUNT_NUMBER",
    "RELIGION",
    "ETHNICITY",
    "POLITICAL_OPINION",
    "SEXUAL_ORIENTATION",
)
