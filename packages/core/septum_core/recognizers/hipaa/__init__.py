from __future__ import annotations

"""HIPAA recognizer pack package.

``ENTITY_TYPES`` is the union of PII categories this regulation
requires to be masked. Source of truth for both the API seed
and the standalone core/MCP engine.
"""

ENTITY_TYPES: tuple[str, ...] = (
    "PERSON_NAME",
    "DATE_OF_BIRTH",
    "PHONE_NUMBER",
    "EMAIL_ADDRESS",
    "POSTAL_ADDRESS",
    "CITY",
    "MEDICAL_RECORD_NUMBER",
    "HEALTH_INSURANCE_ID",
    "DIAGNOSIS",
    "MEDICATION",
    "CLINICAL_NOTE",
    "BIOMETRIC_ID",
    "SOCIAL_SECURITY_NUMBER",
    "IP_ADDRESS",
    "DEVICE_ID",
    "LICENSE_PLATE",
    "URL",
)
