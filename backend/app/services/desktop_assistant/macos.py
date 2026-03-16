from __future__ import annotations

"""macOS implementation of desktop assistant automation."""

import platform
import shlex
import subprocess
import time
from typing import Final

import pyautogui  # type: ignore[import]
import pyperclip  # type: ignore[import]

from .base import (
    DesktopAssistant,
    DesktopAssistantError,
    DesktopAssistantTarget,
    DesktopAssistantWindowNotFoundError,
)
from .config import DesktopAssistantConfig


_APPLE_SCRIPT_TIMEOUT_SECONDS: Final[int] = 10
_FOCUS_POLL_INTERVAL_SECONDS: Final[float] = 0.25


class MacOSDesktopAssistant(DesktopAssistant):
    """Desktop assistant automation for macOS using AppleScript and pyautogui."""

    def __init__(self, config: DesktopAssistantConfig) -> None:
        if platform.system().lower() != "darwin":
            raise DesktopAssistantError("MacOSDesktopAssistant can only be used on macOS.")
        self._config = config

    def send_message(
        self,
        text: str,
        target: DesktopAssistantTarget,
        open_new_chat: bool,
    ) -> None:
        # Clipboard operations must not be logged; the text is never written to logs.
        pyperclip.copy(text)

        app_name = self._resolve_app_name(target)
        self._activate_app(app_name)

        if target is DesktopAssistantTarget.CHATGPT:
            should_open_new_chat = open_new_chat or self._config.chatgpt_new_chat_default
            if should_open_new_chat and self._config.chatgpt_new_chat_shortcut:
                self._send_shortcut(self._config.chatgpt_new_chat_shortcut)

        # Paste and send with standard shortcuts.
        if platform.system().lower() == "darwin":
            pyautogui.hotkey("command", "v")
            pyautogui.press("enter")
        else:  # pragma: no cover - defensive, should not happen in this class
            pyautogui.hotkey("ctrl", "v")
            pyautogui.press("enter")

    def _resolve_app_name(self, target: DesktopAssistantTarget) -> str:
        if target is DesktopAssistantTarget.CHATGPT:
            return self._config.chatgpt_app_name
        if target is DesktopAssistantTarget.CLAUDE:
            return self._config.claude_app_name
        raise DesktopAssistantError(f"Unsupported desktop assistant target: {target.value}")

    def _activate_app(self, app_name: str) -> None:
        script = f'tell application "{app_name}" to activate'
        try:
            subprocess.run(
                ["osascript", "-e", script],
                check=True,
                capture_output=True,
                text=True,
                timeout=_APPLE_SCRIPT_TIMEOUT_SECONDS,
            )
        except subprocess.SubprocessError as exc:  # noqa: BLE001
            raise DesktopAssistantWindowNotFoundError(
                f"Unable to activate desktop assistant application {shlex.quote(app_name)}."
            ) from exc

        if not self._wait_for_frontmost(app_name):
            raise DesktopAssistantWindowNotFoundError(
                f"Desktop assistant application {shlex.quote(app_name)} did not become frontmost."
            )

    def _wait_for_frontmost(self, app_name: str) -> bool:
        """Wait until the given application becomes the frontmost process."""

        script = (
            'tell application "System Events" to get name of first process whose frontmost is true'
        )
        deadline = time.time() + _APPLE_SCRIPT_TIMEOUT_SECONDS
        while time.time() < deadline:
            try:
                completed = subprocess.run(
                    ["osascript", "-e", script],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=_APPLE_SCRIPT_TIMEOUT_SECONDS,
                )
                frontmost = (completed.stdout or "").strip()
                if frontmost == app_name:
                    return True
            except subprocess.SubprocessError:
                # Best-effort; treat as not yet focused and retry until timeout.
                pass
            time.sleep(_FOCUS_POLL_INTERVAL_SECONDS)
        return False

    def _send_shortcut(self, shortcut: str) -> None:
        """Send a keyboard shortcut expressed as a simple chord string."""

        tokens = [token.strip().lower() for token in shortcut.split("+") if token.strip()]
        if not tokens:
            return
        modifiers = [t for t in tokens[:-1] if t]
        key = tokens[-1]
        keys: list[str] = []
        for mod in modifiers:
            if mod in {"cmd", "command"}:
                keys.append("command")
            elif mod in {"ctrl", "control"}:
                keys.append("ctrl")
            elif mod in {"alt", "option"}:
                keys.append("option")
            elif mod in {"shift"}:
                keys.append("shift")
        keys.append(key)
        pyautogui.hotkey(*keys)

