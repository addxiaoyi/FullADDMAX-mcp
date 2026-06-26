"""Token-bucket rate limiting for LLM calls.

Two-level design:

* :class:`RateLimiter` wraps a **global** :class:`TokenBucket` (covers
  every call) and zero or more **per-session** buckets (auto-created
  the first time ``acquire(session_id=...)`` is called).
* :meth:`RateLimiter.acquire` consumes both buckets; if either
  reports ``insufficient``, the call is rejected with
  :class:`~fulladdmax_mcp.errors.RateLimitError` *before* the
  underlying HTTP request is sent.

Design rationale
----------------

* **Token bucket** (not leaky bucket or fixed window) — supports
  bursts up to ``capacity`` and refill at a steady rate, which is
  what real LLM providers (OpenAI / Azure / etc.) actually permit.
* **Two levels** — global cap protects the upstream provider; the
  per-session cap prevents a single session from monopolising the
  global budget.
* **No wait / no retry** — over-limit calls raise immediately. The
  caller surfaces this as ``ERROR: RateLimitError: ...`` to the
  client, which can decide whether to retry. We do not block.
* **Estimate up-front** — :meth:`acquire` takes an
  ``estimated_tokens`` argument (the requested max_tokens from the
  chat completion request). This is a conservative upper bound; the
  real usage is recorded later by :mod:`fulladdmax_mcp.usage`.

Configuration
-------------

Default limits are 0 (unlimited) so the system behaves as before
when no rate limiting is wanted. Call :func:`configure` or
:func:`configure_from_string` (e.g. from a CLI / MCP tool) to
enable.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .errors import RateLimitError

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TokenBucket
# ---------------------------------------------------------------------------


class TokenBucket:
    """Classic token bucket with continuous refill.

    Parameters
    ----------
    capacity:
        Maximum number of tokens the bucket can hold (== burst size).
    refill_per_second:
        How many tokens are added per wall-clock second.
    initial:
        Initial token count (default ``capacity`` so the bucket starts
        full and allows a burst immediately).

    Thread-safety
    -------------
    All operations are guarded by an RLock.
    """

    def __init__(
        self,
        capacity: float,
        refill_per_second: float,
        *,
        initial: float | None = None,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be > 0")
        if refill_per_second <= 0:
            raise ValueError("refill_per_second must be > 0")
        self.capacity = float(capacity)
        self.refill_per_second = float(refill_per_second)
        self.tokens = float(capacity if initial is None else initial)
        self._last_refill = time.monotonic()
        self._lock = threading.RLock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed <= 0:
            return
        self.tokens = min(
            self.capacity,
            self.tokens + elapsed * self.refill_per_second,
        )
        self._last_refill = now

    def available(self) -> float:
        """Return the current token count (refill first)."""
        with self._lock:
            self._refill()
            return self.tokens

    def try_consume(self, n: float) -> tuple[bool, float]:
        """Attempt to consume ``n`` tokens.

        Returns ``(ok, retry_after_seconds)``. If ``ok`` is False,
        ``retry_after_seconds`` is the time until the bucket has at
        least ``n`` tokens (for client-side retry hints).
        """
        if n < 0:
            raise ValueError("n must be >= 0")
        with self._lock:
            self._refill()
            if self.tokens >= n:
                self.tokens -= n
                return True, 0.0
            # how long until we have n?
            deficit = n - self.tokens
            wait = deficit / self.refill_per_second
            return False, wait

    def reset(self, *, full: bool = True) -> None:
        """Reset the bucket (debugging / testing)."""
        with self._lock:
            self.tokens = self.capacity if full else 0.0
            self._last_refill = time.monotonic()

    def to_dict(self) -> dict[str, float]:
        return {
            "capacity": self.capacity,
            "refill_per_second": self.refill_per_second,
            "available": self.available(),
        }


# ---------------------------------------------------------------------------
# RateLimiter
# ---------------------------------------------------------------------------


@dataclass
class RateLimitConfig:
    """Rate-limit configuration (all 0 = unlimited)."""

    global_rpm: int = 0          # requests per minute (global)
    global_tpm: int = 0          # tokens per minute (global)
    per_session_rpm: int = 0     # requests per minute (per session)
    per_session_tpm: int = 0     # tokens per minute (per session)
    default_estimated_tokens: int = 1024  # used when caller passes 0

    def is_enabled(self) -> bool:
        return bool(
            self.global_rpm
            or self.global_tpm
            or self.per_session_rpm
            or self.per_session_tpm
        )


class RateLimiter:
    """Process-level rate limiter. Holds a global + per-session
    TokenBuckets for both requests and tokens.
    """

    def __init__(self, config: RateLimitConfig | None = None) -> None:
        self.config = config or RateLimitConfig()
        self._global_req = self._build_rpm(self.config.global_rpm)
        self._global_tok = self._build_tpm(self.config.global_tpm)
        self._session_lock = threading.RLock()
        self._session_req: dict[str, TokenBucket] = {}
        self._session_tok: dict[str, TokenBucket] = {}
        self._session_ttl: dict[str, float] = {}
        self._session_max_age = 3600.0  # evict after 1h of disuse

    # ---- construction helpers -----------------------------------------

    @staticmethod
    def _build_rpm(rpm: int) -> TokenBucket | None:
        if rpm <= 0:
            return None
        # RPM → tokens/second = rpm / 60; allow burst = rpm/10 (min 1)
        refill = rpm / 60.0
        burst = max(1.0, rpm / 10.0)
        return TokenBucket(capacity=burst, refill_per_second=refill)

    @staticmethod
    def _build_tpm(tpm: int) -> TokenBucket | None:
        if tpm <= 0:
            return None
        refill = tpm / 60.0
        burst = max(1.0, tpm / 10.0)
        return TokenBucket(capacity=burst, refill_per_second=refill)

    def _get_session_buckets(self, session_id: str) -> tuple[TokenBucket | None, TokenBucket | None]:
        with self._session_lock:
            req = self._session_req.get(session_id)
            tok = self._session_tok.get(session_id)
            if req is None and self.config.per_session_rpm > 0:
                req = self._build_rpm(self.config.per_session_rpm)
                self._session_req[session_id] = req
            if tok is None and self.config.per_session_tpm > 0:
                tok = self._build_tpm(self.config.per_session_tpm)
                self._session_tok[session_id] = tok
            self._session_ttl[session_id] = time.monotonic()
            return req, tok

    def evict_idle_sessions(self) -> int:
        """Drop per-session buckets that have not been touched in
        ``_session_max_age`` seconds. Returns the count evicted.
        """
        now = time.monotonic()
        with self._session_lock:
            stale = [
                sid
                for sid, last in self._session_ttl.items()
                if now - last > self._session_max_age
            ]
            for sid in stale:
                self._session_req.pop(sid, None)
                self._session_tok.pop(sid, None)
                self._session_ttl.pop(sid, None)
        return len(stale)

    # ---- public API ----------------------------------------------------

    def acquire(
        self,
        *,
        session_id: str = "default",
        estimated_tokens: int = 0,
    ) -> None:
        """Reserve one request + ``estimated_tokens`` tokens.

        Raises :class:`RateLimitError` if either the global or
        per-session budget is exhausted.
        """
        if not self.config.is_enabled():
            return
        est = max(1, estimated_tokens or self.config.default_estimated_tokens)

        # Request bucket (cost = 1)
        if self._global_req is not None:
            ok, wait = self._global_req.try_consume(1)
            if not ok:
                raise RateLimitError(
                    f"global RPM limit {self.config.global_rpm} reached",
                    retry_after=wait,
                    scope="global_rpm",
                )
        if self.config.per_session_rpm > 0:
            req, _ = self._get_session_buckets(session_id)
            if req is not None:
                ok, wait = req.try_consume(1)
                if not ok:
                    raise RateLimitError(
                        f"per-session RPM limit {self.config.per_session_rpm} "
                        f"reached for session {session_id!r}",
                        retry_after=wait,
                        scope="per_session_rpm",
                    )

        # Token bucket
        if self._global_tok is not None:
            ok, wait = self._global_tok.try_consume(est)
            if not ok:
                # Refund the request budget so the next acquire() isn't
                # penalised for a request we ultimately rejected.
                if self._global_req is not None:
                    self._global_req.tokens = min(
                        self._global_req.capacity, self._global_req.tokens + 1
                    )
                raise RateLimitError(
                    f"global TPM limit {self.config.global_tpm} reached "
                    f"(needed {est} tokens)",
                    retry_after=wait,
                    scope="global_tpm",
                )
        if self.config.per_session_tpm > 0:
            _, tok = self._get_session_buckets(session_id)
            if tok is not None:
                ok, wait = tok.try_consume(est)
                if not ok:
                    if self._global_tok is not None:
                        self._global_tok.tokens = min(
                            self._global_tok.capacity,
                            self._global_tok.tokens + est,
                        )
                    raise RateLimitError(
                        f"per-session TPM limit {self.config.per_session_tpm} "
                        f"reached for session {session_id!r} "
                        f"(needed {est} tokens)",
                        retry_after=wait,
                        scope="per_session_tpm",
                    )

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serialisable summary of the current state."""
        out: dict[str, Any] = {
            "config": {
                "global_rpm": self.config.global_rpm,
                "global_tpm": self.config.global_tpm,
                "per_session_rpm": self.config.per_session_rpm,
                "per_session_tpm": self.config.per_session_tpm,
                "default_estimated_tokens": self.config.default_estimated_tokens,
                "enabled": self.config.is_enabled(),
            },
            "global": {
                "req": self._global_req.to_dict() if self._global_req else None,
                "tok": self._global_tok.to_dict() if self._global_tok else None,
            },
            "per_session_count": len(self._session_req) + len(self._session_tok),
        }
        with self._session_lock:
            out["per_sessions"] = {
                sid: {
                    "req": (self._session_req.get(sid).to_dict()
                            if sid in self._session_req else None),
                    "tok": (self._session_tok.get(sid).to_dict()
                            if sid in self._session_tok else None),
                }
                for sid in set(self._session_req) | set(self._session_tok)
            }
        return out


