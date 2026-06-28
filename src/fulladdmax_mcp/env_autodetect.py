"""Environment auto-detection for the LLM endpoint.

Single source of truth for "where do we get the LLM config when the
user hasn't explicitly called ``configure_llm`` yet".  Pulled out of
``panel.py`` so the LLM client itself can use the same heuristics
during startup and so the four mega tools can answer "what host am I
running under?" consistently.

Detection order (first non-empty wins):

1. Host-injected credentials — the MCP client (Claude Desktop, Cursor,
   Trae, Codex, Continue.dev, GitHub Copilot, Cline, Aider, Zed) leaks
   its own LLM creds into our process environment.  We **reuse** them
   instead of forcing the user to paste a key a second time.  Each host
   is recognised by a known prefix; the matching env var supplies the
   actual key.

2. ``FULLADDMAX_*`` env vars — the explicit override.

3. ``OPENAI_API_KEY`` + ``OPENAI_BASE_URL`` + ``OPENAI_MODEL`` — the
   de-facto standard for OpenAI-compatible clients.

4. Local LLM servers — ``OLLAMA_HOST``, ``VLLM_HOST``,
   ``LMSTUDIO_HOST`` (or fall back to ``http://localhost:11434/v1``).

The output is an :class:`EnvSnapshot` that tells the caller:

* whether anything was found at all (so we can show "no host" vs
  "host-injected" in the panel);
* the resolved ``api_key`` (masked), ``base_url`` and ``model`` (only
  set when the source was env / host — never from a previously set
  ``LLMConfig``, so the helper is idempotent);
* a free-text ``source`` label for diagnostics.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"

# ---------------------------------------------------------------------------
# Host-AI detection
# ---------------------------------------------------------------------------

# (env-var prefix, pretty label, default base_url, default model)
# Order matters — earlier entries win when multiple markers are present.
_AI_HOST_MARKERS: list[tuple[str, str, str, str]] = [
    ("CLAUDE_CODE_",        "Claude Desktop", "https://api.anthropic.com/v1", "claude-sonnet-4-5"),
    ("ANTHROPIC_",          "Anthropic",      "https://api.anthropic.com/v1", "claude-sonnet-4-5"),
    ("CLAUDE_",             "Claude Desktop", "https://api.anthropic.com/v1", "claude-sonnet-4-5"),
    ("CURSOR_",             "Cursor",         DEFAULT_BASE_URL,               "gpt-4o-mini"),
    ("CODEX_",              "Codex CLI",      DEFAULT_BASE_URL,               "gpt-4o-mini"),
    ("OPENAI_CODEX_",       "Codex CLI",      DEFAULT_BASE_URL,               "gpt-4o-mini"),
    ("CONTINUE_",           "Continue.dev",   DEFAULT_BASE_URL,               "gpt-4o-mini"),
    ("COPILOT_",            "GitHub Copilot", DEFAULT_BASE_URL,               "gpt-4o-mini"),
    ("GITHUB_COPILOT_",     "GitHub Copilot", DEFAULT_BASE_URL,               "gpt-4o-mini"),
    ("CLINE_",              "Cline",          DEFAULT_BASE_URL,               "gpt-4o-mini"),
    ("AIDER_",              "Aider",          DEFAULT_BASE_URL,               "gpt-4o-mini"),
    ("ZED_",                "Zed",            DEFAULT_BASE_URL,               "gpt-4o-mini"),
    ("ZED_AGENT_",          "Zed",            DEFAULT_BASE_URL,               "gpt-4o-mini"),
]


def detect_host_ai() -> tuple[str, str]:
    """Return ``(host_id, host_label)`` for whichever AI host spawned us.

    Returns ``("", "")`` if none of the known markers are present.
    The ``host_id`` is a short lowercase tag suitable for branching;
    the ``host_label`` is the human-readable name shown in the UI.
    """
    for prefix, label, _url, _model in _AI_HOST_MARKERS:
        for k in os.environ:
            if k.startswith(prefix):
                # Collapse the longer Claude variants to a single
                # canonical "claude" id (the panel / tests branch on
                # the id; the label still distinguishes them).
                short = prefix.rstrip("_").lower()
                if short in ("claude", "claude_code", "anthropic"):
                    short = "claude"
                return (short, label)
    return ("", "")


# ---------------------------------------------------------------------------
# Chinese cloud LLM providers
# ---------------------------------------------------------------------------

# Each row: (env-var prefix, default base URL, default model, source label).
# All 6 providers speak the OpenAI Chat Completions protocol, so the
# LLMClient needs no special-casing — only the autodetect layer has to
# know which vendor the user is on.
_CN_LLM_PROVIDERS: list[tuple[str, str, str, str]] = [
    (
        "DEEPSEEK_",
        "https://api.deepseek.com/v1",
        "deepseek-chat",
        "DeepSeek (China)",
    ),
    (
        "DASHSCOPE_",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen-plus",
        "Qwen (DashScope, China)",
    ),
    (
        "QWEN_",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen-plus",
        "Qwen (DashScope, China)",
    ),
    (
        "ZHIPUAI_",
        "https://open.bigmodel.cn/api/paas/v4",
        "glm-4-plus",
        "GLM (ZhipuAI, China)",
    ),
    (
        "GLM_",
        "https://open.bigmodel.cn/api/paas/v4",
        "glm-4-plus",
        "GLM (ZhipuAI, China)",
    ),
    (
        "ARK_",
        "https://ark.cn-beijing.volces.com/api/v3",
        "doubao-pro-32k",
        "Doubao (Volcano Ark, China)",
    ),
    (
        "DOUBAO_",
        "https://ark.cn-beijing.volces.com/api/v3",
        "doubao-pro-32k",
        "Doubao (Volcano Ark, China)",
    ),
    (
        "MOONSHOT_",
        "https://api.moonshot.cn/v1",
        "moonshot-v1-128k",
        "Kimi (Moonshot, China)",
    ),
    (
        "KIMI_",
        "https://api.moonshot.cn/v1",
        "moonshot-v1-128k",
        "Kimi (Moonshot, China)",
    ),
    (
        "YI_",
        "https://api.lingyiwanwu.com/v1",
        "yi-large",
        "Yi (01.AI, China)",
    ),
    (
        "LINGYIWANWU_",
        "https://api.lingyiwanwu.com/v1",
        "yi-large",
        "Yi (01.AI, China)",
    ),
]


def _resolve_cn_llm() -> tuple[str, str, str]:
    """Pick (base_url, model, source) for whichever Chinese vendor the
    user is on.  Looks at env-var prefixes — first match wins.  If no
    specific provider marker is present (e.g. user only set
    ``DEEPSEEK_API_KEY``) we still pick DeepSeek via the
    "API_KEY was set" fallback below.
    """
    for prefix, base, model, label in _CN_LLM_PROVIDERS:
        for k in os.environ:
            if k.startswith(prefix):
                # Allow the user to override the base URL or model.
                snap = _first_env(prefix.rstrip("_") + "_BASE_URL")
                if snap:
                    base = snap
                snap = _first_env(prefix.rstrip("_") + "_MODEL")
                if snap:
                    model = snap
                return (base, model, label)
    # Fallback: API_KEY was set but no specific env-var prefix
    # matched (shouldn't normally happen since _first_env() above
    # already returned truthy).  Defaults to DeepSeek.
    return ("https://api.deepseek.com/v1", "deepseek-chat", "DeepSeek (China)")


# ---------------------------------------------------------------------------
# Auto-discovered LLM endpoint
# ---------------------------------------------------------------------------


@dataclass
class EnvSnapshot:
    """Resolved LLM configuration pulled from the process environment.

    Attributes
    ----------
    api_key:
        Discovered key, or empty string.  **Never masked here** — the
        caller is responsible for masking before display.
    base_url:
        Discovered base URL, or empty string.
    model:
        Discovered model, or empty string.
    source:
        Human-readable description of where the values came from,
        e.g. ``"Claude Desktop"`` or ``"FULLADDMAX_API_KEY"`` or
        ``""`` when nothing was found.
    host_id:
        When :func:`detect_host_ai` matched, the short id (e.g.
        ``"claude"``); otherwise empty.
    host_label:
        When :func:`detect_host_ai` matched, the pretty name; otherwise
        empty.
    """

    api_key: str = ""
    base_url: str = ""
    model: str = ""
    source: str = ""
    host_id: str = ""
    host_label: str = ""
    extra: dict[str, str] = field(default_factory=dict)


def _first_env(*names: str) -> str:
    for n in names:
        v = os.getenv(n, "")
        if v:
            return v
    return ""


def detect_llm_env() -> EnvSnapshot:
    """Scan the process environment for an LLM endpoint.

    Resolution order (first non-empty wins):

    1. **FULLADDMAX_*** explicit override — the user has hand-picked
       the endpoint in the MCP config, so it beats any host-injected
       creds.
    2. **OPENAI_*** fallbacks (``OPENAI_API_KEY`` / ``OPENAI_BASE_URL``).
    3. **Host-injected** credentials — Claude Desktop / Cursor / Codex
       / Continue.dev / GitHub Copilot / Cline / Aider / Zed leak
       their own creds into the spawned MCP server; we transparently
       reuse them so the user doesn't have to paste a key twice.
    4. **Local LLM servers** (Ollama / vLLM / LM Studio) — useful for
       offline development.

    The returned snapshot is *additive* — it only ever fills in
    fields that the user / host has actually provided.  The caller
    must combine it with an existing :class:`LLMConfig` (i.e. treat
    empty fields as "keep what was there").
    """
    snap = EnvSnapshot()
    host_id, host_label = detect_host_ai()
    snap.host_id, snap.host_label = host_id, host_label

    # 1. Explicit FULLADDMAX_* override (wins over host)
    explicit_key = os.getenv("FULLADDMAX_API_KEY")
    if explicit_key:
        snap.api_key = explicit_key
        snap.base_url = _first_env(
            "FULLADDMAX_BASE_URL", "OPENAI_BASE_URL", "OPENAI_API_BASE",
        ) or DEFAULT_BASE_URL
        snap.model = _first_env("FULLADDMAX_MODEL", "OPENAI_MODEL") or DEFAULT_MODEL
        snap.source = "FULLADDMAX_API_KEY"
        return snap

    # 2. OPENAI_* fallback
    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        snap.api_key = openai_key
        snap.base_url = _first_env(
            "OPENAI_BASE_URL", "OPENAI_API_BASE", "FULLADDMAX_BASE_URL",
        ) or DEFAULT_BASE_URL
        snap.model = _first_env("OPENAI_MODEL", "FULLADDMAX_MODEL") or DEFAULT_MODEL
        snap.source = "OPENAI_API_KEY"
        return snap

    # 3. Host-injected credentials
    if host_label:
        if host_id == "claude":
            snap.api_key = _first_env(
                "ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "CLAUDE_CODE_API_KEY",
            )
            snap.base_url = _first_env(
                "ANTHROPIC_BASE_URL", "ANTHROPIC_API_BASE",
            ) or "https://api.anthropic.com/v1"
            snap.model = _first_env("ANTHROPIC_MODEL", "CLAUDE_MODEL") or "claude-sonnet-4-5"
            snap.source = host_label
            return snap
        if host_id in ("cursor", "codex", "continue", "copilot", "cline",
                       "aider", "zed"):
            # These hosts usually proxy through OpenAI-compat creds.
            snap.api_key = _first_env(
                "CURSOR_API_KEY", "CODEX_API_KEY", "CONTINUE_API_KEY",
                "COPILOT_API_KEY", "AIDER_API_KEY", "ZED_API_KEY",
            )
            snap.base_url = DEFAULT_BASE_URL
            snap.model = DEFAULT_MODEL
            snap.source = host_label
            return snap

    # 4. Chinese cloud LLM providers (all OpenAI-compatible)
    #    Order matters: more-specific env-var prefixes first; for
    #    each provider we accept both the official vendor prefix
    #    (DEEPSEEK_*, DASHSCOPE_*, ...) and a friendly alias
    #    (QWEN_*, GLM_*, KIMI_*, DOUBAO_*, YI_*) so users can pick
    #    the convention they prefer.
    cn = _first_env(
        "DEEPSEEK_API_KEY", "QWEN_API_KEY", "DASHSCOPE_API_KEY",
        "GLM_API_KEY", "ZHIPUAI_API_KEY",
        "DOUBAO_API_KEY", "ARK_API_KEY",
        "KIMI_API_KEY", "MOONSHOT_API_KEY",
        "YI_API_KEY", "LINGYIWANWU_API_KEY",
    )
    if cn:
        snap.api_key = cn
        snap.base_url, snap.model, snap.source = _resolve_cn_llm()
        return snap

    # 5. Local LLM servers
    ollama = _first_env("OLLAMA_HOST")
    if ollama:
        snap.base_url = (ollama.rstrip("/") + "/v1") if not ollama.endswith("/v1") else ollama
        snap.model = _first_env("OLLAMA_MODEL", "OPENAI_MODEL") or "llama3.1"
        snap.source = "Ollama (local)"
        return snap
    vllm = _first_env("VLLM_HOST")
    if vllm:
        snap.base_url = vllm.rstrip("/") + "/v1"
        snap.model = _first_env("VLLM_MODEL", "OPENAI_MODEL") or "meta-llama/Llama-3-8B-Instruct"
        snap.source = "vLLM (local)"
        return snap
    lms = _first_env("LMSTUDIO_HOST")
    if lms:
        snap.base_url = lms.rstrip("/") + "/v1"
        snap.model = _first_env("LMSTUDIO_MODEL", "OPENAI_MODEL") or "qwen2.5-7b-instruct"
        snap.source = "LM Studio (local)"
        return snap

    # 5. Nothing found
    return snap


# Public surface
__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "EnvSnapshot",
    "detect_host_ai",
    "detect_llm_env",
]
