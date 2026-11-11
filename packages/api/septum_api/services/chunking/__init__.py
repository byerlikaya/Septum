"""Chunker v2: pure, block-aware chunking that respects protected ranges."""

from .records import ChunkRecord
from .strategies import Strategy, chunk_blocks

__all__ = ["ChunkRecord", "Strategy", "chunk_blocks"]