# ---------------------------------------------------------------------------
# Module-level singleton + helpers
# ---------------------------------------------------------------------------


_limiter: RateLimiter | None = None


def get_limiter() -> RateLimiter:
    """Return the process-level :class:`RateLimiter`. Initialised on
    first call with default (unlimited) config.
    """
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter()
    return _limiter


def configure(
    global_rpm: int = 0,
    global_tpm: int = 0,
    per_session_rpm: int = 0,
    per_session_tpm: int = 0,
    default_estimated_tokens: int = 1024,
) -> RateLimiter:
    """Replace the module-level limiter with a new one."""
    global _limiter
    _limiter = RateLimiter(
        RateLimitConfig(
            global_rpm=global_rpm,
            global_tpm=global_tpm,
            per_session_rpm=per_session_rpm,
            per_session_tpm=per_session_tpm,
            default_estimated_tokens=default_estimated_tokens,
        )
    )
    log.info(
        "rate limit configured: global_rpm=%d global_tpm=%d "
        "per_session_rpm=%d per_session_tpm=%d",
        global_rpm, global_tpm, per_session_rpm, per_session_tpm,
    )
    return _limiter


def reset() -> None:
    """Reset to the default (unlimited) limiter."""
    global _limiter
    _limiter = RateLimiter()
    log.info("rate limit reset to default (unlimited)")


