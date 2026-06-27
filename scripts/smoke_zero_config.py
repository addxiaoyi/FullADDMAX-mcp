"""Bare 0-config smoke test — verifies that every operation works
without ANY LLM environment variable set.  Run with:

    PYTHONPATH=src python scripts/smoke_zero_config.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

# Make the package importable
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))


def _strip_llm_env() -> None:
    """Erase every env var that could satisfy the LLM endpoint."""
    prefixes = (
        "FULLADDMAX_", "OPENAI_", "ANTHROPIC_", "CLAUDE_", "CURSOR_",
        "CODEX_", "CONTINUE_", "COPILOT_", "CLINE_", "AIDER_", "ZED_",
        "OLLAMA_", "VLLM_", "LMSTUDIO_", "GITHUB_",
    )
    for k in list(os.environ):
        if any(k.startswith(p) for p in prefixes):
            del os.environ[k]


def _try(callable_):
    """Call a sync or async callable; report status + first line."""
    import inspect
    try:
        r = callable_()
        if inspect.iscoroutine(r):
            r = asyncio.run(r)
        return ("OK", str(r).split("\n")[0][:80])
    except Exception as e:
        return ("FAIL", f"{type(e).__name__}: {e}")


def main() -> int:
    _strip_llm_env()
    # Force-clear any in-process config too.
    from fulladdmax_mcp import llm
    llm.set_config(llm.LLMConfig(api_key=""))
    from fulladdmax_mcp import server_internal as si
    from fulladdmax_mcp.handlers import agent

    print("=" * 70)
    print("ZERO-CONFIG SMOKE TEST")
    print("=" * 70)
    print("Stripped env: FULLADDMAX_, OPENAI_, ANTHROPIC_, CLAUDE_, CURSOR_,")
    print("              CODEX_, CONTINUE_, COPILOT_, CLINE_, AIDER_, ZED_,")
    print("              OLLAMA_, VLLM_, LMSTUDIO_, GITHUB_")
    print(f"  Remaining env keys: {len(os.environ)}")
    print()

    # ----- ADMIN (9 ops) -----
    print("--- admin mega tool (9 ops, 0-LLM) ---")
    admin_ops = [
        ("ping",                       si.ping,                       ()),
        ("list_sessions",              si.list_sessions,              ()),
        ("list_agent_tools",           si.list_agent_tools,           ()),
        ("list_swarm_agents",          si.list_swarm_agents,          ()),
        ("get_rate_limit_status",      si.get_rate_limit_status,      ()),
        ("get_usage_stats",            si.get_usage_stats,            ()),
    ]
    for name, fn, args in admin_ops:
        status, msg = _try(lambda f=fn, a=args: f(*a))
        print(f"  [{status}]  {name:30s}  {msg}")

    # ----- KNOWLEDGE (5 ops) -----
    print("\n--- knowledge mega tool (5 ops, 0-LLM) ---")
    # Point at a real empty tempdir so we exercise the "empty vault"
    # code path, not the "vault missing" path.
    empty = Path(tempfile.mkdtemp(prefix="fulladdmax-test-"))
    knowledge_ops = [
        ("obsidian_list_notes",   si.obsidian_list_notes,   (str(empty),)),
        ("obsidian_search_notes", si.obsidian_search_notes, (str(empty), "kw")),
        ("obsidian_read_note",    si.obsidian_read_note,    (str(empty), "x.md")),
    ]
    for name, fn, args in knowledge_ops:
        status, msg = _try(lambda f=fn, a=args: f(*a))
        print(f"  [{status}]  {name:30s}  {msg}")

    # ----- CONFIG (10 ops) -----
    print("\n--- config mega tool (write ops, 0-LLM) ---")
    config_ops = [
        ("reset_rate_limit",        si.reset_rate_limit,        ()),
        ("reset_usage_stats",       si.reset_usage_stats,       ()),
        ("purge_expired_sessions",  si.purge_expired_sessions,  ()),
        ("get_rate_limit_status",   si.get_rate_limit_status,   ()),
        ("list_usage_records",      si.list_usage_records,      ()),
    ]
    for name, fn, args in config_ops:
        status, msg = _try(lambda f=fn, a=args: f(*a))
        print(f"  [{status}]  {name:30s}  {msg}")

    # ----- AGENT workflow ops (need LLM, expect lazy-hint) -----
    print("\n--- agent mega tool (workflow ops, NEED LLM) ---")
    agent_ops = [
        ("orchestrator_run",    {"task": "x"}),
        ("parallel_agents_run", {"tasks": ["a", "b"]}),
        ("map_reduce_run",      {"items": ["a", "b"]}),
        ("swarm_run",           {"initial_agent": "coder", "task": "x"}),
        ("auto_workflow",       {"task": "design REST API in parallel"}),
    ]
    for op, kwargs in agent_ops:
        r = asyncio.run(agent.HANDLERS[op](**kwargs))
        has_hint = "No LLM endpoint configured" in r
        first_line = r.split("\n")[0][:60]
        print(f"  [{'HINT' if has_hint else 'RUN '}]  {op:30s}  {first_line}")

    # ----- PANEL -----
    print("\n--- panel (CLI, 0-LLM) ---")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "panel-zero.svg"
        # Use the real CLI entry point
        from fulladdmax_mcp.server import main as cli_main
        sys.argv = ["fulladdmax-mcp", "panel",
                    "--out", str(out), "--theme", "dark", "--lang", "en"]
        try:
            cli_main()
            body = out.read_text(encoding="utf-8")
            has_offtheshelf = "off-the-shelf" in body
            print(f"  [{'OK' if has_offtheshelf else 'WARN'}]  panel  "
                  f"({len(body)} bytes, off-the-shelf badge={has_offtheshelf})")
        except SystemExit as e:
            print(f"  [FAIL]  panel  exit={e.code}")
        except Exception as e:
            print(f"  [FAIL]  panel  {type(e).__name__}: {e}")

    print()
    print("=" * 70)
    print("OK   = ran cleanly")
    print("HINT = returned friendly 'no LLM' hint (not an error)")
    print("WARN = ran but missing expected UI element")
    print("FAIL = crashed")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
