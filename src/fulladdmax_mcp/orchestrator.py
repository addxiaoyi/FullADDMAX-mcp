"""Orchestrator-Workers workflow.

Three LLM phases:
    1. Planner decomposes the user task into N self-contained subtasks (JSON).
    2. Workers run in parallel on each subtask.
    3. Synthesizer merges all worker outputs into a final answer.

Shared session context is captured before the workers start so that each
worker sees the same baseline, and intermediate state is stashed via
:mod:`fulladdmax_mcp.context` for later inspection.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from .context import new_session, put, snapshot
from .errors import EmptyInputError, LLMError, ToolTimeoutError
from .llm import get_client

log = logging.getLogger(__name__)

PLANNER_SYS = (
    "You are an orchestrator. Given a user task, decompose it into a JSON array of "
    "self-contained subtasks for parallel workers. Output strictly JSON, no prose.\n"
    'Schema: {"subtasks": ["subtask 1", "subtask 2", ...]}.\n'
    "Rules:\n"
    "  - Subtasks must be independent (no cross-dependencies).\n"
    "  - Each subtask must be answerable without seeing other workers' results.\n"
    "  - Use the same language as the user's task.\n"
    "  - Generate EXACTLY the requested number of subtasks."
)
SYNTH_SYS = (
    "You are a synthesizer. Combine the worker outputs into a single coherent final "
    "answer in the user's language. Be concise, well-structured, and accurate. "
    "If workers disagree, point it out."
)
WORKER_SYS = (
    "You are a focused worker agent. Complete the assigned subtask thoroughly. "
    "Return only the result, no meta-commentary."
)


def _extract_json(text: str) -> list[str]:
    """Parse a JSON object/array out of an LLM response, tolerating ``` fences."""
    text = text.strip()
    if text.startswith("```"):
        # split on the first two fences; tolerate a leading 'json'
        parts = text.split("```")
        if len(parts) >= 3:
            text = parts[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMError(
            f"Orchestrator planner returned non-JSON: {e}; raw={text[:200]!r}"
        ) from e
    if isinstance(data, dict):
        subs = data.get("subtasks")
    else:
        subs = data
    if not isinstance(subs, list) or not subs:
        raise LLMError("Planner output did not contain a non-empty 'subtasks' list.")
    cleaned = [str(s).strip() for s in subs if str(s).strip()]
    if not cleaned:
        raise LLMError("Planner output contained only empty subtask strings.")
    return cleaned


async def _plan(task: str, n: int) -> list[str]:
    client = get_client()
    msgs = [
        {"role": "system", "content": PLANNER_SYS},
        {"role": "user", "content": f"Task: {task}\nGenerate exactly {n} subtasks."},
    ]
    raw = await client.chat(msgs)
    return _extract_json(raw)[:n]


async def _worker(idx: int, sub: str, ctx_snapshot: dict[str, Any]) -> str:
    client = get_client()
    ctx_text = json.dumps(ctx_snapshot, ensure_ascii=False) if ctx_snapshot else "(no shared context)"
    msgs = [
        {"role": "system", "content": WORKER_SYS},
        {
            "role": "user",
            "content": f"Shared context: {ctx_text}\nSubtask #{idx + 1}: {sub}",
        },
    ]
    return await client.chat(msgs)


async def _synthesize(task: str, pairs: list[tuple[str, str]]) -> str:
    client = get_client()
    body = "\n\n".join(f"### Subtask {i + 1}: {s}\nResult: {r}" for i, (s, r) in enumerate(pairs))
    msgs = [
        {"role": "system", "content": SYNTH_SYS},
        {
            "role": "user",
            "content": f"Original task: {task}\n\n{body}\n\nProduce the final answer.",
        },
    ]
    return await client.chat(msgs)


async def run(task: str, num_workers: int = 3, timeout: float = 300.0) -> str:
    """Execute the Orchestrator-Workers workflow and return the final answer."""
    if not task or not task.strip():
        raise EmptyInputError("orchestrator_run: 'task' must be a non-empty string.")
    if not 1 <= num_workers <= 10:
        raise EmptyInputError("num_workers must be between 1 and 10.")

    new_session()
    put("original_task", task)
    put("num_workers", num_workers)

    try:
        async with asyncio.timeout(timeout):
            subtasks = await _plan(task, num_workers)
            put("subtasks", subtasks)
            ctx_snapshot = snapshot()
            results = await asyncio.gather(
                *[_worker(i, s, ctx_snapshot) for i, s in enumerate(subtasks)],
                return_exceptions=True,
            )

            pairs: list[tuple[str, str]] = []
            errors: list[str] = []
            for s, r in zip(subtasks, results):
                if isinstance(r, BaseException):
                    msg = f"[ERROR] {type(r).__name__}: {r}"
                    errors.append(f"subtask={s!r} -> {msg}")
                    pairs.append((s, msg))
                else:
                    pairs.append((s, r))

            put("worker_results", [r for _, r in pairs])
            if errors and len(errors) == len(subtasks):
                raise LLMError("All workers failed: " + " | ".join(errors))

            final = await _synthesize(task, pairs)
            put("final", final)
            return final
    except asyncio.TimeoutError as e:
        raise ToolTimeoutError(f"orchestrator_run exceeded {timeout}s") from e
