"""Process-wide shared session context for FullADDMAX-mcp.

Tools can stash intermediate results (e.g. planned subtasks, worker
outputs) so that subsequent tool calls in the same session can read
them.

This module provides two layers:

* **Module-level helper API** (:func:`new_session` / :func:`put` /
  :func:`get` / :func:`snapshot` / :func:`session_id` / etc.) — the
  same surface as before. Internally they delegate to whichever
  :class:`~fulladdmax_mcp.context_store.ContextStore` is currently
  configured, falling back to a :class:`MemoryContextStore` until you
  call :func:`configure_store`.

* **Storage backends** (:mod:`fulladdmax_mcp.context_store`) — pluggable.
  Default is in-process memory; switch to :class:`SqliteContextStore`
  for persistence across process restarts.

* :class:`contextvars.ContextVar` still tracks the *current* session
  id per async task, so tools that do not pass an explicit
  ``session_id=`` argument (e.g. legacy callers, plain Python scripts)
  continue to work.
"""

from __future__ import annotations

import contextvars
import json
import logging
import uuid
from typing import Any

from .context_store import (
    ContextStore,
    ContextStoreError,
    MemoryContextStore,
    SessionInfo,
    SessionNotFoundError,
)

log = logging.getLogger(__name__)

_current_session: contextvars.ContextVar[str] = contextvars.ContextVar(
    "fulladdmax_session_id", default="default"
)

# Module-level store. Initialised lazily so importing this module
# has no side effects.
_store: ContextStore | None = None


# ---------------------------------------------------------------------------
# Store configuration
# ---------------------------------------------------------------------------


def _get_store() -> ContextStore:
    global _store
    if _store is None:
        _store = MemoryContextStore()
        log.debug("context: initialised default MemoryContextStore")
    return _store


def store() -> ContextStore:
    """Return the currently configured :class:`ContextStore`.

    A :class:`MemoryContextStore` is returned the first time this is
    called, and on every subsequent call unless :func:`configure_store`
    has been invoked.
    """
    return _get_store()


def configure_store(new_store: ContextStore) -> ContextStore:
    """Install a new :class:`ContextStore`. The previous store (if any)
    is closed before being replaced. Returns the old store so the
    caller can keep a reference if needed.
    """
    global _store
    old = _store
    if old is not None:
        try:
            old.close()
        except Exception as e:  # noqa: BLE001
            log.warning("error closing previous context store: %s", e)
    _store = new_store
    log.info("context: configured store=%s", type(new_store).__name__)
    return old  # type: ignore[return-value]


def use_memory_store(ttl_seconds: float = 7 * 24 * 3600) -> ContextStore:
    """Convenience: switch to a fresh in-process :class:`MemoryContextStore`."""
    return configure_store(MemoryContextStore(ttl_seconds=ttl_seconds))


def use_sqlite_store(
    path: str,
    ttl_seconds: float = 7 * 24 * 3600,
) -> ContextStore:
    """Convenience: switch to a :class:`SqliteContextStore` at ``path``.

    The database file is created on first call. The store is
    process-global; subsequent tool calls will see the same data.
    """
    from .context_store import SqliteContextStore

    return configure_store(SqliteContextStore(path, ttl_seconds=ttl_seconds))


# ---------------------------------------------------------------------------
# Session id binding
# ---------------------------------------------------------------------------


def new_session() -> str:
    """Create a new session id, register it in the store, and bind it
    as the current session. Returns the new id.
    """
    sid = uuid.uuid4().hex[:12]
    _get_store().create(sid)
    _current_session.set(sid)
    return sid


def bind(sid: str) -> None:
    """Bind to an existing session id (creates it if it does not exist)."""
    if not sid:
        raise ContextStoreError("session_id is empty")
    _get_store().create(sid)
    _current_session.set(sid)


def session_id() -> str:
    """Return the current session id (``"default"`` if nothing bound)."""
    return _current_session.get()


def require_session_id() -> str:
    """Return the current session id; raise if it is the literal
    ``"default"`` placeholder (i.e. no real session was ever bound).

    Use this in workflows that absolutely need an id to write to the
    store.
    """
    sid = session_id()
    if sid == "default":
        raise ContextStoreError(
            "no session has been bound yet; call new_session() or bind() first"
        )
    return sid


# ---------------------------------------------------------------------------
# Convenience API (compatible with the pre-store refactor)
# ---------------------------------------------------------------------------


def put(key: str, value: Any) -> None:
    """Store ``value`` under ``key`` in the current session."""
    _get_store().put(session_id(), key, value)


def get(key: str, default: Any = None) -> Any:
    """Retrieve a value from the current session, or ``default`` if missing."""
    return _get_store().get(session_id(), key, default)


def snapshot() -> dict[str, Any]:
    """Return a shallow copy of the current session's data."""
    return _get_store().snapshot(session_id())


def clear() -> None:
    """Reset the current session's data (keeps the session id)."""
    _get_store().clear(session_id())


def merge(extra: dict[str, Any]) -> None:
    """Merge ``extra`` into the current session's data."""
    _get_store().merge(session_id(), extra)


def dump_json() -> str:
    """Return a JSON string snapshot of the current session (for logging)."""
    return json.dumps(snapshot(), ensure_ascii=False, default=str)


__all__ = [
    "ContextStore",
    "ContextStoreError",
    "MemoryContextStore",
    "SessionInfo",
    "SessionNotFoundError",
    # configuration
    "store",
    "configure_store",
    "use_memory_store",
    "use_sqlite_store",
    # session id
    "new_session",
    "bind",
    "session_id",
    "require_session_id",
    # payload
    "put",
    "get",
    "snapshot",
    "clear",
    "merge",
    "dump_json",
]
