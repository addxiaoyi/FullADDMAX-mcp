"""Orchestrator-Workers demo.

Run::

    # Either set env vars:
    #   FULLADDMAX_BASE_URL, FULLADDMAX_API_KEY, FULLADDMAX_MODEL
    # Or call configure_llm() first via the MCP server.

    python examples/orchestrator_demo.py

This demo decomposes "Design a REST API for a todo app" into 3 subtasks,
runs them in parallel, and synthesizes a final answer.
"""

from __future__ import annotations

import asyncio

from fulladdmax_mcp import orchestrator


async def main() -> None:
    task = "Design a REST API for a todo app with authentication."
    print(f"=== Orchestrator-Workers: {task} ===\n")
    out = await orchestrator.run(task, num_workers=3, timeout=180.0)
    print(out)


if __name__ == "__main__":
    asyncio.run(main())
