"""Map-Reduce pipeline.

The map phase fans out a user-supplied ``map_prompt`` (templated with
``{item}``) across the input ``items``. The reduce phase feeds a single
``reduce_prompt`` (templated with ``{results}``) to the LLM to produce the
final merged answer.

If tools are passed, the map and reduce phases go through
``chat_with_tools`` so the LLM can call registered tools mid-task.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .context import new_session, put
from .errors import EmptyInputError, ToolTimeoutError
from .llm import get_client
from .tools import openai_tool_specs

log = logging.getLogger(__name__)

DEFAULT_MAP = (
    "You are a mapper. Process the following item and return a concise, "
    "structured result. Do not mention the other items.\n\n"
    "Item:\n{item}"
)
DEFAULT_REDUCE = (
    "You are a reducer. Merge the following mapped results into one cohesive, "
    "well-structured final answer. Remove redundancy and resolve conflicts.\n\n"
    "Mapped results:\n{results}"
)


async def _map(
    item: str,
    map_prompt: str,
    sem: asyncio.Semaphore,
    tool_specs: list[dict[str, Any]],
) -> tuple[int, str, str | None]:
    async with sem:
        client = get_client()
        try:
            prompt = map_prompt.format(item=item)
            msgs: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
            if tool_specs:
                from .tools import registry as tool_registry

                text, _ = await client.chat_with_tools(
                    msgs,
                    executor=tool_registry.dispatch_executor,
                    max_steps=6,
                )
                if text.strip():
                    return 0, text, None
                msgs.append(
                    {
                        "role": "system",
                        "content": "Summarise the tool results above concisely.",
                    }
                )
            return 0, await client.chat(msgs), None
        except Exception as e:  # noqa: BLE001
            return 0, "", f"{type(e).__name__}: {e}"


async def run(
    items: list[str],
    map_prompt: str = DEFAULT_MAP,
    reduce_prompt: str = DEFAULT_REDUCE,
    max_concurrent: int = 10,
    timeout: float = 600.0,
    tools: list[str] | None = None,
) -> str:
    """Execute the Map-Reduce pipeline and return the reduced result.

    Args:
        items: 1-100 input items.
        map_prompt: Template for the map phase; must contain ``{item}``.
        reduce_prompt: Template for the reduce phase; must contain ``{results}``.
        max_concurrent: Max parallel map workers (1-10).
        timeout: Per-phase timeout in seconds.
        tools: Whitelist of tool names to expose. ``None`` = every
            registered tool. ``[]`` = no tool-calling.
    """
    if not items:
        raise EmptyInputError("map_reduce_run: 'items' must be a non-empty list.")
    if not 1 <= max_concurrent <= 10:
        raise EmptyInputError("max_concurrent must be between 1 and 10.")

    tool_specs = _resolve_tool_specs(tools)

    new_session()
    put("item_count", len(items))
    put("tools", [t["function"]["name"] for t in tool_specs])
    sem = asyncio.Semaphore(max_concurrent)

    try:
        async with asyncio.timeout(timeout):
            mapped = await asyncio.gather(
                *[_map(it, map_prompt, sem, tool_specs) for it in items]
            )
    except asyncio.TimeoutError as e:
        raise ToolTimeoutError(f"map_reduce_run (map phase) exceeded {timeout}s") from e

    mapped_text = "\n\n---\n\n".join(
        f"### Item {i + 1}\n{out if not err else f'[ERROR] {err}'}"
        for i, (_, out, err) in enumerate(mapped)
    )
    put("mapped_count", len(mapped))
    put("mapped_text", mapped_text)

    try:
        async with asyncio.timeout(timeout):
            client = get_client()
            msgs: list[dict[str, Any]] = [
                {"role": "user", "content": reduce_prompt.format(results=mapped_text)}
            ]
            if tool_specs:
                from .tools import registry as tool_registry

                text, _ = await client.chat_with_tools(
                    msgs,
                    executor=tool_registry.dispatch_executor,
                    max_steps=4,
                )
                if text.strip():
                    final = text
                else:
                    final = await client.chat(msgs)
            else:
                final = await client.chat(msgs)
    except asyncio.TimeoutError as e:
        raise ToolTimeoutError(f"map_reduce_run (reduce phase) exceeded {timeout}s") from e

    put("final", final)
    return final


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
