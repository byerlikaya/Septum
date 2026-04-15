from __future__ import annotations

"""Configuration for the septum-core detection pipeline.

Decouples core detection logic from the backend's SQLAlchemy
``AppSettings`` model so that the air-gapped core package can be
used stand-alone without importing any FastAPI or database layers.
"""

from dataclasses import dataclass, field
from typing import Dict


@dataclass
class SeptumCoreConfig:
    """Runtime switches for the core PII detection pipeline.

    The detector inspects these flags (plus the attached
    :class:`SemanticDetectionPort`, when one is supplied) to decide
    which layers should run for a given sanitization pass.
    """

    use_presidio_layer: bool = True
    use_ner_layer: bool = True
    use_semantic_validation: bool = False
    use_semantic_detection: bool = False
    use_semantic_contextual_detection: bool = False
    ner_model_overrides: Dict[str, str] = field(default_factory=dict)
