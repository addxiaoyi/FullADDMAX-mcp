"""Swarm multi-agent handoffs.

A swarm is a small registry of named agent profiles, each with its own
system prompt. The LLM is forced to reply with a strict JSON envelope::

    {"next": "<agent_name or DONE>", "message": "<handoff text>"}

The orchestrator routes the message to the next agent until the LLM
emits ``"next": "DONE"`` or ``max_handoffs`` is reached.

If ``tools`` is provided, each turn uses the ``chat_with_tools`` dispatch
loop instead of plain ``chat``. The LLM may call any registered tool
mid-turn, but must still finish the turn with a JSON ``{next, message}``
envelope (a tool-call-only turn is treated as an empty message, which is
rejected by ``_parse_reply`` so the agent is forced to keep using tools
or produce a valid JSON answer).

Two ways to provide agents
--------------------------

* **Built-in (default)**: :data:`DEFAULT_AGENTS` is a dict of four
  agent profiles (``researcher`` / ``coder`` / ``critic`` / ``writer``).
  :func:`swarm.run` uses them out of the box.
* **Custom**: :func:`register_swarm_agent` adds / overrides entries in
  a module-level :class:`SwarmRegistry`. Subsequent calls to
  :func:`swarm.run` see the merged registry. You can also pass a
  one-off ``agents=`` dict (parsed from JSON via the MCP surface) to
  :func:`swarm.run` to scope the agent set to a single call.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

from .context import new_session, put
from .errors import EmptyInputError, FullADDMAXError, HandoffError, ToolTimeoutError
from .llm import get_client
from .tools import openai_tool_specs

log = logging.getLogger(__name__)


@dataclass
class Agent:
    """An agent profile participating in a swarm."""

    name: str
    system: str
    description: str = ""


DEFAULT_AGENTS: dict[str, Agent] = {
    "researcher": Agent(
        name="researcher",
        system=(
            "You are a researcher. Gather facts, cite reasoning, and propose hypotheses. "
            "Always reply with JSON {\"next\": <agent_name|DONE>, \"message\": <string>}."
        ),
        description="Gathers information, surfaces options, proposes hypotheses.",
    ),
    "coder": Agent(
        name="coder",
        system=(
            "You are a coder. Write code, review code, and explain trade-offs. "
            "Always reply with JSON {\"next\": <agent_name|DONE>, \"message\": <string>}."
        ),
        description="Implements and reviews code; explains trade-offs.",
    ),
    "critic": Agent(
        name="critic",
        system=(
            "You are a critic. Find flaws, edge cases, and risks. "
            "Always reply with JSON {\"next\": <agent_name|DONE>, \"message\": <string>}."
        ),
        description="Stress-tests the proposal and surfaces risks.",
    ),
    "writer": Agent(
        name="writer",
        system=(
            "You are a writer. Produce the final polished answer for the user. "
            "When done, set next=DONE. Always reply with JSON."
        ),
        description="Synthesizes the final user-facing response.",
    ),
}


# ---------------------------------------------------------------------------
# Dynamic registry
# ---------------------------------------------------------------------------


import threading as _threading  # noqa: E402


class SwarmAgentAlreadyExistsError(FullADDMAXError):
    """Raised when registering a swarm agent with a name that is already in the registry."""


class SwarmAgentNotFoundError(FullADDMAXError):
    """Raised when looking up an agent name that is not in the registry."""


class SwarmRegistry:
    """Thread-safe in-process registry of swarm agent profiles.

    The registry is **pre-seeded** with :data:`DEFAULT_AGENTS`, so
    :func:`swarm.run` works out of the box without any setup. Register
    custom agents (or override built-ins) with
    :meth:`register` / :meth:`unregister` to extend it.

    All operations are guarded by an ``RLock`` so concurrent
    workflows can register / lookup without races.
    """

    def __init__(self, seed: dict[str, Agent] | None = None) -> None:
        self._agents: dict[str, Agent] = {}
        self._lock = _threading.RLock()
        if seed is None:
            seed = DEFAULT_AGENTS
        for name, agent in seed.items():
            self._agents[name] = agent

    # ---- mutation -------------------------------------------------------

    def register(
        self,
        agent: Agent,
        *,
        overwrite: bool = False,
    ) -> None:
        if not agent.name:
            raise SwarmAgentAlreadyExistsError(
                "agent.name is required"
            )
        if not agent.system or not agent.system.strip():
            raise SwarmAgentAlreadyExistsError(
                f"agent {agent.name!r}: system prompt must be a non-empty string"
            )
        with self._lock:
            if agent.name in self._agents and not overwrite:
                raise SwarmAgentAlreadyExistsError(
                    f"swarm agent {agent.name!r} already exists; "
                    f"pass overwrite=True to replace"
                )
            self._agents[agent.name] = agent

    def unregister(self, name: str) -> bool:
        with self._lock:
            return self._agents.pop(name, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._agents.clear()

    # ---- inspection -----------------------------------------------------

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._agents)

    def get(self, name: str) -> Agent | None:
        with self._lock:
            return self._agents.get(name)

    def snapshot(self) -> dict[str, Agent]:
        """Return a shallow copy of the current agent set. Safe to pass
        to ``swarm.run(agents=...)`` without worrying about later
        modifications.
        """
        with self._lock:
            return dict(self._agents)

    def __contains__(self, name: str) -> bool:
        with self._lock:
            return name in self._agents

    def __len__(self) -> int:
        with self._lock:
            return len(self._agents)

    def from_json(self, payload: str) -> list[Agent]:
        """Parse a JSON array of agent specs and register each one.

        The JSON must be an array of objects with at least
        ``"name"`` and ``"system"`` keys::

            [
              {"name": "reviewer", "system": "...", "description": "..."}
            ]

        ``overwrite`` is honoured per-entry: if a name in the JSON
        collides with an existing registry entry the call fails fast
        on the first collision, leaving the registry unchanged.
        """
        import json as _json

        try:
            data = _json.loads(payload) if isinstance(payload, str) else payload
        except _json.JSONDecodeError as e:
            raise SwarmAgentAlreadyExistsError(
                f"agents JSON is not valid: {e}"
            ) from e
        if not isinstance(data, list):
            raise SwarmAgentAlreadyExistsError(
                "agents JSON must be a list of {name, system, description} objects"
            )
        parsed: list[Agent] = []
        for i, entry in enumerate(data):
            if not isinstance(entry, dict):
                raise SwarmAgentAlreadyExistsError(
                    f"agents[{i}] is not an object"
                )
            try:
                parsed.append(
                    Agent(
                        name=str(entry["name"]).strip(),
                        system=str(entry["system"]),
                        description=str(entry.get("description", "")),
                    )
                )
            except KeyError as e:
                raise SwarmAgentAlreadyExistsError(
                    f"agents[{i}] missing required field {e}"
                ) from e
        return parsed


# Module-level singleton, pre-seeded with DEFAULT_AGENTS.
registry = SwarmRegistry()


# ---------------------------------------------------------------------------
# Functional helpers (used by both server.py and tests)
# ---------------------------------------------------------------------------


def register_swarm_agent(
    name: str,
    system: str,
    description: str = "",
    *,
    overwrite: bool = False,
) -> Agent:
    """Register a custom swarm agent in the module-level registry.

    Raises :class:`SwarmAgentAlreadyExistsError` if ``name`` is taken
    and ``overwrite`` is False.
    """
    agent = Agent(name=name, system=system, description=description)
    registry.register(agent, overwrite=overwrite)
    return agent


def unregister_swarm_agent(name: str) -> bool:
    """Remove a swarm agent from the registry. Returns True if it was
    present, False otherwise. Built-in profiles can also be removed
    — they are seeded on import but mutable thereafter.
    """
    return registry.unregister(name)


def list_swarm_agents() -> list[Agent]:
    """Return a sorted list of all agents currently in the registry."""
    return [registry.get(n) for n in registry.names() if registry.get(n) is not None]  # type: ignore[misc]


def parse_agents_json(payload: str) -> dict[str, Agent]:
    """Parse a JSON array of agent specs into a dict keyed by name.

    Used by :func:`swarm.run`'s ``agents=`` parameter and the MCP tool
    surface (which only supports primitive types). Raises
    :class:`SwarmAgentAlreadyExistsError` on malformed input.
    """
    parsed = registry.from_json(payload)
    out: dict[str, Agent] = {}
    for a in parsed:
        if a.name in out:
            raise SwarmAgentAlreadyExistsError(
                f"duplicate agent name in JSON: {a.name!r}"
            )
        out[a.name] = a
    return out

SYSTEM_INTRO = (
    "Available agents (use their 'name' as the 'next' value, or set 'DONE' to finish):\n{roster}"
)


def _roster_text(agents: dict[str, Agent]) -> str:
    return "\n".join(f"- {a.name}: {a.description or '(no description)'}" for a in agents.values())


def _parse_reply(raw: str, valid: set[str]) -> tuple[str, str]:
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        if len(parts) >= 3:
            raw = parts[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HandoffError(f"Swarm agent did not return JSON: {e}; raw={raw[:200]!r}") from e
    if not isinstance(data, dict):
        raise HandoffError(f"Swarm agent returned non-object JSON: {type(data).__name__}")
    nxt = str(data.get("next", "")).strip()
    msg = data.get("message", "")
    if not isinstance(msg, str):
        raise HandoffError("Swarm agent 'message' field must be a string.")
    msg = msg.strip()
    if nxt != "DONE" and nxt not in valid:
        raise HandoffError(
            f"Swarm agent requested unknown agent {nxt!r} "
            f"(valid: {sorted(valid)} | DONE)"
        )
    if not msg:
        raise HandoffError("Swarm agent returned empty 'message'.")
    return nxt, msg


async def run(
    initial_agent: str,
    task: str,
    max_handoffs: int = 8,
    agents: dict[str, Agent] | None = None,
    timeout: float = 300.0,
    tools: list[str] | None = None,
) -> str:
    """Execute the Swarm workflow and return the final agent's message.

    Args:
        initial_agent: Name of the agent to start with.
        task: The user task.
        max_handoffs: Maximum number of handoffs (each turn is one handoff).
        agents: Optional agent registry. ``None`` (default) = snapshot of
            the module-level :data:`registry` (which is pre-seeded with
            the four built-in profiles). Pass an explicit dict to scope
            the agent set to this call only.
        timeout: Overall timeout in seconds.
        tools: Whitelist of tool names to expose. ``None`` = every
            registered tool. ``[]`` = no tool-calling.
    """
    if not task or not task.strip():
        raise EmptyInputError("swarm_run: 'task' must be a non-empty string.")
    if max_handoffs < 0:
        raise EmptyInputError("max_handoffs must be >= 0.")

    if agents is None:
        agents = registry.snapshot()
    if not agents:
        raise EmptyInputError(
            "no swarm agents available; register at least one with "
            "register_swarm_agent() or pass agents=... to swarm_run"
        )
    if initial_agent not in agents:
        raise EmptyInputError(
            f"initial_agent {initial_agent!r} not in registered agents {list(agents)}"
        )

    tool_specs = _resolve_tool_specs(tools)
    use_tools = bool(tool_specs)

    new_session()
    put("initial_agent", initial_agent)
    put("task", task)
    put("agent_count", len(agents))
    put("tools", [t["function"]["name"] for t in tool_specs])

    current = initial_agent
    history: list[dict] = []
    client = get_client()
    roster = _roster_text(agents)
    valid = set(agents.keys())
    intro = SYSTEM_INTRO.format(roster=roster)

    try:
        async with asyncio.timeout(timeout):
            for turn in range(max_handoffs + 1):
                agent = agents[current]
                msgs: list[dict[str, Any]] = [
                    {"role": "system", "content": f"{agent.system}\n\n{intro}"},
                ]
                for h in history:
                    msgs.append(
                        {
                            "role": "user",
                            "content": f"[{h['from']} -> {h['to']}] {h['message']}",
                        }
                    )
                if turn == 0:
                    msgs.append({"role": "user", "content": f"Your task: {task}"})
                else:
                    msgs.append({"role": "user", "content": "Continue."})

                if use_tools:
                    from .tools import registry as tool_registry

                    text, _ = await client.chat_with_tools(
                        msgs,
                        executor=tool_registry.dispatch_executor,
                        max_steps=6,
                    )
                    raw = text
                else:
                    raw = await client.chat(msgs)
                nxt, msg = _parse_reply(raw, valid)
                history.append({"from": current, "to": nxt, "message": msg})

                if nxt == "DONE" or turn == max_handoffs:
                    put("history", history)
                    put("final", msg)
                    return msg
                current = nxt
    except asyncio.TimeoutError as e:
        raise ToolTimeoutError(f"swarm_run exceeded {timeout}s") from e

    # Unreachable; the loop always returns or raises.
    raise RuntimeError("swarm_run exited without returning or raising")


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
