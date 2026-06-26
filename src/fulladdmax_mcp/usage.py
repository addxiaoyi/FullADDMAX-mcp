"""Token usage tracking and cost control.

Every successful LLM call records a :class:`UsageRecord` into a
process-global :class:`UsageStore`. The store can be :class:`MemoryUsageStore`
(default) or :class:`SqliteUsageStore` (persistent; same pattern as
:mod:`context_store`).

Cost is computed from the model pricing table (:data:`MODEL_PRICING`)
which maps a model name (e.g. ``"gpt-4o"``) to a
:class:`ModelPricing` (USD per 1M prompt / completion tokens). Unknown
models fall back to ``$0`` and just record the token counts; the
client can override pricing at runtime via
:func:`configure_pricing_override` (MCP) or
:meth:`UsageStore.set_pricing`.

Usage records contain:

* :attr:`UsageRecord.session_id` — what context this LLM call belonged
  to (taken from the active :func:`context.session_id`).
* :attr:`UsageRecord.model` — the model name that was queried.
* :attr:`UsageRecord.prompt_tokens` / :attr:`completion_tokens` /
  :attr:`total_tokens` — verbatim from the OpenAI ``usage`` block.
* :attr:`UsageRecord.cost_usd` — `prompt * price_in + completion *
  price_out`.
* :attr:`UsageRecord.ts` — wall-clock time of the call.

Designed to fail safe: if the store / pricing layer raises, the
underlying LLM call's response is still returned to the caller.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .errors import UsageStoreError

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pricing table
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelPricing:
    """USD price per 1M tokens.

    Prices as of June 2026 for reference. Override at runtime with
    :func:`configure_pricing_override` (MCP) or
    :meth:`UsageStore.set_pricing`.
    """

    model: str
    prompt_per_million: float
    completion_per_million: float

    def cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (
            prompt_tokens * self.prompt_per_million
            + completion_tokens * self.completion_per_million
        ) / 1_000_000.0


MODEL_PRICING: dict[str, ModelPricing] = {
    "gpt-4o": ModelPricing("gpt-4o", 2.50, 10.00),
    "gpt-4o-mini": ModelPricing("gpt-4o-mini", 0.15, 0.60),
    "gpt-4-turbo": ModelPricing("gpt-4-turbo", 10.00, 30.00),
    "gpt-3.5-turbo": ModelPricing("gpt-3.5-turbo", 0.50, 1.50),
    "o1": ModelPricing("o1", 15.00, 60.00),
    "o1-mini": ModelPricing("o1-mini", 3.00, 12.00),
}


def get_pricing(model: str) -> ModelPricing | None:
    """Return pricing for ``model`` (case-insensitive, tolerates
    suffixes like ``gpt-4o-2024-05-13``).
    """
    if model in MODEL_PRICING:
        return MODEL_PRICING[model]
    lower = model.lower()
    for key, pricing in MODEL_PRICING.items():
        if key == lower or lower.startswith(key + "-") or lower.startswith(key + "_"):
            return pricing
    return None


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Return the USD cost for the given token counts and model.

    Returns 0.0 if the model is not in the pricing table (and logs
    a debug line).
    """
    pricing = get_pricing(model)
    if pricing is None:
        log.debug("no pricing for model %r; cost reported as 0.0", model)
        return 0.0
    return pricing.cost(prompt_tokens, completion_tokens)


# ---------------------------------------------------------------------------
# UsageRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UsageRecord:
    """A single LLM call's token usage + cost."""

    session_id: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    ts: float  # time.time()
    workflow: str = ""  # e.g. "orchestrator" / "swarm" / "llm"


@dataclass
class UsageSummary:
    """Aggregated usage over a window."""

    records: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    by_model: dict[str, "UsageSummary"] = field(default_factory=dict)
    by_session: dict[str, "UsageSummary"] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "records": self.records,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "by_model": {k: v.to_dict() for k, v in self.by_model.items()},
            "by_session": {k: v.to_dict() for k, v in self.by_session.items()},
        }


def _empty_summary() -> "UsageSummary":
    return UsageSummary(0, 0, 0, 0, 0.0)


