from __future__ import annotations

import asyncio
import logging
from typing import Any, Mapping

import httpx

from ..llm_errors import LLMRouterError

logger = logging.getLogger(__name__)


async def post_with_retries(
    url: str,
    headers: Mapping[str, str],
    json: Mapping[str, Any],
    max_attempts: int = 3,
    base_backoff_seconds: float = 0.5,
) -> httpx.Response:
    """Send an HTTP POST request with simple exponential backoff."""
    attempt = 0
    last_error: Exception | None = None

    while attempt < max_attempts:
        attempt += 1
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, headers=dict(headers), json=dict(json))
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            last_error = exc
            status_code = exc.response.status_code
            try:
                body_len = len(exc.response.text)
            except Exception:  # noqa: BLE001
                body_len = -1

            logger.error(
                "LLM HTTP error while calling provider: url=%s status=%s body_len=%s",
                url,
                status_code,
                body_len,
            )

            if attempt >= max_attempts:
                break
            backoff = base_backoff_seconds * (2 ** (attempt - 1))
            await asyncio.sleep(backoff)
        except httpx.HTTPError as exc:
            last_error = exc
            logger.error("LLM transport error while calling provider: url=%s error=%s", url, exc)
            if attempt >= max_attempts:
                break
            backoff = base_backoff_seconds * (2 ** (attempt - 1))
            await asyncio.sleep(backoff)

    if isinstance(last_error, httpx.HTTPStatusError):
        status_code = last_error.response.status_code
        raise LLMRouterError(
            f"LLM provider request failed after {max_attempts} attempts (status={status_code})."
        ) from last_error

    raise LLMRouterError(f"LLM provider request failed after {max_attempts} attempts.") from last_error

