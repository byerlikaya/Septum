"""Structured JSON logging configuration for Septum."""

import logging
import sys

from pythonjsonlogger.json import JsonFormatter


def setup_structured_logging(level: str = "INFO") -> None:
    """Configure root logger with JSON formatter for structured output.

    Call once at application startup before any logging statements.
    """
    formatter = JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        rename_fields={"asctime": "timestamp", "levelname": "level", "name": "logger"},
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Keep noisy libraries quiet
    for name in (
        "presidio_analyzer.recognizer_registry",
        "paddlex",
        "paddleocr",
        "httpx",
        "httpcore",
        "uvicorn.access",
    ):
        logging.getLogger(name).setLevel(logging.ERROR)
