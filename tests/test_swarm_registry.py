"""Tests for the dynamic Swarm agent registry."""

from __future__ import annotations

import pytest

from fulladdmax_mcp import swarm
from fulladdmax_mcp.errors import EmptyInputError
from fulladdmax_mcp.swarm import (
    DEFAULT_AGENTS,
    Agent,
    SwarmAgentAlreadyExistsError,
    SwarmRegistry,
    list_swarm_agents,
    parse_agents_json,
    register_swarm_agent,
    registry as module_registry,
    unregister_swarm_agent,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_registry():
    """Restore the module-level registry to its seeded state around
    every test. Each test gets a clean slate of the four built-ins.
    """
    module_registry.clear()
    for a in DEFAULT_AGENTS.values():
        module_registry.register(a, overwrite=True)
    yield
    module_registry.clear()
    for a in DEFAULT_AGENTS.values():
        module_registry.register(a, overwrite=True)


# ---------------------------------------------------------------------------
# SwarmRegistry
# ---------------------------------------------------------------------------


def test_registry_seeded_with_defaults():
    names = set(module_registry.names())
    assert {"researcher", "coder", "critic", "writer"} <= names


def test_registry_register_new_agent():
    a = Agent(name="reviewer", system="Review.", description="Reviews things.")
    module_registry.register(a)
    assert "reviewer" in module_registry
    assert module_registry.get("reviewer").description == "Reviews things."


def test_registry_register_duplicate_raises():
    a = Agent(name="researcher", system="x")
    with pytest.raises(SwarmAgentAlreadyExistsError, match="already exists"):
        module_registry.register(a)


def test_registry_register_with_overwrite_replaces():
    new_researcher = Agent(
        name="researcher",
        system="You are the lead researcher. Be thorough.",
        description="Custom researcher.",
    )
    module_registry.register(new_researcher, overwrite=True)
    got = module_registry.get("researcher")
    assert got.description == "Custom researcher."
    assert "thorough" in got.system


def test_registry_rejects_empty_name():
    a = Agent(name="", system="x")
    with pytest.raises(SwarmAgentAlreadyExistsError, match="name is required"):
        module_registry.register(a)


def test_registry_rejects_empty_system():
    a = Agent(name="x", system="")
    with pytest.raises(SwarmAgentAlreadyExistsError, match="non-empty"):
        module_registry.register(a)
    a2 = Agent(name="x", system="   ")
    with pytest.raises(SwarmAgentAlreadyExistsError, match="non-empty"):
        module_registry.register(a2)


def test_registry_unregister_returns_true_when_present():
    module_registry.register(Agent(name="tmp", system="x"))
    assert module_registry.unregister("tmp") is True
    assert "tmp" not in module_registry


def test_registry_unregister_returns_false_when_absent():
    assert module_registry.unregister("nope") is False


def test_registry_unregister_builtin():
    assert module_registry.unregister("writer") is True
    assert "writer" not in module_registry


def test_registry_snapshot_isolates_from_mutation():
    snap = module_registry.snapshot()
    snap["ghost"] = Agent(name="ghost", system="x")
    assert "ghost" not in module_registry


def test_registry_clear_empties_everything():
    module_registry.clear()
    assert len(module_registry) == 0


def test_registry_names_sorted():
    names = module_registry.names()
    assert names == sorted(names)


def test_independent_registry_does_not_share_state():
    r1 = SwarmRegistry()
    r2 = SwarmRegistry()
    r1.register(Agent(name="a", system="x"), overwrite=True)
    r1.register(Agent(name="b", system="x"), overwrite=True)
    r1.unregister("a")
    r1.unregister("b")
    # r1 is now empty
    assert "a" not in r1 and "b" not in r1
    # r2 still has the defaults
    assert "researcher" in r2


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def test_register_swarm_agent_functional_form():
    register_swarm_agent(
        name="legal",
        system="You are a legal reviewer. Be precise.",
        description="Legal compliance reviewer.",
    )
    assert "legal" in module_registry
    assert module_registry.get("legal").description == "Legal compliance reviewer."


def test_register_swarm_agent_duplicate_raises():
    with pytest.raises(SwarmAgentAlreadyExistsError):
        register_swarm_agent(name="coder", system="x")


def test_unregister_swarm_agent_functional_form():
    register_swarm_agent(name="tmp", system="x")
    assert unregister_swarm_agent("tmp") is True
    assert unregister_swarm_agent("tmp") is False  # idempotent


def test_list_swarm_agents_returns_all():
    register_swarm_agent(name="x", system="x")
    agents = list_swarm_agents()
    names = {a.name for a in agents}
    assert {"researcher", "coder", "critic", "writer", "x"} <= names


def test_list_swarm_agents_sorted():
    register_swarm_agent(name="zzz", system="x")
    register_swarm_agent(name="aaa", system="x")
    names = [a.name for a in list_swarm_agents()]
    assert names == sorted(names)


# ---------------------------------------------------------------------------
# parse_agents_json
# ---------------------------------------------------------------------------


def test_parse_agents_json_happy_path():
    payload = """[
        {"name": "a", "system": "You are A.", "description": "First."},
        {"name": "b", "system": "You are B."}
    ]"""
    parsed = parse_agents_json(payload)
    assert set(parsed) == {"a", "b"}
    assert parsed["a"].description == "First."
    assert parsed["b"].description == ""  # default


def test_parse_agents_json_rejects_invalid_json():
    with pytest.raises(SwarmAgentAlreadyExistsError, match="not valid"):
        parse_agents_json("not json")


def test_parse_agents_json_rejects_non_list():
    with pytest.raises(SwarmAgentAlreadyExistsError, match="must be a list"):
        parse_agents_json('{"name": "x", "system": "y"}')


def test_parse_agents_json_rejects_non_object_entry():
    with pytest.raises(SwarmAgentAlreadyExistsError, match="not an object"):
        parse_agents_json('[1, 2, 3]')


def test_parse_agents_json_rejects_missing_name():
    with pytest.raises(SwarmAgentAlreadyExistsError, match="missing"):
        parse_agents_json('[{"system": "x"}]')


def test_parse_agents_json_rejects_missing_system():
    with pytest.raises(SwarmAgentAlreadyExistsError, match="missing"):
        parse_agents_json('[{"name": "x"}]')


def test_parse_agents_json_rejects_duplicate_names_in_payload():
    payload = """[
        {"name": "a", "system": "x"},
        {"name": "a", "system": "y"}
    ]"""
    with pytest.raises(SwarmAgentAlreadyExistsError, match="duplicate"):
        parse_agents_json(payload)


def test_parse_agents_json_strips_whitespace_from_name():
    parsed = parse_agents_json('[{"name": "  spaced  ", "system": "x"}]')
    assert "spaced" in parsed


# ---------------------------------------------------------------------------
# swarm.run() with custom agents
# ---------------------------------------------------------------------------


async def test_swarm_run_uses_registry_by_default(
    mock_chat, make_response
):
    """When agents= is None, swarm.run uses the module-level registry
    snapshot (which contains the 4 built-ins)."""
    responses = iter(
        [
            # researcher turn: hand off to writer
            make_response('{"next": "writer", "message": "research done"}'),
            # writer turn: done
            make_response('{"next": "DONE", "message": "final answer"}'),
        ]
    )
    mock_chat.post("/v1/chat/completions").mock(
        side_effect=lambda req: next(responses)
    )

    out = await swarm.run("researcher", "x", max_handoffs=2)
    assert out == "final answer"


async def test_swarm_run_uses_passed_agents(mock_chat, make_response):
    """When agents= is provided, it REPLACES the registry for this call."""
    custom = {
        "alpha": Agent(
            name="alpha", system="You are alpha. Reply with JSON.",
            description="First.",
        ),
        "beta": Agent(
            name="beta", system="You are beta. Reply with JSON.",
            description="Second.",
        ),
    }
    responses = iter(
        [
            make_response('{"next": "beta", "message": "alpha done"}'),
            make_response('{"next": "DONE", "message": "beta final"}'),
        ]
    )
    mock_chat.post("/v1/chat/completions").mock(
        side_effect=lambda req: next(responses)
    )

    out = await swarm.run("alpha", "x", max_handoffs=2, agents=custom)
    assert out == "beta final"


async def test_swarm_run_with_custom_registry_agent(
    mock_chat, make_response
):
    """Register a custom agent and start a swarm run with it."""
    register_swarm_agent(
        name="historian",
        system="You are a historian. Reply with JSON {next, message}.",
        description="Historical context expert.",
    )
    register_swarm_agent(
        name="writer",
        system="You are a writer. Reply with JSON {next, message}. End with DONE.",
        description="Writes the final answer.",
        overwrite=True,
    )

    responses = iter(
        [
            make_response('{"next": "writer", "message": "history facts"}'),
            make_response('{"next": "DONE", "message": "final polished"}'),
        ]
    )
    mock_chat.post("/v1/chat/completions").mock(
        side_effect=lambda req: next(responses)
    )

    out = await swarm.run("historian", "tell me about the roman empire",
                          max_handoffs=4)
    assert out == "final polished"


async def test_swarm_run_rejects_initial_not_in_passed_agents():
    custom = {
        "alpha": Agent(name="alpha", system="x"),
    }
    with pytest.raises(EmptyInputError, match="not in registered agents"):
        await swarm.run("ghost", "x", agents=custom)


async def test_swarm_run_rejects_initial_not_in_registry_after_unregister():
    """Unregistering a built-in should make subsequent runs reject it."""
    unregister_swarm_agent("researcher")
    with pytest.raises(EmptyInputError, match="not in registered agents"):
        await swarm.run("researcher", "x")


async def test_swarm_run_with_empty_registry_raises():
    """If the registry is empty and no agents are passed, fail fast
    with a clear error (not a confusing KeyError later)."""
    module_registry.clear()
    with pytest.raises(EmptyInputError, match="no swarm agents available"):
        await swarm.run("any", "x")
