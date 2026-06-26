"""Tool registry for the agent workflows.

The workflows in :mod:`fulladdmax_mcp.orchestrator` etc. can call the LLM
with a list of "tools" the model is allowed to invoke. Those tools are
described to the LLM in OpenAI's ``{type: function, function: {name,
description, parameters}}`` shape, and when the model emits a
``tool_calls`` entry we route the call back to a registered Python
async function.

The registry lives at module level so that any tool, in any thread / task,
can register itself once (typically at server start) and have it picked
up by the next workflow run.

Self-recursion guard
--------------------

A workflow agent calling a tool that itself triggers a workflow would
cause an infinite loop. The agent-facing MCP tools (``orchestrator_run``
etc.) are **never** added to this registry by default; only
user-registered tools (via :func:`register_tool`) end up here.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal

from .llm import ToolSpec

log = logging.getLogger(__name__)

ToolFn = Callable[..., Awaitable[Any]]


@dataclass
class _Registered:
    name: str
    description: str
    parameters: dict[str, Any]
    fn: ToolFn


# The set of MCP tool names that are the agent-orchestration surface
# itself. They must never appear in the tool list sent to the LLM,
# otherwise an LLM could call them and re-enter the orchestrator
# recursively.
DEFAULT_EXCLUDE = frozenset(
    {
        "ping",
        "configure_llm",
        "orchestrator_run",
        "parallel_agents_run",
        "map_reduce_run",
        "swarm_run",
    }
)


class ToolRegistry:
    """In-process registry of agent-callable tools."""

    def __init__(self) -> None:
        self._tools: dict[str, _Registered] = {}

    # ---- registration ---------------------------------------------------

    def register(
        self,
        fn: ToolFn,
        *,
        name: str | None = None,
        description: str | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> None:
        """Register an async function as a tool the agent can call.

        - ``name`` defaults to the function name.
        - ``description`` defaults to the function's docstring (first
          non-empty line).
        - ``parameters`` defaults to a permissive JSON Schema
          (``{"type": "object", "additionalProperties": True}``) unless
          the function is annotated and we can introspect it.
        """
        tool_name = name or fn.__name__
        tool_desc = description or _first_doc_line(fn) or ""
        tool_params = parameters or _default_schema()
        if tool_name in self._tools:
            raise ValueError(f"tool {tool_name!r} is already registered")
        self._tools[tool_name] = _Registered(
            name=tool_name,
            description=tool_desc,
            parameters=tool_params,
            fn=fn,
        )
        log.info("registered tool %s", tool_name)

    def unregister(self, name: str) -> bool:
        return self._tools.pop(name, None) is not None

    def clear(self) -> None:
        self._tools.clear()

    # ---- inspection -----------------------------------------------------

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def get(self, name: str) -> _Registered | None:
        return self._tools.get(name)

    def openai_specs(self, *, exclude: set[str] | None = None) -> list[ToolSpec]:
        """Return the OpenAI-compatible ``tools`` list, minus the
        names in ``exclude`` (defaults to :data:`DEFAULT_EXCLUDE`).
        """
        ex = exclude if exclude is not None else DEFAULT_EXCLUDE
        specs: list[ToolSpec] = []
        for t in self._tools.values():
            if t.name in ex:
                continue
            specs.append(
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
            )
        return specs

    # ---- dispatch -------------------------------------------------------

    async def dispatch(self, call: dict[str, Any]) -> Any:
        """Execute a single ``tool_call`` dict.

        Parses ``function.arguments`` (a JSON string in the OpenAI
        protocol) and passes it to the registered function as keyword
        arguments. Returns whatever the function returns (it is later
        stringified by the LLM client).
        """
        fn_call = call.get("function", {})
        name = fn_call.get("name", "")
        raw_args = fn_call.get("arguments", "{}")
        reg = self._tools.get(name)
        if reg is None:
            raise KeyError(f"tool {name!r} is not registered")

        if isinstance(raw_args, dict):
            kwargs = dict(raw_args)
        elif isinstance(raw_args, str):
            import json

            try:
                parsed = json.loads(raw_args) if raw_args.strip() else {}
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"tool {name!r} arguments are not valid JSON: {e}"
                ) from e
            if not isinstance(parsed, dict):
                raise ValueError(
                    f"tool {name!r} arguments must be a JSON object, got {type(parsed).__name__}"
                )
            kwargs = parsed
        else:
            raise TypeError(
                f"tool {name!r} arguments must be str or dict, got {type(raw_args).__name__}"
            )

        return await reg.fn(**kwargs)

    async def dispatch_executor(
        self, call: dict[str, Any]
    ) -> str:
        """Wrap :meth:`dispatch` with a stringified result suitable for
        the LLM client. Errors are caught and returned as ``"ERROR: ..."``
        strings so the LLM can see what went wrong.
        """
        try:
            result = await self.dispatch(call)
        except Exception as e:  # noqa: BLE001
            return f"ERROR: {type(e).__name__}: {e}"
        if isinstance(result, str):
            return result
        try:
            import json
            return json.dumps(result, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(result)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

registry = ToolRegistry()


def register_tool(
    fn: ToolFn,
    *,
    name: str | None = None,
    description: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> ToolFn:
    """Functional form of :meth:`ToolRegistry.register`. Usable as a
    decorator::

        @register_tool
        async def get_weather(city: str) -> str: ...

    Or with explicit metadata::

        register_tool(
            get_weather,
            name="get_weather",
            description="Look up current weather for a city.",
            parameters={"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
        )
    """
    registry.register(fn, name=name, description=description, parameters=parameters)
    return fn


def unregister_tool(name: str) -> bool:
    return registry.unregister(name)


def openai_tool_specs(*, exclude: set[str] | None = None) -> list[ToolSpec]:
    """Convenience for workflows: get the OpenAI tool list."""
    return registry.openai_specs(exclude=exclude)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _first_doc_line(fn: ToolFn) -> str:
    doc = inspect.getdoc(fn)
    if not doc:
        return ""
    for line in doc.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _default_schema() -> dict[str, Any]:
    """Fallback schema when the user does not provide one. Permissive
    enough that any JSON object is accepted.
    """
    return {
        "type": "object",
        "properties": {},
        "additionalProperties": True,
    }
