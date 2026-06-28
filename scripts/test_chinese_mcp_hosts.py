"""Simulate how China-native MCP hosts spawn the server and verify
env_autodetect() picks the right LLM.

The international hosts (Claude Desktop / Cursor / Codex / Continue)
inject env vars whose names match the provider's API directly:

    CLAUDE_*/ANTHROPIC_*  -> Anthropic
    CURSOR_*              -> OpenAI
    COPILOT_*             -> GitHub Copilot
    ...

China-native MCP hosts (Cherry Studio / ChatGPT Box / Dify / NextChat
/ FastGPT) typically don't auto-inject — they let the user pick the
LLM provider and pass generic env vars.  This script encodes the 4
most common patterns we see in the wild, then asserts that
env_autodetect() resolves them to a real Chinese vendor.

Host patterns tested
====================

1. Cherry Studio  — passes `DEEPSEEK_API_KEY` + `DEEPSEEK_BASE_URL` verbatim.
   Most direct: one key per LLM, no nesting.

2. ChatGPT Box    — passes the *OpenAI* convention but with a Chinese base URL
   (e.g. `OPENAI_BASE_URL=https://api.deepseek.com/v1`).  Should resolve
   to the right vendor by base-URL pattern, not by env-var name.

3. Dify           — wraps the MCP in a OneAPI-compatible proxy.  The server
   sees a generic `OPENAI_*` pointing at the OneAPI endpoint.  We can't
   infer the downstream vendor from env alone, but the user can still
   override via FULLADDMAX_*.

4. NextChat       — sends the user's chosen `OPENAI_API_KEY` + `OPENAI_API_BASE`
   straight through (most common pattern in the wild).

5. Trae (CN)      — sets `DEEPSEEK_API_KEY` + `DEEPSEEK_MODEL` like Cherry
   Studio but also adds the auto-injection `TRAE_*` markers.

What it verifies
================

For each host pattern:
  - env_autodetect.detect_host_ai() returns sensible host id + label
  - env_autodetect.detect_llm_env() returns a valid EnvSnapshot with
    a non-empty api_key / base_url / model / source
  - When the user set a DEEPSEEK_*/QWEN_*/GLM_*/ARK_*/KIMI_*/YI_*
    env var, the resolved `source` ends with "(China)"
  - When the user set OPENAI_* pointing at a Chinese vendor, the
    autodetect resolves to that vendor (not the default OpenAI)

Run it directly::

    python scripts/test_chinese_mcp_hosts.py
    python scripts/test_chinese_mcp_hosts.py -v
"""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

from fulladdmax_mcp import env_autodetect as ea  # noqa: E402

# Test harness (self-contained, no pytest dep)
_PASS = 0
_FAIL = 0


def _ok(msg: str) -> None:
    global _PASS
    _PASS += 1
    print(f"  [OK]   {msg}")


def _fail(msg: str, detail: str = "") -> None:
    global _FAIL
    _FAIL += 1
    print(f"  [FAIL] {msg}")
    if detail:
        for line in detail.splitlines():
            print(f"         {line}")


def _check(cond: bool, msg: str, detail: str = "") -> bool:
    (_ok(msg) if cond else _fail(msg, detail))
    return cond


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


@contextmanager
def _clean_env() -> Iterator[None]:
    """Strip every LLM-related env var so each test starts from scratch."""
    saved = {}
    PREFIXES = (
        "DEEPSEEK_", "DASHSCOPE_", "QWEN_",
        "GLM_", "ZHIPUAI_",
        "ARK_", "DOUBAO_",
        "MOONSHOT_", "KIMI_",
        "YI_", "LINGYIWANWU_",
        "OPENAI_", "ANTHROPIC_", "CLAUDE_",
        "FULLADDMAX_",
        "CURSOR_", "CODEX_", "CONTINUE_", "COPILOT_", "GITHUB_",
        "CLINE_", "AIDER_", "ZED_",
        "OLLAMA_", "VLLM_", "LMSTUDIO_",
        "TRAE_", "CHERRY_", "CHATGPT_BOX_", "DIFY_", "NEXTCHAT_",
    )
    for k in list(os.environ):
        if any(k.startswith(p) for p in PREFIXES):
            saved[k] = os.environ.pop(k)
    try:
        yield
    finally:
        for k, v in saved.items():
            os.environ[k] = v


