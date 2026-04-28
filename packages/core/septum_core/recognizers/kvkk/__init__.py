from __future__ import annotations

"""KVKK recognizer pack package.

``ENTITY_TYPES`` is the union of PII categories this regulation
requires to be masked. Source of truth for both the API seed
and the standalone core/MCP engine.
"""

ENTITY_TYPES: tuple[str, ...] = (
    "PERSON_NAME",
    "FIRST_NAME",
    "LAST_NAME",
    "NATIONAL_ID",
    "PASSPORT_NUMBER",
    "SOCIAL_SECURITY_NUMBER",
    "TAX_ID",
    "PHONE_NUMBER",
    "EMAIL_ADDRESS",
    "POSTAL_ADDRESS",
    "STREET_ADDRESS",
    "CITY",
    "LOCATION",
    "COORDINATES",
    "DATE_OF_BIRTH",
    "LICENSE_PLATE",
    "IP_ADDRESS",
    "COOKIE_ID",
    "DEVICE_ID",
    "BIOMETRIC_ID",
    "DNA_PROFILE",
    "HEALTH_INSURANCE_ID",
    "DIAGNOSIS",
    "MEDICATION",
    "CLINICAL_NOTE",
    "RELIGION",
    "ETHNICITY",
    "POLITICAL_OPINION",
    "SEXUAL_ORIENTATION",
    "FINANCIAL_ACCOUNT",
    "CREDIT_CARD_NUMBER",
    "BANK_ACCOUNT_NUMBER",
    "CUSTOMER_REFERENCE_ID",
)
