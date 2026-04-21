from __future__ import annotations

"""Shared fixtures for septum-mcp tests.

Every test exercises a real :class:`SeptumEngine` wired with the
``gdpr`` pack but with the NER transformer layer disabled, so the
test suite runs fully offline and does not pull a multi-hundred-MB
model on first execution.
"""

import pytest
from septum_core import SeptumCoreConfig, SeptumEngine
from septum_core.ports import NullSemanticDetectionPort


@pytest.fixture
def engine() -> SeptumEngine:
    config = SeptumCoreConfig(use_presidio_layer=True, use_ner_layer=False)
    return SeptumEngine(
        regulations=["gdpr"],
        config=config,
        semantic_port=NullSemanticDetectionPort(),
    )
