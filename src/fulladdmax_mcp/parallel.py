"""Bounded parallel agent fan-out.

Runs up to ``max_concurrent`` (max 10) independent LLM calls in parallel and
collects their outputs. A single failing worker is recorded but does not
abort the whole batch.

If tools are passed, each task is given the tool list and goes through
``chat_with_tools`` so the LLM can call registered tools mid-task.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from .context import new_session, put, snapshot
from .errors import EmptyInputError, ToolTimeoutError
from .i18n import t as _t
from .llm import get_client
from .tools import openai_tool_specs

log = logging.getLogger(__name__)

SYS = (
    "You are a parallel research agent. Complete the assigned task precisely. "
    "You may call available tools to gather information. Return only the final result."
)


async def _one(
    idx: int,
    t: str,
    sem: asyncio.Semaphore,
    shared: dict[str, Any],
    tool_specs: list[dict[str, Any]],
) -> tuple[int, str, str | None]:
    async with sem:
        client = get_client()
        try:
            ctx = json.dumps(shared, ensure_ascii=False) if shared else "(none)"
            msgs: list[dict[str, Any]] = [
                {"role": "system", "content": SYS},
                {
                    "role": "user",
                    "content": f"Shared context: {ctx}\nTask #{idx + 1}: {t}",
                },
            ]
            if tool_specs:
                from .tools import registry as tool_registry

                text, _ = await client.chat_with_tools(
                    msgs,
                    executor=tool_registry.dispatch_executor,
                    max_steps=6,
                )
                if text.strip():
                    return idx, text, None
                # Loop ended with no final text; force a summary.
                msgs.append(
                    {
                        "role": "system",
                        "content": (
                            "Summarise the tool results above into a concise final answer."
                        ),
                    }
                )
            out = await client.chat(msgs)
            return idx, out, None
        except Exception as e:  # noqa: BLE001 - record but don't propagate
            return idx, "", f"{type(e).__name__}: {e}"


async def run(
    tasks: list[str],
    max_concurrent: int = 10,
    timeout: float = 300.0,
    shared_context: dict[str, Any] | None = None,
    tools: list[str] | None = None,
) -> str:
    """Run independent tasks in parallel and return a Markdown report.

    Args:
        tasks: 1-10 independent prompt strings.
        max_concurrent: Maximum number of tasks running at once.
        timeout: Overall timeout in seconds.
        shared_context: Optional initial context (otherwise the current
            session snapshot is used).
        tools: Whitelist of tool names to expose. ``None`` = every
            registered tool. ``[]`` = no tool-calling.
    """
    if not tasks:
        raise EmptyInputError(
            _t("wf_empty_task", op="parallel_agents_run: 'tasks' must be a non-empty list")
        )
    if not 1 <= max_concurrent <= 10:
        raise EmptyInputError(_t("wf_num_concurrent"))

    tool_specs = _resolve_tool_specs(tools)

    new_session()
    put("task_count", len(tasks))
    put("max_concurrent", max_concurrent)
    put("tools", [t["function"]["name"] for t in tool_specs])
    shared = shared_context if shared_context is not None else snapshot()
    sem = asyncio.Semaphore(max_concurrent)

    try:
        async with asyncio.timeout(timeout):
            results = await asyncio.gather(
                *[_one(i, t, sem, shared, tool_specs) for i, t in enumerate(tasks)]
            )
    except asyncio.TimeoutError as e:
        raise ToolTimeoutError(
            _t("wf_timeout", op="parallel_agents_run", seconds=timeout)
        ) from e

    results.sort(key=lambda x: x[0])
    put("results", [r for _, r, _ in results])

    parts: list[str] = []
    for idx, out, err in results:
        if err:
            parts.append(f"## Task #{idx + 1} (ERROR)\n{err}")
        else:
            parts.append(f"## Task #{idx + 1}\n{out}")
    return "\n\n---\n\n".join(parts)


def _resolve_tool_specs(whitelist: list[str] | None) -> list[dict[str, Any]]:
    all_specs = openai_tool_specs()
    if whitelist is None:
        return all_specs
    if not whitelist:
        return []
    wanted = set(whitelist)
    selected = [s for s in all_specs if s["function"]["name"] in wanted]
    missing = wanted - {s["function"]["name"] for s in selected}
    if missing:
        log.warning("tools not found in registry (skipped): %s", sorted(missing))
    return selected
