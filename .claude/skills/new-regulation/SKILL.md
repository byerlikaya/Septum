---
name: new-regulation
description: Creates a new privacy regulation pack for Septum. Use when adding support for a new country's data protection law. Invoke with /new-regulation.
---

# New Regulation Pack Skill

When invoked, ask the user:
1. Regulation ID (short, lowercase, underscore: e.g., `pdpa_th`, `popia`, `dpdp`)
2. Full display name (e.g., "Personal Data Protection Act")
3. Country/Region (e.g., "Thailand")
4. Official reference URL
5. Which entity types from the master list does this regulation cover?
   Show the full master list and ask user to select.

Then generate:

## 1. `packages/api/septum_api/services/recognizers/{regulation_id}/recognizers.py`

```python
"""
{REGULATION_DISPLAY_NAME} ({REGULATION_ID.upper()}) recognizer pack for Septum.
Region: {REGION}
Reference: {OFFICIAL_URL}

Covered entity types:
{entity_type_list_as_comments}
"""
from __future__ import annotations
from typing import List
from presidio_analyzer import EntityRecognizer


def get_recognizers() -> List[EntityRecognizer]:
    """
    Returns all Presidio recognizers for {REGULATION_DISPLAY_NAME}.
    Each recognizer covers one or more entity types mandated by this regulation.
    """
    recognizers: List[EntityRecognizer] = []

    # --- Standard recognizers (reuse from Presidio defaults) ---
    # These are already built into Presidio for common entity types.
    # Only add custom ones below for regulation-specific or country-specific IDs.

    # Example: if this regulation requires masking national IDs specific to {REGION}:
    # from app.services.recognizers.{regulation_id}.national_id import {REGION}IDRecognizer
    # recognizers.append({REGION}IDRecognizer())

    return recognizers
```

## 2. `packages/api/septum_api/services/recognizers/{regulation_id}/__init__.py`

```python
# {REGULATION_DISPLAY_NAME} recognizer pack
```

## 3. Seed data entry in `database.py`

Add to the `BUILTIN_REGULATIONS` list:
```python
{
    "id": "{regulation_id}",
    "display_name": "{REGULATION_DISPLAY_NAME}",
    "region": "{REGION}",
    "description": "Brief description of the regulation and its scope.",
    "official_url": "{OFFICIAL_URL}",
    "entity_types": [
        # List the selected entity types here
        "PERSON_NAME",
        "EMAIL_ADDRESS",
        # ... add all selected types
    ],
    "is_builtin": True,
    "is_active": False,  # user activates manually
},
```

## 4. `backend/tests/test_policy_composer.py` — add test

```python
def test_{regulation_id}_regulation_activates_correct_entities(db_session):
    """
    Activating {regulation_id} must add its entity types to the composed policy.
    """
    # Activate only this regulation
    reg = db_session.query(RegulationRuleset).filter_by(id="{regulation_id}").first()
    reg.is_active = True
    db_session.commit()

    composer = PolicyComposer()
    policy = composer.compose(db_session)

    # Assert key entity types are present
    assert "PERSON_NAME" in policy.entity_types
    # Add assertions for all expected entity types
```

## 5. `backend/tests/test_sanitizer.py` — add integration test

```python
def test_sanitizer_with_{regulation_id}(db_session):
    """
    End-to-end: activate {regulation_id}, run sanitizer on sample text,
    verify expected entities are masked.
    """
    sample_text = "Sample text containing PII relevant to {REGION}"
    # TODO: add realistic sample text with PII for this regulation's region
    
    policy = PolicyComposer().compose(db_session)
    anon_map = AnonymizationMap(document_id=0, language="en")
    sanitizer = PIISanitizer(policy=policy, settings=default_settings())
    result = sanitizer.sanitize(sample_text, language="en", anon_map=anon_map)
    
    assert result.entity_count > 0
    # Verify no original PII remains in sanitized text
    # TODO: add specific assertions
```

## 6. Update frontend regulation list

The frontend fetches regulations from GET /api/regulations.
No manual frontend changes needed — the seed data drives the UI automatically.

After generating, remind the user to:
- Review entity types selection (can be updated in Settings → Regulations)
- Add country-specific recognizers if the regulation covers national ID numbers
  (use /new-recognizer for each ID type)
- Write realistic PII samples for the integration test
- Consider running /new-recognizer for any country-specific ID formats