def configure_from_string(spec: str) -> RateLimiter:
    """Configure from a compact string, e.g.
    ``"global=60r/120k|session=10r/20k|est=2048"``.

    Format: ``key=value,key=value,...`` or pipe-separated
    ``key1=val1|key2=val2``. Recognised keys: ``global`` (rpm/tpm
    pair as ``Nr/Nk``), ``session`` (same shape), ``est`` (single
    integer for default estimated tokens). ``r`` and ``req`` both
    mean requests; ``t`` and ``tok`` both mean tokens.
    """
    spec = spec.replace(",", "|")
    cfg = RateLimitConfig()
    for tok in spec.split("|"):
        tok = tok.strip()
        if not tok:
            continue
        if "=" not in tok:
            raise ValueError(f"bad rate-limit token: {tok!r}")
        key, _, val = tok.partition("=")
        key = key.strip().lower()
        val = val.strip()
        if key in ("global", "session"):
            r, t = _parse_rpm_tpm(val)
            if key == "global":
                cfg.global_rpm = r
                cfg.global_tpm = t
            else:
                cfg.per_session_rpm = r
                cfg.per_session_tpm = t
        elif key in ("est", "default_est", "default_estimated"):
            cfg.default_estimated_tokens = int(val)
        else:
            raise ValueError(f"unknown rate-limit key: {key!r}")
    return configure(
        global_rpm=cfg.global_rpm,
        global_tpm=cfg.global_tpm,
        per_session_rpm=cfg.per_session_rpm,
        per_session_tpm=cfg.per_session_tpm,
        default_estimated_tokens=cfg.default_estimated_tokens,
    )


