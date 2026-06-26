"""Tests for the Map-Reduce pipeline."""

from __future__ import annotations

import pytest

from fulladdmax_mcp import mapreduce as mr_mod
from fulladdmax_mcp.errors import EmptyInputError


async def test_mapreduce_default_templates(mock_chat, make_response):
    route = mock_chat.post("/chat/completions").mock(
        side_effect=[
            make_response("mapped-1"),
            make_response("mapped-2"),
            make_response("reduced"),
        ]
    )
    out = await mr_mod.run(["a", "b"])
    assert out == "reduced"
    assert route.call_count == 3


async def test_mapreduce_custom_templates(mock_chat, make_response):
    map_prompt = "MAP[{item}]"
    reduce_prompt = "REDUCE[{results}]"
    route = mock_chat.post("/chat/completions").mock(
        side_effect=[
            make_response("m1"),
            make_response("m2"),
            make_response("R"),
        ]
    )
    out = await mr_mod.run(["x", "y"], map_prompt=map_prompt, reduce_prompt=reduce_prompt)
    assert out == "R"
    assert route.call_count == 3
    # The map call body should contain the substituted item.
    body = route.calls[0].request.content.decode()
    assert "MAP[x]" in body
    body2 = route.calls[1].request.content.decode()
    assert "MAP[y]" in body2
    body3 = route.calls[2].request.content.decode()
    assert "REDUCE[" in body3
    assert "m1" in body3 and "m2" in body3


async def test_mapreduce_empty_raises():
    with pytest.raises(EmptyInputError):
        await mr_mod.run([])


async def test_mapreduce_invalid_concurrency_raises():
    with pytest.raises(EmptyInputError):
        await mr_mod.run(["x"], max_concurrent=0)
