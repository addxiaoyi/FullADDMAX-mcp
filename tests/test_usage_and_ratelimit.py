"""Tests for the token-usage tracking and rate-limiting systems."""

from __future__ import annotations

import json
import time

import pytest

from fulladdmax_mcp import rate_limit, usage
from fulladdmax_mcp.errors import RateLimitError
from fulladdmax_mcp.rate_limit import (
    RateLimitConfig,
    RateLimiter,
    TokenBucket,
    _parse_rpm_tpm,
    configure,
    configure_from_string,
    get_limiter,
    reset,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singletons():
    """Force a clean store + limiter for every test."""
    saved_usage = usage._store
    saved_limiter = rate_limit._limiter
    usage._store = None
    rate_limit._limiter = None
    yield
    if usage._store is not saved_usage and usage._store is not None:
        try:
            usage._store.close()
        except Exception:
            pass
    usage._store = saved_usage
    rate_limit._limiter = saved_limiter


# ---------------------------------------------------------------------------
# Model pricing
# ---------------------------------------------------------------------------


def test_get_pricing_known_models():
    p = usage.get_pricing("gpt-4o")
    assert p is not None
    assert p.prompt_per_million == 2.50


def test_get_pricing_case_insensitive():
    p = usage.get_pricing("GPT-4O")
    assert p is not None
    assert p.prompt_per_million == 2.50


def test_get_pricing_with_dated_suffix():
    p = usage.get_pricing("gpt-4o-2024-05-13")
    assert p is not None
    assert p.prompt_per_million == 2.50


def test_get_pricing_unknown_model_returns_none():
    assert usage.get_pricing("totally-made-up-model-9000") is None


def test_estimate_cost_known_model():
    cost = usage.estimate_cost("gpt-4o", 1_000_000, 1_000_000)
    assert abs(cost - (2.50 + 10.00)) < 0.001


def test_estimate_cost_unknown_returns_zero():
    assert usage.estimate_cost("zzz", 1000, 1000) == 0.0


def test_estimate_cost_partial():
    cost = usage.estimate_cost("gpt-4o-mini", 500_000, 100_000)
    expected = 500_000 * 0.15 / 1e6 + 100_000 * 0.60 / 1e6
    assert abs(cost - expected) < 1e-6


# ---------------------------------------------------------------------------
# UsageStore
# ---------------------------------------------------------------------------


def test_memory_store_record_and_list():
    s = usage.MemoryUsageStore()
    rec = s.record_call(
        model="gpt-4o", prompt_tokens=100, completion_tokens=50,
        session_id="s1",
    )
    assert rec.total_tokens == 150
    assert rec.cost_usd > 0
    listed = s.list()
    assert len(listed) == 1
    assert listed[0].session_id == "s1"
    assert listed[0].total_tokens == 150


def test_memory_store_filter_by_session():
    s = usage.MemoryUsageStore()
    s.record_call(model="gpt-4o", prompt_tokens=10, completion_tokens=10, session_id="a")
    s.record_call(model="gpt-4o", prompt_tokens=20, completion_tokens=20, session_id="b")
    out = s.list(session_id="a")
    assert len(out) == 1
    assert out[0].total_tokens == 20


def test_memory_store_filter_by_model():
    s = usage.MemoryUsageStore()
    s.record_call(model="gpt-4o", prompt_tokens=10, completion_tokens=10, session_id="x")
    s.record_call(model="gpt-3.5-turbo", prompt_tokens=10, completion_tokens=10, session_id="x")
    out = s.list(model="gpt-3.5-turbo")
    assert len(out) == 1
    assert out[0].model == "gpt-3.5-turbo"


def test_memory_store_clear():
    s = usage.MemoryUsageStore()
    s.record_call(model="gpt-4o", prompt_tokens=10, completion_tokens=10, session_id="x")
    s.clear()
    assert s.list() == []


def test_sqlite_store_round_trip(tmp_path):
    path = tmp_path / "u.db"
    s1 = usage.SqliteUsageStore(path)
    s1.record_call(model="gpt-4o", prompt_tokens=100, completion_tokens=50, session_id="s1")
    s1.record_call(model="gpt-3.5-turbo", prompt_tokens=10, completion_tokens=10, session_id="s2")
    s1.close()
    s2 = usage.SqliteUsageStore(path)
    listed = s2.list(limit=10)
    assert len(listed) == 2
    # newest first
    assert listed[0].session_id == "s2"
    s2.close()


def test_summary_groups_by_model_and_session():
    s = usage.MemoryUsageStore()
    s.record_call(model="gpt-4o", prompt_tokens=10, completion_tokens=10, session_id="a")
    s.record_call(model="gpt-4o", prompt_tokens=20, completion_tokens=20, session_id="a")
    s.record_call(model="gpt-3.5-turbo", prompt_tokens=5, completion_tokens=5, session_id="b")
    summary = s.summary()
    assert summary.records == 3
    assert summary.prompt_tokens == 35
    assert summary.completion_tokens == 35
    assert summary.total_tokens == 70
    assert "gpt-4o" in summary.by_model
    assert "gpt-3.5-turbo" in summary.by_model
    assert summary.by_model["gpt-4o"].records == 2
    assert summary.by_session["a"].total_tokens == 60


def test_module_level_store_singleton():
    a = usage.store()
    b = usage.store()
    assert a is b


def test_configure_store_swaps_and_closes(tmp_path):
    path = tmp_path / "u.db"
    s1 = usage.SqliteUsageStore(path)
    s1.record_call(model="gpt-4o", prompt_tokens=1, completion_tokens=1, session_id="x")
    returned = usage.configure_store(s1)
    # First swap: nothing was active, returned is None.
    assert returned is None
    assert usage.store() is s1
    s2 = usage.MemoryUsageStore()
    returned2 = usage.configure_store(s2)
    # Second swap: the previous store (s1) was closed and is returned.
    assert usage.store() is s2
    assert returned2 is s1
    s2.close()


def test_set_pricing_override():
    s = usage.MemoryUsageStore()
    s.set_pricing("my-custom-model", usage.ModelPricing("my-custom-model", 1.0, 2.0))
    p = usage.get_pricing("my-custom-model")
    assert p is not None
    assert p.prompt_per_million == 1.0


# ---------------------------------------------------------------------------
# TokenBucket
# ---------------------------------------------------------------------------


def test_bucket_starts_full():
    b = TokenBucket(capacity=10, refill_per_second=1)
    assert b.available() == 10


def test_bucket_consume():
    b = TokenBucket(capacity=10, refill_per_second=1)
    ok, wait = b.try_consume(3)
    assert ok
    assert wait == 0
    assert b.available() == pytest.approx(7, abs=0.1)


def test_bucket_rejects_when_insufficient():
    b = TokenBucket(capacity=2, refill_per_second=0.5)
    ok, _ = b.try_consume(2)
    assert ok
    ok, wait = b.try_consume(1)
    assert not ok
    assert wait > 0  # 1 token / 0.5 per s = 2s


def test_bucket_refills_over_time():
    b = TokenBucket(capacity=2, refill_per_second=1000)
    b.try_consume(2)
    time.sleep(0.05)
    # refilled ~50 tokens, but capacity cap = 2
    assert b.available() == pytest.approx(2, abs=0.5)


def test_bucket_caps_at_capacity():
    b = TokenBucket(capacity=5, refill_per_second=1000)
    time.sleep(0.5)
    assert b.available() <= 5


def test_bucket_validates_args():
    with pytest.raises(ValueError):
        TokenBucket(capacity=0, refill_per_second=1)
    with pytest.raises(ValueError):
        TokenBucket(capacity=1, refill_per_second=0)


def test_bucket_to_dict():
    b = TokenBucket(capacity=5, refill_per_second=1)
    d = b.to_dict()
    assert d["capacity"] == 5
    assert d["refill_per_second"] == 1


# ---------------------------------------------------------------------------
# RateLimiter
# ---------------------------------------------------------------------------


def test_unlimited_does_not_raise():
    rl = RateLimiter(RateLimitConfig())
    for _ in range(1000):
        rl.acquire(estimated_tokens=10_000)


def test_global_rpm_caps_requests():
    rl = RateLimiter(RateLimitConfig(global_rpm=60))  # burst = 6
    for _ in range(6):
        rl.acquire(estimated_tokens=1)
    with pytest.raises(RateLimitError) as exc:
        rl.acquire(estimated_tokens=1)
    assert exc.value.scope == "global_rpm"


def test_global_tpm_caps_tokens():
    rl = RateLimiter(RateLimitConfig(global_tpm=1200))  # burst = 120
    rl.acquire(estimated_tokens=100)
    rl.acquire(estimated_tokens=20)
    with pytest.raises(RateLimitError) as exc:
        rl.acquire(estimated_tokens=50)  # would exceed burst
    assert exc.value.scope == "global_tpm"


def test_per_session_rpm_isolates_sessions():
    rl = RateLimiter(RateLimitConfig(per_session_rpm=60))  # burst = 6
    for _ in range(6):
        rl.acquire(session_id="alice", estimated_tokens=1)
    # alice exhausted
    with pytest.raises(RateLimitError) as exc:
        rl.acquire(session_id="alice", estimated_tokens=1)
    assert exc.value.scope == "per_session_rpm"
    # bob still has full budget
    rl.acquire(session_id="bob", estimated_tokens=1)


def test_per_session_tpm_isolates_sessions():
    rl = RateLimiter(RateLimitConfig(per_session_tpm=1200))
    rl.acquire(session_id="alice", estimated_tokens=120)
    with pytest.raises(RateLimitError) as exc:
        rl.acquire(session_id="alice", estimated_tokens=50)
    assert exc.value.scope == "per_session_tpm"
    # bob OK
    rl.acquire(session_id="bob", estimated_tokens=120)


def test_global_and_per_session_together():
    rl = RateLimiter(
        RateLimitConfig(global_rpm=600, per_session_rpm=60)  # global burst 60, per 6
    )
    # exhaust alice's per-session
    for _ in range(6):
        rl.acquire(session_id="alice", estimated_tokens=1)
    with pytest.raises(RateLimitError) as exc:
        rl.acquire(session_id="alice", estimated_tokens=1)
    assert exc.value.scope == "per_session_rpm"


def test_rate_limit_error_has_retry_after():
    rl = RateLimiter(RateLimitConfig(global_rpm=60))  # burst 6
    for _ in range(6):
        rl.acquire(estimated_tokens=1)
    with pytest.raises(RateLimitError) as exc:
        rl.acquire(estimated_tokens=1)
    assert exc.value.retry_after > 0


def test_evict_idle_sessions():
    rl = RateLimiter(RateLimitConfig(per_session_rpm=60))
    rl._session_max_age = 0.01  # shorten for the test
    rl.acquire(session_id="s1", estimated_tokens=1)
    time.sleep(0.05)
    evicted = rl.evict_idle_sessions()
    assert evicted == 1


def test_snapshot_is_json_serialisable():
    rl = RateLimiter(RateLimitConfig(global_rpm=60, per_session_rpm=30))
    rl.acquire(session_id="s1", estimated_tokens=1)
    snap = rl.snapshot()
    json.dumps(snap)  # must not raise


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def test_configure_replaces_limiter():
    a = configure(global_rpm=10)
    b = get_limiter()
    assert a is b
    c = configure(global_rpm=20)
    # configure returns the *new* limiter (a replacement), not the
    # old one. The module-level get_limiter() now points to the new one.
    assert c is get_limiter()
    assert c.config.global_rpm == 20
    assert a.config.global_rpm == 10  # the old one is still valid


def test_reset_returns_to_unlimited():
    configure(global_rpm=10)
    reset()
    assert get_limiter().config.global_rpm == 0


def test_configure_from_string_full():
    rl = configure_from_string("global=60r/120k|session=10r/20k|est=2048")
    assert rl.config.global_rpm == 60
    assert rl.config.global_tpm == 120_000
    assert rl.config.per_session_rpm == 10
    assert rl.config.per_session_tpm == 20_000
    assert rl.config.default_estimated_tokens == 2048


def test_configure_from_string_minimal():
    rl = configure_from_string("global=100")
    assert rl.config.global_rpm == 100
    assert rl.config.global_tpm == 0


def test_configure_from_string_rejects_unknown_key():
    with pytest.raises(ValueError, match="unknown rate-limit key"):
        configure_from_string("foo=1")


def test_configure_from_string_rejects_bad_value():
    with pytest.raises(ValueError, match="bad rate-limit value"):
        configure_from_string("global=NaNr")


def test_configure_from_string_comma_separator():
    rl = configure_from_string("global=60r/120k,session=10r/20k")
    assert rl.config.global_rpm == 60
    assert rl.config.per_session_tpm == 20_000


# ---------------------------------------------------------------------------
# _parse_rpm_tpm unit tests
# ---------------------------------------------------------------------------


def test_parse_rpm_tpm_empty():
    assert _parse_rpm_tpm("") == (0, 0)


def test_parse_rpm_tpm_simple():
    assert _parse_rpm_tpm("60/120") == (60, 120)


def test_parse_rpm_tpm_with_suffix():
    assert _parse_rpm_tpm("60r/120t") == (60, 120)
    assert _parse_rpm_tpm("60req/120tok") == (60, 120)


def test_parse_rpm_tpm_with_k_multiplier():
    assert _parse_rpm_tpm("60k") == (60_000, 0)
    assert _parse_rpm_tpm("1.5m") == (1_500_000, 0)


# ---------------------------------------------------------------------------
# llm.py integration: rate limit fires BEFORE the HTTP call
# ---------------------------------------------------------------------------


async def test_rate_limit_blocks_llm_call(mock_chat, make_response):
    """When the rate limit is hit, chat() must raise RateLimitError
    and the HTTP layer must NOT be called."""
    from fulladdmax_mcp import llm

    # 6 calls fit in the burst; the 7th hits the limit. All are mocked.
    mock_chat.post("/v1/chat/completions").mock(
        return_value=make_response("ok")
    )
    configure(global_rpm=60)  # burst = 6
    for _ in range(6):
        out = await llm.get_client().chat([{"role": "user", "content": "x"}])
        assert out == "ok"
    # 7th call must fail BEFORE the HTTP request is sent (no mock is
    # necessary to observe this, but respx will assert it is NOT
    # called because the limiter raises first).
    with pytest.raises(RateLimitError):
        await llm.get_client().chat([{"role": "user", "content": "x"}])


async def test_rate_limit_blocks_per_session_only(mock_chat, make_response):
    """Per-session limit only blocks that session."""
    from fulladdmax_mcp import context as ctx_mod
    from fulladdmax_mcp import llm

    mock_chat.post("/v1/chat/completions").mock(
        return_value=make_response("ok")
    )
    configure(per_session_rpm=60)  # burst = 6
    ctx_mod.bind("alice")
    for _ in range(6):
        await llm.get_client().chat([{"role": "user", "content": "x"}])
    with pytest.raises(RateLimitError) as exc:
        await llm.get_client().chat([{"role": "user", "content": "x"}])
    assert exc.value.scope == "per_session_rpm"
    # bob still OK
    ctx_mod.bind("bob")
    out = await llm.get_client().chat([{"role": "user", "content": "x"}])
    assert out == "ok"


async def test_usage_recorded_on_successful_call(mock_chat, make_response_raw):
    """A successful chat completion records a UsageRecord with the
    real prompt/completion token counts from the response."""
    from fulladdmax_mcp import llm

    # The conftest base is https://mock.local with model "mock-1".
    # Register a price for "mock-1" so we can assert cost > 0.
    usage.store().set_pricing(
        "mock-1", usage.ModelPricing("mock-1", 1.0, 2.0)
    )
    body = {
        "id": "x", "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 42, "completion_tokens": 7, "total_tokens": 49},
    }
    mock_chat.post("/v1/chat/completions").mock(return_value=make_response_raw(body))

    await llm.get_client().chat([{"role": "user", "content": "x"}])
    summary = usage.store().summary()
    assert summary.records == 1
    assert summary.prompt_tokens == 42
    assert summary.completion_tokens == 7
    assert summary.total_tokens == 49
    assert summary.cost_usd > 0


async def test_no_record_when_usage_block_missing(mock_chat, make_response_raw):
    """Local LLM servers that omit ``usage`` should not crash the call."""
    from fulladdmax_mcp import llm

    body = {
        "id": "x", "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
        # no usage
    }
    mock_chat.post("/v1/chat/completions").mock(return_value=make_response_raw(body))
    out = await llm.get_client().chat([{"role": "user", "content": "x"}])
    assert out == "hi"
    # no record stored
    assert usage.store().list() == []


# ---------------------------------------------------------------------------
# (helpers below — placed at module bottom so they don't conflict with
# pytest's fixture discovery)
# ---------------------------------------------------------------------------


@pytest.fixture
def make_response_raw():
    from httpx import Response
    def _make(body: dict, status_code: int = 200) -> Response:
        return Response(status_code, json=body)
    return _make
