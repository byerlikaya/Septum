from __future__ import annotations

"""Configuration helpers for desktop assistant integration.

This module centralises how application and window identifiers are
resolved for desktop assistant automation. It combines environment
variables with persisted application settings when available.
"""

from dataclasses import dataclass
import os
from typing import Optional

from ...models.settings import AppSettings
from .base import DesktopAssistantTarget


@dataclass
class DesktopAssistantConfig:
    """Resolved configuration for desktop assistant automation."""

    chatgpt_app_name: str
    chatgpt_window_title: str
    claude_app_name: str
    claude_window_title: str
    chatgpt_new_chat_shortcut: str
    claude_new_chat_shortcut: str
    chatgpt_new_chat_default: bool


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None:
        return default
    stripped = value.strip()
    return stripped or default


def load_desktop_assistant_config(settings: Optional[AppSettings] = None) -> DesktopAssistantConfig:
    """Build :class:`DesktopAssistantConfig` from environment and AppSettings.

    Environment variables provide the primary mapping to desktop apps so that
    users can adapt to different client names or window titles:

    - ``CHATGPT_APP_NAME`` (default: ``ChatGPT``)
    - ``CHATGPT_WINDOW_TITLE`` (default: ``ChatGPT``)
    - ``CLAUDE_APP_NAME`` (default: ``Claude``)
    - ``CLAUDE_WINDOW_TITLE`` (default: ``Claude``)
    - ``CHATGPT_NEW_CHAT_SHORTCUT`` (default: ``cmd+n`` on macOS)
    - ``CLAUDE_NEW_CHAT_SHORTCUT`` (default: empty string)

    The persisted settings row can additionally express whether a new chat
    should be opened by default when targeting ChatGPT.
    """

    chatgpt_app_name = _env("CHATGPT_APP_NAME", "ChatGPT")
    chatgpt_window_title = _env("CHATGPT_WINDOW_TITLE", "ChatGPT")
    claude_app_name = _env("CLAUDE_APP_NAME", "Claude")
    claude_window_title = _env("CLAUDE_WINDOW_TITLE", "Claude")
    chatgpt_new_chat_shortcut = _env("CHATGPT_NEW_CHAT_SHORTCUT", "cmd+n")
    claude_new_chat_shortcut = _env("CLAUDE_NEW_CHAT_SHORTCUT", "")

    default_new_chat = False
    if settings is not None:
        default_new_chat = bool(
            getattr(settings, "desktop_assistant_chatgpt_new_chat_default", False)
        )

    return DesktopAssistantConfig(
        chatgpt_app_name=chatgpt_app_name,
        chatgpt_window_title=chatgpt_window_title,
        claude_app_name=claude_app_name,
        claude_window_title=claude_window_title,
        chatgpt_new_chat_shortcut=chatgpt_new_chat_shortcut,
        claude_new_chat_shortcut=claude_new_chat_shortcut,
        chatgpt_new_chat_default=default_new_chat,
    )


def target_display_name(target: DesktopAssistantTarget) -> str:
    """Return a human-readable label for a target without localisation.

    This string is intended for logs and error messages only. User-facing
    text must be provided from the frontend i18n layer.
    """

    if target is DesktopAssistantTarget.CHATGPT:
        return "ChatGPT"
    if target is DesktopAssistantTarget.CLAUDE:
        return "Claude"
    return target.value

