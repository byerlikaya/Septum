from __future__ import annotations

"""Septum backend package initialization.

Loads environment variables from a local `.env` file so that settings such as
LLM provider API keys are picked up automatically in development.
"""

from dotenv import load_dotenv


# Load variables from `.env` in the backend directory (and parents) if present.
load_dotenv()


