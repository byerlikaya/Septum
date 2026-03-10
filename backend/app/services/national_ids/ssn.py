from __future__ import annotations

import re
from typing import Final, Pattern

from .validator_base import BaseIDValidator


class SSNValidator(BaseIDValidator):
    """Validator for United States Social Security Numbers (SSN)."""

    name: Final[str] = "SSN"
    pattern: Final[Pattern[str]] = re.compile(r"^\d{9}$")

    def _validate_algorithmically(self, normalized: str) -> bool:
        """Validate SSN using structural and blacklist rules (no checksum exists)."""
        if len(normalized) != 9:
            return False

        area = int(normalized[0:3])
        group = int(normalized[3:5])
        serial = int(normalized[5:9])

        if area == 0 or area == 666 or 900 <= area <= 999:
            return False

        if group == 0 or serial == 0:
            return False

        return True

