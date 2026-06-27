"""Internal pure-function implementations for FullADDMAX-mcp tools.

This module holds the 28 business functions that used to be exposed
as individual ``@mcp.tool()`` decorators in :mod:`fulladdmax_mcp.server`.
They are kept here (importable by both the dispatcher handlers in
:mod:`fulladdmax_mcp.handlers` and the legacy white-box tests in
``tests/``) so that the mega-tool refactor can swap out the MCP tool
surface without touching any of the underlying logic.

The 4 mega tools (``agent`` / ``knowledge`` / ``config`` / ``admin``)
are registered separately in :mod:`fulladdmax_mcp.server` and simply
route calls into the appropriate ``HANDLERS`` dict in
:mod:`fulladdmax_mcp.handlers`.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from . import __version__
from . import context as ctx_mod
from . import mapreduce, obsidian, orchestrator, parallel, rate_limit, swarm, usage
from .errors import FullADDMAXError
from .llm import LLMConfig, get_config, set_config
from .tools import (
    DEFAULT_EXCLUDE,
    openai_tool_specs,
    registry as tool_registry,
    register_tool,
)

log = logging.getLogger("fulladdmax-mcp.internal")


# ---------------------------------------------------------------------------
# ping / configure_llm
# ---------------------------------------------------------------------------


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


def configure_llm(
    base_url: str,
    api_key: str,
    model: str = "gpt-4o-mini",
    temperature: float = 0.7,
    max_tokens: int = 2048,
    timeout: float = 60.0,
    max_retries: int = 2,
) -> str:
    """Configure the LLM endpoint used by every workflow."""
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
# tool registry
# ---------------------------------------------------------------------------


def list_agent_tools() -> str:
    """List the tools currently registered for agent function-calling."""
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
    lines.append(json.dumps(openai_tool_specs(), indent=2, ensure_ascii=False))
    lines.append("```")
    return "\n".join(lines)


def unregister_agent_tool(name: str) -> str:
    """Unregister a previously registered agent tool by name."""
    if tool_registry.unregister(name):
        return f"Unregistered: {name}"
    return f"skipped: {name!r} is not registered"


# ---------------------------------------------------------------------------
# Obsidian vault integration
# ---------------------------------------------------------------------------


def obsidian_list_notes(vault_path: str, folder: str = "", limit: int = 500) -> str:
    """List all ``.md`` notes in an Obsidian vault (or a subfolder)."""
    try:
        return obsidian.list_notes_tool(vault_path, folder=folder, limit=limit)
    except FullADDMAXError as e:
        return f"ERROR: {type(e).__name__}: {e}"


def obsidian_read_note(vault_path: str, path: str) -> str:
    """Read a single note from an Obsidian vault."""
    try:
        return obsidian.read_note_tool(vault_path, path)
    except FullADDMAXError as e:
        return f"ERROR: {type(e).__name__}: {e}"


def obsidian_search_notes(
    vault_path: str,
    keyword: str,
    folder: str = "",
    case_sensitive: bool = False,
    limit: int = 50,
) -> str:
    """Search for ``keyword`` across note bodies and frontmatter."""
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


def obsidian_write_note(
    vault_path: str,
    path: str,
    body: str,
    frontmatter_json: str = "",
    overwrite: bool = False,
) -> str:
    """Create or overwrite a note in an Obsidian vault."""
    try:
        return obsidian.write_note_tool(
            vault_path, path, body, frontmatter_json, overwrite=overwrite
        )
    except FullADDMAXError as e:
        return f"ERROR: {type(e).__name__}: {e}"


def obsidian_append_note(vault_path: str, path: str, content: str) -> str:
    """Append text to a note's body, creating the note if needed."""
    try:
        return obsidian.append_note_tool(vault_path, path, content)
    except FullADDMAXError as e:
        return f"ERROR: {type(e).__name__}: {e}"


# Expose the same five functions to the agent function-calling registry
# so workers can read / search / write Obsidian notes while executing a
# workflow.  The path-traversal guard lives in :class:`Vault`.
register_tool(obsidian.list_notes_tool, name="obsidian_list_notes")
register_tool(obsidian.read_note_tool, name="obsidian_read_note")
register_tool(obsidian.search_notes_tool, name="obsidian_search_notes")
register_tool(obsidian.write_note_tool, name="obsidian_write_note")
register_tool(obsidian.append_note_tool, name="obsidian_append_note")


# ---------------------------------------------------------------------------
# Persistent context store
# ---------------------------------------------------------------------------


