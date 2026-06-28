"""Multi-agent workflow operations for the ``agent`` mega tool.

The ``agent`` tool exposes 7 operations:

* ``orchestrator_run`` — planner → N workers → synthesizer
* ``parallel_agents_run`` — fan-out, up to 10 concurrent
* ``map_reduce_run`` — map phase, then reduce phase
* ``swarm_run`` — JSON handoffs between named agents
* ``auto_workflow`` — heuristic router that picks one of the above
  based on the task wording, the list of registered agent tools and
  the current LLM configuration.  Designed for "I just want to
  delegate a task without learning four APIs" callers.
* ``delegate`` — AI-driven self-spawning: the framework heuristically
  splits a task into N independent sub-tasks and runs them in
  parallel.  Sub-agents can themselves call ``delegate`` (recursion
  is bounded by ``max_depth``, default 2).
* ``hive_run`` — *三 6 六部 + 蜂巢* (Six-Ministries hive).  Spawns
  one worker per "ministry" (analyst / planner / executor / critic /
  synthesizer / dispatcher) all at once, with a second wave that
  feeds the critic's feedback back to the other ministries.  No
  hard cap on the number of sub-agents — the budget is enforced
  by the LLM's rate-limit + token accounting.

Lazy-loading
------------

The four LLM-bound operations (``orchestrator_run``,
``parallel_agents_run`` ``map_reduce_run`` ``swarm_run``,
``delegate``, ``hive_run``) check ``LLMConfig.is_configured()``
before dispatching.  When no key is available — neither explicit nor
host-inherited — the handler returns a Markdown hint block (NOT an
error) showing the user how to fix things in one line.  This keeps
the rest of the mega tool (``admin``, ``knowledge``, ``config``)
usable while the LLM is still unset.
"""

from __future__ import annotations

import re
from typing import Any

from .. import context as ctx_mod
from .. import i18n
from .. import server_internal
from ..dispatcher import OperationHandler, register_schema
from ..llm import get_config
from ..param_parser import FieldSpec
from ..tools import openai_tool_specs, registry as tool_registry


SCHEMAS: dict[str, dict[str, FieldSpec]] = {
    "orchestrator_run": {
        "task": FieldSpec(required=True, type=str),
        "num_workers": FieldSpec(required=False, type=int, default=3),
        "timeout": FieldSpec(required=False, type=float, default=300.0),
        "tools": FieldSpec(required=False, type=list, items_type=str, default=None),
    },
    "parallel_agents_run": {
        "tasks": FieldSpec(required=True, type=list, items_type=str),
        "max_concurrent": FieldSpec(required=False, type=int, default=10),
        "timeout": FieldSpec(required=False, type=float, default=300.0),
        "tools": FieldSpec(required=False, type=list, items_type=str, default=None),
    },
    "map_reduce_run": {
        "items": FieldSpec(required=True, type=list, items_type=str),
        "map_prompt": FieldSpec(required=False, type=str, default=""),
        "reduce_prompt": FieldSpec(required=False, type=str, default=""),
        "max_concurrent": FieldSpec(required=False, type=int, default=10),
        "timeout": FieldSpec(required=False, type=float, default=600.0),
        "tools": FieldSpec(required=False, type=list, items_type=str, default=None),
    },
    "swarm_run": {
        "initial_agent": FieldSpec(required=True, type=str),
        "task": FieldSpec(required=True, type=str),
        "max_handoffs": FieldSpec(required=False, type=int, default=8),
        "timeout": FieldSpec(required=False, type=float, default=300.0),
        "tools": FieldSpec(required=False, type=list, items_type=str, default=None),
        "agents_json": FieldSpec(required=False, type=str, default=""),
    },
    "auto_workflow": {
        "task": FieldSpec(required=True, type=str),
        "prefer": FieldSpec(
            required=False, type=str, default=""
        ),  # "orchestrator"|"parallel"|"map_reduce"|"swarm"
        "tools": FieldSpec(required=False, type=list, items_type=str, default=None),
        "items": FieldSpec(required=False, type=list, items_type=str, default=None),
    },
    # The recursive self-spawning op.  AI agents call this when a
    # sub-task is large enough to warrant parallel sub-agents of its
    # own.  The framework enforces a depth cap (default 2) so the
    # recursion can never explode.
    "delegate": {
        "task": FieldSpec(required=True, type=str),
        "children": FieldSpec(
            required=False, type=list, items_type=str, default=None
        ),  # pre-defined subtasks; if None we split heuristically
        "max_depth": FieldSpec(required=False, type=int, default=2),
        "max_parallel": FieldSpec(required=False, type=int, default=5),
        "split": FieldSpec(
            required=False, type=str, default="auto"
        ),  # "auto" | "always" | "never"
        "tools": FieldSpec(required=False, type=list, items_type=str, default=None),
    },
    # 三省六部蜂巢: full multi-ministerial cascade.  No silent
    # truncation — out-of-range waves raises so the user knows.
    # Hard limits (all configurable):
    #   * max_subagents  — total sub-agent budget (default 200)
    #   * max_depth      — recursion cap for nested hive_run calls
    #                      (None = uncapped)
    #   * max_waves      — hard ceiling on waves (default 20)
    "hive_run": {
        "task": FieldSpec(required=True, type=str),
        "departments": FieldSpec(
            required=False, type=list, items_type=str, default=None
        ),  # custom minister names; default = 6 classical ministries
        "waves": FieldSpec(required=False, type=int, default=2),
        "max_subagents": FieldSpec(required=False, type=int, default=200),
        "max_depth": FieldSpec(
            required=False, type=int, default=None
        ),  # None = uncapped; int = recursion cap
        "tools": FieldSpec(required=False, type=list, items_type=str, default=None),
    },
}


