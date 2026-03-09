from __future__ import annotations

"""Base abstractions for document ingestion in Septum.

This module defines the core interfaces and data structures used by all
ingesters. Each concrete ingester is responsible for:

- Decrypting and reading the raw file bytes from disk.
- Extracting plain text (and lightweight structural metadata such as page
  numbers or sheet indices) in an asynchronous, non-blocking fashion.
- Returning an ``IngestionResult`` that the higher-level document processor
  can pass through the sanitization and chunking pipeline.

All ingesters must be fully type-hinted and async, and they MUST NOT log or
otherwise expose any raw PII. Only non-sensitive metadata (sizes, counts,
formats) may be logged by callers.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Protocol


@dataclass(slots=True)
class IngestionResult:
    """Represents the outcome of a single document ingestion operation.

    Attributes:
        text: The raw extracted textual content of the document after basic
            normalization, but before any PII sanitization. This value MUST
            only be kept in-memory and must never be logged or persisted
            in plaintext beyond the controlled pipeline.
        metadata: Lightweight, non-PII metadata describing the document,
            such as page counts, slide counts, or sheet names. This MUST
            NOT contain any raw personal data; clients are responsible for
            enforcing this constraint.
    """

    text: str
    metadata: Dict[str, Any]


class BaseIngester(Protocol):
    """Protocol that all document ingesters must implement.

    Ingester implementations are responsible for a specific class of file
    formats (e.g., PDFs, Word documents, spreadsheets) and are looked up by
    the :class:`IngestionRouter` based on MIME type and/or file format.
    """

    async def ingest(
        self,
        file_path: Path,
        *,
        mime_type: str,
        file_format: str,
    ) -> IngestionResult:
        """Ingest the document at ``file_path`` and return extracted content.

        Args:
            file_path: Absolute path to the encrypted file on disk. Ingester
                implementations are responsible for opening and decrypting
                the file using the shared crypto utilities.
            mime_type: The MIME type of the file as detected by python-magic.
            file_format: A normalized, lower-case file format identifier
                (e.g., ``"pdf"``, ``"docx"``, ``"xlsx"``).

        Returns:
            An :class:`IngestionResult` containing the extracted text and
            safe metadata describing the structure of the document.
        """

        ...

