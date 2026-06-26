"""Tests for function-calling / tool-calling support.

Three layers, top-down:
  1. LLMClient.chat_with_tools (protocol + dispatch loop)
  2. ToolRegistry (register / dispatch / openai_specs / self-exclude)
  3. Workflow integration: orchestrator / parallel / map_reduce / swarm
     honor the ``tools=`` parameter and call into the registry.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from fulladdmax_mcp import (
    llm as llm_mod,
    mapreduce,
    orchestrator,
    parallel,
    swarm,
)
from fulladdmax_mcp.errors import HandoffError, LLMError
from fulladdmax_mcp.llm import LLMConfig, get_client, set_config
from fulladdmax_mcp.tools import (
    DEFAULT_EXCLUDE,
    ToolRegistry,
    openai_tool_specs,
    register_tool,
    registry,
    unregister_tool,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_registry():
    """Wipe the module-level registry before and after every test."""
    registry.clear()
    yield
    registry.clear()


# ---------------------------------------------------------------------------
# 1. LLMClient: chat_with_tools
# ---------------------------------------------------------------------------


def _tool_call_msg(call_id: str, name: str, arguments: str | dict) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": (
                arguments if isinstance(arguments, str)
                else json.dumps(arguments)
            ),
        },
    }


def _assistant_msg(content: str = "", tool_calls: list[dict] | None = None) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def _msg_to_response(message: dict[str, Any]) -> httpx.Response:
    """Wrap a synthetic assistant message in an OpenAI chat-completions response."""
    return httpx.Response(
        200,
        json={
            "id": "test",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": "stop",
                }
            ],
        },
    )


async def test_chat_with_tools_returns_text_when_no_tool_call(
    mock_chat, make_response
):
    mock_chat.post("/v1/chat/completions").mock(
        return_value=make_response("just text")
    )
    out, _ = await get_client().chat_with_tools(
        [{"role": "user", "content": "hi"}],
        executor=lambda c: "should-not-run",
    )
    assert out == "just text"


async def test_chat_with_tools_dispatches_and_loops(
    mock_chat, make_response
):
    """LLM returns a tool_call, then a final text. We expect:
      - executor is invoked once with the parsed call dict
      - the loop terminates after the second LLM call (no tool_calls)
      - the result string is returned
    """
    seen_args: list[Any] = []

    async def executor(call):
        seen_args.append(call)
        return "tool-result"

    responses = iter(
        [
            _msg_to_response(
                _assistant_msg(
                    tool_calls=[_tool_call_msg("c1", "echo", {"x": 1})]
                )
            ),
            make_response("final answer"),
        ]
    )
    mock_chat.post("/v1/chat/completions").mock(
        side_effect=lambda req: next(responses)
    )

    out, transcript = await get_client().chat_with_tools(
        [{"role": "user", "content": "go"}],
        executor=executor,
    )
    assert out == "final answer"
    assert len(seen_args) == 1
    assert seen_args[0]["function"]["name"] == "echo"
    assert seen_args[0]["function"]["arguments"] == {"x": 1}
    # Transcript captures both LLM turns
    assert len(transcript) == 2


async def test_chat_with_tools_max_steps_limits_loop(
    mock_chat, make_response
):
    """If the LLM always returns tool_calls, the loop stops at max_steps."""

    async def executor(call):
        return "ok"

    def always_call(req):
        return _msg_to_response(
            _assistant_msg(tool_calls=[_tool_call_msg("c", "echo", {})])
        )

    mock_chat.post("/v1/chat/completions").mock(side_effect=always_call)
    out, transcript = await get_client().chat_with_tools(
        [{"role": "user", "content": "go"}],
        executor=executor,
        max_steps=3,
    )
    # 3 steps, all tool calls -> no final text
    assert out == ""
    assert len(transcript) == 3


async def test_chat_with_tools_swallows_executor_errors(
    mock_chat, make_response
):
    """If executor raises, the error is fed back to the LLM as a tool
    result and the loop continues (does not crash the caller).
    """

    async def bad_executor(call):
        raise RuntimeError("nope")

    responses = iter(
        [
            _msg_to_response(
                _assistant_msg(tool_calls=[_tool_call_msg("c", "boom", {})])
            ),
            make_response("recovered"),
        ]
    )
    mock_chat.post("/v1/chat/completions").mock(
        side_effect=lambda req: next(responses)
    )

    out, _ = await get_client().chat_with_tools(
        [{"role": "user", "content": "go"}],
        executor=bad_executor,
    )
    assert out == "recovered"


async def test_chat_raw_returns_message_with_tool_calls(
    mock_chat, make_response
):
    """``chat_raw`` is the lower-level helper that surfaces tool_calls
    without dispatching them. Used by callers that want to inspect.
    """
    mock_chat.post("/v1/chat/completions").mock(
        return_value=_msg_to_response(
            _assistant_msg(
                content="",
                tool_calls=[_tool_call_msg("c", "echo", {"k": "v"})],
            )
        )
    )
    msg = await get_client().chat_raw(
        [{"role": "user", "content": "hi"}]
    )
    assert msg["role"] == "assistant"
    assert msg["tool_calls"][0]["function"]["name"] == "echo"


# ---------------------------------------------------------------------------
# 2. ToolRegistry
# ---------------------------------------------------------------------------


async def test_registry_register_and_dispatch():
    r = ToolRegistry()

    async def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    r.register(add)
    assert "add" in r
    assert r.get("add").description == "Add two numbers."

    out = await r.dispatch(_tool_call_msg("c1", "add", {"a": 2, "b": 3}))
    assert out == 5


async def test_registry_dispatch_string_args():
    r = ToolRegistry()

    async def echo(text: str) -> str:
        return text

    r.register(echo)
    out = await r.dispatch(_tool_call_msg("c1", "echo", '{"text": "hi"}'))
    assert out == "hi"


async def test_registry_rejects_duplicate():
    r = ToolRegistry()

    async def f(x: int) -> int:
        return x

    r.register(f)
    with pytest.raises(ValueError, match="already registered"):
        r.register(f)


async def test_registry_unregister():
    r = ToolRegistry()

    async def f(x: int) -> int:
        return x

    r.register(f)
    assert r.unregister("f")
    assert "f" not in r
    assert r.unregister("f") is False  # idempotent


async def test_registry_openai_specs_format():
    r = ToolRegistry()

    async def get_weather(city: str) -> str:
        """Look up the weather for a city."""
        return "sunny"

    r.register(
        get_weather,
        parameters={
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    )
    specs = r.openai_specs()
    assert len(specs) == 1
    spec = specs[0]
    assert spec["type"] == "function"
    assert spec["function"]["name"] == "get_weather"
    assert "Look up the weather" in spec["function"]["description"]
    assert spec["function"]["parameters"]["properties"]["city"]["type"] == "string"


async def test_registry_openai_specs_excludes_self():
    """The orchestration tool names are excluded by DEFAULT_EXCLUDE
    even if a tool with that name is registered (defence in depth)."""
    r = ToolRegistry()

    async def orchestrator_run() -> str:
        return "hi"

    r.register(orchestrator_run)
    specs = r.openai_specs()
    assert all(
        s["function"]["name"] not in DEFAULT_EXCLUDE
        for s in specs
    )


async def test_registry_dispatch_unknown_tool_raises():
    r = ToolRegistry()
    with pytest.raises(KeyError, match="not registered"):
        await r.dispatch(_tool_call_msg("c", "nope", {}))


async def test_registry_dispatch_bad_json_raises():
    r = ToolRegistry()

    async def f(x: int) -> int:
        return x

    r.register(f)
    with pytest.raises(ValueError, match="not valid JSON"):
        await r.dispatch(_tool_call_msg("c", "f", "{not json"))


async def test_registry_dispatch_non_object_raises():
    r = ToolRegistry()

    async def f(x: int) -> int:
        return x

    r.register(f)
    with pytest.raises(ValueError, match="JSON object"):
        await r.dispatch(_tool_call_msg("c", "f", "[1,2,3]"))


async def test_register_tool_decorator():
    @register_tool
    async def double(n: int) -> int:
        """Double n."""
        return n * 2

    assert "double" in registry
    out = await registry.dispatch(_tool_call_msg("c", "double", {"n": 5}))
    assert out == 10

    unregister_tool("double")
    assert "double" not in registry


# ---------------------------------------------------------------------------
# 3. Workflow integration
# ---------------------------------------------------------------------------


@pytest.fixture
def make_tool_response():
    """Factory: returns a helper that builds a tool-call HTTP response."""

    def _build(name: str, args: dict[str, Any], call_id: str = "c1") -> httpx.Response:
        return _msg_to_response(
            _assistant_msg(
                content="",
                tool_calls=[_tool_call_msg(call_id, name, args)],
            )
        )

    return _build


async def test_orchestrator_with_tool_call(
    mock_chat, make_response, make_tool_response
):
    """Planner returns 2 subtasks, worker #1 calls our tool, worker #2
    answers directly, synth writes a final. With ``tools`` enabled, the
    workers should go through ``chat_with_tools``."""

    @register_tool
    async def lookup(key: str) -> str:
        return f"value-for-{key}"

    responses = iter(
        [
            make_response('{"subtasks": ["A", "B"]}'),
            # Worker #1: tool call → tool result → final text
            make_tool_response("lookup", {"key": "a"}),
            make_response("worker-1-final"),
            # Worker #2: direct text
            make_response("worker-2-final"),
            # Synth: direct text
            make_response("final-synth"),
        ]
    )
    mock_chat.post("/v1/chat/completions").mock(
        side_effect=lambda req: next(responses)
    )

    out = await orchestrator.run("do x", num_workers=2, tools=["lookup"])
    assert out == "final-synth"


async def test_orchestrator_explicit_empty_tools_disables_function_calling(
    mock_chat, make_response
):
    """``tools=[]`` should mean: no function-calling, plain chat."""
    mock_chat.post("/v1/chat/completions").mock(
        side_effect=[
            make_response('{"subtasks":["A"]}'),
            make_response("worker-direct"),
            make_response("synth"),
        ]
    )
    out = await orchestrator.run("x", num_workers=1, tools=[])
    assert out == "synth"


async def test_parallel_with_tool_calls(mock_chat, make_response, make_tool_response):
    @register_tool
    async def greet(name: str) -> str:
        return f"hello {name}"

    responses = iter(
        [
            # task #1: tool call → tool result → final
            make_tool_response("greet", {"name": "alice"}, call_id="c1"),
            make_response("task-1-final"),
            # task #2: direct
            make_response("task-2-final"),
        ]
    )
    mock_chat.post("/v1/chat/completions").mock(
        side_effect=lambda req: next(responses)
    )

    out = await parallel.run(["t1", "t2"], tools=["greet"])
    assert "## Task #1" in out
    assert "task-1-final" in out
    assert "## Task #2" in out
    assert "task-2-final" in out


async def test_map_reduce_with_tool_calls(
    mock_chat, make_response, make_tool_response
):
    @register_tool
    async def enrich(item: str) -> str:
        return f"({item})"

    responses = iter(
        [
            make_tool_response("enrich", {"item": "x"}, call_id="c1"),
            make_response("mapped-1"),
            make_response("mapped-2"),
            make_response("reduced"),
        ]
    )
    mock_chat.post("/v1/chat/completions").mock(
        side_effect=lambda req: next(responses)
    )

    out = await mapreduce.run(["x", "y"], tools=["enrich"])
    assert out == "reduced"


async def test_swarm_with_tool_calls(mock_chat, make_response, make_tool_response):
    """Swarm: researcher calls a tool then returns JSON handoff."""

    @register_tool
    async def lookup(q: str) -> str:
        return f"answer({q})"

    responses = iter(
        [
            # researcher turn: tool call → tool result → JSON handoff
            make_tool_response("lookup", {"q": "weather"}, call_id="c1"),
            make_response('{"next": "writer", "message": "sunny"}'),
            # writer turn: JSON DONE
            make_response('{"next": "DONE", "message": "final sunny"}'),
        ]
    )
    mock_chat.post("/v1/chat/completions").mock(
        side_effect=lambda req: next(responses)
    )

    out = await swarm.run(
        "researcher", "what is the weather", max_handoffs=4, tools=["lookup"]
    )
    assert out == "final sunny"


async def test_workflow_without_tools_still_works(mock_chat, make_response):
    """Default ``tools=None`` => no tools sent => plain chat (regression)."""
    mock_chat.post("/v1/chat/completions").mock(
        side_effect=[
            make_response('{"subtasks":["A"]}'),
            make_response("worker-1"),
            make_response("synth"),
        ]
    )
    out = await orchestrator.run("x", num_workers=1)
    assert out == "synth"


# ---------------------------------------------------------------------------
# 4. openai_tool_specs module-level helper
# ---------------------------------------------------------------------------


def test_openai_tool_specs_excludes_orchestration_tools():
    @register_tool
    async def f() -> str:
        return "x"

    @register_tool
    async def orchestrator_run() -> str:  # noqa: F811
        return "should be excluded"

    specs = openai_tool_specs()
    names = {s["function"]["name"] for s in specs}
    assert "f" in names
    assert "orchestrator_run" not in names