for _name, _schema in SCHEMAS.items():
    register_schema(_name, _schema)


# ---------------------------------------------------------------------------
# Lazy-load guard
# ---------------------------------------------------------------------------

_LAZY_HINT = """\
> ℹ️ **No LLM endpoint configured** — `{op}` needs a chat-completions URL to run.
>
> Pick the option that matches your setup:
>
> ```bash
> # A. Paste your OpenAI / OpenRouter / DeepSeek / Qwen key
> config(operation="configure_llm", params_json='{{"base_url":"https://api.openai.com/v1","api_key":"sk-..."}}')
>
> # B. Use a local LLM server (no key needed)
> config(operation="configure_llm", params_json='{{"base_url":"http://localhost:11434/v1","api_key":"ollama","model":"llama3.1"}}')
>
> # C. Have your MCP host (Claude Desktop / Cursor / Codex) inject its own creds
> #    — make sure FULLADDMAX_API_KEY or OPENAI_API_KEY is reachable in the
> #    shell that launches the MCP server.
> ```
>
> You can also preview what's configured: `admin(operation="ping")`.
> Run `fulladdmax-mcp panel` to see a live status dashboard.
>
> Until then, the other operations (`knowledge`, `config`, `admin`,
> and `auto_workflow`) still work — they don't need an LLM.
"""


def _lazy_hint(op: str) -> str:
    return _LAZY_HINT.format(op=op)


def _require_llm(op: str) -> str | None:
    """Return a lazy-hint Markdown block if the LLM is unset, else None.

    Set env ``FULLADDMAX_AGENT_OFFLINE=1`` to make every LLM-bound
    op return ``None`` here so it can take a deterministic stub
    path.  This lets the agent mega tool work end-to-end **without
    any LLM configured at all** — the stub returns a structured
    Markdown framework that the caller can fill in, copy, or use
    as a checklist.

    Reads the env at call time (not import time) so a test that
    sets ``FULLADDMAX_AGENT_OFFLINE=1`` after import still takes
    effect.
    """
    if _offline_agent_mode() or get_config().is_configured():
        return None
    return _lazy_hint(op)


# ---------------------------------------------------------------------------
# Offline stub workers
#
# When FULLADDMAX_AGENT_OFFLINE=1 (or all autodetected LLM keys are
# blank AND the offline flag is forced) the 7 LLM-bound ops fall
# through to deterministic Markdown skeletons.  No HTTP, no LLM, no
# third-party model, no GPU.  Useful for:
#   * local dev / CI smoke tests
#   * shipping a runnable demo with zero API keys
#   * giving an LLM agent a structured "thinking framework" it
#     can fill in itself rather than relying on a sub-LLM call
# ---------------------------------------------------------------------------

_OFFLINE_BANNER = (
    "\n> ℹ️  *Offline mode: structured Markdown framework.  "
    "Set `FULLADDMAX_API_KEY` (or run inside an MCP host that "
    "injects one) to swap this for real LLM output.*\n"
)


def _stub_worker(role: str, task: str, *, extra: str = "") -> str:
    """Render a single stub worker output."""
    return (
        f"### worker: {role}\n"
        f"**input task:** {task}\n"
        f"\n{_OFFLINE_BANNER}\n"
        f"**checklist (fill in or hand to a human / agent):**\n"
        f"\n"
        f"- **Goal** — restate what success looks like in 1 sentence.\n"
        f"- **Inputs** — list the data / docs / decisions needed.\n"
        f"- **Constraints** — time, budget, compliance, technical.\n"
        f"- **Approach** — the 3-5 steps you would take.\n"
        f"- **Risks** — what could go wrong + how to detect early.\n"
        f"- **Deliverable** — concrete artifact to ship at the end.\n"
        f"\n"
        f"{extra}"
    ).rstrip() + "\n"


def _stub_synthesizer(workers: list[tuple[str, str]]) -> str:
    """Combine N worker outputs into a stub synthesis."""
    body = "\n\n---\n\n".join(out for _, out in workers)
    return (
        f"### synthesis (offline)\n"
        f"**workers combined: {len(workers)}**\n"
        f"{_OFFLINE_BANNER}\n"
        f"{body}\n"
    )


