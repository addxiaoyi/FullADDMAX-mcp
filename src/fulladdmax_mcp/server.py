"""FastMCP server entry point for FullADDMAX-mcp.

Exposes six tools over the MCP stdio transport:

    * ``ping``                    - health check
    * ``configure_llm``          - set the OpenAI-compatible endpoint
    * ``orchestrator_run``       - Orchestrator-Workers workflow
    * ``parallel_agents_run``    - bounded parallel agent fan-out
    * ``map_reduce_run``         - sharded Map-Reduce pipeline
    * ``swarm_run``              - lightweight agent handoffs

Run with::

    fulladdmax-mcp             # stdio (default)
    python -m fulladdmax_mcp   # same
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from . import __version__
from . import mapreduce, orchestrator, parallel, swarm
from .errors import FullADDMAXError
from .llm import LLMConfig, get_config, set_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("fulladdmax-mcp")

mcp = FastMCP(
    name="FullADDMAX-mcp",
    instructions=(
        "FullADDMAX-mcp: a multi-agent orchestration MCP server. "
        "Provides four workflows: orchestrator_run (planner + parallel workers + synthesizer), "
        "parallel_agents_run (bounded fan-out, max 10 concurrent), "
        "map_reduce_run (sharded processing), and swarm_run (agent handoffs with shared history). "
        "Always call configure_llm(base_url, api_key, model) first to set credentials. "
        "Call ping() to verify the server is healthy and to inspect the current config."
    ),
)


# ---------------------------------------------------------------------------
# Configuration / health
# ---------------------------------------------------------------------------


@mcp.tool()
def ping() -> str:
    """Health check. Returns the server version and the current LLM config (with the API key redacted)."""
    cfg = get_config()
    return (
        f"FullADDMAX-mcp v{__version__} OK\n"
        f"base_url  : {cfg.base_url}\n"
        f"model     : {cfg.model}\n"
        f"api_key   : {(cfg.api_key[:4] + '****') if cfg.api_key else '(unset)'}\n"
        f"timeout   : {cfg.timeout}s\n"
        f"retries   : {cfg.max_retries}"
    )


@mcp.tool()
def configure_llm(
    base_url: str,
    api_key: str,
    model: str = "gpt-4o-mini",
    temperature: float = 0.7,
    max_tokens: int = 2048,
    timeout: float = 60.0,
    max_retries: int = 2,
) -> str:
    """Configure the LLM endpoint used by every workflow.

    Call this once before using any other workflow tool. Subsequent calls
    replace the current configuration.

    Args:
        base_url: OpenAI-compatible base URL, e.g. ``https://api.openai.com/v1``,
            ``https://openrouter.ai/api/v1``, ``https://api.deepseek.com/v1``,
            or a local ``http://localhost:11434/v1`` for Ollama.
        api_key: API key for the endpoint.
        model: Model name (e.g. ``gpt-4o-mini``, ``deepseek-chat``,
            ``qwen2.5-72b-instruct``).
        temperature: Sampling temperature (0-2).
        max_tokens: Maximum tokens per LLM response.
        timeout: Per-request timeout in seconds.
        max_retries: Number of retries on transient failures (5xx / network).
    """
    if not base_url or not base_url.strip():
        return "ERROR: base_url is required."
    if not api_key or not api_key.strip():
        return "ERROR: api_key is required."

    set_config(
        LLMConfig(
            base_url=base_url.rstrip("/"),
            api_key=api_key,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            max_retries=max_retries,
        )
    )
    log.info("LLM configured: %s", get_config().masked())
    return f"Configured: model={model} base_url={base_url.rstrip('/')}"


# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------


@mcp.tool()
async def orchestrator_run(
    task: str,
    num_workers: int = 3,
    timeout: float = 300.0,
    ctx: Context | None = None,
) -> str:
    """Orchestrator-Workers: a planner agent decomposes ``task`` into
    ``num_workers`` self-contained subtasks, workers run them in parallel,
    and a synthesizer merges the results.

    Args:
        task: The high-level task to accomplish.
        num_workers: Number of parallel workers (1-10, default 3).
        timeout: Overall timeout in seconds.
    """
    if ctx is not None:
        await ctx.info(f"orchestrator_run start: workers={num_workers}")
    try:
        return await orchestrator.run(task, num_workers=num_workers, timeout=timeout)
    except FullADDMAXError as e:
        return f"ERROR: {type(e).__name__}: {e}"


@mcp.tool()
async def parallel_agents_run(
    tasks: list[str],
    max_concurrent: int = 10,
    timeout: float = 300.0,
) -> str:
    """Run multiple independent tasks in parallel (max 10 concurrent).

    Each task gets the same shared session context. A single failure is
    recorded as ``## Task #N (ERROR)`` but does not abort the batch.

    Args:
        tasks: List of independent task prompts (1-10 entries).
        max_concurrent: Concurrency cap (1-10).
        timeout: Overall timeout in seconds.
    """
    try:
        return await parallel.run(
            tasks, max_concurrent=max_concurrent, timeout=timeout
        )
    except FullADDMAXError as e:
        return f"ERROR: {type(e).__name__}: {e}"


@mcp.tool()
async def map_reduce_run(
    items: list[str],
    map_prompt: str = "",
    reduce_prompt: str = "",
    max_concurrent: int = 10,
    timeout: float = 600.0,
) -> str:
    """Map-Reduce: process ``items`` in parallel (map), then merge (reduce).

    ``map_prompt`` must contain the placeholder ``{item}``; the current item
    is substituted in. ``reduce_prompt`` must contain ``{results}``; the
    merged map outputs are substituted in. Both default to a generic prompt
    that works for most text-sharding tasks.

    Args:
        items: List of input items to process.
        map_prompt: Template containing ``{item}``.
        reduce_prompt: Template containing ``{results}``.
        max_concurrent: Map-phase concurrency (1-10).
        timeout: Overall timeout in seconds.
    """
    try:
        return await mapreduce.run(
            items,
            map_prompt=map_prompt or mapreduce.DEFAULT_MAP,
            reduce_prompt=reduce_prompt or mapreduce.DEFAULT_REDUCE,
            max_concurrent=max_concurrent,
            timeout=timeout,
        )
    except FullADDMAXError as e:
        return f"ERROR: {type(e).__name__}: {e}"


@mcp.tool()
async def swarm_run(
    initial_agent: str,
    task: str,
    max_handoffs: int = 8,
    timeout: float = 300.0,
) -> str:
    """Swarm multi-agent collaboration with lightweight handoffs.

    Starts at ``initial_agent`` (one of ``researcher`` / ``coder`` / ``critic``
    / ``writer``). Each agent replies with strict JSON
    ``{"next": <agent_name|DONE>, "message": <string>}``; the orchestrator
    routes the message to the next agent until the LLM emits ``DONE`` or
    ``max_handoffs`` is reached.

    Args:
        initial_agent: Starting agent name.
        task: The user task to accomplish.
        max_handoffs: Maximum agent-to-agent handoffs (default 8).
        timeout: Overall timeout in seconds.
    """
    try:
        return await swarm.run(
            initial_agent, task, max_handoffs=max_handoffs, timeout=timeout
        )
    except FullADDMAXError as e:
        return f"ERROR: {type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the FullADDMAX-mcp server on stdio."""
    log.info("Starting FullADDMAX-mcp v%s", __version__)
    mcp.run()


if __name__ == "__main__":
    main()
