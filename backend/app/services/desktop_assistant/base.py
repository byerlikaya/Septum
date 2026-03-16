from __future__ import annotations

"""Abstractions for sending messages to local desktop assistant applications.

This layer is intentionally generic and does not know anything about
Septum's privacy or RAG pipeline. It only knows how to route a text
message to a selected desktop assistant client on the same machine.
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Final


class DesktopAssistantTarget(str, Enum):
    """Supported desktop assistant targets."""

    CHATGPT = "chatgpt"
    CLAUDE = "claude"


class DesktopAssistantError(Exception):
    """Base error for desktop assistant automation failures."""


class DesktopAssistantWindowNotFoundError(DesktopAssistantError):
    """Raised when the target assistant window or application cannot be focused."""


class DesktopAssistant(ABC):
    """Abstract interface for platform-specific desktop assistant automation."""

    SUPPORTED_TARGETS: Final[tuple[DesktopAssistantTarget, ...]] = (
        DesktopAssistantTarget.CHATGPT,
        DesktopAssistantTarget.CLAUDE,
    )

    @abstractmethod
    def send_message(
        self,
        text: str,
        target: DesktopAssistantTarget,
        open_new_chat: bool,
    ) -> None:
        """Send the given text to the selected desktop assistant client.

        Implementations must:
        - Avoid logging the raw ``text`` argument anywhere.
        - Use only local OS automation (window focus, clipboard, keystrokes).
        - Raise :class:`DesktopAssistantError` on any failure.
        """

