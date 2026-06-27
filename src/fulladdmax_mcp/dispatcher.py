"""Unified dispatch layer for the 4 mega tools.

A mega tool (``agent`` / ``knowledge`` / ``config`` / ``admin``)
receives an ``operation`` string and a JSON-encoded ``params_json``
blob, then routes the call to the matching :class:`OperationHandler`
in its area's ``HANDLERS`` dict.

The dispatcher is intentionally tiny — it does:

1. Validate the ``operation`` (empty / unknown -> ``ERROR: bad_op: ...``)
2. Parse + validate ``params_json`` (see :mod:`fulladdmax_mcp.param_parser`)
3. Bind the top-level ``session_id`` to the per-task :class:`ContextVar`
4. Invoke the handler
5. Convert any exception to a redacted ``"ERROR: handler: ..."`` line

Secret redaction
----------------

Before any error string is returned, the dispatcher walks it and
replaces the value of any field whose key (case-insensitive) is in
:data:`SECRET_KEYS` with ``"<first-4-chars>****"`` (or ``"****"`` if
the value is too short).  This prevents ``api_key`` and friends from
leaking into MCP tool responses.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Awaitable, Callable

from . import context as ctx_mod
from .param_parser import (
    SECRET_KEYS,
    FieldSpec,
    parse_params,
)
from .param_parser_errors import (
    BadJson,
    BadParam,
    BadType,
    ParamError,
)

log = logging.getLogger(__name__)


# Type alias for an operation handler.  Handlers are async, accept
# business parameters as keyword arguments, and return a Markdown /
# JSON string for the MCP client.
OperationHandler = Callable[..., Awaitable[str]]


# Per-operation parameter schema.  Populated by each handler module
# (see :mod:`fulladdmax_mcp.handlers`).  The dispatch function does
# not look at this — the handler module is responsible for calling
# :func:`parse_params` with its own schema.
SCHEMAS: dict[str, dict[str, FieldSpec]] = {}


# Reusable pre-compiled regex for redaction.  We pattern-match
#   "key": "..."   (string values, double quotes — JSON)
#   "key": 12345    (numeric values — JSON)
#   key=value       (Python repr / str(exception) — common in tracebacks)
# Case-insensitive match on the key.
_REDACT_STRING = re.compile(
    r'"(?P<key>[A-Za-z_][A-Za-z0-9_]*)"\s*:\s*"(?P<val>[^"\\]*(?:\\.[^"\\]*)*)"'
)
_REDACT_NUMBER = re.compile(
    r'"(?P<key>[A-Za-z_][A-Za-z0-9_]*)"\s*:\s*(?P<val>-?\d+(?:\.\d+)?)'
)
# Match `key=value` where key is a known secret and value runs up to
# the next whitespace / comma / closing bracket / quote.
_REDACT_KV = re.compile(
    r'(?P<key>api_key|apikey|secret|token|password|authorization|auth|credential|credentials|private_key)'
    r'\s*=\s*'
    r"(?P<val>[^\s,)\]'\"]+)",
    re.IGNORECASE,
)


def _mask(value: str) -> str:
    """Return a 4-char prefix + '****' representation of ``value``.

    We take the first four characters verbatim, regardless of whether
    they are alphanumeric, punctuation, or unicode — the caller has
    already decided that the value is sensitive, so we just need a
    stable, human-readable prefix.
    """
    if not value:
        return "****"
    return f"{value[:4]}****"


def _is_secret_key(key: str) -> bool:
    return key.lower() in SECRET_KEYS


def _redact_text(text: str) -> str:
    """Walk ``text`` and replace secret values with a masked form.

    The function is intentionally conservative — it only touches
    substrings that look like JSON object keys or ``key=value`` pairs,
    so it never accidentally mutates user-visible prose.
    """
    def sub_string(m: re.Match[str]) -> str:
        key = m.group("key")
        if not _is_secret_key(key):
            return m.group(0)
        return f'"{key}": "{_mask(m.group("val"))}"'

    def sub_number(m: re.Match[str]) -> str:
        key = m.group("key")
        if not _is_secret_key(key):
            return m.group(0)
        return f'"{key}": "{_mask(m.group("val"))}"'

    def sub_kv(m: re.Match[str]) -> str:
        key = m.group("key")
        return f'{key}={_mask(m.group("val"))}'

    text = _REDACT_STRING.sub(sub_string, text)
    text = _REDACT_NUMBER.sub(sub_number, text)
    text = _REDACT_KV.sub(sub_kv, text)
    return text


def _redact_in_obj(obj: Any) -> Any:
    """Recursively walk a structured object and mask secret values."""
    if isinstance(obj, dict):
        out: dict[Any, Any] = {}
        for k, v in obj.items():
            if isinstance(k, str) and _is_secret_key(k):
                out[k] = _mask("" if v is None else str(v))
            else:
                out[k] = _redact_in_obj(v)
        return out
    if isinstance(obj, list):
        return [_redact_in_obj(x) for x in obj]
    return obj


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def register_schema(name: str, schema: dict[str, FieldSpec]) -> None:
    """Register the parameter schema for one operation.

    Handler modules call this at import time.  :func:`dispatch` then
    looks the schema up by operation name.
    """
    SCHEMAS[name] = schema


async def dispatch(
    handlers: dict[str, OperationHandler],
    operation: str,
    params_json: str,
    *,
    session_id: str = "",
    secret_keys: set[str] | None = None,
) -> str:
    """Route ``operation`` to its handler with parsed params.

    Parameters
    ----------
    handlers:
        The ``HANDLERS`` dict for the current mega tool's area.  Keys
        are operation names, values are async callables.
    operation:
        The operation name (e.g. ``"ping"``).  Empty string is an error.
    params_json:
        JSON-encoded parameter object.  Empty string is treated as
        ``{}``.  Any JSON / type / required-field error is reported
        as ``"ERROR: bad_*: ..."``.
    session_id:
        Top-level session id.  Bound via :func:`fulladdmax_mcp.context.bind`
        for the duration of the call so any handler that touches
        the persistent context store writes to the right place.
    secret_keys:
        Override :data:`SECRET_KEYS` (testing only).

    Returns
    -------
    str
        The handler's Markdown / JSON output on success, or a
        ``"ERROR: <category>: <message>"`` line on failure.
    """
    if not isinstance(handlers, dict):
        return "ERROR: bad_op: handlers must be a dict (internal bug)"

    # 1) operation validation
    if not operation or not operation.strip():
        return "ERROR: bad_op: operation is required"
    op = operation.strip()
    if op not in handlers:
        available = sorted(handlers.keys())
        return (
            f"ERROR: bad_op: unknown operation {op!r}. "
            f"available: {available}"
        )

    # 2) bind session_id (best-effort)
    if session_id:
        try:
            ctx_mod.bind(session_id)
        except Exception as e:  # noqa: BLE001
            return _redact_text(
                f"ERROR: handler: invalid session_id {session_id!r}: {e}"
            )

    # 3) parse + validate params
    schema = SCHEMAS.get(op, {})
    try:
        params, warnings = parse_params(params_json, schema)
    except ParamError as e:
        return _redact_text(e.to_error_line())
    except Exception as e:  # noqa: BLE001
        return _redact_text(f"ERROR: handler: param parser crashed: {e!r}")

    # 4) invoke handler
    handler = handlers[op]
    try:
        result = await handler(**params)
    except Exception as e:  # noqa: BLE001
        log.exception("handler %r raised", op)
        return _redact_text(f"ERROR: handler: {type(e).__name__}: {e}")

    # 5) attach warnings as a trailing note
    if warnings and isinstance(result, str):
        warn_line = "\n".join(f"[warn] {w}" for w in warnings)
        return f"{result}\n{warn_line}"
    return result


# Public helpers (used by handler modules + tests)
__all__ = [
    "SECRET_KEYS",
    "OperationHandler",
    "SCHEMAS",
    "dispatch",
    "register_schema",
    "_redact_text",
    "_redact_in_obj",
    "_mask",
]
