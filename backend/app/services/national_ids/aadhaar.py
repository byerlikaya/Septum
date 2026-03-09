from __future__ import annotations

from typing import Final, List

from .validator_base import BaseIDValidator


class AadhaarValidator(BaseIDValidator):
    """Validator for Indian Aadhaar numbers using the Verhoeff checksum algorithm."""

    name: Final[str] = "AADHAAR"
    # Aadhaar numbers are 12 digits after normalization.
    # Pattern is defined implicitly via algorithmic checks.
    # We reuse BaseIDValidator's normalization but override pattern logic via algorithm.

    # Verhoeff tables (multiplication and permutation).
    _D_TABLE: Final[List[List[int]]] = [
        [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
        [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
        [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
        [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
        [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
        [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
        [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
        [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
        [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
        [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
    ]

    _P_TABLE: Final[List[List[int]]] = [
        [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
        [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
        [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
        [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
        [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
        [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
        [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
        [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
    ]

    def __init__(self) -> None:
        # Aadhaar does not use a simple regex pattern; algorithmic validation
        # is performed directly on the normalized digits.
        self.pattern = None  # type: ignore[assignment]
        super().__init__()

    def _validate_algorithmically(self, normalized: str) -> bool:
        """Validate Aadhaar number using the Verhoeff checksum (digit length = 12)."""
        if len(normalized) != 12 or not normalized.isdigit():
            return False

        return self._verhoeff_checksum(normalized) == 0

    def validate(self, value: str) -> bool:  # type: ignore[override]
        """Override to bypass regex and rely purely on Verhoeff and length checks."""
        normalized = self._normalize(value)
        return self._validate_algorithmically(normalized)

    def _verhoeff_checksum(self, num: str) -> int:
        """Compute Verhoeff checksum value for the given numeric string."""
        c = 0
        for i, ch in enumerate(reversed(num)):
            digit = int(ch)
            c = self._D_TABLE[c][self._P_TABLE[i % 8][digit]]
        return c

    def compute_check_digit(self, partial: str) -> str:
        """Compute the Verhoeff check digit for a partial Aadhaar (11 digits)."""
        if len(partial) != 11 or not partial.isdigit():
            raise ValueError("Aadhaar base must be 11 digits.")

        # Brute-force search for a digit that makes the full number checksum zero.
        for candidate in range(10):
            full = f"{partial}{candidate}"
            if self._verhoeff_checksum(full) == 0:
                return str(candidate)

        raise ValueError("No valid Verhoeff check digit found for Aadhaar base.")

