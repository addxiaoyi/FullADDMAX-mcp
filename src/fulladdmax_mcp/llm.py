"""Thin async OpenAI-compatible LLM client used by all workflows.

The client speaks the ``/v1/chat/completions`` protocol, so it works with
OpenAI, OpenRouter, DeepSeek, Qwen, local Ollama (with ``/v1``), vLLM,
LM Studio, etc.

Configuration is process-wide and mutable via :func:`set_config`, which is
invoked by the ``configure_llm`` MCP tool.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable

import httpx

from . import context as ctx_mod
from . import i18n
from .env_autodetect import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    EnvSnapshot,
    detect_host_ai,
    detect_llm_env,
)
from .errors import LLMError, LLMTimeoutError
from .rate_limit import get_limiter
from .usage import UsageRecord, store as usage_store

log = logging.getLogger(__name__)

# DEFAULT_BASE_URL / DEFAULT_MODEL re-exported for backward compatibility.
__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "LLMConfig",
    "LLMClient",
    "ToolSpec",
    "ToolCall",
    "get_config",
    "get_client",
    "set_config",
    "aclose",
]

# Type alias for an OpenAI-compatible tool schema:
#   {"type": "function", "function": {"name": ..., "description": ..., "parameters": {...}}}
ToolSpec = dict[str, Any]

# Returned by :meth:`LLMClient.chat_with_tools` when the model emits a
# non-empty ``tool_calls`` list.
ToolCall = dict[str, Any]  # {"id", "type": "function", "function": {"name", "arguments"}}


def _normalize_tool_calls(raw: Any) -> list[ToolCall]:
    """Coerce a ``tool_calls`` payload from any OpenAI-compatible
    provider into the canonical wire format our dispatcher expects.

    Canonical form (OpenAI):
        {
          "id":       "call_abc123",
          "type":     "function",
          "function": {"name": "...", "arguments": "JSON string"},
        }

    Variations we see in the wild, mostly from China-cloud providers
    (GLM / 豆包 Doubao / Qwen) and from a few local LLM servers:

    1. ``arguments`` as a **dict** instead of a JSON string
       (GLM-4 sometimes does this).  We ``json.dumps`` it.
    2. ``id`` field named ``tool_call_id`` (some 豆包 versions).
    3. ``type`` field missing or named ``kind``.  We default to
       ``"function"``.
    4. ``name`` at top level instead of under ``function`` (rare).
    5. ``arguments`` empty / None / ``{}``.  We treat as ``"{}"``.

    Returns a fresh list — the input is never mutated.
    """
    if not isinstance(raw, list):
        return []
    out: list[ToolCall] = []
    for i, call in enumerate(raw):
        if not isinstance(call, dict):
            continue

        # --- 1. pull out ``function`` (or build one) ---
        fn = call.get("function")
        if not isinstance(fn, dict):
            # Some 豆包 responses put ``name`` + ``arguments`` at top level.
            fn = {}
            if "name" in call:
                fn["name"] = call["name"]
            if "arguments" in call:
                fn["arguments"] = call["arguments"]
            # ``parameters`` (豆包 alt) -> rename to ``arguments`` for
            # downstream uniformity.
            elif "parameters" in call:
                fn["arguments"] = call["parameters"]

        # --- 2. coerce ``arguments`` to a JSON string ---
        args = fn.get("arguments", "")
        if args is None or args == "":
            args = "{}"
        elif isinstance(args, (dict, list)):
            try:
                args = json.dumps(args, ensure_ascii=False)
            except (TypeError, ValueError):
                args = str(args)
        elif not isinstance(args, str):
            args = str(args)

        # --- 3. resolve ``id`` (prefer ``id``, fall back to ``tool_call_id``) ---
        call_id = call.get("id") or call.get("tool_call_id") or f"call_{i}"

        # --- 4. resolve ``type`` (default to "function") ---
        call_type = call.get("type") or call.get("kind") or "function"

        out.append(
            {
                "id": call_id,
                "type": call_type,
                "function": {
                    "name": fn.get("name", ""),
                    "arguments": args,
                },
            }
        )
    return out


@dataclass
class LLMConfig:
    """Runtime LLM configuration."""

    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""
    model: str = DEFAULT_MODEL
    temperature: float = 0.7
    max_tokens: int = 2048
    timeout: float = 60.0
    max_retries: int = 2
    # Optional list of OpenAI-compatible tool specs made available to every
    # chat completion. Mutate via :func:`set_config` or pass ``tools=`` to
    # :meth:`LLMClient.chat` / :meth:`LLMClient.chat_with_tools`.
    tools: list[ToolSpec] = field(default_factory=list)
    # "auto" (default), "none", or {"type": "function", "function": {"name": "x"}}
    tool_choice: str | dict[str, Any] = "auto"

    @classmethod
    def from_env(cls) -> "LLMConfig":
        """Read configuration from the process environment.

        Uses :func:`fulladdmax_mcp.env_autodetect.detect_llm_env` to
        resolve the endpoint, which transparently picks up host-injected
        credentials (Claude Desktop, Cursor, Codex, ...), explicit
        ``FULLADDMAX_*`` overrides, ``OPENAI_*`` fallbacks, and local
        LLM servers (Ollama, vLLM, LM Studio).  Fields not present in
        the environment fall back to the dataclass defaults.
        """
        snap: EnvSnapshot = detect_llm_env()
        return cls(
            base_url=(snap.base_url or DEFAULT_BASE_URL).rstrip("/"),
            api_key=snap.api_key,
            model=snap.model or DEFAULT_MODEL,
            temperature=float(os.getenv("FULLADDMAX_TEMPERATURE", "0.7")),
            max_tokens=int(os.getenv("FULLADDMAX_MAX_TOKENS", "2048")),
            timeout=float(os.getenv("FULLADDMAX_TIMEOUT", "60")),
            max_retries=int(os.getenv("FULLADDMAX_MAX_RETRIES", "2")),
        )

    def is_configured(self) -> bool:
        """Return True iff an ``api_key`` has been set (real or inherited)."""
        return bool(self.api_key)

    def source(self) -> str:
        """Best-effort label for *where* the current config came from.

        Used by the panel and lazy-loading error messages to show a
        friendly explanation like "inherited from Cursor" instead of
        the cryptic "api_key=(unset)".
        """
        host_id, host_label = detect_host_ai()
        if host_label and self.api_key:
            return f"inherited from {host_label}"
        if self.api_key and os.getenv("FULLADDMAX_API_KEY"):
            return "FULLADDMAX_API_KEY"
        if self.api_key and os.getenv("OPENAI_API_KEY"):
            return "OPENAI_API_KEY"
        if self.api_key:
            return "configured"
        return ""

    def masked(self) -> dict[str, Any]:
        """Return a dict suitable for logging (api_key is redacted)."""
        key = (self.api_key[:4] + "****") if self.api_key else "(unset)"
        return {
            "base_url": self.base_url,
            "api_key": key,
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout": self.timeout,
        }


class LLMClient:
    """Async OpenAI-compatible chat client with bounded retries."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.config.base_url,
                timeout=httpx.Timeout(self.config.timeout),
                headers={"Authorization": f"Bearer {self.config.api_key}"},
            )
        return self._client

    async def _request(
        self, messages: list[dict[str, Any]], **overrides: Any
    ) -> dict[str, Any]:
        """Low-level: send a single chat-completions request, return the
        raw ``message`` dict from ``choices[0]``. Raises :class:`LLMError`
        on transport / 4xx errors; retries on 5xx + network errors.
        """
        if not self.config.api_key:
            raise LLMError(i18n.t("llm_not_configured"))

        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        # Inject tool specs only when the LLMConfig has any, unless the caller
        # passed an explicit ``tools=`` override (e.g. None to disable for one
        # call, or a custom list).
        if self.config.tools and "tools" not in overrides:
            payload["tools"] = list(self.config.tools)
            payload["tool_choice"] = overrides.pop("tool_choice", self.config.tool_choice)
        payload.update(overrides)

        # Rate limit: reserve a slot + estimated tokens. Raises
        # RateLimitError before the HTTP call goes out, so an over-limit
        # client never even hits the upstream provider.
        try:
            get_limiter().acquire(
                session_id=ctx_mod.session_id(),
                estimated_tokens=int(payload.get("max_tokens") or 0),
            )
        except Exception:
            # Make sure we re-raise RateLimitError untouched; anything
            # else from the limiter is unexpected and also propagated.
            raise

        client = await self._get_client()
        last_exc: Exception | None = None

        for attempt in range(self.config.max_retries + 1):
            try:
                resp = await client.post("/chat/completions", json=payload)
            except httpx.TimeoutException as e:
                last_exc = LLMTimeoutError(i18n.t("llm_timeout", seconds=self.config.timeout))
            except httpx.HTTPError as e:
                last_exc = LLMError(
                    i18n.t("llm_network", err=f"{type(e).__name__}: {e}")
                )
            else:
                if resp.status_code < 400:
                    data = resp.json()
                    try:
                        message = data["choices"][0]["message"]
                    except (KeyError, IndexError, TypeError) as e:
                        raise LLMError(
                            i18n.t("llm_malformed", detail=f"{e}; body={str(data)[:300]}")
                        ) from e
                    if not isinstance(message, dict):
                        raise LLMError(
                            i18n.t(
                                "llm_malformed",
                                detail=f"message is {type(message).__name__}",
                            )
                        )
                    # Normalize tool_calls to OpenAI's wire format.  Most
                    # Chinese cloud providers (GLM / 豆包 / Qwen) speak
                    # the same protocol, but their actual responses have
                    # small variations: arguments sometimes a dict, the
                    # id field sometimes named ``tool_call_id``,
                    # ``type`` field sometimes missing.  See
                    # :func:`_normalize_tool_calls` for the full list.
                    if "tool_calls" in message:
                        message["tool_calls"] = _normalize_tool_calls(
                            message["tool_calls"]
                        )
                    # Record token usage. Failures here MUST NOT affect
                    # the call result — the LLM already gave us an answer.
                    self._record_usage(data, model=payload["model"])
                    return message
                # 4xx -> permanent; 5xx -> retriable
                body_preview = resp.text[:300]
                err = LLMError(i18n.t("llm_http", status=resp.status_code, body=body_preview))
                if 400 <= resp.status_code < 500:
                    raise err from None
                last_exc = err

            if attempt < self.config.max_retries:
                backoff = 2**attempt
                log.warning(
                    "LLM call failed (attempt %d/%d): %s; retrying in %ds",
                    attempt + 1,
                    self.config.max_retries + 1,
                    last_exc,
                    backoff,
                )
                await asyncio.sleep(backoff)

        assert last_exc is not None
        raise last_exc

    def _record_usage(self, response_data: dict[str, Any], *, model: str) -> None:
        """Persist token usage from an OpenAI ``usage`` block.

        Silently no-ops on missing / malformed ``usage`` (some local
        LLM servers omit it) and on any store error (we never want
        bookkeeping to fail the LLM call).
        """
        usage = response_data.get("usage")
        if not isinstance(usage, dict):
            return
        try:
            prompt = int(usage.get("prompt_tokens", 0))
            completion = int(usage.get("completion_tokens", 0))
            if prompt + completion == 0:
                return
            usage_store().record_call(
                model=model,
                prompt_tokens=prompt,
                completion_tokens=completion,
                session_id=ctx_mod.session_id(),
            )
        except Exception as e:  # noqa: BLE001
            log.debug("usage record failed (ignored): %s", e)

    async def chat(self, messages: list[dict[str, str]], **overrides: Any) -> str:
        """Send a chat completion request and return the assistant text.

        Any ``tool_calls`` returned by the model are ignored — the text
        content is returned as-is. Use :meth:`chat_with_tools` for an
        end-to-end tool dispatch loop.
        """
        message = await self._request(messages, **overrides)
        return str(message.get("content") or "")

    async def chat_stream(
        self, messages: list[dict[str, str]], **overrides: Any
    ) -> "AsyncIterator[str]":
        """Send a chat completion request and yield content chunks as
        they arrive from the LLM.

        Uses the OpenAI ``stream=true`` Server-Sent-Events protocol.
        Works with every OpenAI-compatible endpoint (OpenAI / Anthropic
        / DeepSeek / Qwen / GLM / 豆包 / Kimi / Ollama / vLLM / LM Studio).

        Yields ``str`` content fragments.  ``tool_calls`` chunks are
        ignored in stream mode (call :meth:`chat_with_tools` for the
        tool-calling loop).  Errors are raised eagerly, not streamed.

        The MCP tool surface stays synchronous — this method is for
        library users (e.g. a custom dispatcher that wants to feed
        chunks to a TUI / web UI).  The existing ``chat()`` /
        ``chat_with_tools()`` paths are unchanged.
        """
        if not self.config.api_key:
            raise LLMError(i18n.t("llm_not_configured"))

        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "stream": True,
        }
        if self.config.tools and "tools" not in overrides:
            payload["tools"] = list(self.config.tools)
            payload["tool_choice"] = overrides.pop(
                "tool_choice", self.config.tool_choice
            )
        payload.update(overrides)

        # Reserve a rate-limit slot before opening the stream.
        try:
            get_limiter().acquire(
                session_id=ctx_mod.session_id(),
                estimated_tokens=int(payload.get("max_tokens") or 0),
            )
        except Exception:
            raise

        # Force stream=True — caller cannot disable it.
        payload["stream"] = True

        client = await self._get_client()
        # We do NOT retry on stream errors: the response is mid-flight
        # and the caller has already started seeing partial output.
        # Retrying would re-charge tokens + produce duplicate text.
        try:
            async with client.stream(
                "POST", "/chat/completions", json=payload
            ) as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    raise LLMError(
                        f"LLM HTTP {resp.status_code}: {body.decode('utf-8', 'replace')[:300]}"
                    )
                # OpenAI SSE format:
                #   data: {"choices": [{"delta": {"content": "..."}}]}
                #   data: [DONE]
                # Some China-cloud providers (Qwen DashScope, GLM) emit
                # the same wire format; some (e.g. vLLM) omit the
                # leading space after `data:`.  We tolerate both.
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        # Some servers send keep-alive comments or
                        # partial lines; skip silently.
                        continue
                    try:
                        delta = (
                            chunk.get("choices", [{}])[0]
                            .get("delta", {})
                        )
                    except (KeyError, IndexError, TypeError):
                        continue
                    content = delta.get("content")
                    if content:
                        yield content
        except httpx.TimeoutException as e:
            raise LLMTimeoutError(
                i18n.t("llm_stream_timeout", err=str(e))
            ) from e
        except httpx.HTTPError as e:
            raise LLMError(
                i18n.t("llm_stream_network", err=f"{type(e).__name__}: {e}")
            ) from e

    async def chat_raw(
        self, messages: list[dict[str, Any]], **overrides: Any
    ) -> dict[str, Any]:
        """Send a chat completion request and return the full ``message``
        dict (with ``content``, ``tool_calls``, ``role``, ...).

        Useful for callers that want to inspect tool calls themselves.
        """
        return await self._request(messages, **overrides)

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        executor: "Callable[[ToolCall], Awaitable[Any]]",
        max_steps: int = 6,
        **overrides: Any,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Run a tool-calling loop.

        The LLM is invoked. If the response contains ``tool_calls``, each
        call is dispatched to ``executor`` (which must return a JSON-
        serialisable result string), the result is appended to the
        conversation as a ``role: tool`` message, and the LLM is invoked
        again. The loop terminates when:

        * the LLM returns a message without ``tool_calls`` (text answer), or
        * ``max_steps`` is reached, or
        * ``executor`` raises (the error message is fed back to the LLM).

        Returns ``(final_text, transcript)`` where ``transcript`` is a
        list of ``{"role": "assistant"|"tool", ...}`` messages for
        logging/debugging.
        """
        if max_steps < 1:
            raise ValueError("max_steps must be >= 1")

        transcript: list[dict[str, Any]] = []
        # Local copy so we can mutate the conversation safely.
        convo: list[dict[str, Any]] = list(messages)
        last_text = ""
        for step in range(max_steps):
            message = await self._request(convo, **overrides)
            transcript.append(message)
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                last_text = str(message.get("content") or "")
                break

            # Append the assistant message (with tool_calls) to the
            # conversation so the LLM sees its own call when we add the
            # tool result messages after.
            convo.append(
                {
                    "role": "assistant",
                    "content": message.get("content") or "",
                    "tool_calls": tool_calls,
                }
            )

            for call in tool_calls:
                name = call.get("function", {}).get("name", "")
                raw_args = call.get("function", {}).get("arguments", "{}")
                call_id = call.get("id", "")
                # OpenAI sends arguments as a JSON string. Parse it for the
                # executor's convenience while preserving the raw string
                # under ``_raw_arguments`` for callers that want it.
                if isinstance(raw_args, str):
                    try:
                        parsed_args = json.loads(raw_args) if raw_args.strip() else {}
                    except json.JSONDecodeError:
                        parsed_args = raw_args  # let the executor see the bad string
                else:
                    parsed_args = raw_args
                call_for_executor = {
                    **call,
                    "function": {**call.get("function", {}), "arguments": parsed_args},
                    "_raw_arguments": raw_args,
                }
                try:
                    result = await executor(call_for_executor)
                except Exception as e:  # noqa: BLE001
                    log.warning("tool %s raised: %s", name, e)
                    result = f"ERROR: {type(e).__name__}: {e}"
                # Stringify the result for the LLM.
                if not isinstance(result, str):
                    try:
                        result = json.dumps(result, ensure_ascii=False)
                    except (TypeError, ValueError):
                        result = str(result)
                convo.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": result,
                    }
                )
        else:
            log.warning("chat_with_tools reached max_steps=%d without a final answer", max_steps)

        return last_text, transcript

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None


# ---------------------------------------------------------------------------
# Module-level singleton state
# ---------------------------------------------------------------------------

_cfg: LLMConfig = LLMConfig.from_env()
_client: LLMClient = LLMClient(_cfg)


def get_config() -> LLMConfig:
    return _cfg


def get_client() -> LLMClient:
    return _client


def set_config(cfg: LLMConfig) -> None:
    """Replace the global config and reset the underlying client.

    The previous :class:`httpx.AsyncClient` is closed best-effort; the new
    client is created lazily on the next call.
    """
    global _cfg, _client
    _cfg = cfg
    old = _client
    _client = LLMClient(cfg)
    # Best-effort cleanup; we don't await here because this is a sync setter.
    if old._client is not None and not old._client.is_closed:  # type: ignore[attr-defined]
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(old.aclose())  # type: ignore[attr-defined]
            else:
                loop.run_until_complete(old.aclose())  # type: ignore[attr-defined]
        except RuntimeError:
            pass


async def aclose() -> None:
    """Close the global client (call from server shutdown if needed)."""
    await _client.aclose()
