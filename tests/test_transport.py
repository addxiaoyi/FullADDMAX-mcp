"""Tests for the HTTP/SSE transport entry point."""

from __future__ import annotations

import pytest

from fulladdmax_mcp.server import (
    Transport,
    _build_arg_parser,
    _normalize_transport,
    mcp,
)


def test_normalize_transport_http_alias():
    assert _normalize_transport("http") == "streamable-http"
    assert _normalize_transport("streamable-http") == "streamable-http"
    assert _normalize_transport("stdio") == "stdio"
    assert _normalize_transport("sse") == "sse"


def test_arg_parser_defaults_to_stdio_localhost_8000():
    parser = _build_arg_parser()
    args = parser.parse_args([])
    assert args.transport == "stdio"
    assert args.host == "127.0.0.1"
    assert args.port == 8000
    assert args.mount_path is None
    assert args.log_level == "INFO"


def test_arg_parser_http_mode():
    parser = _build_arg_parser()
    args = parser.parse_args(
        ["--transport", "streamable-http", "--host", "0.0.0.0", "--port", "9000"]
    )
    assert args.transport == "streamable-http"
    assert args.host == "0.0.0.0"
    assert args.port == 9000


def test_arg_parser_http_alias():
    parser = _build_arg_parser()
    args = parser.parse_args(["--transport", "http"])
    assert args.transport == "http"
    assert _normalize_transport(args.transport) == "streamable-http"


def test_arg_parser_rejects_unknown_transport():
    parser = _build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--transport", "webrtc"])


def test_arg_parser_rejects_bad_port():
    parser = _build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--port", "not-a-number"])


def test_mcp_settings_have_http_defaults():
    """FastMCP settings expose host/port so we can override them at runtime."""
    assert hasattr(mcp, "settings")
    assert mcp.settings.host == "127.0.0.1"
    assert mcp.settings.port == 8000


def test_mcp_settings_mutable_for_http_bind():
    """The settings object accepts host/port mutation at runtime."""
    original_host = mcp.settings.host
    original_port = mcp.settings.port
    try:
        mcp.settings.host = "0.0.0.0"
        mcp.settings.port = 9123
        assert mcp.settings.host == "0.0.0.0"
        assert mcp.settings.port == 9123
    finally:
        mcp.settings.host = original_host
        mcp.settings.port = original_port


def test_transport_literal_includes_streamable_http():
    # Type guard: the alias must be present in the Literal.
    assert "streamable-http" in Transport.__args__
    assert "stdio" in Transport.__args__
    assert "sse" in Transport.__args__