def configure_context_store(
    backend: str = "memory",
    sqlite_path: str = "",
    ttl_seconds: float = 7 * 24 * 3600,
) -> str:
    """Switch the persistent context store."""
    try:
        if backend == "memory":
            ctx_mod.use_memory_store(ttl_seconds=ttl_seconds)
            return f"Configured MemoryContextStore (ttl={ttl_seconds}s)"
        if backend == "sqlite":
            if not sqlite_path:
                return "ERROR: sqlite_path is required when backend='sqlite'"
            ctx_mod.use_sqlite_store(sqlite_path, ttl_seconds=ttl_seconds)
            return f"Configured SqliteContextStore at {sqlite_path} (ttl={ttl_seconds}s)"
        return f"ERROR: unknown backend {backend!r} (use 'memory' or 'sqlite')"
    except Exception as e:  # noqa: BLE001
        return f"ERROR: {type(e).__name__}: {e}"


def list_sessions() -> str:
    """List every session currently in the store."""
    info = ctx_mod.store().list_sessions()
    if not info:
        return "No sessions in store."
    now = time.time()
    lines = [f"Sessions ({len(info)}):", ""]
    lines.append("| session_id | keys | last_access | age |")
    lines.append("|------------|------|-------------|-----|")
    for s in info:
        age = int(now - s.last_access)
        lines.append(f"| `{s.session_id}` | {s.size} | {int(s.last_access)} | {age}s |")
    lines.append("")
    lines.append("```json")
    lines.append(
        json.dumps(
            [
                {
                    "session_id": s.session_id,
                    "size": s.size,
                    "created_at": s.created_at,
                    "last_access": s.last_access,
                }
                for s in info
            ],
            indent=2,
        )
    )
    lines.append("```")
    return "\n".join(lines)


def get_session(session_id: str) -> str:
    """Return the full payload of a session as JSON."""
    try:
        snap = ctx_mod.store().snapshot(session_id)
    except Exception as e:  # noqa: BLE001
        return f"ERROR: {type(e).__name__}: {e}"
    return json.dumps(snap, ensure_ascii=False, indent=2, default=str)


def delete_session(session_id: str) -> str:
    """Delete a session and all of its keys."""
    if ctx_mod.store().delete(session_id):
        return f"deleted: {session_id}"
    return f"skipped: {session_id!r} not in store"


def purge_expired_sessions(ttl_seconds: float = 0) -> str:
    """Remove all sessions whose ``last_access`` is older than ``ttl_seconds``."""
    try:
        if ttl_seconds <= 0:
            count = ctx_mod.store().purge_expired()
        else:
            count = ctx_mod.store().purge_expired(ttl_seconds=ttl_seconds)
    except Exception as e:  # noqa: BLE001
        return f"ERROR: {type(e).__name__}: {e}"
    return f"purged: {count} session(s)"


# ---------------------------------------------------------------------------
# Token usage + rate limiting
# ---------------------------------------------------------------------------


def configure_rate_limit(
    global_rpm: int = 0,
    global_tpm: int = 0,
    per_session_rpm: int = 0,
    per_session_tpm: int = 0,
    default_estimated_tokens: int = 1024,
) -> str:
    """Configure the token-bucket rate limiter."""
    try:
        rate_limit.configure(
            global_rpm=global_rpm,
            global_tpm=global_tpm,
            per_session_rpm=per_session_rpm,
            per_session_tpm=per_session_tpm,
            default_estimated_tokens=default_estimated_tokens,
        )
    except Exception as e:  # noqa: BLE001
        return f"ERROR: {type(e).__name__}: {e}"
    return (
        f"Configured rate limit: global {global_rpm}r/{global_tpm}t per-min, "
        f"per-session {per_session_rpm}r/{per_session_tpm}t per-min "
        f"(est={default_estimated_tokens})"
    )


def reset_rate_limit() -> str:
    """Reset the rate limiter to unlimited."""
    rate_limit.reset()
    return "Rate limit reset to unlimited."


def get_rate_limit_status() -> str:
    """Return the current rate-limit configuration and bucket state."""
    snap = rate_limit.get_limiter().snapshot()
    cfg = snap["config"]
    lines = [
        f"Rate limit **{'enabled' if cfg['enabled'] else 'unlimited'}**:",
        "",
        f"- global_rpm: {cfg['global_rpm']}",
        f"- global_tpm: {cfg['global_tpm']}",
        f"- per_session_rpm: {cfg['per_session_rpm']}",
        f"- per_session_tpm: {cfg['per_session_tpm']}",
        f"- default_estimated_tokens: {cfg['default_estimated_tokens']}",
        f"- active per-session buckets: {snap['per_session_count']}",
        "",
        "```json",
        json.dumps(snap, indent=2, default=str),
        "```",
    ]
    return "\n".join(lines)


