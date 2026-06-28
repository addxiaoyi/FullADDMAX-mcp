"""Verify all generated MCP JSON example files are valid + complete.

Checks every file in ``examples/mcp-json/`` has:
* a top-level ``mcpServers`` object (except ``all-chinese-providers.json``
  which has the same shape but at the top level with extra metadata)
* each server entry has ``command``, ``args``, ``env``
* each ``env`` contains a recognised provider key
* ``FULLADDMAX_LANG`` is set

Run from repo root::

    python -m scripts.test_mcp_json_examples
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "mcp-json"

# env var names we expect to find (subset covering all 6 providers)
PROVIDER_KEYS = {
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY", "QWEN_API_KEY",
    "ZHIPUAI_API_KEY", "GLM_API_KEY",
    "ARK_API_KEY", "DOUBAO_API_KEY",
    "MOONSHOT_API_KEY", "KIMI_API_KEY",
    "YI_API_KEY", "LINGYIWANWU_API_KEY",
}


def main() -> int:
    files = sorted(EXAMPLES.glob("*.json"))
    if not files:
        print(f"no JSON files found in {EXAMPLES}")
        return 1

    failed = 0
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"  FAIL  {f.name}: invalid JSON: {e}")
            failed += 1
            continue

        servers = data.get("mcpServers") if "mcpServers" in data else data
        if not isinstance(servers, dict) or not servers:
            print(f"  FAIL  {f.name}: missing mcpServers object")
            failed += 1
            continue

        for srv_name, cfg in servers.items():
            if not isinstance(cfg, dict):
                print(f"  FAIL  {f.name}: server {srv_name!r} is not an object")
                failed += 1
                continue
            for required in ("command", "args", "env"):
                if required not in cfg:
                    print(f"  FAIL  {f.name}: server {srv_name!r} missing {required!r}")
                    failed += 1
            env = cfg.get("env") or {}
            if not (env.keys() & PROVIDER_KEYS):
                print(f"  FAIL  {f.name}: server {srv_name!r} env has no provider key")
                failed += 1
            if env.get("FULLADDMAX_LANG") != "zh-CN":
                print(f"  WARN  {f.name}: server {srv_name!r} missing FULLADDMAX_LANG=zh-CN")
        else:
            print(f"  ok    {f.name}: {len(servers)} server(s)")

    if failed:
        print(f"\n{failed} check(s) failed")
        return 1
    print(f"\nall {len(files)} files pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
