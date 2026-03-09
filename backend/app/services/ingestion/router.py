from __future__ import annotations

"""Routing layer for document ingestion.

The :class:`IngestionRouter` selects an appropriate ingester implementation
based on the detected MIME type and normalized file format of an uploaded
document. It provides a simple, async interface that higher-level services
can depend on without needing to know about individual ingester classes.
"""

from pathlib import Path
from typing import Dict, Mapping, Optional, Type

from .base import BaseIngester, IngestionResult


class IngestionRouter:
    """Registry and dispatcher for document ingesters.

    The router maintains an internal mapping from normalized file formats
    (e.g., ``"pdf"``, ``"docx"``, ``"xlsx"``) to concrete ingester classes.
    Resolution is performed primarily on ``file_format`` with the option to
    extend lookups by MIME type if needed in the future.
    """

    def __init__(
        self,
        ingesters: Optional[Mapping[str, Type[BaseIngester]]] = None,
    ) -> None:
        """Initialize the router with an optional ingester mapping.

        Args:
            ingesters: Optional mapping from normalized file format to
                ingester class. If omitted, an empty registry is created and
                ingesters must be registered programmatically.
        """

        self._registry: Dict[str, Type[BaseIngester]] = {
            k.lower(): v for k, v in (ingesters or {}).items()
        }

    def register(self, file_format: str, ingester_cls: Type[BaseIngester]) -> None:
        """Register or replace the ingester class for ``file_format``."""

        self._registry[file_format.lower()] = ingester_cls

    def get_ingester(self, file_format: str) -> Optional[Type[BaseIngester]]:
        """Return the ingester class for the given format, if any."""

        return self._registry.get(file_format.lower())

    async def ingest(
        self,
        file_path: Path,
        *,
        mime_type: str,
        file_format: str,
    ) -> IngestionResult:
        """Dispatch ingestion to the appropriate ingester.

        Args:
            file_path: Absolute path to the encrypted file on disk.
            mime_type: MIME type detected by python-magic.
            file_format: Normalized file format identifier, used as the
                primary lookup key for selecting an ingester.

        Raises:
            ValueError: If no ingester is registered for the given format.
        """

        ingester_cls = self.get_ingester(file_format)
        if ingester_cls is None:
            raise ValueError(f"No ingester registered for format '{file_format}'.")

        ingester = ingester_cls()
        return await ingester.ingest(file_path=file_path, mime_type=mime_type, file_format=file_format)

