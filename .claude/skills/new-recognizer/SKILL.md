---
name: new-recognizer
description: Creates a new country-specific ID validator and Presidio recognizer for Septum. Use when adding a new national ID format. Invoke with /new-recognizer.
---

# New National ID Recognizer Skill

When invoked, ask the user:
1. Country name and ISO 3166-1 alpha-2 code (e.g., "Netherlands", "NL")
2. ID name (e.g., "BSN — Burger Service Nummer")
3. Digit/character length and format
4. Checksum algorithm (if any): Luhn, Verhoeff, MOD 11, custom, none
5. Which regulation(s) require this ID to be masked? (e.g., GDPR, PIPEDA)

Then generate:

## 1. `packages/api/septum_api/services/national_ids/{country_code_lower}.py`

```python
"""
{COUNTRY} {ID_NAME} validator for Septum.
Implements format validation and checksum verification.
"""
from __future__ import annotations
import re
from app.services.national_ids.validator_base import BaseIDValidator


class {CountryCode}{IDName}Validator(BaseIDValidator):
    """
    Validates {COUNTRY} {ID_NAME} numbers.
    Format: {format_description}
    Checksum: {checksum_description}
    Reference: {official_reference_url}
    """

    regex: str = r"{regex_pattern}"  # pre-filter pattern

    def validate(self, value: str) -> bool:
        """
        Returns True only if the value passes both format and checksum validation.
        Never raises — returns False on any unexpected input.
        """
        clean = re.sub(r"[\s\-]", "", value)
        if not re.fullmatch(self.regex, clean):
            return False
        return self._checksum(clean)

    def _checksum(self, clean: str) -> bool:
        """
        Implements {checksum_description}.
        TODO: fill in algorithm.
        """
        # Example for MOD 11:
        # digits = [int(c) for c in clean]
        # total = sum(d * w for d, w in zip(digits, weights))
        # return total % 11 == 0
        raise NotImplementedError("Implement checksum algorithm")
```

## 2. `packages/api/septum_api/services/recognizers/{regulation}/recognizers.py` — add recognizer

```python
from presidio_analyzer import PatternRecognizer, Pattern
from app.services.national_ids.{country_code_lower} import {CountryCode}{IDName}Validator

_validator = {CountryCode}{IDName}Validator()

{country_code_upper}_{ID_NAME_UPPER}_RECOGNIZER = PatternRecognizer(
    supported_entity="NATIONAL_ID",
    name="{COUNTRY}_{ID_NAME}_Recognizer",
    patterns=[
        Pattern(
            name="{COUNTRY}_{ID_NAME}_pattern",
            regex=_validator.regex,
            score=0.5,  # boosted to 0.85 after checksum passes
        )
    ],
    context=["{context_word_1}", "{context_word_2}"],
    # Post-filter: only accept if checksum passes
    validation_func=lambda x: _validator.validate(x),
)
```

## 3. Register validator in `national_ids/__init__.py`

```python
from .{country_code_lower} import {CountryCode}{IDName}Validator
```

## 4. `backend/tests/test_national_ids.py` — add test cases

```python
class Test{CountryCode}{IDName}Validator:
    validator = {CountryCode}{IDName}Validator()

    def test_valid_id(self):
        """Known valid {ID_NAME} must pass."""
        assert self.validator.validate("{valid_example}") is True

    def test_invalid_checksum(self):
        """Digit-flipped ID must fail checksum."""
        assert self.validator.validate("{invalid_example}") is False

    def test_wrong_length(self):
        """Wrong length must fail pre-filter regex."""
        assert self.validator.validate("123") is False

    def test_with_spaces(self):
        """Common formatted version (with spaces) must still validate."""
        assert self.validator.validate("{formatted_example}") is True
```

## 5. Update regulation seed data

In `database.py` startup seed, add the new entity type to relevant regulation(s):
```python
# In {regulation_id} entity list:
"NATIONAL_ID",  # or more specific type if needed
```

After generating, summarize what was created and remind the user to:
- Fill in the checksum algorithm
- Add a real valid/invalid example for tests
- Confirm which regulation pack this belongs to