def _stub_orchestrator(task: str, num_workers: int) -> str:
    workers = [(f"worker-{i + 1}", _stub_worker(f"worker-{i + 1}", task))
               for i in range(num_workers)]
    return (
        f"### orchestrator (offline stub)\n"
        f"**mission:** {task}\n"
        f"**planner → {num_workers} workers → synthesizer**\n"
        f"{_OFFLINE_BANNER}\n"
        + _stub_synthesizer(workers)
    )


def _stub_parallel(tasks: list[str], max_concurrent: int) -> str:
    workers = [(f"worker-{i + 1}", _stub_worker(f"worker-{i + 1}", t))
               for i, t in enumerate(tasks)]
    return (
        f"### parallel_agents_run (offline stub)\n"
        f"**{len(tasks)} tasks, max_concurrent={max_concurrent}**\n"
        f"{_OFFLINE_BANNER}\n"
        + _stub_synthesizer(workers)
    )


def _stub_map_reduce(items: list[str]) -> str:
    map_section = _stub_synthesizer(
        [(f"item-{i + 1}", _stub_worker(f"item-{i + 1}", it))
         for i, it in enumerate(items)]
    ).replace("synthesis (offline)", "map phase (offline)")
    return (
        f"### map_reduce_run (offline stub)\n"
        f"**{len(items)} items, no LLM**\n"
        f"{_OFFLINE_BANNER}\n"
        f"{map_section}\n"
        f"### reduce phase (offline)\n"
        f"{_OFFLINE_BANNER}\n"
        f"**checklist to combine all map outputs:**\n"
        f"\n"
        f"- Group by theme / common answer.\n"
        f"- Deduplicate overlapping findings.\n"
        f"- Rank by impact / confidence.\n"
        f"- Produce a 5-bullet executive summary.\n"
    )


def _stub_swarm(initial_agent: str, task: str, max_handoffs: int) -> str:
    handoffs = "\n".join(
        f"  {i + 1}.  ← handoff to next agent (decision: ?)"
        for i in range(max_handoffs)
    )
    return (
        f"### swarm_run (offline stub)\n"
        f"**initial_agent:** {initial_agent}\n"
        f"**mission:** {task}\n"
        f"**max_handoffs:** {max_handoffs}\n"
        f"{_OFFLINE_BANNER}\n"
        f"**handoff plan (template, fill in or hand to an LLM):**\n"
        f"\n"
        f"{_stub_worker(initial_agent, task)}\n"
        f"**handoff chain:**\n{handoffs}\n"
    )


def _stub_delegate(task: str, sub_tasks: list[str]) -> str:
    workers = [(f"subagent-{i + 1}", _stub_worker(f"subagent-{i + 1}", t))
               for i, t in enumerate(sub_tasks)]
    return (
        f"### delegate (offline stub)\n"
        f"**mission:** {task}\n"
        f"**sub-agents spawned: {len(sub_tasks)}**\n"
        f"{_OFFLINE_BANNER}\n"
        + _stub_synthesizer(workers)
    )


def _stub_hive(task: str, ministries: list[dict[str, str]],
               waves: int, critic_idx: int | None) -> str:
    lines: list[str] = [
        f"### hive_run (offline stub — 三 6 六部 + 蜂巢)",
        f"**mission:** {task}",
        f"**ministries ({len(ministries)}), waves={waves}**",
        _OFFLINE_BANNER,
        "**ministry outputs (filled by the framework, no LLM):**",
        "",
    ]
    for wave in range(1, waves + 1):
        lines.append(f"--- wave {wave}/{waves} ---")
        for m in ministries:
            label = m["name"]
            if wave > 1 and critic_idx is not None and ministries.index(m) == critic_idx:
                label += " (refining per critic feedback)"
            lines.append(
                f"### {label}\n"
                f"**angle:** {m['angle']}\n"
                f"{_OFFLINE_BANNER}\n"
                f"**offline framework:**\n"
                f"\n"
                f"- **What this ministry observes** — (fill)\n"
                f"- **What this ministry recommends** — (fill)\n"
                f"- **What this ministry blocks** — (fill)\n"
                f"- **What this ministry needs from others** — (fill)\n"
            )
    lines.append("--- final synthesis (offline) ---")
    lines.append(_OFFLINE_BANNER)
    lines.append(
        "Each ministry's framework above is ready to be filled in, "
        "or handed to a human reviewer, or executed by an LLM agent "
        "that has its own credentials.  The framework guarantees "
        "that no angle is forgotten."
    )
    return "\n".join(lines)


# Offline-mode toggles are evaluated lazily so the env can change
# at runtime (e.g. a test sets it, a CLI tool unsets it).
def _offline_agent_mode() -> bool:
    import os
    return os.environ.get("FULLADDMAX_AGENT_OFFLINE") == "1"


