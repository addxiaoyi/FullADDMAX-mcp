"""Tests for the Swarm workflow."""

from __future__ import annotations

import pytest

from fulladdmax_mcp import swarm as swarm_mod
from fulladdmax_mcp.errors import EmptyInputError, HandoffError


async def test_swarm_handoff_chain(mock_chat, make_response):
    route = mock_chat.post("/chat/completions").mock(
        side_effect=[
            make_response('{"next": "coder", "message": "from researcher"}'),
            make_response('{"next": "writer", "message": "from coder"}'),
            make_response('{"next": "DONE", "message": "from writer"}'),
        ]
    )
    out = await swarm_mod.run("researcher", "design api", max_handoffs=8)
    assert out == "from writer"
    assert route.call_count == 3


async def test_swarm_drops_to_done_after_max_handoffs(mock_chat, make_response):
    route = mock_chat.post("/chat/completions").mock(
        side_effect=[
            make_response('{"next": "coder", "message": "p1"}'),
            make_response('{"next": "writer", "message": "p2"}'),
            make_response('{"next": "critic", "message": "p3"}'),
        ]
    )
    out = await swarm_mod.run("researcher", "x", max_handoffs=2)
    assert out == "p3"
    assert route.call_count == 3


async def test_swarm_bad_json_raises(mock_chat, make_response):
    mock_chat.post("/chat/completions").mock(return_value=make_response("not json"))
    with pytest.raises(HandoffError, match="did not return JSON"):
        await swarm_mod.run("researcher", "x")


async def test_swarm_unknown_next_raises(mock_chat, make_response):
    mock_chat.post("/chat/completions").mock(
        return_value=make_response('{"next": "ghost", "message": "x"}')
    )
    with pytest.raises(HandoffError, match="unknown agent"):
        await swarm_mod.run("researcher", "x")


async def test_swarm_empty_message_raises(mock_chat, make_response):
    mock_chat.post("/chat/completions").mock(
        return_value=make_response('{"next": "coder", "message": "  "}')
    )
    with pytest.raises(HandoffError, match="empty"):
        await swarm_mod.run("researcher", "x")


async def test_swarm_unknown_initial_raises():
    with pytest.raises(EmptyInputError):
        await swarm_mod.run("nope", "x")


async def test_swarm_empty_task_raises():
    with pytest.raises(EmptyInputError):
        await swarm_mod.run("researcher", "   ")
