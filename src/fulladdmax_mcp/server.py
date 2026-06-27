"""FastMCP server entry point for FullADDMAX-mcp.

Exposes **4 mega tools** over the MCP stdio / streamable-http transport:

    * ``agent``     — multi-agent workflows (orchestrator / parallel / map_reduce / swarm)
    * ``knowledge`` — Obsidian vault read / write
    * ``config``    — runtime configuration & registry mutations
    * ``admin``     — read-only / status queries

Each mega tool accepts three top-level arguments::

    operation    : str   # one of the names listed in the tool's docstring
    params_json  : str   # JSON-encoded business parameters ({} for none)
    session_id   : str   # optional, top-level session for context isolation

The 28 business functions are still importable from this module
(``from fulladdmax_mcp.server import ping, configure_llm, ...``) so
the existing 200+ white-box tests continue to pass without
modification.  They are no longer exposed as MCP tools, however —
callers must use the mega tool form.

Run with::

    fulladdmax-mcp             # stdio (default; for Claude Desktop / Cursor / Trae)
    fulladdmax-mcp --transport streamable-http --host 127.0.0.1 --port 8000
    python -m fulladdmax_mcp.server --transport http
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Literal

from mcp.server.fastmcp import FastMCP

from . import __version__, server_internal as _si
from .dispatcher import dispatch
from .handlers import admin as _admin_h
from .handlers import agent as _agent_h
from .handlers import config as _config_h
from .handlers import knowledge as _knowledge_h

# Make the 28 internal functions available as module-level names
# (backward-compat for tests / direct import).
ping = _si.ping
configure_llm = _si.configure_llm
list_agent_tools = _si.list_agent_tools
unregister_agent_tool = _si.unregister_agent_tool
obsidian_list_notes = _si.obsidian_list_notes
obsidian_read_note = _si.obsidian_read_note
obsidian_search_notes = _si.obsidian_search_notes
obsidian_write_note = _si.obsidian_write_note
obsidian_append_note = _si.obsidian_append_note
configure_context_store = _si.configure_context_store
list_sessions = _si.list_sessions
get_session = _si.get_session
delete_session = _si.delete_session
purge_expired_sessions = _si.purge_expired_sessions
configure_rate_limit = _si.configure_rate_limit
reset_rate_limit = _si.reset_rate_limit
get_rate_limit_status = _si.get_rate_limit_status
get_usage_stats = _si.get_usage_stats
list_usage_records = _si.list_usage_records
reset_usage_stats = _si.reset_usage_stats
configure_pricing_override = _si.configure_pricing_override
register_swarm_agent = _si.register_swarm_agent
unregister_swarm_agent = _si.unregister_swarm_agent
list_swarm_agents = _si.list_swarm_agents
orchestrator_run = _si.orchestrator_run
parallel_agents_run = _si.parallel_agents_run
map_reduce_run = _si.map_reduce_run
swarm_run = _si.swarm_run

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("fulladdmax-mcp")

mcp = FastMCP(
    name="FullADDMAX-mcp",
    instructions=(
        "FullADDMAX-mcp: a multi-agent orchestration MCP server. "
        "Exposes four mega tools — agent, knowledge, config, admin — "
        "each with an `operation` and `params_json` argument. "
        "Always call config(operation='configure_llm', params_json='{...}') first to set credentials. "
        "Call admin(operation='ping', params_json='') to verify the server is healthy."
    ),
)


# ---------------------------------------------------------------------------
# Mega tool registrations
# ---------------------------------------------------------------------------


async def _call(handlers, operation: str, params_json: str, session_id: str) -> str:
    """Tiny helper that wraps the dispatcher and forwards the top-level session_id."""
    return await dispatch(
        handlers.HANDLERS,
        operation,
        params_json,
        session_id=session_id,
    )


@mcp.tool()
async def agent(operation: str, params_json: str = "", session_id: str = "") -> str:
    """Multi-agent workflows: orchestrator / parallel / map_reduce / swarm.

    Operations
    ----------
    orchestrator_run
        Planner + parallel workers + synthesizer.
        params: ``{"task": str, "num_workers"?: int=3, "timeout"?: float=300, "tools"?: list[str]}``
        Example::

            agent(operation="orchestrator_run",
                  params_json='{"task": "Write 3 haiku about autumn", "num_workers": 3}')

    parallel_agents_run
        Bounded parallel fan-out (max 10 concurrent).
        params: ``{"tasks": list[str], "max_concurrent"?: int=10, "timeout"?: float=300, "tools"?: list[str]}``

    map_reduce_run
        Sharded processing: map each item, then reduce.
        params: ``{"items": list[str], "map_prompt"?: str, "reduce_prompt"?: str, "max_concurrent"?: int=10, "timeout"?: float=600, "tools"?: list[str]}``

    swarm_run
        Lightweight agent handoffs with shared history.
        params: ``{"initial_agent": str, "task": str, "max_handoffs"?: int=8, "timeout"?: float=300, "tools"?: list[str], "agents_json"?: str}``
        ``agents_json`` is a JSON array string of {name, system, description}.

    auto_workflow
        Heuristic router: pick the best of the four workflows above
        from the task wording.  params: ``{"task": str, "prefer"?: str,
        "tools"?: list[str], "items"?: list[str]}``

    delegate  *recommended for parallel efficiency*
        AI-driven self-spawning of sub-agents.  Hand the framework a
        task and it will heuristically split it into independent
        sub-tasks and run them in parallel.  Use this whenever you see
        a multi-part request that can be parallelised::

            # instead of one big agent doing 5 things sequentially,
            # call delegate to let the framework split and parallelise
            agent(operation="delegate",
                  params_json='{"task": "Compare Python, Rust and Go for CLI tools"}')

        params: ``{"task": str, "children"?: list[str], "max_depth"?: int=2,
        "max_parallel"?: int=5, "split"?: str="auto", "tools"?: list[str]}``
        * ``children`` — pre-defined sub-tasks (skip the heuristic).
        * ``max_depth`` — recursion cap so sub-agents can themselves
          call ``delegate``.  Default 2.
        * ``split`` — ``"auto"`` (default), ``"always"`` (force split),
          or ``"never"`` (single agent).

    hive_run  *三省六部 + 蜂巢 — full multi-ministry cascade, no hard cap*
        Fan out N "ministries" in parallel, all attacking the same
        task from different angles, then re-fan with the critic's
        feedback so every ministry can refine.  Designed for the
        "3+ independent sub-tasks, no limit" use case::

            agent(operation="hive_run",
                  params_json='{"task": "Design a global payment system"}')
            # → spawns 吏/户/礼/兵/刑/工 ministries in wave 1
            # → 刑部 (Justice) critic output feeds back to all
            # → wave 2: every ministry refines its answer
            # → 12 sub-agents fired in <1 second

        params: ``{"task": str, "departments"?: list[str], "waves"?: int=2,
        "max_subagents"?: int=200, "max_depth"?: int|null, "tools"?: list[str]}``
        * ``departments`` — custom minister names.  Default = the
          six classical ministries (吏/户/礼/兵/刑/工).
        * ``waves`` — number of waves (wave 1 = initial fan-out,
          wave 2+ = critic-refined refinement).  Default 2, hard
          ceiling **20** (passing above raises ValueError — no
          silent truncation).
        * ``max_subagents`` — hard safety cap (default 200) on total
          sub-agents fired across all waves.  When hit, the current
          wave is skipped and the run collapses to the synthesiser.
        * ``max_depth`` — recursion cap for nested ``hive_run`` calls
          (default ``null`` = uncapped).  When the LLM tries to call
          ``hive_run`` inside ``hive_run`` and the running depth
          would exceed ``max_depth``, the nested call is downgraded
          to a single ``parallel_agents_run`` so the cascade can't
          loop forever.

    Notes
    -----
    Set ``session_id`` to bind the run to a specific context-store
    session; leave empty for a fresh short-lived session.

    Performance tip
    ---------------
    When a user request contains 3+ independent parts (e.g. "research
    A, B, and C"), call ``delegate`` instead of doing them one-by-one.
    The framework spawns up to ``max_parallel`` concurrent sub-agents
    and synthesises the result.

    For the "go wide AND deep" case (3+ perspectives on a single
    complex task), call ``hive_run`` — six ministries attack from
    different angles simultaneously, then refine.  No cap on the
    number of sub-agents, only on the budget.
    """
    return await _call(_agent_h, operation, params_json, session_id)


@mcp.tool()
async def knowledge(operation: str, params_json: str = "", session_id: str = "") -> str:
    """Obsidian vault read / write.

    Operations
    ----------
    obsidian_list_notes
        params: ``{"vault_path": str, "folder"?: str="", "limit"?: int=500}``
    obsidian_read_note
        params: ``{"vault_path": str, "path": str}``
    obsidian_search_notes
        params: ``{"vault_path": str, "keyword": str, "folder"?: str="", "case_sensitive"?: bool=false, "limit"?: int=50}``
    obsidian_write_note
        params: ``{"vault_path": str, "path": str, "body": str, "frontmatter_json"?: str="", "overwrite"?: bool=false}``
    obsidian_append_note
        params: ``{"vault_path": str, "path": str, "content": str}``

    Example::

        knowledge(operation="obsidian_list_notes",
                  params_json='{"vault_path": "D:/notes"}')
    """
    return await _call(_knowledge_h, operation, params_json, session_id)


@mcp.tool()
async def config(operation: str, params_json: str = "", session_id: str = "") -> str:
    """Runtime configuration & registry mutations (write side).

    Operations
    ----------
    configure_llm
        params: ``{"base_url": str, "api_key": str, "model"?: str="gpt-4o-mini", "temperature"?: float=0.7, "max_tokens"?: int=2048, "timeout"?: float=60, "max_retries"?: int=2}``
    configure_context_store
        params: ``{"backend"?: str="memory"|"sqlite", "sqlite_path"?: str, "ttl_seconds"?: float}``
    configure_rate_limit
        params: ``{"global_rpm"?: int, "global_tpm"?: int, "per_session_rpm"?: int, "per_session_tpm"?: int, "default_estimated_tokens"?: int}``
    configure_pricing_override
        params: ``{"model": str, "prompt_per_million": float, "completion_per_million": float}``
    register_swarm_agent
        params: ``{"name": str, "system": str, "description"?: str, "overwrite"?: bool}``
    unregister_swarm_agent
        params: ``{"name": str}``
    unregister_agent_tool
        params: ``{"name": str}``
    reset_rate_limit
        params: ``{}``
    reset_usage_stats
        params: ``{}``
    purge_expired_sessions
        params: ``{"ttl_seconds"?: float=0}``

    Example::

        config(operation="configure_llm",
               params_json='{"base_url": "https://api.openai.com/v1", "api_key": "sk-...",
                             "model": "gpt-4o-mini"}')
    """
    return await _call(_config_h, operation, params_json, session_id)


@mcp.tool()
async def admin(operation: str, params_json: str = "", session_id: str = "") -> str:
    """Read-only / status queries.

    Operations
    ----------
    ping
        params: ``{}``
    list_sessions
        params: ``{}``
    get_session
        params: ``{"session_id": str}``
    delete_session
        params: ``{"session_id": str}``
    list_agent_tools
        params: ``{}``
    list_swarm_agents
        params: ``{}``
    get_rate_limit_status
        params: ``{}``
    get_usage_stats
        params: ``{"session_id"?: str, "model"?: str, "since_ts"?: float}``
    list_usage_records
        params: ``{"session_id"?: str, "model"?: str, "since_ts"?: float, "limit"?: int=50}``

    Note: the top-level ``session_id`` argument is the *caller's*
    session (used for context isolation); the ``session_id`` inside
    ``params_json`` is the *target* session to look up / delete.

    Example::

        admin(operation="ping", params_json="")
        admin(operation="get_session",
              params_json='{"session_id": "abc123def456"}')
    """
    return await _call(_admin_h, operation, params_json, session_id)


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
    parser.add_argument(
        "panel",
        nargs="?",
        default=None,
        help=(
            "If set to 'panel', dispatch into the SVG dashboard generator. "
            "Use 'fulladdmax-mcp panel --help' for sub-options."
        ),
    )
    return parser


def _build_panel_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fulladdmax-mcp panel",
        description=(
            "Render a static SVG dashboard of the server state. "
            "Collects data by invoking the 4 mega tools in-process."
        ),
    )
    parser.add_argument(
        "--out",
        default="docs/panel.svg",
        help="Output SVG path (default: docs/panel.svg).",
    )
    parser.add_argument(
        "--theme",
        default="dark",
        choices=("dark", "light", "paper"),
        help="Colour theme (default: dark).",
    )
    parser.add_argument(
        "--lang",
        default="en",
        choices=("en", "zh"),
        help="UI language: 'en' (English) or 'zh' (Chinese).",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Serve the SVG over HTTP with auto-refresh instead of writing a static file.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Port for --serve (default: 8765).",
    )
    parser.add_argument(
        "--refresh",
        type=int,
        default=5,
        help="Refresh interval in seconds for --serve (default: 5).",
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
    # If the first positional is 'panel', skip the outer parser and go
    # straight into the panel subparser — this way flags like --out and
    # --theme don't collide with the outer (transport) parser.
    raw_argv = argv if argv is not None else sys.argv[1:]
    if raw_argv and raw_argv[0] == "panel":
        from . import panel as _panel_mod

        panel_args = _build_panel_arg_parser().parse_args(raw_argv[1:])
        asyncio.run(
            _panel_mod.run(
                out=panel_args.out,
                theme=panel_args.theme,
                lang=panel_args.lang,
                serve=panel_args.serve,
                port=panel_args.port,
                refresh=panel_args.refresh,
            )
        )
        return

    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    # Subcommand dispatch: 'fulladdmax-mcp panel ...'
    if args.panel == "panel":
        # Lazy import to avoid a circular import (panel imports `mcp`
        # from this module).
        from . import panel as _panel_mod

        # Re-parse argv minus the 'panel' positional, but keep the rest
        # of the original argv intact (handles both `fulladdmax-mcp panel`
        # and `python -m fulladdmax_mcp panel`).
        remaining = [a for a in (argv or sys.argv[1:]) if a != "panel"]
        panel_args = _build_panel_arg_parser().parse_args(remaining)
        asyncio.run(
            _panel_mod.run(
                out=panel_args.out,
                theme=panel_args.theme,
                lang=panel_args.lang,
                serve=panel_args.serve,
                port=panel_args.port,
                refresh=panel_args.refresh,
            )
        )
        return

    logging.getLogger().setLevel(args.log_level)
    transport: Transport = _normalize_transport(args.transport)

    if transport != "stdio":
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        log.info(
            "Starting FullADDMAX-mcp v%s on %s://%s:%s (transport=%s, mount=%s)",
            _si.__version__ if hasattr(_si, "__version__") else "0.5.0",
            "http",
            args.host,
            args.port,
            transport,
            args.mount_path or "(default)",
        )
    else:
        log.info("Starting FullADDMAX-mcp on stdio")

    mcp.run(transport=transport, mount_path=args.mount_path)


if __name__ == "__main__":
    main(sys.argv[1:])
