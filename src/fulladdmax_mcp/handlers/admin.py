"""Read-only / status-query operations for the ``admin`` mega tool.

The ``admin`` tool exposes 9 operations:

* ``ping``
* ``list_sessions``
* ``get_session``
* ``delete_session``
* ``list_agent_tools``
* ``list_swarm_agents``
* ``get_rate_limit_status``
* ``get_usage_stats``
* ``list_usage_records``
"""

from __future__ import annotations

from .. import server_internal
from ..dispatcher import OperationHandler, register_schema
from ..param_parser import FieldSpec


# ---- schemas ---------------------------------------------------------------


SCHEMAS: dict[str, dict[str, FieldSpec]] = {
    "ping": {},
    "list_sessions": {},
    "get_session": {
        "session_id": FieldSpec(required=True, type=str),
    },
    "delete_session": {
        "session_id": FieldSpec(required=True, type=str),
    },
    "list_agent_tools": {},
    "list_swarm_agents": {},
    "get_rate_limit_status": {},
    "get_usage_stats": {
        "session_id": FieldSpec(required=False, type=str, default=""),
        "model": FieldSpec(required=False, type=str, default=""),
        "since_ts": FieldSpec(required=False, type=float, default=0.0),
    },
    "list_usage_records": {
        "session_id": FieldSpec(required=False, type=str, default=""),
        "model": FieldSpec(required=False, type=str, default=""),
        "since_ts": FieldSpec(required=False, type=float, default=0.0),
        "limit": FieldSpec(required=False, type=int, default=50),
    },
}


# Register every schema with the global dispatcher registry.
for _name, _schema in SCHEMAS.items():
    register_schema(_name, _schema)


# ---- handlers --------------------------------------------------------------


async def _ping(**_: object) -> str:
    return server_internal.ping()


async def _list_sessions(**_: object) -> str:
    return server_internal.list_sessions()


async def _get_session(*, session_id: str, **_: object) -> str:
    return server_internal.get_session(session_id)


async def _delete_session(*, session_id: str, **_: object) -> str:
    return server_internal.delete_session(session_id)


async def _list_agent_tools(**_: object) -> str:
    return server_internal.list_agent_tools()


async def _list_swarm_agents(**_: object) -> str:
    return server_internal.list_swarm_agents()


async def _get_rate_limit_status(**_: object) -> str:
    return server_internal.get_rate_limit_status()


async def _get_usage_stats(
    *,
    session_id: str = "",
    model: str = "",
    since_ts: float = 0.0,
    **_: object,
) -> str:
    return server_internal.get_usage_stats(
        session_id=session_id, model=model, since_ts=since_ts
    )


async def _list_usage_records(
    *,
    session_id: str = "",
    model: str = "",
    since_ts: float = 0.0,
    limit: int = 50,
    **_: object,
) -> str:
    return server_internal.list_usage_records(
        session_id=session_id, model=model, since_ts=since_ts, limit=limit
    )


HANDLERS: dict[str, OperationHandler] = {
    "ping": _ping,
    "list_sessions": _list_sessions,
    "get_session": _get_session,
    "delete_session": _delete_session,
    "list_agent_tools": _list_agent_tools,
    "list_swarm_agents": _list_swarm_agents,
    "get_rate_limit_status": _get_rate_limit_status,
    "get_usage_stats": _get_usage_stats,
    "list_usage_records": _list_usage_records,
}
