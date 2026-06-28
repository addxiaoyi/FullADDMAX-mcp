"""Generate per-host, per-provider MCP JSON example files.

Run from the repo root::

    python scripts/generate_mcp_json_examples.py

Outputs to ``examples/mcp-json/`` with one file per host x provider.
All snippets come from a single ``PROVIDERS`` table so adding a new
LLM vendor only takes one place to edit.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "examples" / "mcp-json"

# Each provider maps to (env var key, default model, label).
# Adding a 7th Chinese vendor is a 1-line change here.
PROVIDERS: dict[str, tuple[str, str, str]] = {
    "deepseek": ("DEEPSEEK_API_KEY",  "deepseek-chat",       "DeepSeek 深度求索"),
    "qwen":     ("DASHSCOPE_API_KEY", "qwen-plus",            "通义千问 (DashScope)"),
    "glm":      ("ZHIPUAI_API_KEY",   "glm-4-plus",           "智谱 GLM"),
    "doubao":   ("ARK_API_KEY",       "doubao-pro-32k",       "字节豆包 (火山方舟)"),
    "kimi":     ("MOONSHOT_API_KEY",  "moonshot-v1-128k",     "Kimi 月之暗面"),
    "yi":       ("YI_API_KEY",        "yi-large",             "零一万物 Yi"),
}

# Map model env vars per provider.  Defaults cover the canonical
# alias; the second entry is what the autodetect() also reads.
MODEL_VARS: dict[str, tuple[str, ...]] = {
    "deepseek": ("DEEPSEEK_MODEL",),
    "qwen":     ("QWEN_MODEL", "DASHSCOPE_MODEL"),
    "glm":      ("GLM_MODEL", "ZHIPUAI_MODEL"),
    "doubao":   ("ARK_MODEL", "DOUBAO_MODEL"),
    "kimi":     ("MOONSHOT_MODEL", "KIMI_MODEL"),
    "yi":       ("YI_MODEL", "LINGYIWANWU_MODEL"),
}

# Hosts that take the standard ``mcpServers`` object.  Cherry Studio,
# ChatGPT Box, Dify, NextChat all use a near-identical shape.
STANDARD_HOSTS = ("claude-desktop", "cursor", "cline", "trae",
                  "cherry-studio", "chatgpt-box", "nextchat")


def _build_env(provider: str) -> dict[str, str]:
    key_var, _, _ = PROVIDERS[provider]
    primary_model_var = MODEL_VARS[provider][0]
    _, default_model, _ = PROVIDERS[provider]
    env = {
        key_var: f"你的-{provider}-key",
        primary_model_var: default_model,
        "FULLADDMAX_LANG": "zh-CN",
    }
    return env


def _server_block(provider: str) -> dict[str, Any]:
    _, _, label = PROVIDERS[provider]
    return {
        "command": "uvx",
        "args": ["fulladdmax-mcp"],
        "env": _build_env(provider),
        "description": f"FullADDMAX-mcp · {label}",
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    n = 0
    # Per-provider × per-host individual files
    for provider in PROVIDERS:
        for host in STANDARD_HOSTS:
            block = {"mcpServers": {f"fulladdmax-{provider}": _server_block(provider)}}
            out_path = OUT / f"{host}.{provider}.json"
            out_path.write_text(
                json.dumps(block, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            n += 1

    # Combined file: 6 providers, all using the standard "mcpServers" shape.
    combined = {
        "_comment": "FullADDMAX-mcp · 6 大中国 LLM 的 MCP JSON 模板。挑一段 → 替换 key → 粘到对应主机。",
        "_docs": "docs/mcp-configs.md",
        "mcpServers": {
            f"fulladdmax-{p}": _server_block(p) for p in PROVIDERS
        },
    }
    (OUT / "all-chinese-providers.json").write_text(
        json.dumps(combined, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    n += 1

    print(f"wrote {n} files to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