def _parse_rpm_tpm(val: str) -> tuple[int, int]:
    """Parse ``"60r/120k"`` -> ``(60, 120_000)``.

    Accepted grammar (per segment):

    * Optional ``r`` / ``req`` / ``t`` / ``tok`` suffix (case-insensitive).
    * Optional ``k`` / ``m`` multiplier suffix (``60k`` = 60,000).

    Resolution rules (in priority order):

    1. If a segment has an explicit ``r`` / ``req`` -> requests;
       ``t`` / ``tok`` -> tokens.
    2. Otherwise the **position** decides: first segment is requests,
       second is tokens.
    3. If only one segment is given, the missing side defaults to 0.

    Returns ``(0, 0)`` on empty input.
    """
    val = val.strip()
    if not val:
        return 0, 0
    parts = [p.strip() for p in val.split("/") if p.strip()]
    parsed: list[tuple[int, str]] = []
    for p in parts:
        raw = p.lower()
        kind = ""  # "" | "r" | "t"
        if raw.endswith("req"):
            kind, raw = "r", raw[:-3]
        elif raw.endswith("tok"):
            kind, raw = "t", raw[:-3]
        elif raw.endswith("r"):
            kind, raw = "r", raw[:-1]
        elif raw.endswith("t"):
            kind, raw = "t", raw[:-1]
        mult = 1
        if raw.endswith("k"):
            mult, raw = 1_000, raw[:-1]
        elif raw.endswith("m"):
            mult, raw = 1_000_000, raw[:-1]
        try:
            n = int(float(raw) * mult)
        except ValueError as e:
            raise ValueError(f"bad rate-limit value: {p!r}") from e
        parsed.append((n, kind))

    # Resolve: any explicit kind wins, position fills the rest
    rpm = 0
    tpm = 0
    for i, (n, kind) in enumerate(parsed[:2]):
        if kind == "r":
            rpm = n
        elif kind == "t":
            tpm = n
        elif kind == "" and i == 0 and rpm == 0:
            rpm = n
        elif kind == "" and i == 1 and tpm == 0:
            tpm = n
    return rpm, tpm


__all__ = [
    "TokenBucket",
    "RateLimitConfig",
    "RateLimiter",
    "get_limiter",
    "configure",
    "reset",
    "configure_from_string",
]
