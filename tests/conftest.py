"""Shared pytest fixtures."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

import fulladdmax_mcp.llm as llm_mod
from fulladdmax_mcp.llm import LLMConfig, set_config


@pytest.fixture(autouse=True)
def _reset_llm_config():
    """Every test gets a fresh LLM config so we never leak state."""
    set_config(
        LLMConfig(
            base_url="https://mock.local/v1",
            api_key="sk-test",
            model="mock-1",
            timeout=10.0,
            max_retries=1,
        )
    )
    yield
    set_config(LLMConfig())


@pytest.fixture
def mock_chat():
    """Return a respx router that intercepts ``/v1/chat/completions`` calls."""
    with respx.mock(base_url="https://mock.local", assert_all_called=False) as router:
        yield router


def make_chat_response(content: str, status: int = 200) -> Response:
    """Build a fake OpenAI-style chat completion response."""
    return Response(
        status,
        json={
            "id": "test",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
        },
    )


def make_chat_error(status: int, body: str = "boom") -> Response:
    return Response(status, text=body)


@pytest.fixture
def make_response():
    return make_chat_response


@pytest.fixture
def make_error():
    return make_chat_error