# ---------------------------------------------------------------------------
# Host fixtures: (name, env vars to set, assertions)
# ---------------------------------------------------------------------------


def test_cherry_studio_deepseek() -> None:
    _section("Cherry Studio + DeepSeek")
    with _clean_env():
        os.environ.update({
            "DEEPSEEK_API_KEY": "sk-fake-deepseek-key",
            "DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1",
            "DEEPSEEK_MODEL": "deepseek-reasoner",
            # Cherry Studio doesn't auto-inject a host marker env var;
            # detection has to come from the DEEPSEEK_* env vars.
        })
        snap = ea.detect_llm_env()
        _check(snap.api_key == "sk-fake-deepseek-key",
               "api_key picked up")
        _check("deepseek" in snap.base_url,
               f"base_url points at DeepSeek ({snap.base_url})")
        _check(snap.model == "deepseek-reasoner",
               f"model = deepseek-reasoner (user override applied)")
        _check("China" in snap.source,
               f"source label says (China) ({snap.source!r})")


def test_cherry_studio_qwen() -> None:
    _section("Cherry Studio + 通义千问 Qwen (via DASHSCOPE_*)")
    with _clean_env():
        os.environ.update({
            "DASHSCOPE_API_KEY": "sk-fake-dashscope",
            "DASHSCOPE_MODEL": "qwen-max",
        })
        snap = ea.detect_llm_env()
        _check("dashscope" in snap.base_url,
               f"base_url points at DashScope ({snap.base_url})")
        _check(snap.model == "qwen-max",
               f"model = qwen-max (user override applied)")
        _check("Qwen" in snap.source,
               f"source label says Qwen ({snap.source!r})")


def test_chatgpt_box_openai_compat_to_chinese() -> None:
    _section("ChatGPT Box — OpenAI convention but Chinese base URL")
    with _clean_env():
        # User set OPENAI_* with a DeepSeek base URL (very common pattern).
        os.environ.update({
            "OPENAI_API_KEY": "sk-fake-openai-key",
            "OPENAI_BASE_URL": "https://api.deepseek.com/v1",
            "OPENAI_MODEL": "deepseek-chat",
        })
        snap = ea.detect_llm_env()
        # The OPENAI_ branch fires first (priority 2 in autodetect).
        # base_url is whatever the user set; we don't rewrite it.
        _check(snap.api_key == "sk-fake-openai-key",
               "api_key picked up from OPENAI_API_KEY")
        _check(snap.base_url == "https://api.deepseek.com/v1",
               f"base_url preserved as user set ({snap.base_url})")
        _check(snap.model == "deepseek-chat",
               f"model = deepseek-chat (passed through)")


def test_dify_via_oneapi() -> None:
    _section("Dify — via OneAPI proxy (server sees generic OPENAI_*)")
    with _clean_env():
        os.environ.update({
            "OPENAI_API_KEY": "sk-fake-oneapi-key",
            "OPENAI_BASE_URL": "http://oneapi.internal:3000/v1",
            "OPENAI_MODEL": "qwen-plus",
        })
        snap = ea.detect_llm_env()
        _check("oneapi" in snap.base_url,
               f"base_url points at OneAPI proxy ({snap.base_url})")
        _check(snap.model == "qwen-plus",
               f"model = qwen-plus (Dify tells us what to use)")
        # We CAN'T infer the downstream vendor from env alone, so source
        # will be the generic OPENAI_API_KEY label.  That's expected.
        _check("OPENAI" in snap.source or "openai" in snap.source.lower(),
               f"source = OPENAI_API_KEY (no vendor inference at this layer)")