# Re-bind at import time so the module-level constant is current.
_OFFLINE_AGENT_MODE = _offline_agent_mode()


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def _orchestrator_run(
    *,
    task: str,
    num_workers: int = 3,
    timeout: float = 300.0,
    tools: list[str] | None = None,
    **_: object,
) -> str:
    if (hint := _require_llm("orchestrator_run")) is not None:
        return hint
    if _offline_agent_mode() or not get_config().is_configured():
        # Offline mode: deterministic stub.
        return _stub_orchestrator(task, num_workers)
    return await server_internal.orchestrator_run(
        task,
        num_workers=num_workers,
        timeout=timeout,
        tools=tools,
    )


async def _parallel_agents_run(
    *,
    tasks: list[str],
    max_concurrent: int = 10,
    timeout: float = 300.0,
    tools: list[str] | None = None,
    **_: object,
) -> str:
    if (hint := _require_llm("parallel_agents_run")) is not None:
        return hint
    if not get_config().is_configured():
        return _stub_parallel(tasks, max_concurrent)
    return await server_internal.parallel_agents_run(
        tasks,
        max_concurrent=max_concurrent,
        timeout=timeout,
        tools=tools,
    )


async def _map_reduce_run(
    *,
    items: list[str],
    map_prompt: str = "",
    reduce_prompt: str = "",
    max_concurrent: int = 10,
    timeout: float = 600.0,
    tools: list[str] | None = None,
    **_: object,
) -> str:
    if (hint := _require_llm("map_reduce_run")) is not None:
        return hint
    if not get_config().is_configured():
        return _stub_map_reduce(items)
    return await server_internal.map_reduce_run(
        items,
        map_prompt=map_prompt,
        reduce_prompt=reduce_prompt,
        max_concurrent=max_concurrent,
        timeout=timeout,
        tools=tools,
    )


async def _swarm_run(
    *,
    initial_agent: str,
    task: str,
    max_handoffs: int = 8,
    timeout: float = 300.0,
    tools: list[str] | None = None,
    agents_json: str = "",
    **_: object,
) -> str:
    if (hint := _require_llm("swarm_run")) is not None:
        return hint
    if _offline_agent_mode() or not get_config().is_configured():
        return _stub_swarm(initial_agent, task, max_handoffs)
    return await server_internal.swarm_run(
        initial_agent,
        task,
        max_handoffs=max_handoffs,
        timeout=timeout,
        tools=tools,
        agents_json=agents_json,
    )


# ---------------------------------------------------------------------------
# auto_workflow: heuristic router
# ---------------------------------------------------------------------------

# Keyword groups drive the routing decision.  Each list is OR-ed
# together; the first group with a hit wins.
_ROUTING_RULES: list[tuple[str, list[str]]] = [
    ("map_reduce", [
        r"map[-_ ]reduce", r"\bmap\b.*\bover\b", r"\bfor each\b", r"\bshard\b",
        r"\bchunk\b.*\bthrough\b", r"\bbatch\b.*\bprocess\b", r"\bcollect\b.*\bsummar",
    ]),
    ("swarm", [
        r"\bhandoff\b", r"\bswarm\b", r"\bcritic\b", r"\breviewer\b",
        r"\bcoder\b.*\bwriter\b", r"\bteam of\b", r"\bdebate\b",
        r"\bchain of agents\b",
    ]),
    ("parallel", [
        r"\bin parallel\b", r"\bconcurrent(ly)?\b", r"\bfan[- ]?out\b",
        r"\bmultiple agents?\b.*\bat once\b", r"\brun all\b",
    ]),
]

_ROUTING_RE = [
    (name, re.compile("|".join(patterns), re.IGNORECASE))
    for name, patterns in _ROUTING_RULES
]


def _pick_workflow(task: str, prefer: str, has_items: bool) -> str:
    """Heuristic choice of which mega tool best fits the task."""
    if prefer:
        key = prefer.strip().lower()
        if key in ("orchestrator", "parallel", "map_reduce", "swarm"):
            return key
    # 1) explicit ``items`` → map_reduce
    if has_items:
        return "map_reduce"
    # 2) keyword scoring
    for name, rx in _ROUTING_RE:
        if rx.search(task):
            return name
    # 3) default
    return "orchestrator"


def _describe_tools(tools: list[str] | None) -> str:
    """Render a one-line summary of the agent tools that will be exposed."""
    if not tools:
        # `None` = expose all registered tools except excluded ones.
        names = tool_registry.names()
    else:
        names = list(tools)
    if not names:
        return "No agent tools registered (workflow will be chat-only)."
    specs = openai_tool_specs()
    lines = [f"  - {n} (params: " + ", ".join(
        (specs[i]["function"]["parameters"].get("properties") or {}).keys()
        if i < len(specs) else "?"
    ) + ")" for i, n in enumerate(names)]
    return "\n".join(lines)


