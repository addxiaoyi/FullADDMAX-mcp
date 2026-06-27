"""Verify the A+B fixes to hive_run recursion handling."""
import asyncio
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fulladdmax_mcp.handlers import agent as a
from fulladdmax_mcp import context as ctx

# Patch
a._require_llm = lambda *a, **k: None
async def _stub(tasks, **k):
    return f"[stub] fired {len(tasks)}"
a.server_internal.parallel_agents_run = _stub

# 1) waves=1000 should raise loud
print("Test 1: waves=1000 -> ValueError")
try:
    asyncio.run(a._hive_run(task="x", waves=1000))
    print("  FAIL: no error")
except ValueError as e:
    print(f"  OK: raised: {e}")

# 2) waves=5 should work
print()
print("Test 2: waves=5 -> works")
out = asyncio.run(a._hive_run(task="x", waves=5))
print(f"  waves actually run: {out.count('--- wave')}")

# 3) max_depth=1 with depth=1 -> downgraded
print()
print("Test 3: max_depth=1 with depth=1 -> downgraded")
ctx.put("hive_depth", 1)
out = asyncio.run(a._hive_run(task="x", waves=2, max_depth=1))
print(f"  output contains 'downgraded': {'downgraded' in out}")
print(f"  message excerpt: {out.splitlines()[0]}")

# 4) max_depth=None with depth=5 -> no downgrade
print()
print("Test 4: max_depth=None with depth=5 -> no downgrade")
ctx.put("hive_depth", 5)
out = asyncio.run(a._hive_run(task="x", waves=1, max_depth=None))
print(f"  output contains 'downgraded': {'downgraded' in out}")

# 5) depth restored after nested call
print()
print("Test 5: depth restored after call")
ctx.put("hive_depth", 2)
asyncio.run(a._hive_run(task="x", waves=1, max_depth=5))
restored = ctx.get("hive_depth", 0)
print(f"  depth before: 2")
print(f"  depth after: {restored}")
print(f"  restored: {restored == 2}")
