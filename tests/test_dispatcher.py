"""Unit tests for :mod:`fulladdmax_mcp.dispatcher` and
:mod:`fulladdmax_mcp.param_parser`.

These tests cover the error / edge cases of the mega-tool plumbing
without going through the FastMCP server at all.
"""

from __future__ import annotations

import pytest

from fulladdmax_mcp.dispatcher import (
    SCHEMAS,
    _mask,
    _redact_in_obj,
    _redact_text,
    dispatch,
)
from fulladdmax_mcp.param_parser import FieldSpec, parse_params
from fulladdmax_mcp.param_parser_errors import (
    BadJson,
    BadParam,
    BadType,
    ParamError,
)


# ---------------------------------------------------------------------------
# _mask / _redact_text / _redact_in_obj
# ---------------------------------------------------------------------------


def test_mask_short_value():
    assert _mask("sk-abcd") == "sk-a****"
    assert _mask("") == "****"
    assert _mask("a") == "a****"
    assert _mask("abcd") == "abcd****"


def test_redact_text_replaces_secret_strings():
    raw = '{"api_key": "sk-abcdef1234", "model": "gpt-4o-mini"}'
    out = _redact_text(raw)
    assert "sk-a****" in out
    assert "abcdef" not in out
    # non-secret stays intact
    assert "gpt-4o-mini" in out


def test_redact_text_replaces_secret_numbers():
    raw = '"token": 1234567890'
    out = _redact_text(raw)
    assert "1234****" in out
    assert "1234567890" not in out


def test_redact_text_is_case_insensitive():
    raw = '"API_KEY": "secret-9876"'
    out = _redact_text(raw)
    assert "secr****" in out
    assert "secret-9876" not in out


def test_redact_text_does_not_touch_non_secret_keys():
    raw = '"model": "gpt-4o-mini"'
    assert _redact_text(raw) == raw


def test_redact_in_obj_walks_dicts_and_lists():
    obj = {
        "api_key": "sk-secret123",
        "model": "gpt-4o",
        "items": [{"token": "abc-xyz"}, {"name": "ok"}],
    }
    out = _redact_in_obj(obj)
    assert out["api_key"] == "sk-s****"
    assert out["model"] == "gpt-4o"
    assert out["items"][0]["token"] == "abc-****"
    assert out["items"][1]["name"] == "ok"


# ---------------------------------------------------------------------------
# parse_params: happy path
# ---------------------------------------------------------------------------


def test_parse_params_empty_string():
    cleaned, warns = parse_params("", {})
    assert cleaned == {}
    assert warns == []


def test_parse_params_whitespace_only():
    cleaned, warns = parse_params("   ", {})
    assert cleaned == {}
    assert warns == []


def test_parse_params_basic_required_field():
    schema = {"name": FieldSpec(required=True, type=str)}
    cleaned, warns = parse_params('{"name": "alice"}', schema)
    assert cleaned == {"name": "alice"}
    assert warns == []


def test_parse_params_uses_default_when_missing():
    schema = {"limit": FieldSpec(required=False, type=int, default=10)}
    cleaned, warns = parse_params("{}", schema)
    assert cleaned == {"limit": 10}
    assert warns == []


def test_parse_params_int_coercion():
    schema = {"n": FieldSpec(required=True, type=int)}
    cleaned, _ = parse_params('{"n": 42}', schema)
    assert cleaned == {"n": 42}


def test_parse_params_float_coercion():
    schema = {"x": FieldSpec(required=True, type=float)}
    cleaned, _ = parse_params('{"x": 3.14}', schema)
    assert cleaned == {"x": 3.14}


def test_parse_params_bool_coercion_from_string():
    schema = {"flag": FieldSpec(required=True, type=bool)}
    assert parse_params('{"flag": "true"}', schema)[0] == {"flag": True}
    assert parse_params('{"flag": "false"}', schema)[0] == {"flag": False}
    assert parse_params('{"flag": "yes"}', schema)[0] == {"flag": True}


def test_parse_params_list_validation():
    schema = {"items": FieldSpec(required=True, type=list, items_type=str)}
    cleaned, _ = parse_params('{"items": ["a", "b"]}', schema)
    assert cleaned == {"items": ["a", "b"]}


def test_parse_params_null_treated_as_missing_with_default():
    schema = {"limit": FieldSpec(required=False, type=int, default=5)}
    cleaned, _ = parse_params('{"limit": null}', schema)
    assert cleaned == {"limit": 5}


def test_parse_params_unknown_field_warning():
    schema = {"name": FieldSpec(required=True, type=str)}
    cleaned, warns = parse_params('{"name": "x", "extra": 1}', schema)
    assert cleaned == {"name": "x"}
    assert any("extra" in w for w in warns)


def test_parse_params_choices_enforced():
    schema = {"mode": FieldSpec(required=True, type=str, choices=("a", "b"))}
    with pytest.raises(BadParam):
        parse_params('{"mode": "c"}', schema)


# ---------------------------------------------------------------------------
# parse_params: error paths
# ---------------------------------------------------------------------------


