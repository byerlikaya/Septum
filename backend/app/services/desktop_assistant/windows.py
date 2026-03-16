from __future__ import annotations

"""Windows implementation of desktop assistant automation."""

import platform
import time
from typing import Final

import pyautogui  # type: ignore[import]
import pygetwindow as gw  # type: ignore[import]
import pyperclip  # type: ignore[import]

from .base import (
    DesktopAssistant,
    DesktopAssistantError,
    DesktopAssistantTarget,
    DesktopAssistantWindowNotFoundError,
)
from .config import DesktopAssistantConfig


_WINDOW_ACTIVATION_SLEEP_SECONDS: Final[float] = 0.5


class WindowsDesktopAssistant(DesktopAssistant):
    """Desktop assistant automation for Windows using pygetwindow and pyautogui."""

    def __init__(self, config: DesktopAssistantConfig) -> None:
        if platform.system().lower() != "windows":
            raise DesktopAssistantError("WindowsDesktopAssistant can only be used on Windows.")
        self._config = config

    def send_message(
        self,
        text: str,
        target: DesktopAssistantTarget,
        open_new_chat: bool,
    ) -> None:
        # Clipboard operations must not be logged; the text is never written to logs.
        pyperclip.copy(text)

        window_title = self._resolve_window_title(target)
        window = self._find_window(window_title)
        if window is None:
            raise DesktopAssistantWindowNotFoundError(
                f"Unable to find desktop assistant window with title containing: {window_title!r}"
            )
        window.activate()
        time.sleep(_WINDOW_ACTIVATION_SLEEP_SECONDS)

        # New-chat semantics for Windows are not standardised; for now we only
        # support a generic paste-and-send behaviour.
        pyautogui.hotkey("ctrl", "v")
        pyautogui.press("enter")

    def _resolve_window_title(self, target: DesktopAssistantTarget) -> str:
        if target is DesktopAssistantTarget.CHATGPT:
            return self._config.chatgpt_window_title
        if target is DesktopAssistantTarget.CLAUDE:
            return self._config.claude_window_title
        raise DesktopAssistantError(f"Unsupported desktop assistant target: {target.value}")

    def _find_window(self, title_substring: str):
        matches = gw.getWindowsWithTitle(title_substring)
        if not matches:
            return None
        return matches[0]

