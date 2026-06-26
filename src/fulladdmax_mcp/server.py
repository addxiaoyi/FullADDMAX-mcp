"""FastMCP server entry point for FullADDMAX-mcp.

Exposes six tools over the MCP stdio transport:

    * ``ping``                    - health check
    * ``configure_llm``          - set the OpenAI-compatible endpoint
    * ``orchestrator_run``       - Orchestrator-Workers workflow
    * ``parallel_agents_run``    - bounded parallel agent fan-out
    * ``map_reduce_run``         - sharded Map-Reduce pipeline
    * ``swarm_run``              - lightweight agent handoffs

Run with::

    fulladdmax-mcp             # stdio (default; for Claude Desktop / Cursor / Trae)
    fulladdmax-mcp --transport streamable-http --host 127.0.0.1 --port 8000
    python -m fulladdmax_mcp.server --transport http
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Literal

from mcp.server.fastmcp import Context, FastMCP

from . import __version__
from . import mapreduce, obsidian, orchestrator, parallel, swarm
from .errors import FullADDMAXError
from .llm import LLMConfig, get_config, set_config
from .tools import (
    DEFAULT_EXCLUDE,
    ToolRegistry,
    openai_tool_specs,
    registry as tool_registry,
    register_tool,
)

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
# Tool registry
# ---------------------------------------------------------------------------


@mcp.tool()
def list_agent_tools() -> str:
    """List the tools currently registered for agent function-calling.

    The agent workflows (orchestrator_run, parallel_agents_run, etc.) can
    optionally pass these tools to the LLM so it can call them mid-loop.
    Built-in orchestration tools (orchestrator_run, parallel_agents_run,
    map_reduce_run, swarm_run, ping, configure_llm) are excluded by default
    to prevent self-recursion.

    Returns a Markdown report, one ``- name`` bullet per tool, plus a
    JSON block of the OpenAI tool specs that are actually sent to the LLM.
    """
    if not tool_registry.names():
        return (
            "No agent tools registered. Use `register_tool` to add one, "
            "or import fulladdmax_mcp.tools in your own MCP server."
        )
    lines = ["Registered agent tools:", ""]
    for name in tool_registry.names():
        reg = tool_registry.get(name)
        assert reg is not None
        lines.append(f"- **{name}** — {reg.description or '(no description)'}")
    lines.append("")
    lines.append("OpenAI specs (excluded: " + ", ".join(sorted(DEFAULT_EXCLUDE)) + "):")
    lines.append("```json")
    import json as _json

    lines.append(_json.dumps(openai_tool_specs(), indent=2, ensure_ascii=False))
    lines.append("```")
    return "\n".join(lines)


@mcp.tool()
def unregister_agent_tool(name: str) -> str:
    """Unregister a previously registered agent tool by name.

    No-op (and returns 'skipped') if the tool was not registered.
    """
    if tool_registry.unregister(name):
        return f"Unregistered: {name}"
    return f"skipped: {name!r} is not registered"


# ---------------------------------------------------------------------------
# Obsidian vault integration
# ---------------------------------------------------------------------------


@mcp.tool()
def obsidian_list_notes(vault_path: str, folder: str = "", limit: int = 500) -> str:
    """List all ``.md`` notes in an Obsidian vault (or a subfolder).

    Each tool takes ``vault_path`` as a parameter so one server can
    serve many vaults in the same session. Paths are validated against
    the vault root to prevent directory traversal.
    """
    try:
        return obsidian.list_notes_tool(vault_path, folder=folder, limit=limit)
    except FullADDMAXError as e:
        return f"ERROR: {type(e).__name__}: {e}"


@mcp.tool()
def obsidian_read_note(vault_path: str, path: str) -> str:
    """Read a single note from an Obsidian vault.

    Returns a Markdown report with the frontmatter block and the body.
    """
    try:
        return obsidian.read_note_tool(vault_path, path)
    except FullADDMAXError as e:
        return f"ERROR: {type(e).__name__}: {e}"


@mcp.tool()
def obsidian_search_notes(
    vault_path: str,
    keyword: str,
    folder: str = "",
    case_sensitive: bool = False,
    limit: int = 50,
) -> str:
    """Search for ``keyword`` across note bodies and frontmatter.

    Returns a Markdown list of ``path — snippet`` lines. Search is
    case-insensitive by default.
    """
    try:
        return obsidian.search_notes_tool(
            vault_path,
            keyword,
            folder=folder,
            case_sensitive=case_sensitive,
            limit=limit,
        )
    except FullADDMAXError as e:
        return f"ERROR: {type(e).__name__}: {e}"


@mcp.tool()
def obsidian_write_note(
    vault_path: str,
    path: str,
    body: str,
    frontmatter_json: str = "",
    overwrite: bool = False,
) -> str:
    """Create or overwrite a note in an Obsidian vault.

    ``frontmatter_json`` is a JSON object string (e.g.
    ``'{"tags": ["work"], "status": "draft"}'``). Leave empty to skip
    the frontmatter block. Fails if the note exists and ``overwrite``
    is False.
    """
    try:
        return obsidian.write_note_tool(
            vault_path, path, body, frontmatter_json, overwrite=overwrite
        )
    except FullADDMAXError as e:
        return f"ERROR: {type(e).__name__}: {e}"


@mcp.tool()
def obsidian_append_note(vault_path: str, path: str, content: str) -> str:
    """Append text to a note's body, creating the note if needed.

    Useful for incrementally building daily notes, research trails, or
    agent run logs. Existing frontmatter is preserved.
    """
    try:
        return obsidian.append_note_tool(vault_path, path, content)
    except FullADDMAXError as e:
        return f"ERROR: {type(e).__name__}: {e}"


# Also expose the same five functions to the agent function-calling
# registry so workers can read / search / write Obsidian notes while
# executing a workflow. The path-traversal guard lives in :class:`Vault`.
register_tool(obsidian.list_notes_tool, name="obsidian_list_notes")
register_tool(obsidian.read_note_tool, name="obsidian_read_note")
register_tool(obsidian.search_notes_tool, name="obsidian_search_notes")
register_tool(obsidian.write_note_tool, name="obsidian_write_note")
register_tool(obsidian.append_note_tool, name="obsidian_append_note")


# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------


@mcp.tool()
async def orchestrator_run(
    task: str,
    num_workers: int = 3,
    timeout: float = 300.0,
    tools: list[str] | None = None,
    ctx: Context | None = None,
) -> str:
    """Orchestrator-Workers: a planner agent decomposes ``task`` into
    ``num_workers`` self-contained subtasks, workers run them in parallel,
    and a synthesizer merges the results.

    Args:
        task: The high-level task to accomplish.
        num_workers: Number of parallel workers (1-10, default 3).
        timeout: Overall timeout in seconds.
        tools: Optional whitelist of agent tool names the workers may
            call. ``None`` (default) = every registered tool. ``[]`` =
            disable tool-calling entirely. See ``list_agent_tools``.
    """
    if ctx is not None:
        await ctx.info(f"orchestrator_run start: workers={num_workers}")
    try:
        return await orchestrator.run(
            task, num_workers=num_workers, timeout=timeout, tools=tools
        )
    except FullADDMAXError as e:
        return f"ERROR: {type(e).__name__}: {e}"


@mcp.tool()
async def parallel_agents_run(
    tasks: list[str],
    max_concurrent: int = 10,
    timeout: float = 300.0,
    tools: list[str] | None = None,
) -> str:
    """Run multiple independent tasks in parallel (max 10 concurrent).

    Each task gets the same shared session context. A single failure is
    recorded as ``## Task #N (ERROR)`` but does not abort the batch.

    Args:
        tasks: List of independent task prompts (1-10 entries).
        max_concurrent: Concurrency cap (1-10).
        timeout: Overall timeout in seconds.
        tools: Optional whitelist of tool names each task may call.
            ``None`` (default) = every registered tool. ``[]`` = no
            tool-calling. See ``list_agent_tools``.
    """
    try:
        return await parallel.run(
            tasks, max_concurrent=max_concurrent, timeout=timeout, tools=tools
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
    tools: list[str] | None = None,
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
        tools: Optional whitelist of tool names the map / reduce phases
            may call. ``None`` (default) = every registered tool. ``[]``
            = no tool-calling.
    """
    try:
        return await mapreduce.run(
            items,
            map_prompt=map_prompt or mapreduce.DEFAULT_MAP,
            reduce_prompt=reduce_prompt or mapreduce.DEFAULT_REDUCE,
            max_concurrent=max_concurrent,
            timeout=timeout,
            tools=tools,
        )
    except FullADDMAXError as e:
        return f"ERROR: {type(e).__name__}: {e}"