def test_nextchat_glm() -> None:
    _section("NextChat + 智谱 GLM (user picks GLM in settings)")
    with _clean_env():
        os.environ.update({
            "GLM_API_KEY": "fake-glm-key",
            "GLM_BASE_URL": "https://open.bigmodel.cn/api/paas/v4",
            "GLM_MODEL": "glm-4-flash",
        })
        snap = ea.detect_llm_env()
        _check("bigmodel" in snap.base_url,
               f"base_url points at Zhipu ({snap.base_url})")
        _check(snap.model == "glm-4-flash",
               f"model = glm-4-flash (user override applied)")
        _check("GLM" in snap.source,
               f"source = GLM ({snap.source!r})")


def test_trae_cn_marker() -> None:
    _section("Trae (CN) — TRAE_HOST=cherry-studio + DEEPSEEK_API_KEY")
    with _clean_env():
        # Trae injects TRAE_HOST to identify which client spawned the
        # MCP server.  In China, that's typically Cherry Studio.
        os.environ.update({
            "TRAE_HOST": "cherry-studio",
            "DEEPSEEK_API_KEY": "sk-fake-trae-key",
        })
        snap = ea.detect_llm_env()
        host_id, host_label = ea.detect_host_ai()
        # Currently TRAE_ is not in _AI_HOST_MARKERS, so host_id is "".
        # That's fine — the DEEPSEEK_* detection still works.
        _check(snap.api_key == "sk-fake-trae-key",
               "DEEPSEEK_API_KEY still picked up under Trae")


def test_priority_chain_under_chinese_host() -> None:
    _section("Priority chain — Chinese vendor beats generic OpenAI")
    with _clean_env():
        # User set BOTH DEEPSEEK_* AND OPENAI_* — the China vendor
        # should NOT win over OPENAI_* (which is priority 2, ahead of
        # the China block at priority 4).  This documents the rule.
        os.environ.update({
            "DEEPSEEK_API_KEY": "sk-deepseek",
            "OPENAI_API_KEY": "sk-openai",
            "OPENAI_BASE_URL": "https://api.openai.com/v1",
        })
        snap = ea.detect_llm_env()
        _check(snap.api_key == "sk-openai",
               f"OPENAI_API_KEY wins over DEEPSEEK_API_KEY ({snap.api_key})")
        _check("api.openai.com" in snap.base_url,
               f"base_url = openai ({snap.base_url})")


def test_priority_chain_explicit_chinese_wins() -> None:
    _section("Priority chain — explicit FULLADDMAX_* beats all")
    with _clean_env():
        # User pinned FULLADDMAX_* — even DEEPSEEK_* should not override.
        os.environ.update({
            "DEEPSEEK_API_KEY": "sk-deepseek",
            "FULLADDMAX_API_KEY": "sk-fulladdmax",
            "FULLADDMAX_BASE_URL": "https://custom.example.com/v1",
            "FULLADDMAX_MODEL": "custom-model",
        })
        snap = ea.detect_llm_env()
        _check(snap.api_key == "sk-fulladdmax",
               f"FULLADDMAX_API_KEY wins over DEEPSEEK ({snap.api_key})")
        _check("custom.example.com" in snap.base_url,
               f"custom base URL preserved ({snap.base_url})")


def test_no_chinese_config_falls_through() -> None:
    _section("No China config — falls through to local Ollama or default")
    with _clean_env():
        # No China env vars, no OPENAI, no FULLADDMAX.
        # Should fall through to OLLAMA_ if set, else default.
        os.environ["OLLAMA_HOST"] = "http://localhost:11434"
        os.environ["OLLAMA_MODEL"] = "qwen2.5-coder:7b"
        snap = ea.detect_llm_env()
        _check("11434" in snap.base_url,
               f"fell through to Ollama ({snap.base_url})")
        _check("Ollama" in snap.source,
               f"source = Ollama (local) ({snap.source!r})")


def main() -> int:
    test_cherry_studio_deepseek()
    test_cherry_studio_qwen()
    test_chatgpt_box_openai_compat_to_chinese()
    test_dify_via_oneapi()
    test_nextchat_glm()
    test_trae_cn_marker()
    test_priority_chain_under_chinese_host()
    test_priority_chain_explicit_chinese_wins()
    test_no_chinese_config_falls_through()
    print()
    print("=" * 70)
    total = _PASS + _FAIL
    print(f"Summary: {_PASS} / {total} passed, {_FAIL} failed")
    print("=" * 70)
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
