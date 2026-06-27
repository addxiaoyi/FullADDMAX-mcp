"""Exception hierarchy for ``param_parser`` and ``dispatcher``.

Each exception carries the raw offending value (or excerpt) so the
:class:`fulladdmax_mcp.dispatcher.dispatch` function can render a
user-friendly error message that includes where the failure occurred.
"""

from __future__ import annotations

from typing import Any


class ParamError(ValueError):
    """Base class for all param_parser / dispatcher errors.

    Subclasses are caught by :func:`dispatch` and converted to a
    ``"ERROR: <category>: <message>"`` string.  Instances of this base
    class are not raised directly.
    """

    category: str = "param"

    def __init__(self, message: str, *, raw_excerpt: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.raw_excerpt = raw_excerpt

    def to_error_line(self) -> str:
        if self.raw_excerpt:
            return f"ERROR: {self.category}: {self.message} (raw: {self.raw_excerpt!r})"
        return f"ERROR: {self.category}: {self.message}"


class BadJson(ParamError):
    category = "bad_json"


class BadParam(ParamError):
    """Raised for missing required fields, wrong top-level type, etc."""

    category = "bad_param"


class BadType(ParamError):
    """Raised when a field's JSON-decoded value does not match the
    declared :class:`FieldSpec.type`."""

    category = "bad_type"


def _excerpt(value: Any, max_len: int = 80) -> str:
    """Return a safe string excerpt of ``value`` for error messages."""
    if value is None:
        return ""
    s = str(value)
    if len(s) <= max_len:
        return s
    return s[:max_len] + "..."