async def _auto_workflow(
    *,
    task: str,
    prefer: str = "",
    tools: list[str] | None = None,
    items: list[str] | None = None,
    **_: object,
) -> str:
    """Route ``task`` to the most appropriate workflow.

    Returns a Markdown plan that previews the chosen workflow, the
    tools it will expose, and the effective parameters — **then**
    runs it.  If the LLM is unset, the call is rejected with a hint
    block (same as the explicit workflow ops).
    """
    if (hint := _require_llm("auto_workflow")) is not None:
        return hint

    choice = _pick_workflow(task, prefer, has_items=bool(items))
    tool_summary = _describe_tools(tools)

    plan_lines = [
        f"### auto_workflow → `{choice}`",
        "",
        f"**Task:** {task}",
        "",
        f"**Tools to expose:**\n{tool_summary}",
        "",
    ]

    if choice == "orchestrator":
        plan_lines += [
            "**Plan:** planner splits the task into 3 subtasks → 3 "
            "workers run in parallel → synthesizer merges.",
            "",
        ]
        body = await server_internal.orchestrator_run(task, num_workers=3,
                                                     tools=tools)
    elif choice == "parallel":
        sub_tasks = items or [
            f"Perspective {i + 1} on: {task}"
            for i in range(3)
        ]
        plan_lines += [
            f"**Plan:** run {len(sub_tasks)} sub-tasks concurrently.",
            "",
        ]
        body = await server_internal.parallel_agents_run(sub_tasks,
                                                        tools=tools)
    elif choice == "map_reduce":
        actual_items = items or [task]
        plan_lines += [
            f"**Plan:** map over {len(actual_items)} items → reduce.",
            "",
        ]
        body = await server_internal.map_reduce_run(actual_items,
                                                    tools=tools)
    elif choice == "swarm":
        initial = "coder"
        plan_lines += [
            f"**Plan:** start with `{initial}` and handoff up to 8 times.",
            "",
        ]
        body = await server_internal.swarm_run(initial, task, tools=tools)
    else:  # pragma: no cover
        body = "ERROR: auto_workflow: unknown choice"

    return "\n".join(plan_lines) + "\n---\n\n" + body


HANDLERS: dict[str, OperationHandler] = {
    "orchestrator_run": _orchestrator_run,
    "parallel_agents_run": _parallel_agents_run,
    "map_reduce_run": _map_reduce_run,
    "swarm_run": _swarm_run,
    "auto_workflow": _auto_workflow,
}


# ---------------------------------------------------------------------------
# delegate: AI-driven self-splitting of sub-tasks
# ---------------------------------------------------------------------------

# Heuristic split patterns.  We split on:
#   - CJK: ， 、 ； 。 \n  (commas, enumeration, semicolon, period, newline)
#   - EN :  " and ", " then ", ". ", "? ", "! ", "; ", "\n", " & ", ", "
# Each split candidate shorter than MIN_SUBTASK_CHARS is glued to the
# next one to avoid trivial "and" fragments.
_MIN_SUBTASK_CHARS = 12
# NOTE: a single alternation regex causes Python's re to mishandle
# quantifiers in the branches (e.g. `,\s+` greedily consumes the
# comma in the surrounding `,\s+|\b and \b|...`).  We pre-tokenise
# by each delimiter separately and then merge.
_SPLIT_PATTERNS_EN = [
    re.compile(r",\s+"),
    re.compile(r"\.\s+"),
    re.compile(r"\?\s+"),
    re.compile(r"!\s+"),
    re.compile(r";\s+"),
    re.compile(r"\b and \b", re.IGNORECASE),
    re.compile(r"\b then \b", re.IGNORECASE),
    re.compile(r"\b&\s"),
    re.compile(r"\n+"),
]
_SPLIT_PATTERNS_CJK = [
    re.compile(r"，"),
    re.compile(r"、"),
    re.compile(r"；"),
    re.compile(r"。"),
    re.compile(r"\n+"),
]


def _heuristic_split(task: str) -> list[str]:
    """Heuristically split ``task`` into 1..N independent sub-tasks.

    Used by :func:`_delegate` when the caller didn't pre-define
    ``children``.  Returns a list with at least one element.  If the
    task looks atomic, returns ``[task]`` verbatim.
    """
    if not task or not task.strip():
        return [task]
    # Split on the strongest delimiters first, then recursively split
    # each piece on weaker ones until no more splits are possible.
    fragments: list[str] = [task]
    for rx in _SPLIT_PATTERNS_CJK + _SPLIT_PATTERNS_EN:
        next_fragments: list[str] = []
        for frag in fragments:
            next_fragments.extend(p for p in rx.split(frag) if p)
        fragments = next_fragments
        if len(fragments) >= 8:   # cap so we don't fragment 1000s of items
            break
    # Strip whitespace/punctuation from each fragment.
    cleaned = [
        p.strip(" \t\r\n，。、；.?!;,")
        for p in fragments
    ]
    cleaned = [p for p in cleaned if p]
    if not cleaned:
        return [task]
    # Glue ONLY obvious conjunction fragments (length <4 chars) back
    # onto the previous one.  Real sub-tasks (>=4 chars) stay separate.
    out: list[str] = []
    for p in cleaned:
        if out and len(p) < 4:
            out[-1] = (out[-1] + " " + p).strip()
        else:
            out.append(p)
    return out or [task]


