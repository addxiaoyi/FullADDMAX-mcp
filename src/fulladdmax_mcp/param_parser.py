"""Parameter parsing and schema validation for the mega-tool dispatch layer.

Mega tools (see :mod:`fulladdmax_mcp.dispatcher`) receive their
business parameters as a single JSON string (``params_json``).  This
module turns that string into a typed ``dict`` and validates it against
a per-operation schema.

* :class:`FieldSpec` — declarative description of a single field.
* :func:`parse_params` — JSON decode + schema check + default fill.
* :data:`SECRET_KEYS` — set of field names whose values must be
  redacted in any error message that echoes them back.

The output of :func:`parse_params` is always a dict, so the dispatcher
can simply do ``handler(**params)``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .param_parser_errors import BadJson, BadParam, BadType, _excerpt

# A sentinel used to detect "no default provided" in :class:`FieldSpec`.
_MISSING: Any = object()


# Field names whose values must be redacted (api_key[:4] + '****') whenever
# they appear inside an error message.  Matching is case-insensitive.
SECRET_KEYS: set[str] = {
    "api_key",
    "apikey",
    "base_url",
    "secret",
    "token",
    "password",
    "authorization",
    "auth",
    "credential",
    "credentials",
    "private_key",
}


@dataclass
class FieldSpec:
    """Description of one parameter in an operation's input schema.

    Attributes
    ----------
    required:
        If True (default), the field must be present.
    type:
        Expected JSON-decoded type.  Common choices are
        :class:`str`, :class:`int`, :class:`float`, :class:`bool`,
        :class:`list` and :class:`dict`.  ``None`` means "accept any".
    default:
        Value to use when the field is absent.  Use the sentinel
        :data:`_MISSING` to mark the field as required.
    items_type:
        For ``type=list`` / ``type=dict``, the expected element type.
        Used for shallow validation only.
    choices:
        Optional iterable of allowed values.
    """

    required: bool = True
    type: type | None = str
    default: Any = _MISSING
    items_type: type | None = None
    choices: tuple[Any, ...] | None = None

    def has_default(self) -> bool:
        return self.default is not _MISSING


def _coerce_bool(value: Any) -> bool:
    """JSON has no native bool distinction from int — accept common
    spellings used by LLMs (true/false/yes/no/1/0)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"true", "yes", "y", "1"}:
            return True
        if v in {"false", "no", "n", "0", ""}:
            return False
    raise BadType(f"cannot interpret {value!r} as bool")


def _check_type(name: str, value: Any, spec: FieldSpec) -> Any:
    """Validate and (if needed) coerce ``value`` to ``spec.type``."""
    if spec.type is None:
        return value
    if spec.type is bool:
        try:
            return _coerce_bool(value)
        except BadType:
            raise BadType(
                f"field {name!r} expected bool, got {type(value).__name__} ({value!r})"
            ) from None
    if spec.type is int:
        if isinstance(value, bool):
            # bool is subclass of int — reject explicitly
            raise BadType(f"field {name!r} expected int, got bool")
        if not isinstance(value, int):
            raise BadType(
                f"field {name!r} expected int, got {type(value).__name__} ({value!r})"
            )
        return value
    if spec.type is float:
        if isinstance(value, bool):
            raise BadType(f"field {name!r} expected float, got bool")
        if not isinstance(value, (int, float)):
            raise BadType(
                f"field {name!r} expected float, got {type(value).__name__} ({value!r})"
            )
        return float(value)
    if spec.type is str:
        if not isinstance(value, str):
            raise BadType(
                f"field {name!r} expected str, got {type(value).__name__} ({value!r})"
            )
        return value
    if spec.type is list:
        if not isinstance(value, list):
            raise BadType(
                f"field {name!r} expected list, got {type(value).__name__}"
            )
        if spec.items_type is not None and spec.items_type is not bool:
            for i, item in enumerate(value):
                if not isinstance(item, spec.items_type):
                    raise BadType(
                        f"field {name!r}[{i}] expected {spec.items_type.__name__}, "
                        f"got {type(item).__name__}"
                    )
        return value
    if spec.type is dict:
        if not isinstance(value, dict):
            raise BadType(
                f"field {name!r} expected dict, got {type(value).__name__}"
            )
        return value
    # Generic isinstance check for arbitrary types
    if not isinstance(value, spec.type):
        raise BadType(
            f"field {name!r} expected {spec.type.__name__}, got {type(value).__name__}"
        )
    return value


def parse_params(
    params_json: str,
    schema: dict[str, FieldSpec],
    *,
    secret_keys: set[str] = SECRET_KEYS,
) -> tuple[dict[str, Any], list[str]]:
    """Parse and validate a ``params_json`` string against ``schema``.

    Returns ``(params, warnings)`` where ``params`` is a dict ready to
    be splatted into the handler, and ``warnings`` is a list of
    non-fatal diagnostic strings (e.g. unknown fields were ignored).

    Raises
    ------
    BadJson
        ``params_json`` is not valid JSON.
    BadParam
        ``params_json`` is not a JSON object, or a required field is
        missing.
    BadType
        A field's JSON-decoded value does not match its declared type.
    """
    if params_json is None:
        params_json = ""
    if not isinstance(params_json, str):
        raise BadType(
            f"params_json must be a string, got {type(params_json).__name__}"
        )

    # Empty / whitespace -> empty params (handlers may have no required fields).
    if not params_json.strip():
        params: dict[str, Any] = {}
    else:
        try:
            decoded = json.loads(params_json)
        except json.JSONDecodeError as e:
            raise BadJson(
                f"line {e.lineno} column {e.colno}: {e.msg}",
                raw_excerpt=_excerpt(params_json),
            ) from None
        if not isinstance(decoded, dict):
            raise BadParam(
                f"params_json must decode to a JSON object, got {type(decoded).__name__}",
                raw_excerpt=_excerpt(params_json),
            )
        params = decoded

    warnings: list[str] = []
    cleaned: dict[str, Any] = {}

    # Apply declared schema fields.
    for name, spec in schema.items():
        if name in params:
            value = params[name]
            if value is None:
                # Treat JSON null as "missing" so callers can pass nulls safely.
                if spec.required and not spec.has_default():
                    raise BadParam(
                        f"missing required field {name!r} (got null)",
                    )
                if spec.has_default():
                    cleaned[name] = spec.default
                continue
            cleaned[name] = _check_type(name, value, spec)
        else:
            if spec.required and not spec.has_default():
                raise BadParam(f"missing required field {name!r}")
            if spec.has_default():
                cleaned[name] = spec.default
            # else: optional & absent -> omit from cleaned

        # choices check (after type coercion)
        if (
            spec.choices is not None
            and name in cleaned
            and cleaned[name] not in spec.choices
        ):
            raise BadParam(
                f"field {name!r} must be one of {list(spec.choices)!r}, "
                f"got {cleaned[name]!r}"
            )

    # Warn about unknown fields (silently ignored).
    unknown = sorted(set(params) - set(schema))
    if unknown:
        warnings.append(f"ignored unknown fields: {unknown}")

    return cleaned, warnings
