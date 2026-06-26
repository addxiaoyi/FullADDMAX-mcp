"""Parallel agents demo.

Run::

    python examples/parallel_demo.py

Fires off 5 independent questions in parallel and prints a Markdown report.
"""

from __future__ import annotations

import asyncio

from fulladdmax_mcp import parallel


async def main() -> None:
    tasks = [
        "What is the capital of France?",
        "What is 2 + 2?",
        "Name three primary colors.",
        "Who wrote Hamlet?",
        "What is the speed of light in m/s?",
    ]
    print("=== Parallel Agents: 5 independent questions ===\n")
    out = await parallel.run(tasks, max_concurrent=5, timeout=120.0)
    print(out)


if __name__ == "__main__":
    asyncio.run(main())
