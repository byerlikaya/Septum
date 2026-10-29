from __future__ import annotations

import logging
import re

import pytest

from septum_core.recognizers.base_recognizer import RegexPatternConfig, ValidatedPatternRecognizer

_USER_PATTERN = "(?P<ACME_SECRET>a)(?P=ACME_SECRET_TOKEN)"


def test_invalid_pattern_log_omits_pattern_text(caplog: pytest.LogCaptureFixture) -> None:
    config = RegexPatternConfig(name="custom_bad", pattern=_USER_PATTERN)

    with caplog.at_level(logging.ERROR), pytest.raises(re.error):
        ValidatedPatternRecognizer(entity_type="CUSTOM", config=config)

    error_records = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert error_records
    assert "ACME_SECRET_TOKEN" not in caplog.text
