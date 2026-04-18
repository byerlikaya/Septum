from __future__ import annotations

"""GDPR recognizer pack package.

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
    "IP_ADDRESS",
    "MAC_ADDRESS",
    "POSTAL_ADDRESS",
    "STREET_ADDRESS",
    "CITY",
    "DATE_OF_BIRTH",
    "NATIONAL_ID",
    "PASSPORT_NUMBER",
    "TAX_ID",
    "BIOMETRIC_ID",
    "HEALTH_INSURANCE_ID",
    "DIAGNOSIS",
    "MEDICATION",
    "CLINICAL_NOTE",
    "COOKIE_ID",
    "DEVICE_ID",
    "LOCATION",
    "COORDINATES",
    "FINANCIAL_ACCOUNT",
    "CREDIT_CARD_NUMBER",
    "BANK_ACCOUNT_NUMBER",
    "POLITICAL_OPINION",
    "RELIGION",
    "ETHNICITY",
    "SEXUAL_ORIENTATION",
)
