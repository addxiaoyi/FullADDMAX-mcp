"""Swarm multi-agent handoffs.

A swarm is a small registry of named agent profiles, each with its own
system prompt. The LLM is forced to reply with a strict JSON envelope::

    {"next": "<agent_name or DONE>", "message": "<handoff text>"}

The orchestrator routes the message to the next agent until the LLM
emits ``"next": "DONE"`` or ``max_handoffs`` is reached.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from .context import new_session, put
from .errors import EmptyInputError, HandoffError, ToolTimeoutError
from .llm import get_client

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
) -> str:
    """Execute the Swarm workflow and return the final agent's message."""
    if not task or not task.strip():
        raise EmptyInputError("swarm_run: 'task' must be a non-empty string.")
    if max_handoffs < 0:
        raise EmptyInputError("max_handoffs must be >= 0.")

    active = agents if agents is not None else DEFAULT_AGENTS
    if initial_agent not in active:
        raise EmptyInputError(
            f"initial_agent {initial_agent!r} not in registered agents {list(active)}"
        )

    new_session()
    put("initial_agent", initial_agent)
    put("task", task)

    current = initial_agent
    history: list[dict] = []
    client = get_client()
    roster = _roster_text(active)
    valid = set(active.keys())
    intro = SYSTEM_INTRO.format(roster=roster)

    try:
        async with asyncio.timeout(timeout):
            for turn in range(max_handoffs + 1):
                agent = active[current]
                msgs: list[dict] = [
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
