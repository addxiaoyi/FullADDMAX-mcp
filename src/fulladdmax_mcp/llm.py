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
from typing import Any, Awaitable, Callable

import httpx

from .errors import LLMError, LLMTimeoutError

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"

# Type alias for an OpenAI-compatible tool schema:
#   {"type": "function", "function": {"name": ..., "description": ..., "parameters": {...}}}
ToolSpec = dict[str, Any]

# Returned by :meth:`LLMClient.chat_with_tools` when the model emits a
# non-empty ``tool_calls`` list.
ToolCall = dict[str, Any]  # {"id", "type": "function", "function": {"name", "arguments"}}


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
        """Read configuration from ``FULLADDMAX_*`` (or ``OPENAI_*``) env vars."""
        return cls(
            base_url=os.getenv("FULLADDMAX_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
            api_key=os.getenv("FULLADDMAX_API_KEY", os.getenv("OPENAI_API_KEY", "")),
            model=os.getenv("FULLADDMAX_MODEL", DEFAULT_MODEL),
            temperature=float(os.getenv("FULLADDMAX_TEMPERATURE", "0.7")),
            max_tokens=int(os.getenv("FULLADDMAX_MAX_TOKENS", "2048")),
            timeout=float(os.getenv("FULLADDMAX_TIMEOUT", "60")),
            max_retries=int(os.getenv("FULLADDMAX_MAX_RETRIES", "2")),
        )

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
            raise LLMError(
                "LLM not configured. Call configure_llm(base_url, api_key, model) "
                "or set FULLADDMAX_API_KEY in the environment."
            )

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

        client = await self._get_client()
        last_exc: Exception | None = None

        for attempt in range(self.config.max_retries + 1):
            try:
                resp = await client.post("/chat/completions", json=payload)
            except httpx.TimeoutException as e:
                last_exc = LLMTimeoutError(f"LLM request timed out: {e}")
            except httpx.HTTPError as e:
                last_exc = LLMError(f"LLM network error: {type(e).__name__}: {e}")
            else:
                if resp.status_code < 400:
                    data = resp.json()
                    try:
                        message = data["choices"][0]["message"]
                    except (KeyError, IndexError, TypeError) as e:
                        raise LLMError(
                            f"LLM returned malformed payload: {e}; body={str(data)[:300]}"
                        ) from e
                    if not isinstance(message, dict):
                        raise LLMError(
                            f"LLM returned malformed payload: message is {type(message).__name__}"
                        )
                    return message
                # 4xx -> permanent; 5xx -> retriable
                body_preview = resp.text[:300]
                err = LLMError(f"LLM HTTP {resp.status_code}: {body_preview}")
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

    async def chat(self, messages: list[dict[str, str]], **overrides: Any) -> str:
        """Send a chat completion request and return the assistant text.

        Any ``tool_calls`` returned by the model are ignored — the text
        content is returned as-is. Use :meth:`chat_with_tools` for an
        end-to-end tool dispatch loop.
        """
        message = await self._request(messages, **overrides)
        return str(message.get("content") or "")

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
