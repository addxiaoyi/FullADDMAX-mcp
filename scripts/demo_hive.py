"""Quick visual demo of hive_run's 6 ministries + lazy-hint behavior."""
import os, sys, asyncio
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Strip ALL LLM env so we exercise the lazy-hint path.
for k in list(os.environ):
    if k.startswith(("FULLADDMAX_", "OPENAI_", "ANTHROPIC_", "CLAUDE_",
                      "CURSOR_", "CODEX_", "CONTINUE_", "AIDER_")):
        del os.environ[k]
from fulladdmax_mcp import llm
llm.set_config(llm.LLMConfig(api_key=""))
from fulladdmax_mcp.handlers import agent as a

print("=" * 60)
print("DEFAULT 6 MINISTRIES  (三省六部)")
print("=" * 60)
for i, m in enumerate(a._DEFAULT_MINISTRIES, 1):
    print(f"  {i}. {m['name']:<22s}  {m['angle']}")

print()
print("=" * 60)
print("hive_run WITHOUT LLM  ->  lazy-hint")
print("=" * 60)
result = asyncio.run(a._hive_run(task="设计全球支付系统", waves=2))
for line in result.splitlines()[:14]:
    print("  " + line)
print(f"  ... ({len(result.splitlines())} lines total)")
