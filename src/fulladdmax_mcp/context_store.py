"""Pluggable storage for persistent session context.

Two implementations are provided out of the box:

* :class:`MemoryContextStore` — an in-process dict, useful for tests
  and short-lived processes. The default if you never call
  :func:`context.configure_store`.
* :class:`SqliteContextStore` — a single-file SQLite database
  (stdlib ``sqlite3``). Survives process restarts, supports a
  configurable TTL, and is the recommended backend for any
  long-running MCP server.

Both implement the same :class:`ContextStore` ABC so the rest of the
code base is storage-agnostic.

Thread / process model
----------------------

* :class:`MemoryContextStore` is guarded by an :class:`RLock` and is
  safe to use from multiple threads in the same process.
* :class:`SqliteContextStore` uses SQLite with ``check_same_thread=False``
  and a per-call re-entrant lock; safe to use from any thread in the
  same process. For multi-process deployments, use a different
  ``path`` per process or rely on SQLite's own locking.

TTL
---

Each session has a ``last_access`` timestamp. On every read the store
may bump it (see ``touch_on_read``). On every write, it is bumped
unconditionally. :meth:`purge_expired` deletes sessions whose
``last_access`` is older than ``ttl_seconds`` and returns the count
removed; you can run it periodically (or call it explicitly via
:func:`fulladdmax_mcp.server.purge_expired_sessions`).

Serialisation
-------------

Values are stored as JSON. Any JSON-serialisable value works
(``str`` / ``int`` / ``list`` / ``dict`` / ``bool`` / ``None``). For
non-JSON-serialisable values, pass ``default=str`` in :meth:`put` or
let the store stringify non-serialisable values transparently.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ContextStoreError(Exception):
    """Raised on any storage-layer problem (SQLite I/O, bad config, etc)."""


class SessionNotFoundError(KeyError):
    """Raised by :meth:`ContextStore.require` when a session id is unknown."""


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionInfo:
    """Metadata about a stored session (no payload)."""

    session_id: str
    last_access: float
    created_at: float
    size: int  # number of keys


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class ContextStore(ABC):
    """Storage backend for session context payloads.

    All methods are thread-safe. Implementations may add their own
    internal locks.
    """

    @abstractmethod
    def create(self, session_id: str) -> None:
        """Create an empty session. Idempotent: existing sessions are
        left unchanged (and their ``last_access`` is *not* bumped — a
        fresh :meth:`put` will do that)."""

    @abstractmethod
    def exists(self, session_id: str) -> bool: ...

    @abstractmethod
    def put(
        self,
        session_id: str,
        key: str,
        value: Any,
        *,
        default: Any = None,
    ) -> None:
        """Store ``value`` under ``key`` in ``session_id``. Creates the
        session if it does not exist. Bumps ``last_access``.

        ``default`` is the ``json.dumps(..., default=...)`` strategy
        used when ``value`` is not JSON-serialisable.
        """

    @abstractmethod
    def get(self, session_id: str, key: str, default: Any = None) -> Any: ...

    @abstractmethod
    def snapshot(self, session_id: str) -> dict[str, Any]: ...

    @abstractmethod
    def merge(self, session_id: str, extra: dict[str, Any]) -> None: ...

    @abstractmethod
    def clear(self, session_id: str) -> None: ...

    @abstractmethod
    def delete(self, session_id: str) -> bool: ...

    @abstractmethod
    def list_sessions(self) -> list[SessionInfo]: ...

    @abstractmethod
    def purge_expired(self, ttl_seconds: float | None = None) -> int:
        """Remove sessions whose last_access is older than
        ``ttl_seconds`` (or the store's default TTL if ``None``).
        Returns the number of sessions removed.
        """

    @abstractmethod
    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# In-memory implementation (extracted from context.py)
# ---------------------------------------------------------------------------


DEFAULT_TTL_SECONDS = 7 * 24 * 3600  # 7 days


class MemoryContextStore(ContextStore):
    """Thread-safe in-process dict. Default backend; no I/O.

    Sessions are stored in a flat dict keyed by session id. A parallel
    dict holds ``last_access`` timestamps. An RLock guards all
    mutations.
    """

    def __init__(self, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> None:
        if ttl_seconds <= 0:
            raise ContextStoreError("ttl_seconds must be > 0")
        self._ttl = ttl_seconds
        self._data: dict[str, dict[str, Any]] = {}
        self._meta: dict[str, tuple[float, float]] = {}  # sid -> (created, last_access)
        self._lock = threading.RLock()

    def ttl_seconds(self) -> float:
        return self._ttl

    # ---- helpers --------------------------------------------------------

    def _ensure(self, sid: str) -> None:
        now = time.time()
        with self._lock:
            if sid not in self._data:
                self._data[sid] = {}
                self._meta[sid] = (now, now)
            # else: leave meta alone (create is idempotent)

    def _touch(self, sid: str) -> None:
        now = time.time()
        with self._lock:
            created = self._meta.get(sid, (now, now))[0]
            self._meta[sid] = (created, now)

    def _serialise(self, value: Any, default: Any) -> str:
        try:
            return json.dumps(value, ensure_ascii=False, default=default)
        except (TypeError, ValueError) as e:
            raise ContextStoreError(f"value is not JSON-serialisable: {e}") from e

    def _deserialise(self, raw: str) -> Any:
        return json.loads(raw)

    # ---- API ------------------------------------------------------------

    def create(self, session_id: str) -> None:
        self._ensure(session_id)

    def exists(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._data

    def put(
        self,
        session_id: str,
        key: str,
        value: Any,
        *,
        default: Any = None,
    ) -> None:
        self._ensure(session_id)
        serialised = self._serialise(value, default)
        with self._lock:
            self._data[session_id][key] = serialised
            self._touch(session_id)

    def get(self, session_id: str, key: str, default: Any = None) -> Any:
        with self._lock:
            blob = self._data.get(session_id, {}).get(key)
        if blob is None and key not in (self._data.get(session_id, {}) or {}):
            return default
        return self._deserialise(blob)

    def snapshot(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            raw = dict(self._data.get(session_id, {}))
        return {k: self._deserialise(v) for k, v in raw.items()}

    def merge(self, session_id: str, extra: dict[str, Any]) -> None:
        self._ensure(session_id)
        with self._lock:
            for k, v in extra.items():
                self._data[session_id][k] = self._serialise(v, default=None)
            self._touch(session_id)

    def clear(self, session_id: str) -> None:
        self._ensure(session_id)
        with self._lock:
            self._data[session_id] = {}
            self._touch(session_id)

    def delete(self, session_id: str) -> bool:
        with self._lock:
            existed = session_id in self._data
            self._data.pop(session_id, None)
            self._meta.pop(session_id, None)
            return existed

    def list_sessions(self) -> list[SessionInfo]:
        with self._lock:
            out = [
                SessionInfo(
                    session_id=sid,
                    last_access=meta[1],
                    created_at=meta[0],
                    size=len(self._data.get(sid, {})),
                )
                for sid, meta in self._meta.items()
            ]
        out.sort(key=lambda s: s.last_access, reverse=True)
        return out

    def purge_expired(self, ttl_seconds: float | None = None) -> int:
        ttl = ttl_seconds if ttl_seconds is not None else self._ttl
        if ttl <= 0:
            raise ContextStoreError("ttl_seconds must be > 0")
        cutoff = time.time() - ttl
        with self._lock:
            expired = [
                sid
                for sid, (_created, last_access) in self._meta.items()
                if last_access < cutoff
            ]
            for sid in expired:
                self._data.pop(sid, None)
                self._meta.pop(sid, None)
        if expired:
            log.info("purged %d expired session(s) (ttl=%.0fs)", len(expired), ttl)
        return len(expired)

    def close(self) -> None:
        # No resources to release for in-memory store.
        pass


# ---------------------------------------------------------------------------
# SQLite implementation
# ---------------------------------------------------------------------------


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    created_at   REAL NOT NULL,
    last_access  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS entries (
    session_id   TEXT NOT NULL,
    key          TEXT NOT NULL,
    value        TEXT NOT NULL,
    PRIMARY KEY (session_id, key),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sessions_last_access ON sessions(last_access);
"""


class SqliteContextStore(ContextStore):
    """SQLite-backed context store. Single file, stdlib only.

    The database file is created on first connect. Each session is a
    row in ``sessions``; key/value pairs are rows in ``entries``.
    ``last_access`` is bumped on every write and on every successful
    read (see ``touch_on_read``). Sessions whose ``last_access`` is
    older than ``ttl_seconds`` are removed by
    :meth:`purge_expired`.
    """

    def __init__(
        self,
        path: str | Path,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        *,
        touch_on_read: bool = False,
    ) -> None:
        if ttl_seconds <= 0:
            raise ContextStoreError("ttl_seconds must be > 0")
        self._path = Path(path)
        self._ttl = ttl_seconds
        self._touch_on_read = touch_on_read
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self._path), check_same_thread=False, isolation_level=None
        )
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        with self._lock:
            self._conn.executescript(_SCHEMA)
        log.info("opened SqliteContextStore at %s (ttl=%.0fs)", self._path, self._ttl)

    # ---- connection helpers --------------------------------------------

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def _execmany(self, sql: str, params: list[tuple]) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.executemany(sql, params)

    # ---- API ------------------------------------------------------------

    def ttl_seconds(self) -> float:
        return self._ttl

    def create(self, session_id: str) -> None:
        if not session_id:
            raise ContextStoreError("session_id is empty")
        now = time.time()
        with self._lock:
            # INSERT OR IGNORE keeps existing meta; created_at is fixed
            # on first insert, last_access is left alone.
            self._conn.execute(
                "INSERT OR IGNORE INTO sessions(session_id, created_at, last_access) "
                "VALUES (?, ?, ?)",
                (session_id, now, now),
            )

    def exists(self, session_id: str) -> bool:
        cur = self._exec(
            "SELECT 1 FROM sessions WHERE session_id = ? LIMIT 1", (session_id,)
        )
        return cur.fetchone() is not None

    def put(
        self,
        session_id: str,
        key: str,
        value: Any,
        *,
        default: Any = None,
    ) -> None:
        if not key:
            raise ContextStoreError("key is empty")
        try:
            serialised = json.dumps(value, ensure_ascii=False, default=default)
        except (TypeError, ValueError) as e:
            raise ContextStoreError(f"value is not JSON-serialisable: {e}") from e
        self.create(session_id)
        with self._lock:
            self._conn.execute(
                "INSERT INTO entries(session_id, key, value) VALUES (?, ?, ?) "
                "ON CONFLICT(session_id, key) DO UPDATE SET value = excluded.value",
                (session_id, key, serialised),
            )
            self._conn.execute(
                "UPDATE sessions SET last_access = ? WHERE session_id = ?",
                (time.time(), session_id),
            )

    def get(self, session_id: str, key: str, default: Any = None) -> Any:
        cur = self._exec(
            "SELECT value FROM entries WHERE session_id = ? AND key = ?",
            (session_id, key),
        )
        row = cur.fetchone()
        if row is None:
            return default
        if self._touch_on_read:
            self._exec(
                "UPDATE sessions SET last_access = ? WHERE session_id = ?",
                (time.time(), session_id),
            )
        return json.loads(row[0])

    def snapshot(self, session_id: str) -> dict[str, Any]:
        cur = self._exec(
            "SELECT key, value FROM entries WHERE session_id = ?", (session_id,)
        )
        return {k: json.loads(v) for k, v in cur.fetchall()}

    def merge(self, session_id: str, extra: dict[str, Any]) -> None:
        if not extra:
            return
        self.create(session_id)
        rows = []
        for k, v in extra.items():
            try:
                serialised = json.dumps(v, ensure_ascii=False)
            except (TypeError, ValueError) as e:
                raise ContextStoreError(f"value for {k!r} is not JSON: {e}") from e
            rows.append((session_id, k, serialised))
        with self._lock:
            self._conn.executemany(
                "INSERT INTO entries(session_id, key, value) VALUES (?, ?, ?) "
                "ON CONFLICT(session_id, key) DO UPDATE SET value = excluded.value",
                rows,
            )
            self._conn.execute(
                "UPDATE sessions SET last_access = ? WHERE session_id = ?",
                (time.time(), session_id),
            )

    def clear(self, session_id: str) -> None:
        self.create(session_id)
        with self._lock:
            self._conn.execute(
                "DELETE FROM entries WHERE session_id = ?", (session_id,)
            )
            self._conn.execute(
                "UPDATE sessions SET last_access = ? WHERE session_id = ?",
                (time.time(), session_id),
            )

    def delete(self, session_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM sessions WHERE session_id = ?", (session_id,)
            )
            return cur.rowcount > 0

    def list_sessions(self) -> list[SessionInfo]:
        cur = self._exec(
            "SELECT s.session_id, s.created_at, s.last_access, "
            "(SELECT COUNT(*) FROM entries e WHERE e.session_id = s.session_id) "
            "FROM sessions s ORDER BY s.last_access DESC"
        )
        return [
            SessionInfo(
                session_id=row[0],
                created_at=row[1],
                last_access=row[2],
                size=row[3],
            )
            for row in cur.fetchall()
        ]

    def purge_expired(self, ttl_seconds: float | None = None) -> int:
        ttl = ttl_seconds if ttl_seconds is not None else self._ttl
        if ttl <= 0:
            raise ContextStoreError("ttl_seconds must be > 0")
        cutoff = time.time() - ttl
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM sessions WHERE last_access < ?", (cutoff,)
            )
            count = cur.rowcount
        if count:
            log.info("purged %d expired session(s) from %s (ttl=%.0fs)",
                     count, self._path, ttl)
        return count

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:  # pragma: no cover
                pass