@mcp.tool()
async def swarm_run(
    initial_agent: str,
    task: str,
    max_handoffs: int = 8,
    timeout: float = 300.0,
    tools: list[str] | None = None,
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
        tools: Optional whitelist of tool names each agent may call.
            ``None`` (default) = every registered tool. ``[]`` = no
            tool-calling.
    """
    try:
        return await swarm.run(
            initial_agent, task, max_handoffs=max_handoffs, timeout=timeout, tools=tools
        )
    except FullADDMAXError as e:
        return f"ERROR: {type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

Transport = Literal["stdio", "sse", "streamable-http"]


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fulladdmax-mcp",
        description=(
            "FullADDMAX-mcp: multi-agent orchestration MCP server. "
            "Runs as a stdio MCP server (default) or as an HTTP/SSE server."
        ),
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "sse", "streamable-http", "http"),
        default="stdio",
        help=(
            "MCP transport to use. 'stdio' (default) for Claude Desktop / "
            "Cursor / Trae; 'streamable-http' (alias 'http') for HTTP clients. "
            "'sse' is kept for backward compatibility."
        ),
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind for HTTP transports (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind for HTTP transports (default: 8000).",
    )
    parser.add_argument(
        "--mount-path",
        default=None,
        help=(
            "Optional URL mount path for HTTP transports "
            "(e.g. '/mcp'). Defaults to FastMCP's built-in path."
        ),
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Python logging level (default: INFO).",
    )
    return parser


def _normalize_transport(value: str) -> Transport:
    """Map the user-facing alias ``http`` onto ``streamable-http``."""
    if value == "http":
        return "streamable-http"
    # ``value`` is one of "stdio" / "sse" / "streamable-http" by the choices.
    return value  # type: ignore[return-value]


def main(argv: list[str] | None = None) -> None:
    """Run the FullADDMAX-mcp server.

    Parses CLI arguments, configures the FastMCP settings (host/port for
    HTTP transports) and starts the chosen transport.

    Examples::

        fulladdmax-mcp                              # stdio (default)
        fulladdmax-mcp --transport streamable-http  # HTTP on 127.0.0.1:8000
        fulladdmax-mcp --transport http --host 0.0.0.0 --port 9000
    """
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    logging.getLogger().setLevel(args.log_level)
    transport: Transport = _normalize_transport(args.transport)

    if transport != "stdio":
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        log.info(
            "Starting FullADDMAX-mcp v%s on %s://%s:%s (transport=%s, mount=%s)",
            __version__,
            "http",
            args.host,
            args.port,
            transport,
            args.mount_path or "(default)",
        )
    else:
        log.info("Starting FullADDMAX-mcp v%s on stdio", __version__)

    mcp.run(transport=transport, mount_path=args.mount_path)


if __name__ == "__main__":
    main(sys.argv[1:])
