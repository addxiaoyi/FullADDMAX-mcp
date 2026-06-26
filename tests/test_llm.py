"""Tests for the LLM client."""

from __future__ import annotations

import httpx
import pytest

from fulladdmax_mcp.errors import LLMError, LLMTimeoutError
from fulladdmax_mcp.llm import LLMConfig, get_client, set_config


async def test_chat_success(mock_chat, make_response):
    route = mock_chat.post("/chat/completions").mock(
        return_value=make_response("hello world")
    )
    out = await get_client().chat([{"role": "user", "content": "hi"}])
    assert out == "hello world"
    assert route.call_count == 1


async def test_chat_unconfigured_raises():
    set_config(LLMConfig(api_key=""))
    with pytest.raises(LLMError, match="not configured"):
        await get_client().chat([{"role": "user", "content": "hi"}])


async def test_chat_4xx_no_retry(mock_chat, make_error):
    route = mock_chat.post("/chat/completions").mock(
        return_value=make_error(401, "bad key")
    )
    with pytest.raises(LLMError, match="HTTP 401"):
        await get_client().chat([{"role": "user", "content": "hi"}])
    assert route.call_count == 1


async def test_chat_5xx_retries_then_raises(mock_chat, make_error):
    route = mock_chat.post("/chat/completions").mock(
        return_value=make_error(503, "try later")
    )
    with pytest.raises(LLMError, match="HTTP 503"):
        await get_client().chat([{"role": "user", "content": "hi"}])
    # 1 initial + 1 retry
    assert route.call_count == 2


async def test_chat_5xx_then_success(mock_chat, make_error, make_response):
    route = mock_chat.post("/chat/completions").mock(
        side_effect=[
            make_error(502, "bad gateway"),
            make_response("recovered"),
        ]
    )
    out = await get_client().chat([{"role": "user", "content": "hi"}])
    assert out == "recovered"
    assert route.call_count == 2


async def test_chat_timeout_raises_llm_timeout(mock_chat):
    mock_chat.post("/chat/completions").mock(
        side_effect=httpx.TimeoutException("read timed out")
    )
    with pytest.raises(LLMTimeoutError):
        await get_client().chat([{"role": "user", "content": "hi"}])


async def test_chat_malformed_payload_raises(mock_chat, make_error):
    mock_chat.post("/chat/completions").mock(
        return_value=make_error(200, '{"unexpected": true}')
    )
    with pytest.raises(LLMError, match="malformed payload"):
        await get_client().chat([{"role": "user", "content": "hi"}])


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("FULLADDMAX_BASE_URL", "https://env.example/v1")
    monkeypatch.setenv("FULLADDMAX_API_KEY", "sk-env")
    monkeypatch.setenv("FULLADDMAX_MODEL", "env-model")
    cfg = LLMConfig.from_env()
    assert cfg.base_url == "https://env.example/v1"
    assert cfg.api_key == "sk-env"
    assert cfg.model == "env-model"


def test_config_masked_redacts_key():
    cfg = LLMConfig(api_key="sk-supersecret")
    masked = cfg.masked()
    assert masked["api_key"] == "sk-s****"
    assert "supersecret" not in masked["api_key"]
