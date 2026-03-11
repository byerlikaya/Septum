from __future__ import annotations

"""Septum backend package initialization.

Loads environment variables from the project-level `.env` file so that
settings such as LLM provider API keys are picked up automatically in
development and remain consistent across services.
"""

from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(PROJECT_ROOT / ".env")


