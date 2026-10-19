"""The Presidio spaCy model is never downloaded at runtime."""

from __future__ import annotations

import pytest
import spacy.util
from septum_core import detector
from septum_core.model_integrity import ModelIntegrityError


def test_missing_spacy_model_package_is_refused_not_downloaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import presidio_analyzer.nlp_engine as nlp_engine

    monkeypatch.setattr(detector, "_cached_nlp_engine", None)
    monkeypatch.setattr(spacy.util, "is_package", lambda name: False)
    monkeypatch.setattr(
        nlp_engine.NlpEngineProvider,
        "create_engine",
        lambda self: pytest.fail("engine must not be created"),
    )

    with pytest.raises(ModelIntegrityError) as exc:
        detector._get_shared_nlp_engine()

    assert exc.value.model_id == "en_core_web_sm"
    assert detector._cached_nlp_engine is None