def _should_auto_split(task: str) -> bool:
    """Decide whether ``auto`` split should fire for this task.

    Uses the **raw** split count (before glue-back of short
    fragments) so a comma list of 5 single-letter items still counts
    as "many parts, split them".  Falls back to length+conjunction
    heuristic for long monolithic tasks.
    """
    # Raw split count — split on the strongest delimiter only and
    # count how many pieces we get.  We use CJK + the comma
    # pattern (the two most common "list" indicators).
    raw = task
    for rx in _SPLIT_PATTERNS_CJK + [re.compile(r",\s+"),
                                      re.compile(r"\.\s+"),
                                      re.compile(r"\b and \b", re.IGNORECASE)]:
        raw = rx.sub("\x1f", raw)  # sentinel
    raw_fragments = [p for p in raw.split("\x1f") if p.strip()]
    if len(raw_fragments) >= 2:
        return True
    if len(task) > 200 and re.search(
        r"\b(and|then|plus|also|additionally)\b|，|、|；", task, re.IGNORECASE
    ):
        return True
    return False


async def _delegate(
    *,
    task: str,
    children: list[str] | None = None,
    max_depth: int = 2,
    max_parallel: int = 5,
    split: str = "auto",
    tools: list[str] | None = None,
    **_kwargs: object,
) -> str:
    """AI-driven sub-task parallelisation.

    The caller (an LLM agent or a human) hands a ``task`` to the
    framework; the framework decides whether to split it into
    independent sub-tasks and run them in parallel.  Each sub-task
    becomes its own ``parallel_agents_run`` invocation (so a worker
    can itself decide to call ``delegate`` further — recursion is
    bounded by ``max_depth``).

    Modes
    -----
    * ``"auto"``  — split heuristically; atomic tasks run as a single
      sub-agent.  Default.
    * ``"always"`` — force split; if heuristic returns 1 item we add
      a "and consider alternatives" sibling so at least 2 workers run.
    * ``"never"``  — single worker, no split.

    Returns a Markdown report showing the chosen sub-tasks, each
    worker's result, and a final synthesis.
    """
    if (hint := _require_llm("delegate")) is not None:
        return hint

    # Resolve sub-tasks.
    if children:
        sub_tasks = [c.strip() for c in children if c and c.strip()]
    elif split == "never":
        sub_tasks = [task]
    elif split == "always":
        sub_tasks = _heuristic_split(task)
        if len(sub_tasks) < 2:
            sub_tasks = [task, f"Critically review and provide an alternative take on: {task}"]
    else:  # auto
        sub_tasks = _heuristic_split(task) if _should_auto_split(task) else [task]

    # Cap concurrency.
    sub_tasks = sub_tasks[:max(1, max_parallel)]

    # Offline mode: no LLM configured → return stub framework.
    if not get_config().is_configured():
        return _stub_delegate(task, sub_tasks)

    # Recursion guard: read current depth from the session context.
    # The mega-tool dispatcher already binds the top-level session_id;
    # child calls get nested depths through the per-call ctx binding.
    depth = int(ctx_mod.get("delegate_depth", 0) or 0)
    depth_marker = "  " * depth
    if depth >= max_depth:
        return await server_internal.parallel_agents_run([task], tools=tools)

    # Bump the depth marker for the child calls so nested delegates
    # can see the chain.
    ctx_mod.put("delegate_depth", depth + 1)

    # Run the sub-agents in parallel.
    n = len(sub_tasks)
    lines = [
        f"### delegate (depth={depth}, sub-agents={n})",
        "",
        f"**Original task:** {task}",
        "",
        f"**Sub-tasks (after heuristic split, mode={split!r}):**",
    ]
    for i, st in enumerate(sub_tasks, 1):
        lines.append(f"  {i}. {st}")
    lines.append("")

    if n == 1:
        # No split actually happened — just run the single agent.
        body = await server_internal.parallel_agents_run(
            sub_tasks, max_concurrent=1, tools=tools,
        )
        lines.append("--- worker 1 ---")
        lines.append(body)
    else:
        # Parallel run of all sub-tasks.
        body = await server_internal.parallel_agents_run(
            sub_tasks, max_concurrent=min(n, max_parallel), tools=tools,
        )
        lines.append("--- workers (parallel) ---")
        lines.append(body)

    # Reset depth so siblings at the same level see the right value.
    ctx_mod.put("delegate_depth", depth)
    return "\n".join(lines)


HANDLERS["delegate"] = _delegate


# ---------------------------------------------------------------------------
# hive_run: 三省六部 + 蜂巢出动 (no hard cap on sub-agent count)
# ---------------------------------------------------------------------------

