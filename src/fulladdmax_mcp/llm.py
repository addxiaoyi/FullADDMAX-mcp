"""Thin async OpenAI-compatible LLM client used by all workflows.

The client speaks the ``/v1/chat/completions`` protocol, so it works with
OpenAI, OpenRouter, DeepSeek, Qwen, local Ollama (with ``/v1``), vLLM,
LM Studio, etc.

Configuration is process-wide and mutable via :func:`set_config`, which is
invoked by the ``configure_llm`` MCP tool.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

from .errors import LLMError, LLMTimeoutError

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"


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

    async def chat(self, messages: list[dict[str, str]], **overrides: Any) -> str:
        """Send a chat completion request and return the assistant text.

        Retries on network errors and 5xx responses (exponential backoff).
        4xx responses are surfaced immediately as :class:`LLMError`.
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
                        return str(data["choices"][0]["message"]["content"])
                    except (KeyError, IndexError, TypeError) as e:
                        raise LLMError(
                            f"LLM returned malformed payload: {e}; body={str(data)[:300]}"
                        ) from e
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
