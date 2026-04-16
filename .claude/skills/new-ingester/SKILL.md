---
name: new-ingester
description: Creates a new document format ingester for Septum. Use when adding support for a new file type. Invoke with /new-ingester.
---

# New Ingester Skill

When this skill is invoked, ask the user:
1. What file format? (e.g., .odt, .pages, .numbers)
2. What MIME type(s) does it have?
3. What Python library will extract text from it?

Then generate the following files:

## 1. `packages/api/septum_api/services/ingestion/{format}_ingester.py`

```python
"""
{FORMAT} Ingester for Septum.
Extracts plain text from {format} files for the sanitization pipeline.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

from app.services.ingestion.base import BaseIngester, IngestionResult
from app.utils.text_utils import normalize_unicode
from app.utils.logger import get_logger

logger = get_logger(__name__)
_executor = ThreadPoolExecutor(max_workers=2)


class {Format}Ingester(BaseIngester):
    """
    Ingester for {format} files.
    Runs extraction in a ThreadPoolExecutor to avoid blocking the event loop.
    """

    def _extract_sync(self, file_bytes: bytes, filename: str) -> IngestionResult:
        """
        Synchronous extraction — runs inside ThreadPoolExecutor.
        Replace the body with the actual library calls.
        """
        try:
            # TODO: implement extraction using {library}
            raw_text = ""  # replace with actual extraction

            text = normalize_unicode(raw_text)
            return IngestionResult(
                text=text,
                metadata={"filename": filename, "format": "{format}"},
                confidence=None,
                raw_segments=None,
                warnings=[],
            )
        except Exception as exc:
            logger.error({"event": "ingestion_error", "format": "{format}",
                          "filename": filename, "error": str(exc)})
            raise

    async def extract(self, file_bytes: bytes, filename: str) -> IngestionResult:
        """Async entry point — delegates to sync extraction in thread pool."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            _executor, self._extract_sync, file_bytes, filename
        )
```

## 2. Register in `packages/api/septum_api/services/ingestion/router.py`

Add the new MIME type → ingester mapping:
```python
"{mime_type}": {Format}Ingester,
```

## 3. `backend/tests/test_ingesters.py` — add test case

```python
def test_{format}_ingester_basic():
    """Basic extraction test for {format} files."""
    ingester = {Format}Ingester()
    # Load a small fixture file
    fixture = Path("tests/fixtures/sample.{format}").read_bytes()
    result = asyncio.run(ingester.extract(fixture, "sample.{format}"))
    assert result.text.strip() != ""
    assert result.metadata["format"] == "{format}"
    assert "error" not in result.warnings
```

## 4. Add fixture file

Place a small sample file at: `backend/tests/fixtures/sample.{format}`

## 5. Update supported types table in README

Add a row to the supported document types table.

After generating all files, summarize:
- File created
- MIME types registered
- Test added
- Any manual steps needed (e.g., pip install for the new library)
