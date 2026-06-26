"""Bounded parallel agent fan-out.

Runs up to ``max_concurrent`` (max 10) independent LLM calls in parallel and
collects their outputs. A single failing worker is recorded but does not
abort the whole batch.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from .context import new_session, put, snapshot
from .errors import EmptyInputError, ToolTimeoutError
from .llm import get_client

SYS = (
    "You are a parallel research agent. Complete the assigned task precisely. "
    "Return only the result."
)


async def _one(
    idx: int,
    t: str,
    sem: asyncio.Semaphore,
    shared: dict[str, Any],
) -> tuple[int, str, str | None]:
    async with sem:
        client = get_client()
        try:
            ctx = json.dumps(shared, ensure_ascii=False) if shared else "(none)"
            msgs = [
                {"role": "system", "content": SYS},
                {
                    "role": "user",
                    "content": f"Shared context: {ctx}\nTask #{idx + 1}: {t}",
                },
            ]
            out = await client.chat(msgs)
            return idx, out, None
        except Exception as e:  # noqa: BLE001 - record but don't propagate
            return idx, "", f"{type(e).__name__}: {e}"


async def run(
    tasks: list[str],
    max_concurrent: int = 10,
    timeout: float = 300.0,
    shared_context: dict[str, Any] | None = None,
) -> str:
    """Run independent tasks in parallel and return a Markdown report."""
    if not tasks:
        raise EmptyInputError("parallel_agents_run: 'tasks' must be a non-empty list.")
    if not 1 <= max_concurrent <= 10:
        raise EmptyInputError("max_concurrent must be between 1 and 10.")

    new_session()
    put("task_count", len(tasks))
    put("max_concurrent", max_concurrent)
    shared = shared_context if shared_context is not None else snapshot()
    sem = asyncio.Semaphore(max_concurrent)

    try:
        async with asyncio.timeout(timeout):
            results = await asyncio.gather(
                *[_one(i, t, sem, shared) for i, t in enumerate(tasks)]
            )
    except asyncio.TimeoutError as e:
        raise ToolTimeoutError(f"parallel_agents_run exceeded {timeout}s") from e

    results.sort(key=lambda x: x[0])
    put("results", [r for _, r, _ in results])

    parts: list[str] = []
    for idx, out, err in results:
        if err:
            parts.append(f"## Task #{idx + 1} (ERROR)\n{err}")
        else:
            parts.append(f"## Task #{idx + 1}\n{out}")
    return "\n\n---\n\n".join(parts)