# Classical 6 ministries (三省六部 metaphor).  Each ministry is
# a different "angle of attack" on the same task — the hive fans
# out all 6 at once, then re-fans with critic feedback in wave 2.
_DEFAULT_MINISTRIES = [
    {
        "name": "吏部 (Personnel)",
        "angle": "stakeholders & ownership: who does what, who blocks what",
        "system": "You are the 吏部 (Personnel) minister. Map every "
                  "stakeholder, role, and gatekeeper. Output a Markdown "
                  "list of actors, motivations, and decision points.",
    },
    {
        "name": "户部 (Revenue)",
        "angle": "cost, ROI, pricing, budget, time-to-market",
        "system": "You are the 户部 (Revenue) minister. Quantify every "
                  "cost, benefit, and risk. Output a Markdown table with "
                  "rows = (item, low, mid, high) and a 1-paragraph exec summary.",
    },
    {
        "name": "礼部 (Protocol)",
        "angle": "compliance, ethics, UX, presentation, standards",
        "system": "You are the 礼部 (Protocol) minister. Review the task "
                  "for compliance, accessibility, ethics, and presentation. "
                  "Output a Markdown checklist with PASS/FAIL/REVIEW items.",
    },
    {
        "name": "兵部 (Defense)",
        "angle": "risks, edge cases, failure modes, scaling",
        "system": "You are the 兵部 (Defense) minister. List every plausible "
                  "failure mode, edge case, and scaling bottleneck. Output a "
                  "Markdown table (mode | likelihood | impact | mitigation).",
    },
    {
        "name": "刑部 (Justice)",
        "angle": "critic / red-team / adversarial: what could go wrong?",
        "system": "You are the 刑部 (Justice) minister — the red team. "
                  "Argue AGAINST the proposed approach. Find the weakest "
                  "assumption and the single most likely reason this fails. "
                  "Output a Markdown 'critic's report'.",
    },
    {
        "name": "工部 (Engineering)",
        "angle": "concrete plan, milestones, code/architecture",
        "system": "You are the 工部 (Engineering) minister. Produce a "
                  "concrete plan: architecture, milestones, deliverables. "
                  "Output a Markdown numbered list of steps with estimates.",
    },
]


_HIVE_MAX_WAVES = 20           # hard ceiling on waves (any value above is an error)


