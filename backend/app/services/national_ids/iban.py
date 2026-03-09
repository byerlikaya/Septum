from __future__ import annotations

import re
from typing import Final, Pattern

from .validator_base import BaseIDValidator


class IBANValidator(BaseIDValidator):
    """Validator for International Bank Account Numbers (IBAN)."""

    name: Final[str] = "IBAN"
    # Normalized form keeps letters and digits, strips spaces and separators.
    # Length: 15 to 34 characters, two-letter country code followed by alphanumerics.
    pattern: Final[Pattern[str]] = re.compile(r"^[A-Za-z]{2}[0-9A-Za-z]{13,32}$")

    def _validate_algorithmically(self, normalized: str) -> bool:
        """Validate IBAN using ISO 7064 MOD 97-10 checksum."""
        # Basic structural constraints.
        if not (15 <= len(normalized) <= 34):
            return False

        iban = normalized.replace(" ", "").upper()

        # Move the first four characters to the end.
        rearranged = iban[4:] + iban[:4]

        # Convert letters to numbers (A=10, B=11, ..., Z=35).
        numeric_str = []
        for ch in rearranged:
            if ch.isdigit():
                numeric_str.append(ch)
            elif "A" <= ch <= "Z":
                numeric_str.append(str(ord(ch) - 55))
            else:
                # Invalid character for IBAN.
                return False

        number_str = "".join(numeric_str)

        # Compute mod 97 iteratively to avoid large integers.
        remainder = 0
        for ch in number_str:
            remainder = (remainder * 10 + int(ch)) % 97

        return remainder == 1

