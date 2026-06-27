"""``fulladdmax-mcp panel`` — generate a static SVG dashboard.

The panel command renders a single self-contained SVG (no emoji, pure
SVG primitives) that summarises the current state of the FullADDMAX-mcp
server in **3 core cards**:

1. **Server** — version, uptime, health badge
2. **LLM** — model, base URL, key state (inherited / off-the-shelf / masked)
3. **Agent Tools** — registered tool count, mega-tool list, op count

Usage::

    fulladdmax-mcp panel --out docs/panel.svg
    fulladdmax-mcp panel --serve --port 8765

The data is collected by invoking the 4 mega tools in-process, so the
panel doubles as a smoke test for the mega tool surface.

.. note::

    Earlier revisions of this module supported 3 themes (dark / light
    / paper) × 2 languages (en / zh) = 6 combinations, and 6 cards
    (added Rate Limit, Sessions, Usage, Swarm Agents).  Those added
    noise without helping the user answer "is the server up and is my
    LLM wired?" — the most common question.  Now we ship **3 cards /
    1 theme / 1 language** for a focused single-page dashboard.

    The ``--theme`` and ``--lang`` flags are still accepted for CLI
    compatibility but no longer affect output.  ``zh`` and
    ``light``/``paper`` keys are kept as module-level constants
    so future readers can see what was removed and why.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import logging
import math
import os
import re
import socketserver
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .env_autodetect import detect_host_ai, detect_llm_env
from .server import mcp

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Process start time (for the "uptime" cell)
# ---------------------------------------------------------------------------

_PROCESS_START_TIME = time.time()


# ---------------------------------------------------------------------------
# Theme palette (single dark theme — was 3 themes, simplified to 1)
#
# Kept as a dict so future readers can see the previous values.
# Light/paper palettes intentionally NOT kept; they are noise.
# ---------------------------------------------------------------------------

THEMES: dict[str, dict[str, str]] = {
    "dark": {
        "bg": "#0F0F1E",
        "card": "#1A1A2E",
        "card_alt": "#232342",
        "border": "#3D3D5C",
        "accent": "#9D2BFF",
        "accent2": "#22C6FF",
        "text": "#E8E8F0",
        "muted": "#8888A0",
        "warn": "#FFB454",
        "error": "#FF6B6B",
        "ok": "#50E3A4",
    },
}


# ---------------------------------------------------------------------------
# i18n strings (English only — was en/zh, simplified to en)
#
# Kept as a dict so future readers can see what was removed.
# ---------------------------------------------------------------------------

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "card_server": "Server",
        "card_llm": "LLM",
        "card_tools": "Agent Tools",
        "healthy": "healthy",
        "unhealthy": "unhealthy",
        "uptime": "uptime",
        "version": "version",
        "unset": "(unset)",
        "inherited": "inherited from",
        "inherited_short": "host-LLM",
        "config_needed": "configure FULLADDMAX_API_KEY",
        "dash": "-",
        "enabled": "ENABLED",
        "unlimited": "unlimited",
        "registered": "registered",
        "collect_error": "collect error:",
        "off_the_shelf": "(off-the-shelf)",
        "off_the_shelf_hint": "out-of-the-box · non-LLM ops ready · set FULLADDMAX_API_KEY to enable agent ops",
        "mcp_servers": "mcp servers",
        "mega_tools": "mega tools",
        "agent_ops": "agent ops",
        "total_ops": "total ops",
    },
}


def _t(key: str, lang: str = "en") -> str:
    """Look up a translated string, falling back to English then the key."""
    table = STRINGS.get(lang, STRINGS["en"])
    return table.get(key, STRINGS["en"].get(key, key))


def _fmt_duration_zh(secs: int) -> str:
    """Chinese-friendly duration formatter."""
    secs = max(0, int(secs))
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days > 0:
        return f"{days}天{hours}时{minutes}分"
    if hours > 0:
        return f"{hours}时{minutes}分"
    return f"{minutes}分"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class PanelData:
    """Container for the data displayed in the dashboard.

    Simplified to 3 cards' worth of fields.  Removed: rate_limit_*,
    session_*, usage_*, swarm_agent_names.  See :mod:`__init__`
    changelog for what was dropped.
    """

    healthy: bool = True
    error_message: str = ""

    version: str = ""
    timestamp: str = ""
    uptime_secs: int = 0

    llm_model: str = ""
    llm_base_url: str = ""
    llm_api_key_masked: str = ""
    llm_timeout: str = ""
    llm_max_retries: str = ""

    tool_names: list[str] = field(default_factory=list)

    # Smart detection: when api_key is empty, is the server being
    # driven by a host LLM (Claude Desktop / Cursor / etc.)?
    ai_host: str = ""        # "" / "claude" / "cursor" / "codex" / "continue" / "copilot" / "custom"
    ai_host_label: str = ""  # pretty label, e.g. "Claude Desktop"
    # Resolved source of the LLM config (e.g. "FULLADDMAX_API_KEY",
    # "inherited from Claude Desktop", or "" when nothing is set).
    llm_source: str = ""
    # True if no LLM is configured at all (after host + env autodetect).
    # Used by the panel to render an "off-the-shelf" badge instead of
    # a warning.
    llm_off_the_shelf: bool = True


# Known AI host markers — see :mod:`fulladdmax_mcp.env_autodetect` for
# the canonical list.  The helper re-exports ``detect_host_ai`` from
# there; we keep the alias below so the rest of this module reads
# naturally.
def _detect_ai_host() -> tuple[str, str]:
    return detect_host_ai()


# ---------------------------------------------------------------------------
# Markdown / JSON parsers (lightweight, no external deps)
# ---------------------------------------------------------------------------


_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")
_PIPE_ROW_RE = re.compile(r"^\|(.+)\|$")
_JSON_BLOCK_RE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)


def _first_match(pattern: str, text: str, flags: int = 0) -> str | None:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None


def _extract_json(text: str) -> Any | None:
    """Return the last JSON object embedded in a fenced ```json block."""
    blocks = _JSON_BLOCK_RE.findall(text)
    if not blocks:
        return None
    try:
        return json.loads(blocks[-1])
    except (ValueError, TypeError):
        return None


def _parse_ping(text: str) -> dict[str, str]:
    """Parse the output of :func:`server_internal.ping`."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = _LINE_RE.match(line.strip())
        if m:
            key = m.group(1)
            val = m.group(2).strip()
            # Treat literal "(unset)" / "None" / "" as missing — the
            # panel renders those with smart detection (host-LLM, etc.).
            if val in ("", "(unset)", "None", "null"):
                val = ""
            out[key] = val
    return out


def _parse_list_block(text: str) -> list[str]:
    """Parse a Markdown bullet list ('- name — desc') into a list of names."""
    out: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^- \*\*(\w+)\*\*", line.strip())
        if m:
            out.append(m.group(1))
    return out


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------


async def _call_admin(operation: str, params_json: str = "") -> str:
    """Call the ``admin`` mega tool in-process."""
    tool = mcp._tool_manager._tools["admin"].fn  # type: ignore[attr-defined]
    return await tool(operation=operation, params_json=params_json, session_id="")


async def collect() -> PanelData:
    """Collect a snapshot of the server state by calling the mega tools."""
    data = PanelData(version=__version__)

    # Detect whether the server is being driven by a host LLM (Claude
    # Desktop / Cursor / etc.) — used to render the api_key field
    # smartly when the user hasn't configured their own key yet.
    data.ai_host, data.ai_host_label = _detect_ai_host()

    # Run the env autodetect up front so we can fall back to host-injected
    # or local-LLM values if the in-process LLMConfig is still empty.
    env_snap = detect_llm_env()

    try:
        # 1. ping → LLM config
        ping_text = await _call_admin("ping")
        ping = _parse_ping(ping_text)
        data.llm_model = ping.get("model", "")
        data.llm_base_url = ping.get("base_url", "")
        data.llm_api_key_masked = ping.get("api_key", "")
        data.llm_timeout = ping.get("timeout", "")
        data.llm_max_retries = ping.get("retries", "")

        # Off-the-shelf fallback: if the in-process config didn't pick
        # anything up (e.g. ping happened before set_config), let
        # env_autodetect supply the values it would have used at
        # process start.
        if not data.llm_model and env_snap.model:
            data.llm_model = env_snap.model
        if not data.llm_base_url and env_snap.base_url:
            data.llm_base_url = env_snap.base_url
        if not data.llm_api_key_masked and env_snap.api_key:
            data.llm_api_key_masked = env_snap.api_key[:4] + "****"

        data.llm_source = env_snap.source or ""
        # Off-the-shelf = nothing resolved through any channel.  In
        # this state the server still works for the non-LLM operations
        # (knowledge / config / admin) and the panel renders a soft
        # badge instead of a warning.
        data.llm_off_the_shelf = (
            not data.llm_api_key_masked and not env_snap.api_key
        )

        # 2. mega tools (the 4 main MCP tools: admin/knowledge/config/agent)
        # Note: we read directly from the tool manager, NOT from
        # ``list_agent_tools`` which returns the per-op sub-tool registry.
        data.tool_names = _discover_mcp_servers()

    except Exception as e:  # noqa: BLE001
        log.exception("panel.collect failed")
        data.healthy = False
        data.error_message = f"{type(e).__name__}: {e}"

    data.timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data.uptime_secs = int(time.time() - _PROCESS_START_TIME)
    return data


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _fmt_int(n: int) -> str:
    return f"{n:,}"


def _fmt_duration(secs: int) -> str:
    secs = max(0, int(secs))
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _escape(text: str) -> str:
    """Escape text for safe inclusion in an SVG <text> element."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ---------------------------------------------------------------------------
# SVG rendering
# ---------------------------------------------------------------------------

# Layout constants — 3 cards in a single horizontal row
WIDTH = 1280
HEIGHT = 380
HEADER_H = 70
BODY_TOP = HEADER_H + 16
BODY_H = HEIGHT - BODY_TOP - 16
COLS = 3
ROWS = 1
CARD_GAP = 12
SIDE_PAD = 16
CARD_W = (WIDTH - 2 * SIDE_PAD - 2 * CARD_GAP) // COLS
CARD_H = BODY_H


def _card(x: int, y: int, w: int, h: int, t: dict[str, str], title: str, title_idx: int) -> str:
    """Render the card chrome (background, border, title bar)."""
    out: list[str] = []
    out.append(
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" ry="8" '
        f'fill="{t["card"]}" stroke="{t["border"]}" stroke-width="1"/>'
    )
    # Title bar
    out.append(
        f'<rect x="{x}" y="{y}" width="{w}" height="32" rx="8" ry="8" '
        f'fill="{t["card_alt"]}"/>'
    )
    out.append(
        f'<rect x="{x}" y="{y + 22}" width="{w}" height="10" '
        f'fill="{t["card_alt"]}"/>'
    )
    # Number badge
    badge_x = x + 12
    badge_y = y + 8
    out.append(
        f'<rect x="{badge_x}" y="{badge_y}" width="20" height="16" rx="3" ry="3" '
        f'fill="{t["accent"]}"/>'
    )
    out.append(
        f'<text x="{badge_x + 10}" y="{badge_y + 12}" font-family="monospace" '
        f'font-size="11" font-weight="bold" fill="{t["card"]}" '
        f'text-anchor="middle">{title_idx}</text>'
    )
    # Title
    out.append(
        f'<text x="{badge_x + 30}" y="{badge_y + 12}" font-family="system-ui" '
        f'font-size="14" font-weight="bold" fill="{t["text"]}">{_escape(title)}</text>'
    )
    return "\n".join(out)


def _kv_block(
    x: int,
    y: int,
    w: int,
    rows: list[tuple[str, str]],
    t: dict[str, str],
    *,
    key_width: int = 110,
    row_h: int = 22,
    value_color: str | None = None,
) -> str:
    """Render a key/value list inside a card."""
    out: list[str] = []
    cur_y = y
    for label, value in rows:
        out.append(
            f'<text x="{x}" y="{cur_y + 14}" font-family="system-ui" '
            f'font-size="12" fill="{t["muted"]}">{_escape(label)}</text>'
        )
        out.append(
            f'<text x="{x + key_width}" y="{cur_y + 14}" font-family="monospace" '
            f'font-size="12" fill="{value_color or t["text"]}">{_escape(value)}</text>'
        )
        cur_y += row_h
    return "\n".join(out)


def _bar(
    x: int,
    y: int,
    w: int,
    h: int,
    pct: float,
    t: dict[str, str],
    *,
    fill: str | None = None,
) -> str:
    """Render a horizontal progress bar."""
    fill = fill or t["accent"]
    pct = max(0.0, min(100.0, pct))
    out = [
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3" ry="3" '
        f'fill="{t["card_alt"]}" stroke="{t["border"]}" stroke-width="1"/>',
        f'<rect x="{x}" y="{y}" width="{int(w * pct / 100)}" height="{h}" rx="3" ry="3" '
        f'fill="{fill}"/>',
    ]
    return "\n".join(out)


def _section_llm(d: PanelData, t: dict[str, str], x: int, y: int, lang: str = "en") -> str:
    unset = _t("unset", lang)
    dash = _t("dash", lang)

    # Smart api_key rendering — three states, all friendly:
    #   - real key configured → masked value (sk-xxxx****)
    #   - key empty + host AI detected → "inherited from <host>" badge
    #   - key empty + no host → soft "off-the-shelf" hint
    #     (server still works for knowledge / config / admin; only the
    #     LLM-bound agent ops need a key)
    if d.llm_api_key_masked:
        api_key_text = d.llm_api_key_masked
        api_key_color = t["text"]
        inherited_note = d.llm_source
    elif d.ai_host_label:
        api_key_text = f"{_t('inherited', lang)} {d.ai_host_label}"
        api_key_color = t["ok"]
        inherited_note = (
            f"{_t('inherited_short', lang)}: {d.ai_host_label}"
            if d.llm_source
            else ""
        )
    else:
        # Off-the-shelf state.  No warning, no alarm — just a friendly
        # label that points the user at the configure step when they
        # want to run LLM workflows.
        api_key_text = _t("off_the_shelf", lang) if d.llm_off_the_shelf else unset
        api_key_color = t["muted"]
        inherited_note = _t("off_the_shelf_hint", lang)

    rows: list[tuple[str, str]] = [
        ("model", d.llm_model or unset),
        ("base_url", d.llm_base_url or unset),
        ("api_key", api_key_text),
        ("timeout", d.llm_timeout or dash),
        ("max_retries", d.llm_max_retries or dash),
    ]
    # We render the kv block per-row so we can colour the api_key cell
    # specifically (green = inherited, yellow = unset, white = masked).
    out: list[str] = []
    row_h = 22
    for i, (k, v) in enumerate(rows):
        y_row = y + i * row_h + 14
        # Highlight the api_key row in the host LLM green colour.
        cell_color = api_key_color if k == "api_key" else t["text"]
        out.append(
            f'<text x="{x}" y="{y_row}" font-family="system-ui" font-size="12" '
            f'fill="{t["muted"]}">{_escape(k)}</text>'
        )
        out.append(
            f'<text x="{x + 110}" y="{y_row}" font-family="monospace" font-size="12" '
            f'fill="{cell_color}">{_escape(v)}</text>'
        )

    if inherited_note:
        # Hint line under the kv block, italic + muted.
        note_y = y + 5 * 22 + 8
        out.append(
            f'<text x="{x}" y="{note_y}" font-family="system-ui" font-size="11" '
            f'fill="{t["muted"]}" font-style="italic">{_escape(inherited_note)}</text>'
        )
    return "\n".join(out)


def _section_rate_limit(d: PanelData, t: dict[str, str], x: int, y: int, lang: str = "en") -> str:
    status_color = t["ok"] if d.rate_limit_enabled else t["muted"]
    status_text = _t("enabled", lang) if d.rate_limit_enabled else _t("unlimited", lang)
    dash = _t("dash", lang)
    rows: list[tuple[str, str]] = [
        (_t("status", lang), status_text),
        (_t("global_rpm", lang), str(d.rate_limit_global_rpm) if d.rate_limit_enabled else dash),
        (_t("global_tpm", lang), f"{_fmt_int(d.rate_limit_global_tpm)}" if d.rate_limit_enabled else dash),
        (_t("session_rpm", lang), str(d.rate_limit_session_rpm) if d.rate_limit_session_rpm else dash),
        (_t("session_tpm", lang), f"{_fmt_int(d.rate_limit_session_tpm)}" if d.rate_limit_session_tpm else dash),
        (_t("buckets", lang), str(d.rate_limit_session_buckets)),
    ]
    out = [_kv_block(x, y, CARD_W - 24, rows, t, key_width=120, value_color=status_color)]

    bar_x = x
    bar_y = y + 6 * 22 + 8
    bar_w = CARD_W - 48
    out.append(_bar(bar_x, bar_y, bar_w, 12, 100 if d.rate_limit_enabled else 0, t,
                    fill=t["accent"] if d.rate_limit_enabled else t["muted"]))
    return "\n".join(out)


def _section_sessions(d: PanelData, t: dict[str, str], x: int, y: int, lang: str = "en") -> str:
    out: list[str] = []
    out.append(
        f'<text x="{x}" y="{y + 14}" font-family="system-ui" font-size="12" '
        f'fill="{t["muted"]}">{_escape(_t("total", lang))}</text>'
    )
    out.append(
        f'<text x="{x + 110}" y="{y + 14}" font-family="monospace" font-size="12" '
        f'fill="{t["accent2"]}" font-weight="bold">{_fmt_int(d.session_count)}</text>'
    )
    cur_y = y + 28
    keys_label = _t("keys", lang)
    ago_label = _t("ago", lang)
    for s in d.sessions[:6]:
        sid = str(s.get("session_id", ""))[:14]
        size = int(s.get("size", 0))
        age_secs = max(0, int(time.time() - float(s.get("last_access", time.time()))))
        age_str = _fmt_duration_zh(age_secs) if lang == "zh" else _fmt_duration(age_secs)
        out.append(
            f'<text x="{x}" y="{cur_y + 12}" font-family="monospace" font-size="11" '
            f'fill="{t["text"]}">{_escape(sid)}</text>'
        )
        out.append(
            f'<text x="{x + 130}" y="{cur_y + 12}" font-family="monospace" font-size="11" '
            f'fill="{t["muted"]}">{size} {keys_label}</text>'
        )
        out.append(
            f'<text x="{x + 200}" y="{cur_y + 12}" font-family="monospace" font-size="11" '
            f'fill="{t["muted"]}">{age_str} {ago_label}</text>'
        )
        cur_y += 18
    if not d.sessions:
        out.append(
            f'<text x="{x}" y="{cur_y + 14}" font-family="system-ui" font-size="12" '
            f'fill="{t["muted"]}" font-style="italic">{_escape(_t("no_sessions", lang))}</text>'
        )
    return "\n".join(out)


def _section_usage(d: PanelData, t: dict[str, str], x: int, y: int, lang: str = "en") -> str:
    tok = _t("tok", lang)
    rows: list[tuple[str, str]] = [
        (_t("records", lang), _fmt_int(d.usage_records)),
        (_t("prompt", lang), f"{_fmt_int(d.usage_prompt)} {tok}"),
        (_t("completion", lang), f"{_fmt_int(d.usage_completion)} {tok}"),
        (_t("cost", lang), f"${d.usage_cost_usd:.4f}"),
    ]
    out = [_kv_block(x, y, CARD_W - 24, rows, t, key_width=110)]
    if d.usage_by_model:
        cur_y = y + 4 * 22 + 12
        out.append(
            f'<text x="{x}" y="{cur_y}" font-family="system-ui" font-size="12" '
            f'fill="{t["muted"]}">{_escape(_t("by_model", lang))}</text>'
        )
        cur_y += 16
        rec_label = _t("rec", lang)
        for model, info in d.usage_by_model[:4]:
            try:
                m_records = int(info.get("records", 0))
                m_cost = float(info.get("cost_usd", 0.0))
            except (TypeError, ValueError):
                continue
            out.append(
                f'<text x="{x}" y="{cur_y + 12}" font-family="monospace" font-size="11" '
                f'fill="{t["text"]}">{_escape(model[:24])}</text>'
            )
            out.append(
                f'<text x="{x + 160}" y="{cur_y + 12}" font-family="monospace" font-size="11" '
                f'fill="{t["muted"]}">{m_records} rec · ${m_cost:.4f}</text>'
            )
            cur_y += 18
    return "\n".join(out)


def _section_tools(d: PanelData, t: dict[str, str], x: int, y: int, lang: str = "en") -> str:
    """Render the Agent Tools card body.

    Shows the 4 mega tools (admin / knowledge / config / agent) plus
    a total-ops count summary line at the top.
    """
    out: list[str] = []

    # Headline: total ops registered (sum of all op counts per mega tool)
    total_ops = sum(len(handlers_for(name)) for name in d.tool_names)
    out.append(
        f'<text x="{x}" y="{y + 14}" font-family="system-ui" font-size="12" '
        f'fill="{t["muted"]}">'
        f'{_escape(_t("total_ops", lang))}: '
        f'<tspan fill="{t["accent2"]}" font-weight="bold">{_fmt_int(total_ops)}</tspan>'
        f'</text>'
    )

    # Each mega tool as a row with an op count
    cur_y = y + 36
    for name in d.tool_names:
        op_count = len(handlers_for(name))
        # Coloured dot
        out.append(
            f'<rect x="{x}" y="{cur_y + 4}" width="8" height="8" rx="1" ry="1" '
            f'fill="{t["accent2"]}"/>'
        )
        # Mega tool name
        out.append(
            f'<text x="{x + 16}" y="{cur_y + 12}" font-family="monospace" font-size="13" '
            f'fill="{t["text"]}" font-weight="bold">{_escape(name)}</text>'
        )
        # Op count (right-aligned in the card)
        out.append(
            f'<text x="{x + CARD_W - 48}" y="{cur_y + 12}" font-family="monospace" '
            f'font-size="12" fill="{t["accent2"]}" text-anchor="end">'
            f'{op_count} ops</text>'
        )
        cur_y += 22

    if not d.tool_names:
        out.append(
            f'<text x="{x}" y="{cur_y + 14}" font-family="system-ui" font-size="12" '
            f'fill="{t["muted"]}" font-style="italic">{_escape(_t("no_tools", lang))}</text>'
        )
    return "\n".join(out)


def _section_server(d: PanelData, t: dict[str, str], x: int, y: int, lang: str = "en") -> str:
    """Render the Server card body: version, uptime, mcp-servers count."""
    out: list[str] = []
    uptime_str = _fmt_duration(d.uptime_secs)
    rows: list[tuple[str, str, str | None]] = [
        (_t("version", lang), d.version or __version__, None),
        (_t("uptime", lang), uptime_str, None),
        ("mcp servers", str(len(_discover_mcp_servers())), t["accent2"]),
    ]
    for label, val, color in rows:
        out.append(_kv_row(x, y, label, val, t, value_color=color))
        y += 22
    return "\n".join(out)


def handlers_for(mega_name: str) -> list[str]:
    """Return the list of registered op names for a single mega tool.

    Reads the live ``HANDLERS`` dict on the matching handler module
    so that adding a new op somewhere automatically flows through to
    the panel — no hard-coded lists to keep in sync.
    """
    try:
        from . import handlers as handlers_pkg
        mod = getattr(handlers_pkg, mega_name, None)
        if mod is not None and hasattr(mod, "HANDLERS"):
            return sorted(mod.HANDLERS.keys())
        return []
    except Exception:  # noqa: BLE001
        return []


def _discover_mcp_servers() -> list[str]:
    """Return the names of the configured MCP server entries (for stats)."""
    try:
        from .server import mcp
        if hasattr(mcp, "_tool_manager"):
            return sorted(mcp._tool_manager._tools.keys())
        return []
    except Exception:  # noqa: BLE001
        return []


def _kv_row(x: int, y: int, key: str, value: str, t: dict[str, str],
            key_width: int = 110, value_color: str | None = None) -> str:
    """Render a single 'key: value' row inline (smaller than _kv_block)."""
    color = value_color or t["text"]
    return (
        f'<text x="{x}" y="{y + 14}" font-family="system-ui" font-size="12" '
        f'fill="{t["muted"]}">{_escape(key)}</text>'
        f'<text x="{x + key_width}" y="{y + 14}" font-family="monospace" font-size="12" '
        f'fill="{color}">{_escape(value)}</text>'
    )


def render_svg(data: PanelData, theme: str = "dark", lang: str = "en") -> str:
    """Render :class:`PanelData` as a complete SVG document string.

    The output is a self-contained SVG with no external resources —
    it uses system fonts and inline colour values, so it renders
    correctly in any browser and on GitHub.
    """
    t = THEMES.get(theme, THEMES["dark"])
    out: list[str] = []

    out.append('<?xml version="1.0" encoding="UTF-8"?>')
    out.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" '
        f'width="{WIDTH}" height="{HEIGHT}" '
        f'font-family="system-ui, -apple-system, Segoe UI, sans-serif">'
    )

    out.append(
        f'<rect x="0" y="0" width="{WIDTH}" height="{HEIGHT}" fill="{t["bg"]}"/>'
    )

    # ---- Header ---------------------------------------------------------
    out.append(
        f'<rect x="0" y="0" width="{WIDTH}" height="{HEADER_H}" fill="{t["card"]}"/>'
    )
    out.append(
        f'<rect x="0" y="{HEADER_H - 2}" width="{WIDTH}" height="2" fill="{t["accent"]}"/>'
    )
    # Logo / mark — a small purple hexagon (no emoji, pure path)
    cx, cy = 30, HEADER_H // 2
    r = 16
    pts = []

    for i in range(6):
        ang = math.pi / 2 + i * math.pi / 3
        pts.append(f"{cx + r * math.cos(ang):.1f},{cy + r * math.sin(ang):.1f}")
    out.append(
        f'<polygon points="{" ".join(pts)}" fill="{t["accent"]}" stroke="{t["accent2"]}" '
        f'stroke-width="1.5"/>'
    )
    out.append(
        f'<text x="62" y="{HEADER_H // 2 + 6}" font-size="22" font-weight="bold" '
        f'fill="{t["text"]}">FullADDMAX-mcp</text>'
    )
    out.append(
        f'<text x="240" y="{HEADER_H // 2 + 6}" font-family="monospace" font-size="14" '
        f'fill="{t["accent2"]}">v{data.version}</text>'
    )
    # Right side: timestamp + uptime
    out.append(
        f'<text x="{WIDTH - 16}" y="{HEADER_H // 2 - 4}" font-family="monospace" '
        f'font-size="12" fill="{t["muted"]}" text-anchor="end">'
        f'{_escape(data.timestamp)}</text>'
    )
    uptime_str = _fmt_duration_zh(data.uptime_secs) if lang == "zh" else _fmt_duration(data.uptime_secs)
    out.append(
        f'<text x="{WIDTH - 16}" y="{HEADER_H // 2 + 14}" font-family="monospace" '
        f'font-size="12" fill="{t["muted"]}" text-anchor="end">'
        f'{_escape(_t("uptime", lang))} {uptime_str}</text>'
    )

    # Health badge (left of timestamp area)
    if data.healthy:
        badge_color = t["ok"]
        badge_text = _t("healthy", lang)
    else:
        badge_color = t["error"]
        badge_text = _t("unhealthy", lang)
    badge_w = 80
    badge_x = WIDTH - 16 - 220
    badge_y = HEADER_H // 2 - 12
    out.append(
        f'<rect x="{badge_x}" y="{badge_y}" width="{badge_w}" height="24" rx="12" ry="12" '
        f'fill="{badge_color}"/>'
    )
    out.append(
        f'<circle cx="{badge_x + 14}" cy="{badge_y + 12}" r="4" fill="{t["card"]}"/>'
    )
    out.append(
        f'<text x="{badge_x + 24}" y="{badge_y + 16}" font-size="12" font-weight="bold" '
        f'fill="{t["card"]}">{badge_text}</text>'
    )

    # ---- Body: 1x3 horizontal row of cards ----------------------------
    cards = [
        (1, _t("card_server", lang), _section_server),
        (2, _t("card_llm", lang), _section_llm),
        (3, _t("card_tools", lang), _section_tools),
    ]
    for idx, (num, title, renderer) in enumerate(cards):
        col = idx % COLS
        row = idx // COLS
        x = SIDE_PAD + col * (CARD_W + CARD_GAP)
        y = BODY_TOP + row * (CARD_H + CARD_GAP)
        out.append(_card(x, y, CARD_W, CARD_H, t, title, num))
        out.append(renderer(data, t, x + 16, y + 44, lang))

    if not data.healthy and data.error_message:
        out.append(
            f'<rect x="{SIDE_PAD}" y="{HEIGHT - 36}" width="{WIDTH - 2 * SIDE_PAD}" '
            f'height="24" rx="4" ry="4" fill="{t["error"]}" opacity="0.85"/>'
        )
        out.append(
            f'<text x="{SIDE_PAD + 12}" y="{HEIGHT - 19}" font-family="monospace" '
            f'font-size="12" fill="{t["card"]}">'
            f'{_escape(_t("collect_error", lang))} {_escape(data.error_message[:200])}</text>'
        )

    out.append("</svg>")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------


def write_svg(svg: str, out: Path) -> int:
    """Write ``svg`` to ``out``. Returns the number of bytes written."""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(svg, encoding="utf-8")
    return len(svg.encode("utf-8"))


# ---------------------------------------------------------------------------
# HTTP server for live preview
# ---------------------------------------------------------------------------


class _PanelHTTPHandler(http.server.SimpleHTTPRequestHandler):
    """Serves a single auto-refreshing HTML page that embeds the SVG."""

    svg_text: str = ""
    refresh_secs: int = 5
    bg_color: str = "#0F0F1E"

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/panel", "/index.html"):
            html = (
                "<!DOCTYPE html><html><head>"
                '<meta charset="utf-8">'
                f'<meta http-equiv="refresh" content="{self.refresh_secs}">'
                "<title>FullADDMAX-mcp panel</title>"
                "<style>"
                f"body{{margin:0;background:{self.bg_color};display:flex;"
                "justify-content:center;align-items:flex-start;padding:16px;}"
                "img{max-width:100%;height:auto;}"
                "</style></head><body>"
                f'<img src="/panel.svg" alt="panel">'
                "</body></html>"
            )
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/panel.svg":
            body = self.svg_text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        log.debug(format, *args)


def _make_handler(svg_text: str, refresh_secs: int, bg_color: str = "#0F0F1E") -> type:
    """Build a handler class with the SVG payload baked in."""
    return type(
        "_PanelHTTPHandlerBound",
        (_PanelHTTPHandler,),
        {"svg_text": svg_text, "refresh_secs": refresh_secs, "bg_color": bg_color},
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run(
    out: str | None = "docs/panel.svg",
    theme: str = "dark",
    lang: str = "en",
    serve: bool = False,
    port: int = 8765,
    refresh: int = 5,
) -> None:
    """Generate the SVG snapshot and optionally serve it over HTTP."""
    data = await collect()
    svg = render_svg(data, theme=theme, lang=lang)
    bg_color = THEMES.get(theme, THEMES["dark"])["bg"]

    if serve:
        out_path = Path(out) if out else Path("docs/panel.svg")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(svg, encoding="utf-8")

        async def _refresh_loop() -> None:
            while True:
                await asyncio.sleep(refresh)
                d = await collect()
                _PanelHTTPHandler.svg_text = render_svg(d, theme=theme, lang=lang)
                out_path.write_text(_PanelHTTPHandler.svg_text, encoding="utf-8")

        _PanelHTTPHandler.svg_text = svg
        # Spawn the background refresh in the event loop
        asyncio.create_task(_refresh_loop())

        handler_cls = _make_handler(svg, refresh, bg_color)
        with socketserver.ThreadingTCPServer(("127.0.0.1", port), handler_cls) as httpd:
            log.info("Panel serving on http://127.0.0.1:%d/panel", port)
            print(f"Panel URL: http://127.0.0.1:{port}/panel")
            print(f"Refreshing every {refresh}s. Ctrl+C to stop.")
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                print("\nStopping panel server.")
    else:
        if out is None:
            out = "docs/panel.svg"
        out_path = Path(out)
        n = write_svg(svg, out_path)
        print(f"Wrote {n:,} bytes SVG to {out_path}")


def main() -> None:  # pragma: no cover
    """CLI entry point used by ``python -m fulladdmax_mcp.panel``."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="fulladdmax-mcp panel",
        description="Generate a static SVG dashboard of the server state.",
    )
    parser.add_argument("--out", default="docs/panel.svg")
    # --theme and --lang are kept for CLI compatibility but the panel
    # now ships with a single dark theme and English UI.  Old values
    # (light/paper/zh) are accepted silently for back-compat.
    parser.add_argument("--theme", default="dark",
                        help=argparse.SUPPRESS)
    parser.add_argument(
        "--lang", default="en",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--refresh", type=int, default=5)
    args = parser.parse_args()
    asyncio.run(run(args.out, args.theme, args.lang, args.serve, args.port, args.refresh))


if __name__ == "__main__":  # pragma: no cover
    main()
