from __future__ import annotations

"""CCPA recognizer pack package.

``ENTITY_TYPES`` is the union of PII categories this regulation
requires to be masked. Source of truth for both the API seed
and the standalone core/MCP engine.
"""

ENTITY_TYPES: tuple[str, ...] = (
    "PERSON_NAME",
    "FIRST_NAME",
    "LAST_NAME",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "POSTAL_ADDRESS",
    "STREET_ADDRESS",
    "CITY",
    "IP_ADDRESS",
    "NATIONAL_ID",
    "SOCIAL_SECURITY_NUMBER",
    "PASSPORT_NUMBER",
    "DRIVERS_LICENSE",
    "DATE_OF_BIRTH",
    "BIOMETRIC_ID",
    "FINANCIAL_ACCOUNT",
    "CREDIT_CARD_NUMBER",
    "BANK_ACCOUNT_NUMBER",
    "DEVICE_ID",
    "COOKIE_ID",
    "COORDINATES",
    "LOCATION",
    "RELIGION",
    "ETHNICITY",
    "SEXUAL_ORIENTATION",
)
