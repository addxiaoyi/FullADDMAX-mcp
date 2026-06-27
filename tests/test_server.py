"""Tests for the FastMCP server tool surface (mega-tool refactor).

The server now exposes 4 mega tools (``agent`` / ``knowledge`` / ``config`` /
``admin``) instead of 28 individual MCP tools.  The 28 underlying business
functions are still importable from :mod:`fulladdmax_mcp.server` (they
are re-exported from :mod:`fulladdmax_mcp.server_internal`) so
white-box tests can keep calling them directly.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from fulladdmax_mcp.server import mcp


# ---------------------------------------------------------------------------
# Tool surface
# ---------------------------------------------------------------------------


def test_only_4_mega_tools_registered():
    """The MCP server should expose exactly 4 mega tools."""
    manager = getattr(mcp, "_tool_manager", None)
    assert manager is not None, "FastMCP has no _tool_manager attribute"
    names = set(manager._tools.keys())
    assert names == {"admin", "agent", "config", "knowledge"}, (
        f"Expected 4 mega tools, got: {names}"
    )


def test_mega_tool_signatures_have_operation_params_session_id():
    """Every mega tool must accept (operation, params_json, session_id)."""
    expected = {"operation", "params_json", "session_id"}
    for name in ("admin", "agent", "config", "knowledge"):
        tool = mcp._tool_manager._tools[name]
        sig = inspect.signature(tool.fn)
        assert expected.issubset(set(sig.parameters)), (
            f"{name} signature missing required params; got: {list(sig.parameters)}"
        )


# ---------------------------------------------------------------------------
# Backward-compat: 28 functions are still importable + callable
# ---------------------------------------------------------------------------


def test_ping_reports_version_and_config():
    from fulladdmax_mcp.server import ping
    out = ping()
    assert "FullADDMAX-mcp v" in out
    assert "model" in out
    # API key is masked
    assert "sk-t****" in out or "(unset)" in out


def test_configure_then_ping():
    from fulladdmax_mcp.server import configure_llm, ping
    msg = configure_llm(
        base_url="https://example.com/v1",
        api_key="sk-abc12345",
        model="m-1",
    )
    assert "Configured" in msg
    p = ping()
    assert "m-1" in p
    assert "sk-a****" in p


# ---------------------------------------------------------------------------
# Mega tool calls (in-process, via the registered .fn coroutine)
# ---------------------------------------------------------------------------


async def _call(name: str, **kwargs):
    tool = mcp._tool_manager._tools[name]
    return await tool.fn(**kwargs)


async def test_admin_mega_tool_ping():
    out = await _call("admin", operation="ping", params_json="", session_id="")
    assert "FullADDMAX-mcp v" in out
    assert "model" in out


async def test_admin_mega_tool_unknown_operation():
    out = await _call("admin", operation="nope", params_json="", session_id="")
    assert out.startswith("ERROR: bad_op:")
    assert "available" in out


async def test_config_mega_tool_configure_llm():
    out = await _call(
        "config",
        operation="configure_llm",
        params_json=(
            '{"base_url":"https://example.com/v1",'
            '"api_key":"sk-abc12345","model":"m-1"}'
        ),
        session_id="",
    )
    assert "Configured" in out
    assert "m-1" in out


async def test_agent_mega_tool_unknown_operation_lists_available():
    out = await _call("agent", operation="bad", params_json="", session_id="")
    assert "ERROR: bad_op:" in out
    assert "orchestrator_run" in out  # one of the available ops
    assert "swarm_run" in out


async def test_knowledge_mega_tool_bad_json():
    out = await _call(
        "knowledge", operation="obsidian_list_notes",
        params_json="{not json", session_id="",
    )
    assert "ERROR: bad_json:" in out
