from __future__ import annotations

import re
from typing import Final, List, Pattern

from .validator_base import BaseIDValidator


class CPFValidator(BaseIDValidator):
    """Validator for Brazilian CPF (Cadastro de Pessoas Físicas) numbers."""

    name: Final[str] = "CPF"
    # Normalized form is 11 digits.
    pattern: Final[Pattern[str]] = re.compile(r"^\d{11}$")

    def _validate_algorithmically(self, normalized: str) -> bool:
        """Validate CPF using its two-step modulo-11 checksum."""
        if len(normalized) != 11:
            return False

        digits: List[int] = [int(ch) for ch in normalized]

        # Reject CPFs with all digits equal (e.g., 000..., 111...).
        if len(set(digits)) == 1:
            return False

        first_nine = digits[:9]
        d10_expected, d11_expected = self._compute_check_digits(first_nine)

        return digits[9] == d10_expected and digits[10] == d11_expected

    @staticmethod
    def _compute_check_digits(first_nine: List[int]) -> tuple[int, int]:
        """Compute CPF verifying digits from the first nine digits."""
        if len(first_nine) != 9:
            raise ValueError("CPF check digit computation requires exactly 9 digits.")

        # First check digit.
        sum1 = sum(d * weight for d, weight in zip(first_nine, range(10, 1, -1)))
        rem1 = (sum1 * 10) % 11
        d10 = 0 if rem1 == 10 else rem1

        # Second check digit.
        extended = first_nine + [d10]
        sum2 = sum(d * weight for d, weight in zip(extended, range(11, 1, -1)))
        rem2 = (sum2 * 10) % 11
        d11 = 0 if rem2 == 10 else rem2

        return d10, d11

