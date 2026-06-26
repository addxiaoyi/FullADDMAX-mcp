"""Swarm demo.

Run::

    python examples/swarm_demo.py

Chains: researcher -> coder -> critic -> writer.
Each agent replies with strict JSON to hand off to the next.
"""

from __future__ import annotations

import asyncio

from fulladdmax_mcp import swarm


async def main() -> None:
    task = "Propose a small CLI tool that converts Markdown to PDF."
    print("=== Swarm: researcher -> coder -> critic -> writer ===\n")
    out = await swarm.run("researcher", task, max_handoffs=8, timeout=180.0)
    print(out)


if __name__ == "__main__":
    asyncio.run(main())
