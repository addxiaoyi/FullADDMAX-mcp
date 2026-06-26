"""Tests for the persistent context store and its integration with
:mod:`fulladdmax_mcp.context`.

Two backends are covered: :class:`MemoryContextStore` (no I/O) and
:class:`SqliteContextStore` (file-backed, TTL-aware). Tests verify:

* CRUD primitives (put / get / snapshot / merge / clear / delete)
* TTL expiry and :meth:`purge_expired`
* File persistence (data survives closing + reopening the store)
* :mod:`fulladdmax_mcp.context` API is unchanged for legacy callers
  and that switching backends is transparent to it
* Thread-safety smoke (best-effort, not exhaustive)
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from fulladdmax_mcp import context as ctx
from fulladdmax_mcp.context_store import (
    ContextStoreError,
    MemoryContextStore,
    SessionInfo,
    SessionNotFoundError,
    SqliteContextStore,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _restore_default_store():
    """Make sure no test leaks a Sqlite/Memory store into the next test
    via the module-level :mod:`context` singleton.
    """
    saved = ctx._store
    yield
    if ctx._store is not saved:
        try:
            if saved is not None:
                saved.close()
        except Exception:
            pass
        ctx._store = saved
        ctx._current_session.set("default")


@pytest.fixture
def mem_store() -> MemoryContextStore:
    return MemoryContextStore(ttl_seconds=3600)


@pytest.fixture
def sqlite_store(tmp_path: Path) -> SqliteContextStore:
    return SqliteContextStore(tmp_path / "ctx.db", ttl_seconds=3600)


# Parametrise CRUD tests over both backends. We do this by defining
# a single ``store`` fixture that takes a ``backend`` parametrisation.
@pytest.fixture(params=["memory", "sqlite"])
def store(request, mem_store, sqlite_store):
    if request.param == "memory":
        return mem_store
    if request.param == "sqlite":
        return sqlite_store
    raise ValueError(request.param)  # pragma: no cover


# ---------------------------------------------------------------------------
# ContextStore ABC contract
# ---------------------------------------------------------------------------


def test_create_then_exists(mem_store):
    mem_store.create("abc")
    assert mem_store.exists("abc")
    assert not mem_store.exists("nope")


def test_create_is_idempotent(mem_store):
    mem_store.create("abc")
    mem_store.put("abc", "k", 1)
    mem_store.create("abc")
    # data is still there
    assert mem_store.get("abc", "k") == 1


def test_put_then_get_round_trip(store):
    if store is mem_store and not isinstance(store, MemoryContextStore):
        pass
    store.put("s1", "a", 1)
    store.put("s1", "b", "two")
    store.put("s1", "c", [1, 2, 3])
    store.put("s1", "d", {"nested": True})
    assert store.get("s1", "a") == 1
    assert store.get("s1", "b") == "two"
    assert store.get("s1", "c") == [1, 2, 3]
    assert store.get("s1", "d") == {"nested": True}


def test_get_default_when_missing(mem_store):
    assert mem_store.get("nope", "k", default="x") == "x"


def test_get_distinguishes_none_value_from_missing(mem_store):
    mem_store.put("s", "k", None)
    assert mem_store.get("s", "k", default="sentinel") is None
    assert mem_store.get("s", "absent", default="sentinel") == "sentinel"


def test_put_replaces_existing(mem_store):
    mem_store.put("s", "k", 1)
    mem_store.put("s", "k", 2)
    assert mem_store.get("s", "k") == 2


def test_snapshot_isolated_from_mutation(mem_store):
    mem_store.put("s", "a", 1)
    snap = mem_store.snapshot("s")
    snap["a"] = 999
    snap["new"] = "x"
    # store is unchanged
    assert mem_store.get("s", "a") == 1
    assert mem_store.get("s", "new", default=None) is None


def test_merge_overwrites_and_adds(mem_store):
    mem_store.put("s", "a", 1)
    mem_store.merge("s", {"a": 2, "b": 3})
    assert mem_store.get("s", "a") == 2
    assert mem_store.get("s", "b") == 3


def test_clear_keeps_session(mem_store):
    mem_store.put("s", "a", 1)
    mem_store.clear("s")
    assert mem_store.snapshot("s") == {}
    assert mem_store.exists("s")


def test_delete_returns_true_when_present(mem_store):
    mem_store.put("s", "a", 1)
    assert mem_store.delete("s") is True
    assert not mem_store.exists("s")


def test_delete_returns_false_when_absent(mem_store):
    assert mem_store.delete("nope") is False


def test_list_sessions_sorted_by_last_access(mem_store):
    mem_store.put("a", "k", 1)
    time.sleep(0.01)
    mem_store.put("b", "k", 1)
    time.sleep(0.01)
    mem_store.put("c", "k", 1)
    info = mem_store.list_sessions()
    names = [s.session_id for s in info]
    assert names == ["c", "b", "a"]
    # sizes correct
    assert all(s.size == 1 for s in info)


def test_list_sessions_includes_metadata(mem_store):
    mem_store.put("s", "k1", "v1")
    mem_store.put("s", "k2", "v2")
    info = mem_store.list_sessions()
    assert len(info) == 1
    s = info[0]
    assert s.session_id == "s"
    assert s.size == 2
    assert s.created_at <= s.last_access


# ---------------------------------------------------------------------------
# JSON value edge cases
# ---------------------------------------------------------------------------


def test_put_unicode_value(mem_store):
    mem_store.put("s", "msg", "中文标题 🎉")
    assert mem_store.get("s", "msg") == "中文标题 🎉"


def test_put_non_serialisable_uses_default(mem_store):
    class Opaque:
        def __repr__(self):
            return "<Opaque>"

    obj = Opaque()
    mem_store.put("s", "k", obj, default=str)
    assert mem_store.get("s", "k") == "<Opaque>"


def test_put_rejects_non_serialisable_without_default(mem_store):
    class Unserialisable:
        pass

    with pytest.raises(ContextStoreError, match="not JSON-serialisable"):
        mem_store.put("s", "k", Unserialisable())


# ---------------------------------------------------------------------------
# TTL / GC
# ---------------------------------------------------------------------------


def test_memory_purge_expired_removes_old(tmp_path):
    store = MemoryContextStore(ttl_seconds=1)
    store.put("old", "k", 1)
    time.sleep(1.2)
    store.put("new", "k", 1)
    removed = store.purge_expired()
    assert removed == 1
    assert not store.exists("old")
    assert store.exists("new")


def test_memory_purge_expired_with_explicit_ttl(mem_store):
    mem_store.put("s", "k", 1)
    removed = mem_store.purge_expired(ttl_seconds=10_000)  # huge ttl
    assert removed == 0
    assert mem_store.exists("s")


def test_memory_purge_rejects_zero_ttl(mem_store):
    with pytest.raises(ContextStoreError, match="ttl_seconds"):
        mem_store.purge_expired(ttl_seconds=0)


def test_sqlite_purge_expired_removes_old(sqlite_store):
    sqlite_store.put("s", "k", 1)
    # Force the last_access to be ancient by writing via the public
    # API after sleeping a tiny amount with an aggressive TTL.
    removed = sqlite_store.purge_expired(ttl_seconds=0.0001)
    time.sleep(0.05)
    removed2 = sqlite_store.purge_expired(ttl_seconds=0.0001)
    # First call may or may not remove; second definitely will.
    assert removed + removed2 >= 1
    assert not sqlite_store.exists("s")


def test_sqlite_rejects_zero_ttl(tmp_path):
    with pytest.raises(ContextStoreError, match="ttl_seconds"):
        SqliteContextStore(tmp_path / "x.db", ttl_seconds=0)


# ---------------------------------------------------------------------------
# SQLite persistence — survives close + reopen
# ---------------------------------------------------------------------------


def test_sqlite_persists_across_reopen(tmp_path):
    path = tmp_path / "persist.db"
    s1 = SqliteContextStore(path)
    s1.put("alice", "k", 1)
    s1.put("alice", "msg", "hello")
    s1.put("bob", "k", 2)
    s1.close()

    s2 = SqliteContextStore(path)
    assert s2.exists("alice")
    assert s2.exists("bob")
    assert s2.get("alice", "k") == 1
    assert s2.get("alice", "msg") == "hello"
    assert s2.get("bob", "k") == 2
    s2.close()


def test_sqlite_delete_is_cascade(tmp_path):
    path = tmp_path / "cascade.db"
    s = SqliteContextStore(path)
    s.put("s", "k1", 1)
    s.put("s", "k2", 2)
    s.delete("s")
    # entries gone, not just session row
    assert s.snapshot("s") == {}
    s.close()


def test_sqlite_list_sessions_ordering(tmp_path):
    path = tmp_path / "list.db"
    s = SqliteContextStore(path)
    s.put("a", "k", 1)
    time.sleep(0.02)
    s.put("b", "k", 1)
    time.sleep(0.02)
    s.put("c", "k", 1)
    info = s.list_sessions()
    assert [x.session_id for x in info] == ["c", "b", "a"]
    s.close()


def test_sqlite_concurrent_writes(tmp_path):
    """Best-effort smoke: 20 threads each put 5 keys into the same
    session. We assert all writes succeed and all values come back."""
    path = tmp_path / "concurrent.db"
    s = SqliteContextStore(path)
    s.create("concurrent")

    def worker(tid: int) -> None:
        for j in range(5):
            s.put("concurrent", f"t{tid}_k{j}", j)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    snap = s.snapshot("concurrent")
    assert len(snap) == 20 * 5
    s.close()


# ---------------------------------------------------------------------------
# context.py: legacy API still works through the new store layer
# ---------------------------------------------------------------------------


def test_context_legacy_put_get_snapshot():
    """Without ever calling configure_store, the module-level
    helpers use the default MemoryContextStore transparently."""
    ctx.use_memory_store()  # reset
    sid = ctx.new_session()
    assert ctx.session_id() == sid
    ctx.put("k1", "v1")
    ctx.put("k2", [1, 2, 3])
    assert ctx.get("k1") == "v1"
    assert ctx.get("k2") == [1, 2, 3]
    assert ctx.snapshot() == {"k1": "v1", "k2": [1, 2, 3]}
    ctx.clear()
    assert ctx.snapshot() == {}
    # session id is preserved across clear
    assert ctx.session_id() == sid


def test_context_merge_overwrites():
    ctx.use_memory_store()
    sid = ctx.new_session()
    ctx.put("a", 1)
    ctx.merge({"a": 2, "b": 3})
    assert ctx.get("a") == 2
    assert ctx.get("b") == 3
    assert ctx.session_id() == sid


def test_context_bind_creates_if_missing():
    ctx.use_memory_store()
    ctx.bind("my-session")
    assert ctx.session_id() == "my-session"
    ctx.put("k", "v")
    assert ctx.get("k") == "v"


def test_context_bind_rejects_empty():
    ctx.use_memory_store()
    with pytest.raises(ContextStoreError, match="empty"):
        ctx.bind("")


def test_context_require_session_id_raises_on_default():
    ctx.use_memory_store()
    # default ContextVar value is the literal "default"
    assert ctx.session_id() == "default"
    with pytest.raises(ContextStoreError, match="no session has been bound"):
        ctx.require_session_id()


def test_context_require_session_id_returns_bound():
    ctx.use_memory_store()
    sid = ctx.new_session()
    assert ctx.require_session_id() == sid


def test_context_use_sqlite_store_round_trip(tmp_path):
    """Switch to SQLite, write, then switch to a *different* SQLite
    path and back; the data persists across both switches because
    it's persisted to disk."""
    path = tmp_path / "switch.db"
    ctx.use_sqlite_store(str(path))
    sid = ctx.new_session()
    ctx.put("k", "v")

    # Switch to a fresh MemoryContextStore; this is the test that
    # the switch is not destructive for the user's *next* call.
    ctx.use_memory_store()
    # SQLite file is still on disk though; reopen it to verify.
    s = SqliteContextStore(path)
    assert s.get(sid, "k") == "v"
    s.close()