def get_usage_stats(
    session_id: str = "",
    model: str = "",
    since_ts: float = 0.0,
) -> str:
    """Aggregate token usage and cost, optionally filtered."""
    summary = usage.store().summary(
        session_id=session_id or None,
        model=model or None,
        since_ts=since_ts or None,
    )
    d = summary.to_dict()
    lines = [
        "Token usage summary:",
        "",
        f"- records: {d['records']}",
        f"- prompt_tokens: {d['prompt_tokens']}",
        f"- completion_tokens: {d['completion_tokens']}",
        f"- total_tokens: {d['total_tokens']}",
        f"- cost_usd: ${d['cost_usd']:.6f}",
        "",
        "By model:",
        "",
        "| model | records | prompt | completion | total | cost_usd |",
        "|-------|---------|--------|------------|-------|----------|",
    ]
    for m, s in d["by_model"].items():
        lines.append(
            f"| `{m}` | {s['records']} | {s['prompt_tokens']} | "
            f"{s['completion_tokens']} | {s['total_tokens']} | "
            f"${s['cost_usd']:.6f} |"
        )
    if d["by_session"]:
        lines.append("")
        lines.append("By session:")
        lines.append("")
        lines.append(
            "| session_id | records | prompt | completion | total | cost_usd |"
        )
        lines.append(
            "|------------|---------|--------|------------|-------|----------|"
        )
        for s, sd in d["by_session"].items():
            lines.append(
                f"| `{s}` | {sd['records']} | {sd['prompt_tokens']} | "
                f"{sd['completion_tokens']} | {sd['total_tokens']} | "
                f"${sd['cost_usd']:.6f} |"
            )
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(d, indent=2))
    lines.append("```")
    return "\n".join(lines)


def list_usage_records(
    session_id: str = "",
    model: str = "",
    since_ts: float = 0.0,
    limit: int = 50,
) -> str:
    """List individual usage records, newest first."""
    limit = max(1, min(limit, 1000))
    records = usage.store().list(
        session_id=session_id or None,
        model=model or None,
        since_ts=since_ts or None,
        limit=limit,
    )
    if not records:
        return "No usage records."
    lines = [
        f"Last {len(records)} usage record(s):",
        "",
        "| ts | session | model | prompt | completion | total | cost_usd |",
        "|----|---------|-------|--------|------------|-------|----------|",
    ]
    for r in records:
        lines.append(
            f"| {r.ts:.0f} | `{r.session_id}` | `{r.model}` | "
            f"{r.prompt_tokens} | {r.completion_tokens} | {r.total_tokens} | "
            f"${r.cost_usd:.6f} |"
        )
    lines.append("")
    lines.append("```json")
    lines.append(
        json.dumps(
            [
                {
                    "ts": r.ts,
                    "session_id": r.session_id,
                    "model": r.model,
                    "prompt_tokens": r.prompt_tokens,
                    "completion_tokens": r.completion_tokens,
                    "total_tokens": r.total_tokens,
                    "cost_usd": r.cost_usd,
                }
                for r in records
            ],
            indent=2,
        )
    )
    lines.append("```")
    return "\n".join(lines)


def reset_usage_stats() -> str:
    """Delete all stored usage records. The pricing table is preserved."""
    usage.store().clear()
    return "Usage records cleared."


def configure_pricing_override(
    model: str,
    prompt_per_million: float,
    completion_per_million: float,
) -> str:
    """Override or add a model's pricing (USD per 1M tokens)."""
    if prompt_per_million < 0 or completion_per_million < 0:
        return "ERROR: prices must be >= 0"
    p = usage.ModelPricing(
        model=model,
        prompt_per_million=prompt_per_million,
        completion_per_million=completion_per_million,
    )
    usage.store().set_pricing(model, p)
    return (
        f"Pricing for {model!r}: ${prompt_per_million}/1M prompt, "
        f"${completion_per_million}/1M completion"
    )


# ---------------------------------------------------------------------------
# Dynamic Swarm agent registry
# ---------------------------------------------------------------------------


