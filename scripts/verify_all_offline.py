"""Verify all 7 agent ops work with FULLADDMAX_AGENT_OFFLINE=1."""
import asyncio
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Strip ALL LLM env so even autodetect has nothing to find.
for k in list(os.environ):
    if k.startswith(("FULLADDMAX_", "OPENAI_", "ANTHROPIC_", "CLAUDE_",
                      "CURSOR_", "CODEX_", "CONTINUE_", "AIDER_",
                      "GITHUB_", "LMSTUDIO_", "VLLM_", "OLLAMA_")):
        del os.environ[k]
os.environ["FULLADDMAX_AGENT_OFFLINE"] = "1"

from fulladdmax_mcp import llm
llm.set_config(llm.LLMConfig(api_key=""))
from fulladdmax_mcp.handlers import agent as a

print("=" * 70)
print("ALL 7 AGENT OPS - FULLADDMAX_AGENT_OFFLINE=1 - ZERO LLM")
print("=" * 70)
ops = [
    ("orchestrator_run",    lambda: a._orchestrator_run(task="design payment")),
    ("parallel_agents_run", lambda: a._parallel_agents_run(tasks=["A", "B", "C"])),
    ("map_reduce_run",      lambda: a._map_reduce_run(items=["x", "y", "z"])),
    ("swarm_run",           lambda: a._swarm_run(initial_agent="coder", task="build")),
    ("auto_workflow",       lambda: a._auto_workflow(task="do A,B,C in parallel")),
    ("delegate",            lambda: a._delegate(task="research Beijing, Shanghai, Shenzhen")),
    ("hive_run",            lambda: a._hive_run(task="design payment system", waves=2)),
]
for op_name, op_fn in ops:
    out = asyncio.run(op_fn())
    no_hint = "No LLM endpoint configured" not in out
    first = out.split("\n")[0]
    print(f"  [{'OK' if no_hint else 'FAIL'}]  {op_name:25s}  -> {first[:55]}")

print()
print("=" * 70)
print("hive_run stub output sample (last 8 lines)")
print("=" * 70)
out = asyncio.run(a._hive_run(task="design payment system", waves=1))
for line in out.split("\n")[-12:]:
    print("  " + line)
