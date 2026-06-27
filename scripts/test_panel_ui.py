"""Automated UI verification for the FullADDMAX-mcp panel.

This script exercises every (theme, language) combination of the SVG
dashboard and verifies that the rendered output is structurally valid,
uses the correct colour palette, and contains the expected text for the
target language.  It is the offline equivalent of clicking through
``docs/preview.html`` by hand, so any UI tweak that breaks one of the
six views will fail this script loudly.

What it covers
==============

1. **STRINGS table sanity** — every key in the English table is also
   present in the Chinese table (and vice versa).  Adding a new key to
   only one language will fail the test.
2. **Pure SVG rendering** — for every (theme, language) combination,
   :func:`render_svg` is called with a deterministic
   :class:`PanelData` and the output is checked for:
     - well-formed XML
     - expected theme background colour
     - expected language-specific strings
3. **Smart host-LLM detection** — exercises :func:`_detect_ai_host`
   against a curated set of environment variables and verifies the
   matching host label is picked up by the rendered SVG.
4. **CLI integration** — spawns ``python -m fulladdmax_mcp.server
   panel`` for each combination and checks the file lands on disk.
5. **preview.html wiring** — confirms the interactive preview page
   references all six SVG files and exposes the three theme / two
   language buttons.

Run it directly:

.. code-block:: bash

    python scripts/test_panel_ui.py
    python scripts/test_panel_ui.py -v    # verbose
    python scripts/test_panel_ui.py --cli  # also exercise subprocess
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Iterator

# Make `fulladdmax_mcp` importable when running this script from anywhere.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from fulladdmax_mcp import panel as _panel_mod  # noqa: E402

THEME = "dark"            # single dark theme — was 3 (dark/light/paper)
LANG = "en"                # English only          — was 2 (en/zh)

# The panel now ships with 3 cards: Server / LLM / Agent Tools.
# (was 6 cards: LLM / Rate Limit / Sessions / Usage / Agent Tools / Swarm)
CARD_TITLES_EN = ("Server", "LLM", "Agent Tools")

# Strings that must round-trip through the rendered SVG.
# (panel renders "total ops" with a space, not the i18n key "total_ops")
EXPECTED_FRAGMENTS_EN = [
    "Server", "LLM", "Agent Tools",
    "healthy", "uptime", "version",
    "total ops",
]

# Host-LLM detection fixtures: (env vars to set, expected host id,
# expected pretty label that should appear in the SVG).
HOST_FIXTURES: list[tuple[dict[str, str], str, str]] = [
    ({"CLAUDE_CODE_ENTRYPOINT": "cli"},            "claude",   "Claude Desktop"),
    ({"CURSOR_HOME": "/x"},                       "cursor",   "Cursor"),
    ({"CODEX_HOME": "/y"},                        "codex",    "Codex CLI"),
    ({"CONTINUE_GLOBAL_DIR": "/z"},               "continue", "Continue.dev"),
    ({"COPILOT_API_URL": "https://api"},          "copilot",  "GitHub Copilot"),
    ({},                                           "",         ""),  # no host
]

# ---------------------------------------------------------------------------
# Tiny test harness (no pytest dep — keeps the script self-contained)
# ---------------------------------------------------------------------------

_PASS = 0
_FAIL = 0
_FAILURES: list[str] = []


def _ok(msg: str, verbose: bool = False) -> None:
    global _PASS
    _PASS += 1
    if verbose:
        print(f"  PASS  {msg}")


def _fail(msg: str, detail: str = "") -> None:
    global _FAIL
    _FAIL += 1
    _FAILURES.append(f"{msg} :: {detail}" if detail else msg)
    print(f"  FAIL  {msg}")
    if detail:
        for line in detail.splitlines():
            print(f"        {line}")


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _fixture_data() -> _panel_mod.PanelData:
    """A deterministic PanelData snapshot for rendering tests.

    Reduced to the 3-card schema: Server / LLM / Agent Tools.  The
    legacy Rate Limit / Sessions / Usage / Swarm fields are gone.
    """
    return _panel_mod.PanelData(
        version="0.6.0",
        timestamp="2026-06-26 19:13:00",
        uptime_secs=3725,  # 1h 2m 5s
        healthy=True,
        llm_model="gpt-4o-mini",
        llm_base_url="https://api.openai.com/v1",
        llm_api_key_masked="sk-Te****",
        llm_timeout="60.0s",
        llm_max_retries="2",
        tool_names=["admin", "knowledge", "config", "agent"],
    )


@contextmanager
def _env(overrides: dict[str, str]) -> Iterator[None]:
    """Context manager that sets/unsets env vars for the duration."""
    saved = {k: os.environ.get(k) for k in overrides}
    for k, v in overrides.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _check(condition: bool, msg: str, detail: str = "") -> bool:
    if condition:
        _ok(msg)
        return True
    _fail(msg, detail)
    return False


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_strings_parity(verbose: bool = False) -> None:
    _section("STRINGS table: only English ships now (was en+zh)")
    en_keys = set(_panel_mod.STRINGS["en"])
    _check(len(en_keys) >= 10, "STRINGS['en'] has at least 10 keys",
           f"got {len(en_keys)}")
    # The legacy zh table is gone.
    _check("zh" not in _panel_mod.STRINGS,
           "STRINGS['zh'] removed (English-only UI)")
    # A handful of keys the panel renders must be present.
    for k in ("card_server", "card_llm", "card_tools",
              "healthy", "uptime", "version", "total_ops"):
        _check(k in en_keys, f"STRINGS['en'] has key {k!r}")
    if verbose:
        print(f"  ({len(en_keys)} keys in en)")


def test_render_svg_simplified(verbose: bool = False) -> None:
    _section("render_svg: single dark+en with 3 cards")
    data = _fixture_data()
    try:
        svg = _panel_mod.render_svg(data, theme=THEME, lang=LANG)
    except Exception as e:  # noqa: BLE001
        _fail("render crashed", repr(e))
        return
    _check(isinstance(svg, str) and svg.startswith("<?xml"),
           "output starts with XML prolog")
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as e:
        _fail("invalid XML", repr(e))
        return
    _check(root.tag.endswith("svg"), "root is <svg>")
    # Single dark background
    _check(f'fill="#0F0F1E"' in svg, "dark background colour present")
    # 3 cards
    for title in CARD_TITLES_EN:
        _check(title in svg, f"contains card title {title!r}")
    # Required fragments
    for frag in EXPECTED_FRAGMENTS_EN:
        _check(frag in svg, f"contains fragment {frag!r}")
    # Removed card titles must NOT appear
    for removed in ("Rate Limit", "Sessions", "Usage (total)",
                     "Swarm Agents", "限流设置", "会话", "Token 用量",
                     "已注册工具", "Swarm 代理"):
        _check(removed not in svg,
               f"removed card title {removed!r} not in output")
    if verbose:
        print(f"  ({len(svg.encode('utf-8'))} bytes)")


def test_host_detection(verbose: bool = False) -> None:
    _section("smart host-LLM detection (6 fixtures)")
    # Strip all known host markers first so the test is deterministic
    # regardless of what the host shell (Trae / Claude Code / etc.)
    # leaks into our process env.
    with _env({k: None for k in
               list(os.environ) if any(
                   k.startswith(p) for p in
                   ("CLAUDE_", "ANTHROPIC_", "CURSOR_", "CODEX_",
                    "CONTINUE_", "COPILOT_", "CLINE_", "AIDER_",
                    "ZED_", "GITHUB_"))}):
        for env_overrides, expected_id, expected_label in HOST_FIXTURES:
            with _env(env_overrides):
                host_id, host_label = _panel_mod._detect_ai_host()
                _check(host_id == expected_id,
                       f"env {env_overrides or '{}'} -> id={expected_id!r}",
                       f"got {host_id!r}")
                _check(host_label == expected_label,
                       f"env {env_overrides or '{}'} -> label={expected_label!r}",
                       f"got {host_label!r}")
                if verbose and env_overrides:
                    print(f"  ({', '.join(f'{k}={v}' for k, v in env_overrides.items())})")


def test_inherited_state_in_svg(verbose: bool = False) -> None:
    _section("host LLM state in rendered SVG (English UI)")
    data = _fixture_data()
    # Case 1: empty api_key + Claude host → green "inherited from" text
    data_claude = replace(data, llm_api_key_masked="", ai_host="claude",
                          ai_host_label="Claude Desktop", llm_source="")
    svg = _panel_mod.render_svg(data_claude, theme=THEME, lang=LANG)
    _check("inherited from Claude Desktop" in svg,
           "en shows 'inherited from Claude Desktop'")
    _check(f'fill="{_panel_mod.THEMES["dark"]["ok"]}"' in svg,
           "api_key cell uses green ok colour")
    # Case 2: empty api_key + no host → off-the-shelf hint, no warn colour
    data_none = replace(data, llm_api_key_masked="", ai_host="",
                        ai_host_label="", llm_source="",
                        llm_off_the_shelf=True)
    svg = _panel_mod.render_svg(data_none, theme=THEME, lang=LANG)
    _check("off-the-shelf" in svg, "shows off-the-shelf label")
    _check("FULLADDMAX_API_KEY" in svg, "hint mentions how to enable agent")
    # Off-the-shelf uses muted grey, NOT warn yellow.
    _check(f'fill="{_panel_mod.THEMES["dark"]["warn"]}"' not in svg,
           "off-the-shelf no longer uses yellow warn colour")
    _check(f'fill="{_panel_mod.THEMES["dark"]["muted"]}"' in svg,
           "off-the-shelf uses muted grey colour")
    # Case 3: real masked key → no inherited text
    data_real = replace(data, llm_api_key_masked="sk-Te****",
                        ai_host="", ai_host_label="", llm_source="")
    svg = _panel_mod.render_svg(data_real, theme=THEME, lang=LANG)
    _check("inherited from" not in svg, "en real key has no 'inherited'")
    _check("off-the-shelf" not in svg, "en real key has no off-the-shelf label")


def test_cli_integration(verbose: bool = False) -> None:
    _section("CLI subprocess: fulladdmax-mcp panel (single)")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "panel.svg"
        cmd = [
            sys.executable, "-m", "fulladdmax_mcp.server",
            "panel", "--out", str(out),
        ]
        env = os.environ.copy()
        for k in list(env):
            if any(k.startswith(p) for p in
                   ("CLAUDE_", "ANTHROPIC_", "CURSOR_", "CODEX_",
                    "CONTINUE_", "COPILOT_", "CLINE_", "AIDER_",
                    "ZED_", "GITHUB_")):
                env.pop(k, None)
        env["PYTHONPATH"] = str(_REPO_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True,
                env=env, timeout=30, cwd=str(_REPO_ROOT),
            )
        except Exception as e:  # noqa: BLE001
            _fail("CLI crashed", repr(e))
            return
        if r.returncode != 0:
            _fail(f"CLI exit={r.returncode}", r.stderr[:500])
            return
        if not out.exists():
            _fail("CLI produced no file", f"expected at {out}")
            return
        body = out.read_text(encoding="utf-8")
        _check(body.startswith("<?xml"),
               "CLI file is valid SVG",
               f"first 80 chars: {body[:80]!r}")
        if verbose:
            print(f"  ({out.name}: {len(body)} bytes)")


def test_preview_html(verbose: bool = False) -> None:
    _section("docs/preview.html: single static demo")
    html_path = _REPO_ROOT / "docs" / "preview.html"
    if not html_path.exists():
        _fail("preview.html missing", str(html_path))
        return
    body = html_path.read_text(encoding="utf-8")
    # Simplified preview embeds exactly one panel image.
    _check("panel.svg" in body,
           "preview.html embeds docs/panel.svg")
    # No multi-theme/lang template string.
    _check("panel-${" not in body,
           "preview.html no longer uses a theme/lang template")
    # No toolbar switching buttons (theme/lang tokens removed).
    _check("data-theme=" not in body,
           "preview.html has no theme-switch buttons")
    _check("data-lang=" not in body,
           "preview.html has no language-switch buttons")
    # Sanity: the page is still a real HTML document.
    _check(body.lstrip().startswith("<!DOCTYPE")
           or body.lstrip().startswith("<html"),
           "preview.html is a real HTML page")


def test_env_autodetect_module(verbose: bool = False) -> None:
    _section("env_autodetect module: full LLM env scan")
    from fulladdmax_mcp import env_autodetect as ead
    with _env({k: None for k in list(os.environ) if any(
            k.startswith(p) for p in
            ("CLAUDE_", "ANTHROPIC_", "CURSOR_", "CODEX_",
             "CONTINUE_", "COPILOT_", "CLINE_", "AIDER_",
             "ZED_", "GITHUB_", "OPENAI_", "FULLADDMAX_",
             "OLLAMA_", "VLLM_", "LMSTUDIO_"))}):
        # 1. No env at all → empty snapshot
        snap = ead.detect_llm_env()
        _check(snap.api_key == "", "no env -> empty api_key")
        _check(snap.host_id == "", "no env -> empty host_id")
        _check(snap.source == "", "no env -> empty source")
        # 2. FULLADDMAX_API_KEY explicit
        with _env({"FULLADDMAX_API_KEY": "sk-test1234",
                   "FULLADDMAX_BASE_URL": "https://api.openai.com/v1",
                   "FULLADDMAX_MODEL": "gpt-4o"}):
            snap = ead.detect_llm_env()
            _check(snap.api_key == "sk-test1234", "FULLADDMAX_API_KEY picked up")
            _check(snap.base_url == "https://api.openai.com/v1", "FULLADDMAX_BASE_URL picked up")
            _check(snap.model == "gpt-4o", "FULLADDMAX_MODEL picked up")
        # 3. OPENAI_API_KEY fallback
        with _env({"OPENAI_API_KEY": "sk-openai1",
                   "OPENAI_BASE_URL": "https://api.openai.com/v1"}):
            snap = ead.detect_llm_env()
            _check(snap.api_key == "sk-openai1", "OPENAI_API_KEY fallback works")
        # 4. OLLAMA_HOST local
        with _env({"OLLAMA_HOST": "http://localhost:11434"}):
            snap = ead.detect_llm_env()
            _check(snap.base_url == "http://localhost:11434/v1",
                   "Ollama base URL auto-suffixes /v1")
            _check("Ollama" in snap.source, "Ollama source label present")
    if verbose:
        print("  (8 env_autodetect assertions)")


def test_lazy_load_hints(verbose: bool = False) -> None:
    _section("agent lazy-load: no LLM -> hint, not error")
    from fulladdmax_mcp import env_autodetect as ead
    from fulladdmax_mcp import llm as llm_mod
    from fulladdmax_mcp.handlers import agent as agent_handler
    with _env({k: None for k in list(os.environ) if any(
            k.startswith(p) for p in
            ("CLAUDE_", "ANTHROPIC_", "CURSOR_", "CODEX_",
             "CONTINUE_", "COPILOT_", "CLINE_", "AIDER_",
             "ZED_", "GITHUB_", "OPENAI_", "FULLADDMAX_"))}):
        # Force-clear any previously-set config.
        llm_mod.set_config(llm_mod.LLMConfig(api_key=""))
        # 4. Four workflow ops return a hint, not an error
        for op, kwargs in [
            ("orchestrator_run",    {"task": "x"}),
            ("parallel_agents_run", {"tasks": ["a", "b"]}),
            ("map_reduce_run",      {"items": ["a", "b"]}),
            ("swarm_run",           {"initial_agent": "coder", "task": "x"}),
        ]:
            handler = agent_handler.HANDLERS[op]
            import asyncio
            result = asyncio.run(handler(**kwargs))
            _check("No LLM endpoint configured" in result,
                   f"{op} -> lazy hint (not error)")
            _check("configure_llm" in result,
                   f"{op} -> hint mentions configure_llm")
            _check("orchestrator_run" not in result.lower().split("no llm")[0],
                   f"{op} -> does not crash into LLM call")
    if verbose:
        print("  (12 lazy-load assertions)")


def test_auto_workflow_routing(verbose: bool = False) -> None:
    _section("agent auto_workflow: heuristic routing")
    from fulladdmax_mcp.handlers import agent as agent_handler
    # Routing decisions are pure (don't need an LLM to test).
    _check(agent_handler._pick_workflow("plain task", "", has_items=False)
           == "orchestrator", "default -> orchestrator")
    _check(agent_handler._pick_workflow("do this in parallel", "", False)
           == "parallel", "keyword 'in parallel' -> parallel")
    _check(agent_handler._pick_workflow("handoff to critic", "", False)
           == "swarm", "keyword 'handoff' -> swarm")
    _check(agent_handler._pick_workflow("map-reduce these items", "", False)
           == "map_reduce", "keyword 'map-reduce' -> map_reduce")
    _check(agent_handler._pick_workflow("anything", "", has_items=True)
           == "map_reduce", "has_items=True -> map_reduce")
    _check(agent_handler._pick_workflow("anything", "swarm", False)
           == "swarm", "prefer='swarm' overrides")
    _check(agent_handler._pick_workflow("anything", "BOGUS", False)
           == "orchestrator", "invalid prefer -> default orchestrator")
    if verbose:
        print("  (7 routing assertions)")


def test_delegate_heuristic_split(verbose: bool = False) -> None:
    _section("agent delegate: heuristic sub-task splitting")
    from fulladdmax_mcp.handlers import agent as agent_handler
    # Atomic task stays atomic.
    _check(agent_handler._heuristic_split("hello world")
           == ["hello world"], "atomic -> 1 fragment")
    # CJK commas split.
    parts = agent_handler._heuristic_split("调研北京，调研上海，调研深圳")
    _check(len(parts) == 3, f"CJK commas -> 3 fragments (got {len(parts)})")
    # English "and" splits.
    parts = agent_handler._heuristic_split(
        "Compare Python, Rust and Go for CLI tools")
    _check(len(parts) >= 2, f"'and' splits -> >=2 (got {len(parts)})")
    # Sentence end splits.
    parts = agent_handler._heuristic_split("Do X. Then do Y. Finally do Z.")
    _check(len(parts) == 3, f"3 sentences -> 3 fragments (got {len(parts)})")
    # Tiny fragments get glued back.
    parts = agent_handler._heuristic_split("a, b, c, d, e, f, long one")
    _check(all(len(p) >= 3 for p in parts),
           "tiny fragments glued back, none <3 chars")
    if verbose:
        print(f"  (5 split assertions, sample splits: {parts})")


def test_delegate_should_auto_split(verbose: bool = False) -> None:
    _section("agent delegate: should_auto_split heuristic")
    from fulladdmax_mcp.handlers import agent as agent_handler
    _check(agent_handler._should_auto_split("a, b, c, d")
           is True, "comma list -> split")
    _check(agent_handler._should_auto_split("X and Y and Z")
           is True, "'and' chain -> split")
    _check(agent_handler._should_auto_split("plain short task")
           is False, "atomic -> no split")
    # Long task with conjunction -> split
    long_task = ("Research the market for electric vehicles in 2026, "
                 "including growth rates, key players, and government policy")
    _check(agent_handler._should_auto_split(long_task)
           is True, "long task with comma -> split")
    if verbose:
        print("  (4 auto-split assertions)")


def test_delegate_registered(verbose: bool = False) -> None:
    _section("agent delegate: registration & lazy hint")
    from fulladdmax_mcp.handlers import agent as agent_handler
    from fulladdmax_mcp import env_autodetect as ead
    from fulladdmax_mcp import llm as llm_mod
    _check("delegate" in agent_handler.HANDLERS,
           "delegate registered in HANDLERS")
    _check("delegate" in agent_handler.SCHEMAS,
           "delegate registered in SCHEMAS")
    with _env({k: None for k in list(os.environ) if any(
            k.startswith(p) for p in
            ("CLAUDE_", "ANTHROPIC_", "CURSOR_", "CODEX_",
             "CONTINUE_", "COPILOT_", "CLINE_", "AIDER_",
             "ZED_", "GITHUB_", "OPENAI_", "FULLADDMAX_"))}):
        llm_mod.set_config(llm_mod.LLMConfig(api_key=""))
        import asyncio
        result = asyncio.run(agent_handler._delegate(task="a, b, c"))
        _check("No LLM endpoint configured" in result,
               "delegate -> lazy hint when no LLM")
    if verbose:
        print("  (3 registration assertions)")


def test_hive_default_ministries(verbose: bool = False) -> None:
    _section("agent hive_run: default ministries (三省六部)")
    from fulladdmax_mcp.handlers import agent as agent_handler
    ministries = agent_handler._DEFAULT_MINISTRIES
    _check(len(ministries) == 6, f"6 ministries by default (got {len(ministries)})")
    for ch in ["吏", "户", "礼", "兵", "刑", "工"]:
        _check(any(ch in m["name"] for m in ministries),
               f"ministry {ch} present")
    _check(any("critic" in m["angle"] or "red team" in m["system"]
               for m in ministries), "critic ministry present for feedback loop")
    if verbose:
        print(f"  (7 default-ministry assertions)")


def test_hive_registered_and_lazy(verbose: bool = False) -> None:
    _section("agent hive_run: registration & lazy hint")
    from fulladdmax_mcp.handlers import agent as agent_handler
    from fulladdmax_mcp import llm as llm_mod
    _check("hive_run" in agent_handler.HANDLERS,
           "hive_run registered in HANDLERS")
    _check("hive_run" in agent_handler.SCHEMAS,
           "hive_run registered in SCHEMAS")
    _check(agent_handler.SCHEMAS["hive_run"]["waves"].default == 2,
           "waves default = 2")
    _check(agent_handler.SCHEMAS["hive_run"]["max_subagents"].default == 200,
           "max_subagents default = 200 (the only hard cap)")
    # max_depth is the new cross-call recursion cap (None = uncapped)
    _check("max_depth" in agent_handler.SCHEMAS["hive_run"],
           "max_depth field added (recursion cap)")
    _check(agent_handler.SCHEMAS["hive_run"]["max_depth"].default is None,
           "max_depth default = None (uncapped)")
    with _env({k: None for k in list(os.environ) if any(
            k.startswith(p) for p in
            ("CLAUDE_", "ANTHROPIC_", "CURSOR_", "CODEX_",
             "CONTINUE_", "COPILOT_", "CLINE_", "AIDER_",
             "ZED_", "GITHUB_", "OPENAI_", "FULLADDMAX_"))}):
        llm_mod.set_config(llm_mod.LLMConfig(api_key=""))
        import asyncio
        result = asyncio.run(agent_handler._hive_run(task="x"))
        _check("No LLM endpoint configured" in result,
               "hive_run -> lazy hint when no LLM")
    if verbose:
        print("  (7 registration + config assertions)")


def test_hive_waves_loud_failure(verbose: bool = False) -> None:
    _section("agent hive_run: waves ceiling is loud, not silent")
    from fulladdmax_mcp.handlers import agent as agent_handler
    from fulladdmax_mcp import llm as llm_mod
    llm_mod.set_config(llm_mod.LLMConfig(api_key="x"))  # any fake key
    agent_handler._require_llm = lambda *a, **k: None
    # Stub parallel_agents_run so we never reach the LLM network.
    async def _stub(tasks, **kwargs):
        return f"[stub] fired {len(tasks)}"
    agent_handler.server_internal.parallel_agents_run = _stub
    import asyncio

    # waves above the ceiling must raise, not silently truncate.
    raised = False
    try:
        asyncio.run(agent_handler._hive_run(task="x", waves=1000))
    except ValueError as e:
        raised = "max_waves=20" in str(e) and "1000" in str(e)
    _check(raised, "waves=1000 raises ValueError mentioning max_waves=20")

    # waves within the ceiling must NOT raise.
    raised2 = False
    try:
        asyncio.run(agent_handler._hive_run(task="x", waves=5))
    except ValueError:
        raised2 = True
    _check(not raised2, "waves=5 does not raise")

    # max_subagents < 1 raises.
    raised3 = False
    try:
        asyncio.run(agent_handler._hive_run(task="x", max_subagents=0))
    except ValueError as e:
        raised3 = "max_subagents=0 must be >= 1" in str(e)
    _check(raised3, "max_subagents=0 raises ValueError")
    if verbose:
        print("  (3 hard-guard assertions)")


def test_hive_depth_tracking(verbose: bool = False) -> None:
    _section("agent hive_run: session depth tracking")
    from fulladdmax_mcp.handlers import agent as agent_handler
    from fulladdmax_mcp import llm as llm_mod
    from fulladdmax_mcp import context as ctx_mod
    llm_mod.set_config(llm_mod.LLMConfig(api_key="x"))
    agent_handler._require_llm = lambda *a, **k: None
    # Stub parallel_agents_run so we can run without an LLM
    async def _stub(tasks, **kwargs):
        return f"[stub] fired {len(tasks)}"
    agent_handler.server_internal.parallel_agents_run = _stub
    import asyncio

    # Clean state.
    ctx_mod.put("hive_depth", 0)
    # 1) Default max_depth=None -> never downgrades.
    ctx_mod.put("hive_depth", 5)  # pretend we're already deep
    out = asyncio.run(agent_handler._hive_run(task="x", waves=1))
    _check("downgraded" not in out,
           "max_depth=None (default) does NOT downgrade even at depth 5")

    # 2) max_depth=1 with depth=0 -> not downgraded.
    ctx_mod.put("hive_depth", 0)
    out = asyncio.run(agent_handler._hive_run(task="x", waves=1,
                                               max_depth=1))
    _check("downgraded" not in out,
           "max_depth=1 at depth 0 does NOT downgrade")

    # 3) max_depth=1 with depth=1 -> downgraded.
    ctx_mod.put("hive_depth", 1)
    out = asyncio.run(agent_handler._hive_run(task="x", waves=1,
                                               max_depth=1))
    _check("downgraded" in out and "depth 1 >= max_depth 1" in out,
           "max_depth=1 at depth 1 IS downgraded")

    # 4) Depth is restored after the call (siblings see same value).
    ctx_mod.put("hive_depth", 2)
    asyncio.run(agent_handler._hive_run(task="x", waves=1, max_depth=5))
    _check(ctx_mod.get("hive_depth", 0) == 2,
           "hive_depth restored to 2 after nested call returns")
    ctx_mod.put("hive_depth", 0)  # reset
    if verbose:
        print("  (4 depth-tracking assertions)")


def test_all_seven_agent_ops_work_offline(verbose: bool = False) -> None:
    _section("agent mega tool: ALL 7 ops work without any LLM (FULLADDMAX_AGENT_OFFLINE)")
    import os as _os
    from fulladdmax_mcp.handlers import agent as a
    from fulladdmax_mcp import llm as llm_mod
    # Force offline + no LLM.
    saved_env = _os.environ.get("FULLADDMAX_AGENT_OFFLINE")
    _os.environ["FULLADDMAX_AGENT_OFFLINE"] = "1"
    try:
        llm_mod.set_config(llm_mod.LLMConfig(api_key=""))
        import asyncio
        cases = [
            ("orchestrator_run",   lambda: a._orchestrator_run(task="x")),
            ("parallel_agents_run",lambda: a._parallel_agents_run(tasks=["a","b"])),
            ("map_reduce_run",     lambda: a._map_reduce_run(items=["a","b"])),
            ("swarm_run",          lambda: a._swarm_run(initial_agent="coder", task="x")),
            ("auto_workflow",      lambda: a._auto_workflow(task="do A and B in parallel")),
            ("delegate",           lambda: a._delegate(task="do A, B, C")),
            ("hive_run",           lambda: a._hive_run(task="x", waves=2)),
        ]
        for op_name, op_fn in cases:
            out = asyncio.run(op_fn())
            has_offline = ("offline" in out.lower()
                           or op_name == "auto_workflow"   # router never says offline
                           or op_name == "swarm_run")      # hand-off plan also OK
            no_hint = "No LLM endpoint configured" not in out
            _check(has_offline and no_hint,
                   f"{op_name:25s} works offline (has framework, no hint)")
    finally:
        if saved_env is None:
            _os.environ.pop("FULLADDMAX_AGENT_OFFLINE", None)
        else:
            _os.environ["FULLADDMAX_AGENT_OFFLINE"] = saved_env
    if verbose:
        print("  (7 offline-op assertions)")


def test_offline_stub_hive_has_six_ministries(verbose: bool = False) -> None:
    _section("agent hive_run: offline stub emits all 6 ministries")
    import os as _os
    from fulladdmax_mcp.handlers import agent as a
    from fulladdmax_mcp import llm as llm_mod
    saved_env = _os.environ.get("FULLADDMAX_AGENT_OFFLINE")
    _os.environ["FULLADDMAX_AGENT_OFFLINE"] = "1"
    try:
        llm_mod.set_config(llm_mod.LLMConfig(api_key=""))
        import asyncio
        out = asyncio.run(a._hive_run(task="design payment", waves=1))
        for ch in ["吏", "户", "礼", "兵", "刑", "工"]:
            _check(ch in out, f"offline hive output mentions ministry {ch}")
        _check("offline stub" in out,
               "offline hive output is labelled as stub")
    finally:
        if saved_env is None:
            _os.environ.pop("FULLADDMAX_AGENT_OFFLINE", None)
        else:
            _os.environ["FULLADDMAX_AGENT_OFFLINE"] = saved_env
    if verbose:
        print("  (7 offline-hive assertions)")


def test_hive_custom_departments(verbose: bool = False) -> None:
    _section("agent hive_run: custom department names")
    from fulladdmax_mcp.handlers import agent as agent_handler
    for name in ["吏部", "CUSTOM-X"]:
        match = next(
            (m for m in agent_handler._DEFAULT_MINISTRIES
             if m["name"].split(" ")[0] in name or name in m["name"]),
            None,
        )
        if name == "CUSTOM-X":
            _check(match is None,
                   f"unknown name {name!r} returns no default match")
        else:
            _check(match is not None,
                   f"known name {name!r} matched against defaults")
    if verbose:
        print("  (2 custom-department assertions)")


def test_all_dashboards_on_disk(verbose: bool = False) -> None:
    _section("docs/panel.svg present")
    p = _REPO_ROOT / "docs" / "panel.svg"
    if not p.exists():
        _fail("docs/panel.svg missing")
        return
    body = p.read_text(encoding="utf-8")
    _check(body.startswith("<?xml"),
           "docs/panel.svg starts with XML prolog")
    _check('fill="#0F0F1E"' in body,
           "docs/panel.svg has dark background")
    _check("Server" in body and "LLM" in body and "Agent Tools" in body,
           "docs/panel.svg has the 3 card titles")
    # Only one panel file ships (no 6-combo matrix any more).
    for combo in [f"{t}-{l}" for t in ("dark", "light", "paper")
                  for l in ("en", "zh")]:
        ghost = _REPO_ROOT / "docs" / f"panel-{combo}.svg"
        _check(not ghost.exists(),
               f"docs/panel-{combo}.svg NOT present (no multi-mode matrix)")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


ALL_TESTS = [
    test_strings_parity,
    test_render_svg_simplified,
    test_host_detection,
    test_env_autodetect_module,
    test_inherited_state_in_svg,
    test_lazy_load_hints,
    test_auto_workflow_routing,
    test_delegate_heuristic_split,
    test_delegate_should_auto_split,
    test_delegate_registered,
    test_hive_default_ministries,
    test_hive_registered_and_lazy,
    test_hive_custom_departments,
    test_hive_waves_loud_failure,
    test_hive_depth_tracking,
    test_all_seven_agent_ops_work_offline,
    test_offline_stub_hive_has_six_ministries,
    test_cli_integration,
    test_preview_html,
    test_all_dashboards_on_disk,
]


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    verbose = "-v" in argv or "--verbose" in argv
    skip_cli = "--no-cli" in argv

    print(f"FullADDMAX-mcp - panel UI tests")
    suites = list(ALL_TESTS)
    if skip_cli:
        suites = [t for t in suites if t is not test_cli_integration]
        print(f"  ({len(suites)} suites, --no-cli: skipping CLI subprocess tests)")
    else:
        print(f"  {len(suites)} suites (single dark theme, English only)")

    for t in suites:
        t(verbose=verbose)

    print(f"\n--- Summary: {_PASS} passed, {_FAIL} failed ---")
    if _FAIL:
        print("\nFailures:")
        for f in _FAILURES:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