def register_swarm_agent(
    name: str,
    system: str,
    description: str = "",
    overwrite: bool = False,
) -> str:
    """Register a custom Swarm agent profile."""
    try:
        swarm.register_swarm_agent(
            name=name, system=system, description=description, overwrite=overwrite
        )
    except FullADDMAXError as e:
        return f"ERROR: {type(e).__name__}: {e}"
    action = "updated" if overwrite and name in swarm.registry else "registered"
    return f"{action}: {name} (total: {len(swarm.registry)} agent(s))"


def unregister_swarm_agent(name: str) -> str:
    """Remove a Swarm agent profile from the module-level registry."""
    if swarm.unregister_swarm_agent(name):
        return f"unregistered: {name} (remaining: {len(swarm.registry)} agent(s))"
    return f"skipped: {name!r} is not registered"


def list_swarm_agents() -> str:
    """List every Swarm agent currently registered."""
    agents = swarm.list_swarm_agents()
    if not agents:
        return "No swarm agents registered. Call register_swarm_agent to add one."
    lines = [f"Registered swarm agents ({len(agents)}):", ""]
    for a in agents:
        lines.append(f"- **{a.name}** — {a.description or '(no description)'}")
    lines.append("")
    lines.append("```json")
    lines.append(
        json.dumps(
            [
                {"name": a.name, "system": a.system, "description": a.description}
                for a in agents
            ],
            indent=2,
            ensure_ascii=False,
        )
    )
    lines.append("```")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------


async def orchestrator_run(
    task: str,
    num_workers: int = 3,
    timeout: float = 300.0,
    tools: list[str] | None = None,
    session_id: str = "",
) -> str:
    """Orchestrator-Workers workflow."""
    if session_id:
        try:
            ctx_mod.bind(session_id)
        except Exception as e:  # noqa: BLE001
            return f"ERROR: invalid session_id {session_id!r}: {e}"
    try:
        return await orchestrator.run(
            task, num_workers=num_workers, timeout=timeout, tools=tools
        )
    except FullADDMAXError as e:
        return f"ERROR: {type(e).__name__}: {e}"


async def parallel_agents_run(
    tasks: list[str],
    max_concurrent: int = 10,
    timeout: float = 300.0,
    tools: list[str] | None = None,
    session_id: str = "",
) -> str:
    """Bounded parallel agent fan-out."""
    if session_id:
        try:
            ctx_mod.bind(session_id)
        except Exception as e:  # noqa: BLE001
            return f"ERROR: invalid session_id {session_id!r}: {e}"
    try:
        return await parallel.run(
            tasks, max_concurrent=max_concurrent, timeout=timeout, tools=tools
        )
    except FullADDMAXError as e:
        return f"ERROR: {type(e).__name__}: {e}"


async def map_reduce_run(
    items: list[str],
    map_prompt: str = "",
    reduce_prompt: str = "",
    max_concurrent: int = 10,
    timeout: float = 600.0,
    tools: list[str] | None = None,
    session_id: str = "",
) -> str:
    """Map-Reduce pipeline."""
    if session_id:
        try:
            ctx_mod.bind(session_id)
        except Exception as e:  # noqa: BLE001
            return f"ERROR: invalid session_id {session_id!r}: {e}"
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


async def swarm_run(
    initial_agent: str,
    task: str,
    max_handoffs: int = 8,
    timeout: float = 300.0,
    tools: list[str] | None = None,
    agents_json: str = "",
    session_id: str = "",
) -> str:
    """Swarm multi-agent collaboration with lightweight handoffs."""
    if session_id:
        try:
            ctx_mod.bind(session_id)
        except Exception as e:  # noqa: BLE001
            return f"ERROR: invalid session_id {session_id!r}: {e}"
    agents: dict[str, swarm.Agent] | None = None
    if agents_json and agents_json.strip():
        try:
            agents = swarm.parse_agents_json(agents_json)
        except FullADDMAXError as e:
            return f"ERROR: {type(e).__name__}: {e}"

    effective_agents = (
        agents if agents is not None else swarm.registry.snapshot()
    )
    log.info(
        "swarm_run: initial_agent=%r task=%r max_handoffs=%d "
        "agents_json=%s effective_agents=%s",
        initial_agent,
        task,
        max_handoffs,
        repr(agents_json) if agents_json else "<empty -> use registry>",
        sorted(effective_agents),
    )

    try:
        return await swarm.run(
            initial_agent,
            task,
            max_handoffs=max_handoffs,
            timeout=timeout,
            tools=tools,
            agents=agents,
        )
    except FullADDMAXError as e:
        return f"ERROR: {type(e).__name__}: {e}"
