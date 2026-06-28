"""Lightweight i18n for error messages and user-facing strings.

The server's UI text (panel labels, button names) was deliberately
kept English-only in v0.6.0 (see ``panel.py`` for why).  But
**error messages** are different — they leak straight back to the
MCP host's chat transcript, and Chinese users want them in Chinese.

This module is the dedicated place for that translation work:

* a tiny ``STRINGS`` dict (no gettext, no .po/.mo, no extra deps),
* a ``t(key, lang=None)`` lookup with fallback to English,
* a thread/task-safe ``_current_lang`` global settable via
  :func:`set_lang` or the ``FULLADDMAX_LANG`` env var.

Adding a new translation
------------------------
1. Pick a stable lowercase_snake_case key.
2. Add ``"en"`` and ``"zh-CN"`` entries to :data:`STRINGS`.
3. Use :func:`t` in your handler / module instead of a literal.

That's it.  No code generation, no language negotiation beyond
"set FULLADDMAX_LANG=zh-CN".
"""
from __future__ import annotations

import os
import threading
from typing import Any

# ---------------------------------------------------------------------------
# Translation table
# ---------------------------------------------------------------------------
#
# Conventions:
#   * keys are lowercase_snake_case
#   * en entries are the source of truth (zh-CN may be missing for
#     some keys; we fall back to en in that case)
#   * format strings use ``{name}`` for substitution, NOT %s — same
#     style as the rest of the codebase
# ---------------------------------------------------------------------------

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        # --- validation / parameter errors ---
        "bad_json":           "ERROR: bad_json: {err}",
        "bad_param":          "ERROR: bad_param: {err}",
        "missing_field":      "ERROR: bad_param: missing required field {name!r}",
        "unknown_op":         "ERROR: unknown operation {op!r}; available: {available}",
        "out_of_range":       "ERROR: {name}={value} out of range [{lo}, {hi}]",
        "type_error":         "ERROR: {name} must be {want}, got {got}",

        # --- LLM errors ---
        "llm_not_configured": "ERROR: LLM not configured. Call configure_llm() or set FULLADDMAX_API_KEY.",
        "llm_http":           "ERROR: LLM HTTP {status}: {body}",
        "llm_timeout":        "ERROR: LLM request timed out after {seconds}s",
        "llm_network":        "ERROR: LLM network error: {err}",
        "llm_malformed":      "ERROR: LLM returned malformed payload: {detail}",

        # --- tool execution errors ---
        "tool_call_failed":   "ERROR: tool {name!r} failed: {err}",
        "rate_limited":       "ERROR: rate limit exceeded for session {sid!r}; retry in {wait}s",
        "auth_failed":        "ERROR: authentication failed: {detail}",

        # --- hive / swarm specific ---
        "hive_waves_range":   "ERROR: waves must be 1..20, got {got}",
        "hive_no_ministries": "ERROR: hive_run has no ministries configured",
        "swarm_bad_agents":   "ERROR: swarm agents must be a JSON array string of 2..6 names",

        # --- generic ---
        "internal_error":     "ERROR: internal error: {err}",
        "not_implemented":    "ERROR: operation {op!r} not implemented yet",

        # --- rate limit (scopes from rate_limit.py) ---
        "rate_global_rpm":    "ERROR: global RPM limit {limit} reached",
        "rate_session_rpm":   "ERROR: per-session RPM limit {limit} reached for session {sid!r}",
        "rate_global_tpm":    "ERROR: global TPM limit {limit} reached (needed {needed} tokens)",
        "rate_session_tpm":   "ERROR: per-session TPM limit {limit} reached for session {sid!r} (needed {needed} tokens)",

        # --- LLM input / output validation ---
        "llm_planner_json":   "ERROR: planner returned non-JSON: {err}",
        "llm_planner_empty":  "ERROR: planner output did not contain a non-empty 'subtasks' list",
        "llm_planner_blank":  "ERROR: planner output contained only empty subtask strings",
        "llm_all_workers":    "ERROR: all workers failed: {detail}",
        "llm_stream_timeout": "ERROR: LLM stream timed out: {err}",
        "llm_stream_network": "ERROR: LLM stream network error: {err}",

        # --- workflow validation ---
        "wf_empty_task":      "ERROR: {op}: 'task' must be a non-empty string",
        "wf_num_workers":     "ERROR: num_workers must be between 1 and 10",
        "wf_num_concurrent":  "ERROR: max_concurrent must be between 1 and 10",
        "wf_empty_items":     "ERROR: map_reduce_run: 'items' must be a non-empty list",
        "wf_timeout":         "ERROR: {op} exceeded {seconds}s",

        # --- tool registration ---
        "tool_already":       "ERROR: tool {name!r} is already registered",
        "tool_unknown":       "ERROR: tool {name!r} is not registered",
    },
    "zh-CN": {
        # --- 校验 / 参数错误 ---
        "bad_json":           "ERROR: 参数 JSON 解析失败: {err}",
        "bad_param":          "ERROR: 参数错误: {err}",
        "missing_field":      "ERROR: 缺少必填字段 {name!r}",
        "unknown_op":         "ERROR: 未知操作 {op!r}; 可用: {available}",
        "out_of_range":       "ERROR: {name}={value} 超出范围 [{lo}, {hi}]",
        "type_error":         "ERROR: {name} 应为 {want}, 实际为 {got}",

        # --- LLM 错误 ---
        "llm_not_configured": "ERROR: LLM 未配置。请调用 configure_llm() 或设置 FULLADDMAX_API_KEY 环境变量。",
        "llm_http":           "ERROR: LLM HTTP {status}: {body}",
        "llm_timeout":        "ERROR: LLM 请求超时 ({seconds}s)",
        "llm_network":        "ERROR: LLM 网络错误: {err}",
        "llm_malformed":      "ERROR: LLM 返回格式异常: {detail}",

        # --- 工具执行错误 ---
        "tool_call_failed":   "ERROR: 工具 {name!r} 执行失败: {err}",
        "rate_limited":       "ERROR: 会话 {sid!r} 触发频率限制; 请 {wait}s 后重试",
        "auth_failed":        "ERROR: 鉴权失败: {detail}",

        # --- 蜂巢 / 蜂群 特定 ---
        "hive_waves_range":   "ERROR: waves 必须在 1..20 之间, 当前 {got}",
        "hive_no_ministries": "ERROR: hive_run 未配置任何部门",
        "swarm_bad_agents":   "ERROR: swarm agents 必须是包含 2..6 个名字的 JSON 数组字符串",

        # --- 通用 ---
        "internal_error":     "ERROR: 内部错误: {err}",
        "not_implemented":    "ERROR: 操作 {op!r} 尚未实现",

        # --- 频率限制 (rate_limit.py 作用域) ---
        "rate_global_rpm":    "ERROR: 全局 RPM 限制 {limit} 已达上限",
        "rate_session_rpm":   "ERROR: 会话 {sid!r} 的 RPM 限制 {limit} 已达上限",
        "rate_global_tpm":    "ERROR: 全局 TPM 限制 {limit} 已达上限 (本次需 {needed} tokens)",
        "rate_session_tpm":   "ERROR: 会话 {sid!r} 的 TPM 限制 {limit} 已达上限 (本次需 {needed} tokens)",

        # --- LLM 输入/输出校验 ---
        "llm_planner_json":   "ERROR: 规划器返回的不是 JSON: {err}",
        "llm_planner_empty":  "ERROR: 规划器输出不含非空 subtasks 列表",
        "llm_planner_blank":  "ERROR: 规划器输出只包含空子任务字符串",
        "llm_all_workers":    "ERROR: 所有 worker 均失败: {detail}",
        "llm_stream_timeout": "ERROR: LLM 流式响应超时: {err}",
        "llm_stream_network": "ERROR: LLM 流式网络错误: {err}",

        # --- 工作流校验 ---
        "wf_empty_task":      "ERROR: {op}: 'task' 必须是非空字符串",
        "wf_num_workers":     "ERROR: num_workers 必须在 1..10 之间",
        "wf_num_concurrent":  "ERROR: max_concurrent 必须在 1..10 之间",
        "wf_empty_items":     "ERROR: map_reduce_run: 'items' 必须是非空列表",
        "wf_timeout":         "ERROR: {op} 执行超过 {seconds}s",

        # --- 工具注册 ---
        "tool_already":       "ERROR: 工具 {name!r} 已经注册",
        "tool_unknown":       "ERROR: 工具 {name!r} 未注册",
    },
}


