"""Orchestrator-Workers workflow.

Three LLM phases:
    1. Planner decomposes the user task into N self-contained subtasks (JSON).
    2. Workers run in parallel on each subtask. If tools are available,
       each worker goes through the ``chat_with_tools`` dispatch loop so
       the LLM can call registered tools mid-task.
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
from .i18n import t as _t
from .llm import get_client
from .tools import openai_tool_specs

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
    "You may call the available tools if you need external information. "
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
            _t("llm_planner_json", err=f"{e}; raw={text[:200]!r}")
        ) from e
    if isinstance(data, dict):
        subs = data.get("subtasks")
    else:
        subs = data
    if not isinstance(subs, list) or not subs:
        raise LLMError(_t("llm_planner_empty"))
    cleaned = [str(s).strip() for s in subs if str(s).strip()]
    if not cleaned:
        raise LLMError(_t("llm_planner_blank"))
    return cleaned


async def _plan(task: str, n: int) -> list[str]:
    client = get_client()
    msgs = [
        {"role": "system", "content": PLANNER_SYS},
        {"role": "user", "content": f"Task: {task}\nGenerate exactly {n} subtasks."},
    ]
    raw = await client.chat(msgs)
    return _extract_json(raw)[:n]


async def _worker(
    idx: int,
    sub: str,
    ctx_snapshot: dict[str, Any],
    use_tools: bool,
) -> str:
    """Run a single worker.

    When ``use_tools`` is True, the worker can call any tool in the
    :data:`tool_registry` via the LLM's tool-calling loop. The first
    ``chat_with_tools`` is bounded by ``max_steps`` rounds; if the LLM
    still hasn't produced a final answer we fall back to a plain
    ``chat`` so the caller never sees an empty worker output.
    """
    client = get_client()
    ctx_text = json.dumps(ctx_snapshot, ensure_ascii=False) if ctx_snapshot else "(no shared context)"
    msgs: list[dict[str, Any]] = [
        {"role": "system", "content": WORKER_SYS},
        {
            "role": "user",
            "content": f"Shared context: {ctx_text}\nSubtask #{idx + 1}: {sub}",
        },
    ]
    if use_tools:
        from .tools import registry as tool_registry

        executor = tool_registry.dispatch_executor
        text, _ = await client.chat_with_tools(msgs, executor=executor, max_steps=6)
        if text.strip():
            return text
        # Fallback: dispatch loop didn't produce a final text (all steps
        # were tool calls). Ask the LLM one more time to summarise.
        msgs.append(
            {
                "role": "system",
                "content": (
                    "You have finished using your tools. Now write a concise "
                    "final answer based on the tool results above."
                ),
            }
        )
    return await client.chat(msgs)


async def _synthesize(
    task: str,
    pairs: list[tuple[str, str]],
    use_tools: bool,
) -> str:
    client = get_client()
    body = "\n\n".join(f"### Subtask {i + 1}: {s}\nResult: {r}" for i, (s, r) in enumerate(pairs))
    msgs: list[dict[str, Any]] = [
        {"role": "system", "content": SYNTH_SYS},
        {
            "role": "user",
            "content": f"Original task: {task}\n\n{body}\n\nProduce the final answer.",
        },
    ]
    if use_tools:
        from .tools import registry as tool_registry

        text, _ = await client.chat_with_tools(
            msgs, executor=tool_registry.dispatch_executor, max_steps=4
        )
        if text.strip():
            return text
    return await client.chat(msgs)


async def run(
    task: str,
    num_workers: int = 3,
    timeout: float = 300.0,
    tools: list[str] | None = None,
) -> str:
    """Execute the Orchestrator-Workers workflow and return the final answer.

    Args:
        task: The user task to decompose.
        num_workers: How many parallel workers (1-10).
        timeout: Overall timeout in seconds.
        tools: Optional whitelist of registered tool names to make
            available to the workers / synthesizer. ``None`` (default)
            means "use every registered tool". An empty list disables
            tool-calling entirely (equivalent to the pre-function-calling
            behaviour).
    """
    if not task or not task.strip():
        raise EmptyInputError(_t("wf_empty_task", op="orchestrator_run"))
    if not 1 <= num_workers <= 10:
        raise EmptyInputError(_t("wf_num_workers"))

    tool_specs = _resolve_tool_specs(tools)
    use_tools = bool(tool_specs)

    new_session()
    put("original_task", task)
    put("num_workers", num_workers)
    put("tools", [t["function"]["name"] for t in tool_specs])

    try:
        async with asyncio.timeout(timeout):
            subtasks = await _plan(task, num_workers)
            put("subtasks", subtasks)
            ctx_snapshot = snapshot()
            results = await asyncio.gather(
                *[
                    _worker(i, s, ctx_snapshot, use_tools)
                    for i, s in enumerate(subtasks)
                ],
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
                raise LLMError(_t("llm_all_workers", detail=" | ".join(errors)))

            final = await _synthesize(task, pairs, use_tools)
            put("final", final)
            return final
    except asyncio.TimeoutError as e:
        raise ToolTimeoutError(
            _t("wf_timeout", op="orchestrator_run", seconds=timeout)
        ) from e


def _resolve_tool_specs(whitelist: list[str] | None) -> list[dict[str, Any]]:
    """Return the OpenAI tool spec list the workflows should expose.

    * ``whitelist is None`` -> every registered tool except
      :data:`DEFAULT_EXCLUDE`.
    * ``whitelist == []``   -> no tools (plain chat mode).
    * ``whitelist=[...]``   -> only those names; unknown names are
      ignored with a warning.
    """
    all_specs = openai_tool_specs()
    if whitelist is None:
        return all_specs
    if not whitelist:
        return []
    wanted = set(whitelist)
    selected = [
        spec for spec in all_specs
        if spec["function"]["name"] in wanted
    ]
    missing = wanted - {spec["function"]["name"] for spec in selected}
    if missing:
        log.warning("tools not found in registry (skipped): %s", sorted(missing))
    return selected
