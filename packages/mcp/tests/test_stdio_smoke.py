from __future__ import annotations

"""End-to-end stdio smoke test for the septum-mcp server.

Spawns the real server as a subprocess over stdio using the official
``mcp`` Python client SDK, walks through the full handshake, and
invokes one read-only tool (``list_regulations``) plus a round-trip
``mask_text`` / ``unmask_response`` pair. This is the only test in
the suite that exercises the actual JSON-RPC transport — everything
else tests the pure-Python tool implementations directly.
"""

import json
import sys

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

pytestmark = pytest.mark.asyncio


def _server_parameters(extra_env: dict[str, str] | None = None) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "septum_mcp.server"],
        env={
            "SEPTUM_REGULATIONS": "gdpr",
            "SEPTUM_USE_NER": "false",
            "PYTHONUNBUFFERED": "1",
            **(extra_env or {}),
        },
    )


def _extract_json_payload(result) -> dict:
    """Return the structured data block from a ``CallToolResult``.

    the SDK serialises structured tool output into a JSON text block
    inside ``content``. Tests that care about the actual payload
    parse that block so they can assert on individual fields.
    """
    if getattr(result, "structured_content", None):
        return dict(result.structured_content)
    for block in result.content:
        text = getattr(block, "text", None)
        if text:
            return json.loads(text)
    raise AssertionError(f"No JSON payload found in tool result: {result}")


async def test_stdio_default_tools_exclude_session_map() -> None:
    async with stdio_client(_server_parameters()) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            tools = await session.list_tools()

    names = {tool.name for tool in tools.tools}
    assert names == {
        "mask_text",
        "unmask_response",
        "detect_pii",
        "scan_file",
        "list_regulations",
    }
    assert "session_map" not in (init.instructions or "")


async def test_stdio_session_map_opt_in() -> None:
    params = _server_parameters({"SEPTUM_MCP_EXPOSE_SESSION_MAP": "true"})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()

    assert "get_session_map" in {tool.name for tool in tools.tools}


async def test_stdio_mask_and_unmask_round_trip() -> None:
    async with stdio_client(_server_parameters()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            mask_result = await session.call_tool(
                "mask_text",
                {"text": "Email jane@example.com today.", "language": "en"},
            )
            assert not mask_result.is_error
            mask_payload = _extract_json_payload(mask_result)
            assert "[EMAIL_ADDRESS_1]" in mask_payload["masked_text"]
            session_id = mask_payload["session_id"]
            assert session_id

            unmask_result = await session.call_tool(
                "unmask_response",
                {
                    "text": "Reply to [EMAIL_ADDRESS_1] was sent.",
                    "session_id": session_id,
                },
            )
            assert not unmask_result.is_error
            unmask_payload = _extract_json_payload(unmask_result)
            assert unmask_payload["text"] == "Reply to jane@example.com was sent."


async def test_stdio_list_regulations_reports_gdpr_active() -> None:
    async with stdio_client(_server_parameters()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("list_regulations", {})

    assert not result.is_error
    payload = _extract_json_payload(result)
    by_id = {pack["id"]: pack for pack in payload["regulations"]}
    assert by_id["gdpr"]["is_active"] is True
    assert by_id["kvkk"]["is_active"] is False


async def test_stdio_unknown_session_returns_tool_error() -> None:
    async with stdio_client(_server_parameters()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "unmask_response",
                {"text": "[EMAIL_ADDRESS_1]", "session_id": "missing"},
            )

    assert result.is_error
    joined_text = " ".join(
        getattr(block, "text", "") for block in result.content
    ).lower()
    assert "not found" in joined_text or "expired" in joined_text