# ---------------------------------------------------------------------------
# Language resolution
# ---------------------------------------------------------------------------

# Languages that are present in the table (used for validation +
# documentation).  Keep in sync with STRINGS.keys().
SUPPORTED_LANGS: tuple[str, ...] = ("en", "zh-CN")

# Default language when nothing is set.  English is the
# lingua franca for tool output, and matches the rest of the
# server's user-facing text.
DEFAULT_LANG = "en"

# Env-var name.  Same naming style as the rest of the project
# (FULLADDMAX_LOG_*, FULLADDMAX_AGENT_OFFLINE, ...).
ENV_LANG = "FULLADDMAX_LANG"

# Thread-local current language.  asyncio tasks share the same value
# (we don't need per-task overrides for our use case).
_state = threading.local()


def _coerce(raw: str | None) -> str:
    """Normalise a user-supplied language tag.

    Accepts: "en", "EN", "en-US", "zh", "zh-cn", "zh_CN", "zh-CN".
    Returns one of :data:`SUPPORTED_LANGS` or :data:`DEFAULT_LANG`.
    """
    if not raw:
        return DEFAULT_LANG
    tag = raw.strip().lower().replace("_", "-")
    # zh-CN variants
    if tag in ("zh", "zh-cn", "zh-hans", "zh_cn", "zh-hans-cn"):
        return "zh-CN"
    # en variants
    if tag.startswith("en"):
        return "en"
    # Unknown -> default (English).  We don't raise; mis-config
    # shouldn't break a server that's already running.
    return DEFAULT_LANG