def test_parse_params_invalid_json():
    with pytest.raises(BadJson):
        parse_params("{not json", {})


def test_parse_params_truncated_json():
    with pytest.raises(BadJson):
        parse_params('{"name": "a"', {})


def test_parse_params_json_array_not_object():
    with pytest.raises(BadParam):
        parse_params("[1, 2, 3]", {})


def test_parse_params_json_string_not_object():
    with pytest.raises(BadParam):
        parse_params('"hello"', {})


def test_parse_params_missing_required_field():
    schema = {"name": FieldSpec(required=True, type=str)}
    with pytest.raises(BadParam) as ei:
        parse_params("{}", schema)
    assert "name" in str(ei.value)


def test_parse_params_wrong_type_int():
    schema = {"n": FieldSpec(required=True, type=int)}
    with pytest.raises(BadType) as ei:
        parse_params('{"n": "abc"}', schema)
    assert "n" in str(ei.value)


def test_parse_params_wrong_type_list():
    schema = {"items": FieldSpec(required=True, type=list)}
    with pytest.raises(BadType):
        parse_params('{"items": "abc"}', schema)


def test_parse_params_not_a_string():
    with pytest.raises(BadType):
        parse_params(123, {})  # type: ignore[arg-type]


def test_parse_params_bool_rejects_bare_int_str():
    schema = {"flag": FieldSpec(required=True, type=bool)}
    # bool is subclass of int — passing 1 (int) should still coerce
    cleaned, _ = parse_params('{"flag": 1}', schema)
    assert cleaned == {"flag": True}


def test_parse_params_bool_rejects_junk_string():
    schema = {"flag": FieldSpec(required=True, type=bool)}
    with pytest.raises(BadType):
        parse_params('{"flag": "maybe"}', schema)


# ---------------------------------------------------------------------------
# dispatch: operation routing
# ---------------------------------------------------------------------------


async def _echo_op(**kwargs):
    """Trivial handler used by dispatch tests."""
    return f"ok: {sorted(kwargs.items())}"


async def test_dispatch_unknown_operation_lists_available():
    handlers = {"a": _echo_op, "b": _echo_op}
    out = await dispatch(handlers, "z", "")
    assert "ERROR: bad_op:" in out
    assert "'z'" in out
    assert "available" in out


async def test_dispatch_empty_operation():
    out = await dispatch({}, "", "")
    assert "ERROR: bad_op:" in out
    assert "required" in out


async def test_dispatch_whitespace_operation():
    out = await dispatch({}, "   ", "")
    assert "ERROR: bad_op:" in out


async def test_dispatch_bad_json():
    handlers = {"x": _echo_op}
    out = await dispatch(handlers, "x", "{not json")
    assert "ERROR: bad_json:" in out


async def test_dispatch_missing_required_field():
    # Register a schema requiring a field
    SCHEMAS["_test_required"] = {"req": FieldSpec(required=True, type=str)}
    try:
        out = await dispatch({"_test_required": _echo_op}, "_test_required", "{}")
        assert "ERROR: bad_param:" in out
        assert "req" in out
    finally:
        SCHEMAS.pop("_test_required", None)


async def test_dispatch_handler_exception_is_caught():
    async def boom(**_):
        raise RuntimeError("kaboom with api_key=sk-secret")

    SCHEMAS["_boom"] = {}
    try:
        out = await dispatch({"_boom": boom}, "_boom", "")
        assert "ERROR: handler:" in out
        assert "kaboom" in out
        # secret should be redacted
        assert "sk-s****" in out
        assert "sk-secret" not in out
    finally:
        SCHEMAS.pop("_boom", None)


async def test_dispatch_returns_handler_string():
    SCHEMAS["_echo"] = {"x": FieldSpec(required=False, type=int, default=0)}
    try:
        out = await dispatch({"_echo": _echo_op}, "_echo", '{"x": 7}')
        assert out.startswith("ok:")
        assert "('x', 7)" in out
    finally:
        SCHEMAS.pop("_echo", None)


async def test_dispatch_session_id_binds_context():
    """Top-level session_id is bound to ctx_mod before the handler runs."""
    import fulladdmax_mcp.context as ctx_mod

    captured = {}

    async def capture(**_):
        captured["sid"] = ctx_mod.session_id()
        return "ok"

    SCHEMAS["_capture"] = {}
    try:
        await dispatch({"_capture": capture}, "_capture", "", session_id="my-sid")
        assert captured["sid"] == "my-sid"
    finally:
        SCHEMAS.pop("_capture", None)


async def test_dispatch_no_session_id_leaves_default():
    """When session_id is empty, the handler sees the default 'default' session."""
    import fulladdmax_mcp.context as ctx_mod

    captured = {}

    async def capture(**_):
        captured["sid"] = ctx_mod.session_id()
        return "ok"

    SCHEMAS["_capture2"] = {}
    try:
        await dispatch({"_capture2": capture}, "_capture2", "")
        # default is whatever the context module's default is
        assert captured["sid"] == ctx_mod.session_id()
    finally:
        SCHEMAS.pop("_capture2", None)
