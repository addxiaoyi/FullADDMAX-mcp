"""Tests for the bounded parallel agent runner."""

from __future__ import annotations

import httpx
import pytest

from fulladdmax_mcp import parallel as par_mod
from fulladdmax_mcp.errors import EmptyInputError


async def test_parallel_returns_markdown_sections(mock_chat, make_response):
    route = mock_chat.post("/v1/chat/completions").mock(
        side_effect=[
            make_response("answer-A"),
            make_response("answer-B"),
        ]
    )
    out = await par_mod.run(["task A", "task B"], max_concurrent=2)
    assert "## Task #1" in out and "answer-A" in out
    assert "## Task #2" in out and "answer-B" in out
    assert route.call_count == 2


async def test_parallel_empty_raises():
    with pytest.raises(EmptyInputError):
        await par_mod.run([])


async def test_parallel_invalid_concurrency_raises():
    with pytest.raises(EmptyInputError):
        await par_mod.run(["x"], max_concurrent=0)
    with pytest.raises(EmptyInputError):
        await par_mod.run(["x"], max_concurrent=11)


async def test_parallel_records_error_per_task(mock_chat, make_response):
    mock_chat.post("/v1/chat/completions").mock(
        side_effect=[
            make_response("ok"),
            httpx.Response(500, text="oops"),
        ]
    )
    out = await par_mod.run(["t1", "t2"], max_concurrent=2)
    assert "## Task #1" in out
    assert "(ERROR)" in out