def get_lang() -> str:
    """Return the active language tag (``"en"`` or ``"zh-CN"``).

    Resolution order:
      1. per-thread override set via :func:`set_lang` (rare)
      2. ``FULLADDMAX_LANG`` env var
      3. :data:`DEFAULT_LANG` (``"en"``)
    """
    cur = getattr(_state, "lang", None)
    if cur:
        return cur
    return _coerce(os.environ.get(ENV_LANG))


def set_lang(lang: str | None) -> str:
    """Override the language for the current thread.

    Pass ``None`` to clear the override and fall back to the env var.
    Returns the resolved language after the change.
    """
    if lang is None:
        _state.lang = None
    else:
        _state.lang = _coerce(lang)
    return get_lang()


def t(key: str, /, **kwargs: Any) -> str:
    """Look up a translated string, format it, return the result.

    Falls back: requested lang -> English -> the key itself (so the
    user always sees *something* recognisable, never an empty string).

    Examples
    --------
    >>> t("missing_field", name="task")
    'ERROR: bad_param: missing required field 'task''
    >>> import os; os.environ["FULLADDMAX_LANG"] = "zh-CN"
    >>> t("missing_field", name="task")
    'ERROR: 缺少必填字段 'task''
    """
    lang = get_lang()
    table = STRINGS.get(lang) or STRINGS[DEFAULT_LANG]
    template = table.get(key) or STRINGS[DEFAULT_LANG].get(key) or key
    if not kwargs:
        return template
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError):
        # Mismatched kwargs in the template — return unformatted
        # rather than crash.  Caller can spot the {placeholder}s
        # still in the output and fix the i18n dict.
        return template


__all__ = [
    "DEFAULT_LANG",
    "ENV_LANG",
    "STRINGS",
    "SUPPORTED_LANGS",
    "get_lang",
    "set_lang",
    "t",
]