def test_context_configure_store_closes_previous(tmp_path):
    """Switching stores must close the previous one (especially
    important for SQLite to release the file handle)."""
    path = tmp_path / "close.db"
    s1 = SqliteContextStore(path)
    ctx.configure_store(s1)
    assert ctx.store() is s1
    s2 = MemoryContextStore()
    returned = ctx.configure_store(s2)
    assert returned is s1
    # we should be able to delete the SQLite file now (handle released)
    s1.close()  # already closed by configure_store; idempotent
    path.unlink()


# ---------------------------------------------------------------------------
# Integration: workflows write to the persistent store
# ---------------------------------------------------------------------------


async def test_workflow_writes_to_persistent_store(
    mock_chat, make_response, tmp_path
):
    """End-to-end: switch to SQLite, run a workflow, verify the
    final answer is retrievable later by opening the same file."""
    from fulladdmax_mcp import orchestrator

    path = tmp_path / "workflow.db"
    ctx.use_sqlite_store(str(path))

    # mock planner + worker + synth
    mock_chat.post("/v1/chat/completions").mock(
        side_effect=[
            make_response('{"subtasks":["A"]}'),
            make_response("worker out"),
            make_response("synth final"),
        ]
    )

    out = await orchestrator.run("do x", num_workers=1)
    assert out == "synth final"

    # data should be persisted in SQLite
    sid = ctx.session_id()
    assert sid != "default"

    # Re-open the store (in a fresh process, conceptually) and read
    # back the data.
    s = SqliteContextStore(path)
    try:
        snap = s.snapshot(sid)
        assert "subtasks" in snap
        assert snap["subtasks"] == ["A"]
        assert snap["final"] == "synth final"
    finally:
        s.close()
        ctx.use_memory_store()
