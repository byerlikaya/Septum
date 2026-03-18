from __future__ import annotations

"""Factory for platform-specific desktop assistant implementations."""

import platform
from typing import Optional

from ...models.settings import AppSettings
from .base import DesktopAssistant
from .config import DesktopAssistantConfig, load_desktop_assistant_config


def create_desktop_assistant(settings: Optional[AppSettings] = None) -> DesktopAssistant:
    """Create a platform-specific :class:`DesktopAssistant` instance.

    The instance is configured using environment variables plus the provided
    :class:`AppSettings` row when available. This keeps runtime behaviour
    flexible while persisting only high-level feature toggles in the database.
    Platform-specific modules (macOS: pyautogui/pyperclip, Windows: pygetwindow)
    are imported only when needed so CI and non-GUI environments can load the app.
    """

    config: DesktopAssistantConfig = load_desktop_assistant_config(settings)
    system = platform.system().lower()
    if system == "darwin":
        from .macos import MacOSDesktopAssistant
        return MacOSDesktopAssistant(config)
    if system == "windows":
        from .windows import WindowsDesktopAssistant
        return WindowsDesktopAssistant(config)
    raise RuntimeError(f"Desktop assistant mode is not supported on platform: {system}")