async def _hive_run(
    *,
    task: str,
    departments: list[str] | None = None,
    waves: int = 2,
    max_subagents: int = 200,
    max_depth: int | None = None,
    tools: list[str] | None = None,
    **_kwargs: object,
) -> str:
    """三 6 六部 + 蜂巢  — fan out N ministers in parallel, then re-fan.

    Parameters
    ----------
    task : str
        The mission.  All ministers attack the same task from their
        own angle.
    departments : list[str] | None
        Custom minister names.  If ``None`` (default), the six
        classical ministries (吏/户/礼/兵/刑/工) are used.
    waves : int
        Number of waves.  Wave 1 is the initial fan-out; wave 2+
        re-feeds the critic's (刑部) feedback to the other ministries
        so they can refine.  Default 2, hard ceiling ``max_waves=20``.
        Passing a value above the ceiling **raises** an error
        (no silent truncation) so the user knows.
    max_subagents : int
        Hard cap on total sub-agents fired across all waves.
        Default 200.  When hit, the current wave is skipped and
        the run collapses to the synthesiser.
    max_depth : int | None
        Recursion cap for nested ``hive_run`` calls driven by the
        LLM.  ``None`` (default) = uncapped.  When the LLM tries
        to call ``hive_run`` inside ``hive_run`` and the running
        depth would exceed ``max_depth``, the nested call is
        downgraded to a single ``parallel_agents_run`` so the
        cascade can't loop forever.
    tools : list[str] | None
        Optional list of registered agent-tool names to hand to each
        worker (e.g. ``["obsidian_search", "file_search"]``).

    Returns
    -------
    str
        A Markdown report listing every minister, every wave, and
        the final synthesised answer.
    """
    # ---- Hard guards: fail loud, not silent.  These run BEFORE
    #      the LLM check so a misconfigured caller gets the same
    #      error whether or not an LLM is wired up. ----
    if waves < 1:
        waves = 1
    if waves > _HIVE_MAX_WAVES:
        raise ValueError(i18n.t("hive_waves_range", got=waves))
    if max_subagents < 1:
        raise ValueError(
            f"hive_run: max_subagents={max_subagents} must be >= 1."
        )

    if (hint := _require_llm("hive_run")) is not None:
        return hint

    # ---- Offline mode: deterministic stub.  Resolves ministries
    #      exactly like the LLM path would (so caller sees consistent
    #      labels + sub-agent count) but emits Markdown frameworks. ----
    if not get_config().is_configured():
        # Resolve departments the same way as the live path.
        if departments and isinstance(departments[0], str):
            stub_ministries: list[dict[str, str]] = []
            for name in departments:
                match = next(
                    (m for m in _DEFAULT_MINISTRIES
                     if m["name"].split(" ")[0] in name or name in m["name"]),
                    None,
                )
                if match:
                    stub_ministries.append(match)
                else:
                    stub_ministries.append({
                        "name": name,
                        "angle": f"specialty perspective: {name}",
                        "system": "",
                    })
        else:
            stub_ministries = list(_DEFAULT_MINISTRIES)
        stub_critic = next(
            (i for i, m in enumerate(stub_ministries)
             if "critic" in m["angle"]
             or "Justice" in m["name"] or "刑部" in m["name"]),
            None,
        )
        return _stub_hive(task, stub_ministries, waves, stub_critic)

    # ---- Recursion guard (cross-call, like _delegate's delegate_depth). ----
    current_depth = int(ctx_mod.get("hive_depth", 0) or 0)
    if max_depth is not None and current_depth >= max_depth:
        # Downgrade: run a single parallel pass instead of cascading.
        lines = [
            f"### hive_run (downgraded: depth {current_depth} >= "
            f"max_depth {max_depth})",
            "",
            f"**Mission:** {task}",
            "",
            f"**Reason:** the caller is already inside a hive_run "
            f"at depth {current_depth}.  To prevent runaway recursion "
            f"we collapsed this call to a single ``parallel_agents_run`` "
            f"of the original task.",
            "",
        ]
        body = await server_internal.parallel_agents_run(
            [task], max_concurrent=1, tools=tools,
        )
        lines.append("--- single-pass fallback ---")
        lines.append(body)
        return "\n".join(lines)

    # ---- Resolve ministries ----
    if departments and isinstance(departments[0], str):
        ministries: list[dict[str, str]] = []
        for name in departments:
            match = next(
                (m for m in _DEFAULT_MINISTRIES
                 if m["name"].split(" ")[0] in name or name in m["name"]),
                None,
            )
            if match:
                ministries.append(match)
            else:
                ministries.append({
                    "name": name,
                    "angle": f"specialty perspective: {name}",
                    "system": (f"You are the {name} minister. Apply your "
                               f"specialty perspective to the task and "
                               f"output a Markdown report."),
                })
    else:
        ministries = list(_DEFAULT_MINISTRIES)

    # Soft cap: 6 ministries × waves × heuristic expansion.  We
    # estimate and bail out before exceeding max_subagents.
    estimated = len(ministries) * waves
    if estimated > max_subagents:
        waves = max(1, max_subagents // len(ministries))

    lines: list[str] = [
        f"### hive_run (三 6 六部 + 蜂巢) — {len(ministries)} ministries, "
        f"{waves} wave(s), depth {current_depth}",
        "",
        f"**Mission:** {task}",
        "",
        f"**Ministries ({len(ministries)}):**",
    ]
    for m in ministries:
        lines.append(f"  - **{m['name']}** — {m['angle']}")

    critic_idx = next(
        (i for i, m in enumerate(ministries) if "critic" in m["angle"]
         or "Justice" in m["name"] or "刑部" in m["name"]),
        None,
    )
    critic_feedback: str = ""
    subagent_count = 0
    all_reports: list[dict[str, Any]] = []

    # ---- Enter child context: bump hive_depth so any nested
    #      hive_run call sees the new depth. ----
    ctx_mod.put("hive_depth", current_depth + 1)
    try:
        for wave in range(1, waves + 1):
            lines.append("")
            lines.append(f"--- wave {wave}/{waves} ---")
            sub_tasks: list[str] = []
            for m in ministries:
                if wave == 1:
                    sub = f"[{m['name']}] {task}"
                else:
                    sub = (
                        f"[{m['name']}] REFINEMENT wave {wave} for: {task}\n\n"
                        f"Previous critic's report:\n{critic_feedback}\n\n"
                        f"Update your previous {m['name']} output to address "
                        f"the critic's concerns."
                    )
                sub_tasks.append(sub)
            if subagent_count + len(sub_tasks) > max_subagents:
                lines.append(
                    f"  (budget hit: {subagent_count} used, "
                    f"capping at max_subagents={max_subagents}; this wave "
                    f"is the synthesiser only)")
                break

            body = await server_internal.parallel_agents_run(
                sub_tasks,
                max_concurrent=len(sub_tasks),  # no cap — fire all at once
                tools=tools,
            )
            subagent_count += len(sub_tasks)
            lines.append(body)
            all_reports.append({"wave": wave, "output": body})

            # Pull the critic's output for the next-wave feedback loop.
            if critic_idx is not None and wave < waves:
                parts = body.split("--- worker ")
                if len(parts) > critic_idx:
                    critic_feedback = "--- worker " + parts[critic_idx]
    finally:
        # ---- Leave child context: restore depth for siblings. ----
        ctx_mod.put("hive_depth", current_depth)

    lines.append("")
    lines.append("--- final synthesis ---")
    lines.append(
        f"Sub-agents fired: **{subagent_count}** "
        f"({len(ministries)} ministries × {waves} wave(s) approx, "
        f"capped at max_subagents={max_subagents}).")
    lines.append(
        "Combine the wave outputs above into a single coherent answer. "
        "The 刑部 (critic) report is the most important signal; if it "
        "rejected the main approach, prefer its alternative.")

    return "\n".join(lines)


HANDLERS["hive_run"] = _hive_run
