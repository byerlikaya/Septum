---
paths:
  - "backend/**/*.py"
---

# Smart Test Runner

When running tests after a change, target the relevant test file:

- `sanitizer.py` → `test_sanitizer.py`
- `anonymization_map.py` → `test_anonymization_map.py`
- `national_ids/` → `test_national_ids.py`
- `ingestion/` → `test_ingesters.py`
- `policy_composer.py` → `test_policy_composer.py`
- `crypto.py` → `test_crypto.py`
- `llm_router.py` → `test_llm_router.py`
- `deanonymizer.py` → `test_deanonymizer.py`
- `vector_store.py` → `test_vector_store.py`
- `document_pipeline.py` → `test_document_pipeline.py`
- `document_anon_store.py` → `test_document_anon_store.py`
- `non_pii_filter.py` → `test_non_pii_filter.py`
- `routers/chat.py` → `test_chat_sanitization.py`, `test_chat_context_prompt.py`
- `routers/approval.py` → `test_approval_router.py`
- `prompts.py` → `test_chat_context_prompt.py`
- No specific match → run full suite

All LLM calls in tests must be mocked — never send real requests to cloud LLMs.
