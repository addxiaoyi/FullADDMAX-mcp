"""Write / configuration operations for the ``config`` mega tool.

The ``config`` tool exposes 10 operations:

* ``configure_llm``
* ``configure_context_store``
* ``configure_rate_limit``
* ``configure_pricing_override``
* ``register_swarm_agent``
* ``unregister_swarm_agent``
* ``unregister_agent_tool``
* ``reset_rate_limit``
* ``reset_usage_stats``
* ``purge_expired_sessions``
"""

from __future__ import annotations

from .. import server_internal
from ..dispatcher import OperationHandler, register_schema
from ..param_parser import FieldSpec


# ---- schemas ---------------------------------------------------------------


SCHEMAS: dict[str, dict[str, FieldSpec]] = {
    "configure_llm": {
        "base_url": FieldSpec(required=True, type=str),
        "api_key": FieldSpec(required=True, type=str),
        "model": FieldSpec(required=False, type=str, default="gpt-4o-mini"),
        "temperature": FieldSpec(required=False, type=float, default=0.7),
        "max_tokens": FieldSpec(required=False, type=int, default=2048),
        "timeout": FieldSpec(required=False, type=float, default=60.0),
        "max_retries": FieldSpec(required=False, type=int, default=2),
    },
    "configure_context_store": {
        "backend": FieldSpec(
            required=False, type=str, default="memory", choices=("memory", "sqlite")
        ),
        "sqlite_path": FieldSpec(required=False, type=str, default=""),
        "ttl_seconds": FieldSpec(required=False, type=float, default=7 * 24 * 3600),
    },
    "configure_rate_limit": {
        "global_rpm": FieldSpec(required=False, type=int, default=0),
        "global_tpm": FieldSpec(required=False, type=int, default=0),
        "per_session_rpm": FieldSpec(required=False, type=int, default=0),
        "per_session_tpm": FieldSpec(required=False, type=int, default=0),
        "default_estimated_tokens": FieldSpec(required=False, type=int, default=1024),
    },
    "configure_pricing_override": {
        "model": FieldSpec(required=True, type=str),
        "prompt_per_million": FieldSpec(required=True, type=float),
        "completion_per_million": FieldSpec(required=True, type=float),
    },
    "register_swarm_agent": {
        "name": FieldSpec(required=True, type=str),
        "system": FieldSpec(required=True, type=str),
        "description": FieldSpec(required=False, type=str, default=""),
        "overwrite": FieldSpec(required=False, type=bool, default=False),
    },
    "unregister_swarm_agent": {
        "name": FieldSpec(required=True, type=str),
    },
    "unregister_agent_tool": {
        "name": FieldSpec(required=True, type=str),
    },
    "reset_rate_limit": {},
    "reset_usage_stats": {},
    "purge_expired_sessions": {
        "ttl_seconds": FieldSpec(required=False, type=float, default=0),
    },
}


for _name, _schema in SCHEMAS.items():
    register_schema(_name, _schema)


# ---- handlers --------------------------------------------------------------


async def _configure_llm(
    *,
    base_url: str,
    api_key: str,
    model: str = "gpt-4o-mini",
    temperature: float = 0.7,
    max_tokens: int = 2048,
    timeout: float = 60.0,
    max_retries: int = 2,
    **_: object,
) -> str:
    return server_internal.configure_llm(
        base_url=base_url,
        api_key=api_key,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        max_retries=max_retries,
    )


async def _configure_context_store(
    *,
    backend: str = "memory",
    sqlite_path: str = "",
    ttl_seconds: float = 7 * 24 * 3600,
    **_: object,
) -> str:
    return server_internal.configure_context_store(
        backend=backend, sqlite_path=sqlite_path, ttl_seconds=ttl_seconds
    )


async def _configure_rate_limit(
    *,
    global_rpm: int = 0,
    global_tpm: int = 0,
    per_session_rpm: int = 0,
    per_session_tpm: int = 0,
    default_estimated_tokens: int = 1024,
    **_: object,
) -> str:
    return server_internal.configure_rate_limit(
        global_rpm=global_rpm,
        global_tpm=global_tpm,
        per_session_rpm=per_session_rpm,
        per_session_tpm=per_session_tpm,
        default_estimated_tokens=default_estimated_tokens,
    )


async def _configure_pricing_override(
    *,
    model: str,
    prompt_per_million: float,
    completion_per_million: float,
    **_: object,
) -> str:
    return server_internal.configure_pricing_override(
        model=model,
        prompt_per_million=prompt_per_million,
        completion_per_million=completion_per_million,
    )


async def _register_swarm_agent(
    *,
    name: str,
    system: str,
    description: str = "",
    overwrite: bool = False,
    **_: object,
) -> str:
    return server_internal.register_swarm_agent(
        name=name, system=system, description=description, overwrite=overwrite
    )


async def _unregister_swarm_agent(*, name: str, **_: object) -> str:
    return server_internal.unregister_swarm_agent(name)


async def _unregister_agent_tool(*, name: str, **_: object) -> str:
    return server_internal.unregister_agent_tool(name)


async def _reset_rate_limit(**_: object) -> str:
    return server_internal.reset_rate_limit()


async def _reset_usage_stats(**_: object) -> str:
    return server_internal.reset_usage_stats()


async def _purge_expired_sessions(*, ttl_seconds: float = 0, **_: object) -> str:
    return server_internal.purge_expired_sessions(ttl_seconds=ttl_seconds)


HANDLERS: dict[str, OperationHandler] = {
    "configure_llm": _configure_llm,
    "configure_context_store": _configure_context_store,
    "configure_rate_limit": _configure_rate_limit,
    "configure_pricing_override": _configure_pricing_override,
    "register_swarm_agent": _register_swarm_agent,
    "unregister_swarm_agent": _unregister_swarm_agent,
    "unregister_agent_tool": _unregister_agent_tool,
    "reset_rate_limit": _reset_rate_limit,
    "reset_usage_stats": _reset_usage_stats,
    "purge_expired_sessions": _purge_expired_sessions,
}
