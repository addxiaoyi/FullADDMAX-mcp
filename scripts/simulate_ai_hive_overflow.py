"""End-to-end MCP stdio client that simulates an AI agent trying to
call ``agent(operation="hive_run", params_json='{"task":"x","waves":25}')``
and captures the actual server response.

This proves the ValueError raised inside _hive_run propagates through
the MCP dispatch layer all the way back to the AI as an
``isError=true`` tool result — not a silent truncation.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
PYTHONPATH = str(_REPO / "src")


def main() -> int:
    env = os.environ.copy()
    env["PYTHONPATH"] = PYTHONPATH
    # Force UTF-8 so the Chinese error text round-trips.
    env["PYTHONIOENCODING"] = "utf-8"
    # Strip LLM env so we hit the lazy-hint path first; the waves check
    # fires BEFORE the LLM check, so the value will still raise.
    for k in list(env):
        if k.startswith(("FULLADDMAX_", "OPENAI_", "ANTHROPIC_",
                          "CLAUDE_", "CURSOR_", "CODEX_", "CONTINUE_",
                          "AIDER_", "GITHUB_")):
            del env[k]

    print("=" * 70)
    print("SIMULATED AI:  trying to call hive_run with waves=25")
    print("=" * 70)

    # Spawn the MCP server in stdio mode.
    proc = subprocess.Popen(
        [sys.executable, "-m", "fulladdmax_mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=str(_REPO),
    )

    def send(msg: dict) -> None:
        line = json.dumps(msg)
        print(f"AI -> server: {line[:200]}{'...' if len(line) > 200 else ''}")
        assert proc.stdin is not None
        proc.stdin.write((line + "\n").encode("utf-8"))
        proc.stdin.flush()

    def recv_one() -> dict | None:
        assert proc.stdout is not None
        line = proc.stdout.readline()
        if not line:
            return None
        try:
            return json.loads(line.decode("utf-8"))
        except json.JSONDecodeError:
            return {"_raw": line.decode("utf-8", errors="replace")}

    # 1) MCP initialize handshake.
    send({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "hive-audit-client", "version": "1.0"},
        },
    })
    init_resp = recv_one()
    print(f"server -> AI:  initialize response id={init_resp.get('id')}")
    assert init_resp and init_resp.get("id") == 1, "initialize failed"

    # 2) initialized notification (no response expected).
    send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    # 3) Call agent(operation='hive_run', params_json='{"task":"x","waves":25}')
    send({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {
            "name": "agent",
            "arguments": {
                "operation": "hive_run",
                "params_json": json.dumps({"task": "x", "waves": 25}),
            },
        },
    })
    resp = recv_one()
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()

    print()
    print("=" * 70)
    print("SERVER RESPONSE  (this is what the AI would see)")
    print("=" * 70)
    if resp is None:
        print("  (no response — server may have crashed)")
        return 1
    print(json.dumps(resp, indent=2, ensure_ascii=False))

    # Assertions: ValueError must propagate.
    print()
    print("=" * 70)
    print("ASSERTIONS")
    print("=" * 70)
    ok = True
    is_error = (resp.get("result", {}).get("isError"))
    print(f"  [{'OK' if is_error else 'FAIL'}]  isError=true (MCP-level error)")
    ok &= bool(is_error)

    # The error text is the ValueError message we raised in _hive_run.
    content = resp.get("result", {}).get("content", [])
    text_blocks = [c.get("text", "") for c in content if c.get("type") == "text"]
    all_text = "\n".join(text_blocks)
    print(f"  error text: {all_text[:300]}")

    has_waves = "waves=25" in all_text
    has_ceiling = "max_waves=20" in all_text
    has_value_error = "ValueError" in all_text
    print(f"  [{'OK' if has_waves else 'FAIL'}]  error mentions waves=25")
    print(f"  [{'OK' if has_ceiling else 'FAIL'}]  error mentions max_waves=20")
    print(f"  [{'OK' if has_value_error else 'FAIL'}]  error mentions ValueError")
    ok &= has_waves and has_ceiling and has_value_error

    print()
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
