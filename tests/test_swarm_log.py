"""Tests for the swarm_run entry-point log statement.

The log fires BEFORE the LLM is invoked, so we don't even need to mock
the HTTP layer for the "valid agents_json" cases — the log is asserted
to appear, and then the call is allowed to short-circuit (parse error)
or proceed with a mock.
"""

from __future__ import annotations

import json
import logging

import pytest

from fulladdmax_mcp import server, swarm
from fulladdmax_mcp.swarm import DEFAULT_AGENTS


@pytest.fixture(autouse=True)
def _reset_registry():
    swarm.registry.clear()
    for a in DEFAULT_AGENTS.values():
        swarm.registry.register(a, overwrite=True)
    yield
    swarm.registry.clear()
    for a in DEFAULT_AGENTS.values():
        swarm.registry.register(a, overwrite=True)


@pytest.fixture
def capture_log(caplog):
    caplog.set_level(logging.INFO, logger="fulladdmax-mcp")
    return caplog


def _has_log(caplog, needle: str) -> bool:
    return any(needle in rec.getMessage() for rec in caplog.records)


async def test_log_emitted_with_default_registry(capture_log, mock_chat, make_response):
    mock_chat.post("/v1/chat/completions").mock(
        return_value=make_response('{"next": "DONE", "message": "x"}')
    )

    await server.swarm_run("researcher", "x", max_handoffs=1)

    msgs = [r.getMessage() for r in capture_log.records if "swarm_run:" in r.getMessage()]
    assert len(msgs) == 1
    msg = msgs[0]
    # initial_agent and task present
    assert "initial_agent='researcher'" in msg
    assert "task='x'" in msg
    # empty agents_json -> explicit sentinel
    assert "agents_json=<empty -> use registry>" in msg
    # effective agents list is the seeded defaults, sorted
    assert "effective_agents=" in msg
    for name in ("coder", "critic", "researcher", "writer"):
        assert name in msg


async def test_log_includes_agents_json_content(
    capture_log, mock_chat, make_response
):
    mock_chat.post("/v1/chat/completions").mock(
        return_value=make_response('{"next": "DONE", "message": "x"}')
    )

    custom = json.dumps(
        [
            {"name": "analyst", "system": "s1"},
            {"name": "strategist", "system": "s2"},
        ]
    )
    await server.swarm_run("analyst", "y", agents_json=custom)

    msg = next(
        r.getMessage() for r in capture_log.records if "swarm_run:" in r.getMessage()
    )
    # The full JSON payload is logged via repr (so the user can see
    # exactly what came in over the wire).
    assert '"analyst"' in msg
    assert '"strategist"' in msg
    assert "effective_agents=['analyst', 'strategist']" in msg


async def test_log_includes_max_handoffs(capture_log, mock_chat, make_response):
    mock_chat.post("/v1/chat/completions").mock(
        return_value=make_response('{"next": "DONE", "message": "x"}')
    )

    await server.swarm_run("researcher", "x", max_handoffs=42)
    msg = next(
        r.getMessage() for r in capture_log.records if "swarm_run:" in r.getMessage()
    )
    assert "max_handoffs=42" in msg


def test_log_not_emitted_when_agents_json_invalid(capture_log):
    """If agents_json is malformed, swarm_run returns an ERROR before
    logging the effective agent set (the log fires only after a
    successful parse)."""
    import asyncio

    out = asyncio.run(
        server.swarm_run("x", "y", agents_json="not valid json")
    )
    assert "ERROR" in out
    assert not _has_log(capture_log, "effective_agents=")
