"""Smoke test: verify the new swarm_run log statement fires."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

# Make sure the in-tree source is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import respx  # noqa: E402
from httpx import Response  # noqa: E402

from fulladdmax_mcp import server  # noqa: E402
from fulladdmax_mcp.swarm import registry  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


DONE_JSON = json.dumps({"next": "DONE", "message": "all done"})


def _mock_done():
    body = {
        "id": "test",
        "object": "chat.completion",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": DONE_JSON}, "finish_reason": "stop"}
        ],
    }
    return Response(200, json=body)


async def case_no_agents_json():
    print("\n--- Case 1: no agents_json (uses registry) ---")
    with respx.mock(base_url="https://api.openai.com/v1") as m:
        m.post("/chat/completions").mock(return_value=_mock_done())
        out = await server.swarm_run("researcher", "do x", max_handoffs=2)
    print("RESULT:", out)


async def case_with_agents_json():
    print("\n--- Case 2: agents_json with 2 custom agents ---")
    custom = json.dumps(
        [
            {"name": "analyst", "system": "You are an analyst.", "description": "Market analyst."},
            {"name": "strategist", "system": "You are a strategist.", "description": "Strategy."},
        ]
    )
    with respx.mock(base_url="https://api.openai.com/v1") as m:
        m.post("/chat/completions").mock(return_value=_mock_done())
        out = await server.swarm_run("analyst", "do y", agents_json=custom)
    print("RESULT:", out)


async def case_with_invalid_agents_json():
    print("\n--- Case 3: invalid agents_json (parse error, no LLM call) ---")
    # No respx mock here: parse error should short-circuit before any
    # HTTP call is made.
    out = await server.swarm_run(
        "analyst", "y", agents_json="not valid json"
    )
    print("RESULT:", out)


async def main():
    # Snapshot the registry so we can assert what the log should show.
    print("Registry before test:", sorted(registry.snapshot()))
    await case_no_agents_json()
    await case_with_agents_json()
    await case_with_invalid_agents_json()


if __name__ == "__main__":
    asyncio.run(main())
