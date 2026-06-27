"""Verify the actual hard limits of hive_run."""
import os, sys, asyncio
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Patch _require_llm so we can exercise the handler without an LLM
from fulladdmax_mcp.handlers import agent as a

# 1. Confirm SCHEMAS has no max_depth
print("=" * 60)
print("SCHEMAS audit")
print("=" * 60)
print("hive_run SCHEMAS fields:", list(a.SCHEMAS["hive_run"].keys()))
print("has max_depth?", "max_depth" in a.SCHEMAS["hive_run"])
print("has delegate_depth tracking?", hasattr(a, "ctx_mod") or True)
import inspect
src = inspect.getsource(a._hive_run)
print("uses ctx_mod?", "ctx_mod" in src)
print("uses get/set depth?", "depth" in src and "ctx_mod" in src)

# 2. Patch _require_llm to be a no-op so the handler runs the full body
a._require_llm = lambda *a, **k: None
# Patch parallel_agents_run to return a stub so we can count invocations
calls = []
async def _stub_parallel(tasks, **kwargs):
    calls.append(len(tasks))
    return f"[stub] fired {len(tasks)} workers"
a.server_internal.parallel_agents_run = _stub_parallel

# 3. Run with waves=1000 and observe what happens
print()
print("=" * 60)
print("Call hive_run(task='x', waves=1000)")
print("=" * 60)
result = asyncio.run(a._hive_run(task="x", waves=1000))
# Count actual sub-tasks fired
total = sum(calls)
print(f"waves requested: 1000")
print(f"ministries:      6")
print(f"parallel_agents_run invocations: {len(calls)}")
print(f"total sub-agents fired: {total}")
# The "--- wave" markers in the output reveal how many waves actually ran
waves_in_output = result.count("--- wave")
print(f"waves actually executed (from output): {waves_in_output}")
# Find the cap message
if "waves=" in result:
    for line in result.splitlines():
        if "ministries, " in line:
            print(f"actual reported: {line.strip()}")

# 4. Run with waves=2, count
print()
print("=" * 60)
print("Call hive_run(task='x', waves=2)")
print("=" * 60)
calls.clear()
result2 = asyncio.run(a._hive_run(task="x", waves=2))
print(f"sub-agents fired: {sum(calls)}")
print(f"waves actually executed: {result2.count('--- wave')}")
