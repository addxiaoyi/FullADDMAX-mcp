"""Tests for the shared session context."""

from __future__ import annotations

import threading

from fulladdmax_mcp import context as ctx


def test_put_get_clear():
    ctx.new_session()
    ctx.put("a", 1)
    assert ctx.get("a") == 1
    assert ctx.get("missing", "x") == "x"
    ctx.clear()
    assert ctx.get("a") is None


def test_session_isolation():
    s1 = ctx.new_session()
    ctx.put("k", "v1")
    s2 = ctx.new_session()
    ctx.put("k", "v2")
    assert ctx.get("k") == "v2"
    ctx.bind(s1)
    assert ctx.get("k") == "v1"
    ctx.bind(s2)
    assert ctx.get("k") == "v2"


def test_thread_safe_put():
    """Concurrent writes to the same session must all be visible.

    Each worker thread binds to the same session id explicitly, since
    ``contextvars.ContextVar`` does not propagate to plain threads.
    """
    sid = ctx.new_session()
    n = 50  # smaller than the full demo to keep the test fast

    def worker(i: int):
        ctx.bind(sid)
        ctx.put(f"k{i}", i)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ctx.bind(sid)
    snap = ctx.snapshot()
    assert len(snap) == n
    for i in range(n):
        assert snap[f"k{i}"] == i


def test_merge_and_dump():
    ctx.new_session()
    ctx.put("a", 1)
    ctx.merge({"b": 2, "c": 3})
    snap = ctx.snapshot()
    assert snap == {"a": 1, "b": 2, "c": 3}
    dumped = ctx.dump_json()
    assert '"a": 1' in dumped
