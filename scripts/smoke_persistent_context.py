"""Smoke: in-process simulation of a "restart" by closing the SQLite
store and reopening the same file from scratch. We use a manually
constructed store (not the module-level one) so that the second
"process" really does start from a blank slate.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fulladdmax_mcp.context_store import (
    MemoryContextStore,
    SqliteContextStore,
)


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="famctx_"))
    try:
        db = workdir / "ctx.db"

        # "Process 1" — open a SQLite store, write stuff, close.
        print("=== Process 1 ===")
        s1 = SqliteContextStore(db, ttl_seconds=3600)
        s1.put("alice", "k1", "hello")
        s1.put("alice", "k2", [1, 2, 3])
        s1.put("alice", "nested", {"x": True})
        s1.put("bob", "k", "world")
        s1.create("charlie")
        s1.merge("charlie", {"a": 1, "b": 2})
        s1.close()
        print("wrote 3 sessions and closed DB")

        # "Process 2" — re-open the same file. This is what a
        # post-restart state looks like.
        print("\n=== Process 2 (after restart) ===")
        s2 = SqliteContextStore(db, ttl_seconds=3600)
        info = s2.list_sessions()
        print(f"sessions in store: {len(info)}")
        for x in info:
            print(f"  - {x.session_id}: {x.size} keys, last_access={int(x.last_access)}")

        # data should be intact
        assert s2.get("alice", "k1") == "hello"
        assert s2.get("alice", "k2") == [1, 2, 3]
        assert s2.get("alice", "nested") == {"x": True}
        assert s2.get("bob", "k") == "world"
        assert s2.snapshot("charlie") == {"a": 1, "b": 2}
        print("data integrity OK ✅")

        # TTL
        s2.put("old", "k", "v")
        # Hack the last_access to be ancient
        import sqlite3, time
        s2._conn.execute(
            "UPDATE sessions SET last_access = ? WHERE session_id = ?",
            (time.time() - 10000, "old"),
        )
        removed = s2.purge_expired(ttl_seconds=60)
        assert removed == 1
        assert not s2.exists("old")
        print("TTL purge OK ✅")

        # delete + list
        assert s2.delete("bob") is True
        info = s2.list_sessions()
        assert all(x.session_id != "bob" for x in info)
        print("delete OK ✅")

        s2.close()

        # MemoryContextStore: no persistence (sanity check)
        mem = MemoryContextStore()
        mem.put("p1", "k", "v")
        mem.close()  # data is gone
        mem2 = MemoryContextStore()
        assert not mem2.exists("p1")
        print("Memory store loses data on close (as expected) ✅")

        print("\nALL PERSISTENCE SMOKE CHECKS PASSED ✅")
        return 0
    finally:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
