"""Tests for the FastMCP server tool surface."""

from __future__ import annotations

from fulladdmax_mcp.server import mcp


def test_tools_registered():
    # FastMCP stores tools in an internal manager.
    manager = getattr(mcp, "_tool_manager", None) or getattr(mcp, "_tool_manager", None)
    assert manager is not None, "FastMCP has no _tool_manager attribute"
    names = set(manager._tools.keys())
    expected = {
        "ping",
        "configure_llm",
        "orchestrator_run",
        "parallel_agents_run",
        "map_reduce_run",
        "swarm_run",
    }
    assert expected.issubset(names), f"missing tools: {expected - names}"


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
