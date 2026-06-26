"""Process-wide shared session context for FullADDMAX-mcp.

Tools can stash intermediate results (e.g. planned subtasks, worker outputs)
so that subsequent tool calls in the same session can read them.

Uses :class:`contextvars.ContextVar` for asyncio-aware scoping plus a
process-level dict guarded by an :class:`threading.RLock` for cross-thread
fallback access.
"""

from __future__ import annotations

import contextvars
import json
import uuid
from threading import RLock
from typing import Any

_current_session: contextvars.ContextVar[str] = contextvars.ContextVar(
    "fulladdmax_session_id", default="default"
)

_lock = RLock()
_store: dict[str, dict[str, Any]] = {"default": {}}


def new_session() -> str:
    """Create a new session id and bind it as the current session."""
    sid = uuid.uuid4().hex[:12]
    with _lock:
        _store[sid] = {}
    _current_session.set(sid)
    return sid


def bind(sid: str) -> None:
    """Bind to an existing session id (must already exist)."""
    with _lock:
        if sid not in _store:
            _store[sid] = {}
    _current_session.set(sid)


def session_id() -> str:
    """Return the current session id."""
    return _current_session.get()


def put(key: str, value: Any) -> None:
    """Store a value under ``key`` in the current session."""
    sid = session_id()
    with _lock:
        _store.setdefault(sid, {})[key] = value


def get(key: str, default: Any = None) -> Any:
    """Retrieve a value from the current session, or ``default`` if missing."""
    sid = session_id()
    with _lock:
        return _store.get(sid, {}).get(key, default)


def snapshot() -> dict[str, Any]:
    """Return a shallow copy of the current session's data."""
    sid = session_id()
    with _lock:
        return dict(_store.get(sid, {}))


def clear() -> None:
    """Reset the current session's data (keeps the session id)."""
    sid = session_id()
    with _lock:
        _store[sid] = {}


def merge(extra: dict[str, Any]) -> None:
    """Merge ``extra`` into the current session's data."""
    sid = session_id()
    with _lock:
        _store.setdefault(sid, {}).update(extra)


def dump_json() -> str:
    """Return a JSON string snapshot of the current session (for logging)."""
    return json.dumps(snapshot(), ensure_ascii=False, default=str)
