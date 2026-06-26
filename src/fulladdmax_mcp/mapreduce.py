"""Map-Reduce pipeline.

The map phase fans out a user-supplied ``map_prompt`` (templated with
``{item}``) across the input ``items``. The reduce phase feeds a single
``reduce_prompt`` (templated with ``{results}``) to the LLM to produce the
final merged answer.
"""

from __future__ import annotations

import asyncio

from .context import new_session, put
from .errors import EmptyInputError, ToolTimeoutError
from .llm import get_client

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


async def _map(item: str, map_prompt: str, sem: asyncio.Semaphore) -> tuple[int, str, str | None]:
    async with sem:
        client = get_client()
        try:
            prompt = map_prompt.format(item=item)
            msgs = [{"role": "user", "content": prompt}]
            return 0, await client.chat(msgs), None
        except Exception as e:  # noqa: BLE001
            return 0, "", f"{type(e).__name__}: {e}"


async def run(
    items: list[str],
    map_prompt: str = DEFAULT_MAP,
    reduce_prompt: str = DEFAULT_REDUCE,
    max_concurrent: int = 10,
    timeout: float = 600.0,
) -> str:
    """Execute the Map-Reduce pipeline and return the reduced result."""
    if not items:
        raise EmptyInputError("map_reduce_run: 'items' must be a non-empty list.")
    if not 1 <= max_concurrent <= 10:
        raise EmptyInputError("max_concurrent must be between 1 and 10.")

    new_session()
    put("item_count", len(items))
    sem = asyncio.Semaphore(max_concurrent)

    try:
        async with asyncio.timeout(timeout):
            mapped = await asyncio.gather(*[_map(it, map_prompt, sem) for it in items])
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
            final = await client.chat(
                [{"role": "user", "content": reduce_prompt.format(results=mapped_text)}]
            )
    except asyncio.TimeoutError as e:
        raise ToolTimeoutError(f"map_reduce_run (reduce phase) exceeded {timeout}s") from e

    put("final", final)
    return final
