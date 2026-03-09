from __future__ import annotations

import re
from typing import Final, List, Pattern

from .validator_base import BaseIDValidator


class TCKNValidator(BaseIDValidator):
    """Validator for Turkish national identity numbers (TCKN)."""

    name: Final[str] = "TCKN"
    pattern: Final[Pattern[str]] = re.compile(r"^[1-9]\d{10}$")

    def _validate_algorithmically(self, normalized: str) -> bool:
        """Validate TCKN using checksum rules defined by the Turkish ID standard."""
        if len(normalized) != 11:
            return False

        digits: List[int] = [int(ch) for ch in normalized]

        # Basic structural rule: last two digits are checksums.
        d1, d2, d3, d4, d5, d6, d7, d8, d9, d10, d11 = digits

        odd_sum = d1 + d3 + d5 + d7 + d9
        even_sum = d2 + d4 + d6 + d8

        calculated_d10 = ((odd_sum * 7) - even_sum) % 10
        if d10 != calculated_d10:
            return False

        calculated_d11 = sum(digits[:10]) % 10
        if d11 != calculated_d11:
            return False

        return True

