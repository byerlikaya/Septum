---
paths:
  - "packages/api/septum_api/**/*.py"
---

# Zero-Tolerance Generic Architecture

These patterns are **forbidden** in production code (tests and `national_ids/` are exceptions):

1. **Country/language names in code identifiers**
   - `TurkishPhoneRecognizer` → `ExtendedPhoneRecognizer`
   - `detect_english_text()` → `detect_text_language()`

2. **Hardcoded text patterns or term lists**
   - `["MADDE", "Article"]` → structural detection (numbering, capitalization)
   - Detect by format, not by vocabulary

3. **Language-specific if-branches**
   - `if language == "tr":` → `LOCALE_CASING_RULES.get(lang)`
   - Use mapping tables, not conditional branches

4. **Hardcoded stopwords**
   - Move to DB or keep minimal with `# FUTURE: move to DB`

**Allowed exceptions:** ISO 639-1 codes in mapping tables, HuggingFace model IDs, `national_ids/` algorithmic validators, regulation seed descriptions in `database.py`, test files.

## Before committing, verify:
- No country/language names in class/function/variable names
- No hardcoded term lists (use database)
- No `if language == "specific"` branches
- All language-specific data is in database, config, or user-definable
