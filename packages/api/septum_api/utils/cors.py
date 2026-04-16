"""CORS configuration helpers."""

from __future__ import annotations


def resolve_cors_origins(value: str) -> list[str]:
    """Parse a ``frontend_origin`` config string into a CORS allow-list.

    A literal ``"*"`` (or an empty value) maps to the permissive
    wildcard. Everything else is treated as a comma-separated origin
    list with whitespace stripped, so split deployments can declare
    ``FRONTEND_ORIGIN=https://app.example.com,https://admin.example.com``
    and lock CORS down to those two origins.
    """
    if not value or value.strip() == "*":
        return ["*"]
    return [origin.strip() for origin in value.split(",") if origin.strip()]
