"""Map-Reduce demo.

Run::

    python examples/mapreduce_demo.py

Summarizes 5 short paragraphs in parallel (map), then merges them (reduce)
into a single executive summary.
"""

from __future__ import annotations

import asyncio

from fulladdmax_mcp import mapreduce


async def main() -> None:
    items = [
        "The cat sat on the mat and looked at the window.",
        "Python is a high-level, interpreted, general-purpose programming language.",
        "MCP is a protocol for connecting LLMs to tools and data sources.",
        "Asyncio is a library to write concurrent code using the async/await syntax.",
        "FastMCP is a Python framework for building MCP servers with minimal boilerplate.",
    ]
    print("=== Map-Reduce: 5 short items -> executive summary ===\n")
    out = await mapreduce.run(items, max_concurrent=5, timeout=180.0)
    print(out)


if __name__ == "__main__":
    asyncio.run(main())
