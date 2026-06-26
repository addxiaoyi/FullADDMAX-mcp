"""Tests for the Orchestrator-Workers workflow."""

from __future__ import annotations

import httpx
import pytest

from fulladdmax_mcp import orchestrator as orch_mod
from fulladdmax_mcp.errors import EmptyInputError, LLMError


async def test_orchestrator_pipeline(mock_chat, make_response):
    route = mock_chat.post("/v1/chat/completions").mock(
        side_effect=[
            make_response('{"subtasks":["do A","do B"]}'),
            make_response("result-A"),
            make_response("result-B"),
            make_response("final answer"),
        ]
    )
    out = await orch_mod.run("do thing", num_workers=2)
    assert out == "final answer"
    assert route.call_count == 4


async def test_orchestrator_empty_task_raises():
    with pytest.raises(EmptyInputError):
        await orch_mod.run("  ")


async def test_orchestrator_bad_num_workers_raises():
    with pytest.raises(EmptyInputError):
        await orch_mod.run("x", num_workers=0)
    with pytest.raises(EmptyInputError):
        await orch_mod.run("x", num_workers=11)


async def test_orchestrator_planner_bad_json_raises(mock_chat, make_response):
    mock_chat.post("/v1/chat/completions").mock(
        return_value=make_response("not json at all")
    )
    with pytest.raises(LLMError, match="non-JSON"):
        await orch_mod.run("x", num_workers=2)


async def test_orchestrator_planner_empty_list_raises(mock_chat, make_response):
    mock_chat.post("/v1/chat/completions").mock(
        return_value=make_response('{"subtasks": []}')
    )
    with pytest.raises(LLMError, match="non-empty"):
        await orch_mod.run("x", num_workers=2)


async def test_orchestrator_worker_failure_recorded_but_continues(
    mock_chat, make_response, make_error
):
    """One worker fails, the other succeeds -> synthesis still runs.

    The 5xx worker will be retried once by the LLM client, so the mock
    returns 500 for the first call and 200 on the retry.
    """
    responses = iter(
        [
            make_response('{"subtasks":["A","B"]}'),
            make_response("ok-A"),
            make_error(500, "oops"),
            make_response("ok-B-after-retry"),
            make_response("partial final"),
        ]
    )
    route = mock_chat.post("/v1/chat/completions").mock(
        side_effect=lambda req: next(responses)
    )
    out = await orch_mod.run("x", num_workers=2)
    assert "partial final" in out
    assert route.call_count == 5