def _add_to_summary(target: UsageSummary, rec: UsageRecord) -> None:
    target.records += 1
    target.prompt_tokens += rec.prompt_tokens
    target.completion_tokens += rec.completion_tokens
    target.total_tokens += rec.total_tokens
    target.cost_usd += rec.cost_usd


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class UsageStore(ABC):
    """Storage backend for :class:`UsageRecord`."""

    @abstractmethod
    def record(self, rec: UsageRecord) -> None: ...

    @abstractmethod
    def list(
        self,
        *,
        session_id: str | None = None,
        model: str | None = None,
        since_ts: float | None = None,
        limit: int = 1000,
    ) -> list[UsageRecord]: ...

    @abstractmethod
    def clear(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    # ---- public helpers -----------------------------------------------

    def record_call(
        self,
        *,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        session_id: str,
        workflow: str = "llm",
    ) -> UsageRecord:
        """Convenience: build a :class:`UsageRecord` (with cost) and
        store it. Returns the stored record.
        """
        total = prompt_tokens + completion_tokens
        cost = estimate_cost(model, prompt_tokens, completion_tokens)
        rec = UsageRecord(
            session_id=session_id,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total,
            cost_usd=cost,
            ts=time.time(),
            workflow=workflow,
        )
        self.record(rec)
        return rec

    def summary(
        self,
        *,
        session_id: str | None = None,
        model: str | None = None,
        since_ts: float | None = None,
    ) -> UsageSummary:
        """Aggregate over the same filter as :meth:`list`."""
        records = self.list(
            session_id=session_id, model=model, since_ts=since_ts, limit=10_000_000
        )
        return _build_summary(records)

    def set_pricing(self, model: str, pricing: ModelPricing) -> None:
        """Override or add a model price. Affects *future* records only."""
        MODEL_PRICING[pricing.model] = pricing


# ---------------------------------------------------------------------------


def _build_summary(records: Iterable[UsageRecord]) -> UsageSummary:
    out = _empty_summary()
    for rec in records:
        _add_to_summary(out, rec)
        m = out.by_model.setdefault(rec.model, _empty_summary())
        _add_to_summary(m, rec)
        s = out.by_session.setdefault(rec.session_id, _empty_summary())
        _add_to_summary(s, rec)
    return out


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------


class MemoryUsageStore(UsageStore):
    """Thread-safe in-process list. Default; no I/O."""

    def __init__(self) -> None:
        self._records: list[UsageRecord] = []
        self._lock = threading.RLock()

    def record(self, rec: UsageRecord) -> None:
        with self._lock:
            self._records.append(rec)

    def list(
        self,
        *,
        session_id: str | None = None,
        model: str | None = None,
        since_ts: float | None = None,
        limit: int = 1000,
    ) -> list[UsageRecord]:
        with self._lock:
            out = list(self._records)
        if session_id is not None:
            out = [r for r in out if r.session_id == session_id]
        if model is not None:
            out = [r for r in out if r.model == model]
        if since_ts is not None:
            out = [r for r in out if r.ts >= since_ts]
        return out[-limit:]

    def clear(self) -> None:
        with self._lock:
            self._records.clear()

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# SQLite implementation
# ---------------------------------------------------------------------------


_USAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id        TEXT NOT NULL,
    model             TEXT NOT NULL,
    prompt_tokens     INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    total_tokens      INTEGER NOT NULL,
    cost_usd          REAL NOT NULL,
    ts                REAL NOT NULL,
    workflow          TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_usage_session ON usage(session_id);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage(ts);
"""


class SqliteUsageStore(UsageStore):
    """SQLite-backed usage store. Single file, stdlib only."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self._path), check_same_thread=False, isolation_level=None
        )
        self._conn.execute("PRAGMA journal_mode = WAL")
        with self._lock:
            self._conn.executescript(_USAGE_SCHEMA)
        log.info("opened SqliteUsageStore at %s", self._path)

    def record(self, rec: UsageRecord) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO usage(session_id, model, prompt_tokens, "
                "completion_tokens, total_tokens, cost_usd, ts, workflow) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    rec.session_id,
                    rec.model,
                    rec.prompt_tokens,
                    rec.completion_tokens,
                    rec.total_tokens,
                    rec.cost_usd,
                    rec.ts,
                    rec.workflow,
                ),
            )

    def list(
        self,
        *,
        session_id: str | None = None,
        model: str | None = None,
        since_ts: float | None = None,
        limit: int = 1000,
    ) -> list[UsageRecord]:
        sql = "SELECT session_id, model, prompt_tokens, completion_tokens, total_tokens, cost_usd, ts, workflow FROM usage"
        clauses: list[str] = []
        params: list[Any] = []
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if model is not None:
            clauses.append("model = ?")
            params.append(model)
        if since_ts is not None:
            clauses.append("ts >= ?")
            params.append(since_ts)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            cur = self._conn.execute(sql, params)
            rows = cur.fetchall()
        return [
            UsageRecord(
                session_id=r[0],
                model=r[1],
                prompt_tokens=r[2],
                completion_tokens=r[3],
                total_tokens=r[4],
                cost_usd=r[5],
                ts=r[6],
                workflow=r[7] or "",
            )
            for r in rows
        ]

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM usage")

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:  # pragma: no cover
                pass


# ---------------------------------------------------------------------------
# Module-level store
# ---------------------------------------------------------------------------


_store: UsageStore | None = None


def _get_store() -> UsageStore:
    global _store
    if _store is None:
        _store = MemoryUsageStore()
        log.debug("usage: initialised default MemoryUsageStore")
    return _store


def store() -> UsageStore:
    """Return the currently configured :class:`UsageStore`."""
    return _get_store()


def configure_store(new_store: UsageStore) -> UsageStore:
    """Install a new :class:`UsageStore`. The previous one is closed."""
    global _store
    old = _store
    if old is not None:
        try:
            old.close()
        except Exception as e:  # noqa: BLE001
            log.warning("error closing previous usage store: %s", e)
    _store = new_store
    log.info("usage: configured store=%s", type(new_store).__name__)
    return old  # type: ignore[return-value]


def use_memory_store() -> UsageStore:
    return configure_store(MemoryUsageStore())


def use_sqlite_store(path: str) -> UsageStore:
    return configure_store(SqliteUsageStore(path))


__all__ = [
    "ModelPricing",
    "MODEL_PRICING",
    "get_pricing",
    "estimate_cost",
    "UsageRecord",
    "UsageSummary",
    "UsageStore",
    "MemoryUsageStore",
    "SqliteUsageStore",
    "store",
    "configure_store",
    "use_memory_store",
    "use_sqlite_store",
]
