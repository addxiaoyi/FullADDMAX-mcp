"""Tests for the configurable logging system.

Covers:
  * default config (text / stderr / INFO)
  * level / format / file_path overrides
  * env var precedence (CLI > env > default)
  * JSON formatter shape
  * idempotency (call twice, no duplicated output)
  * file output (writes to disk)
  * file rotation (mocked threshold)
  * validation (bad level / bad format raises)

Run: PYTHONPATH=src python tests/test_logging_config.py
"""
from __future__ import annotations

import io
import json
import logging
import logging.handlers
import os
import sys
import tempfile
from pathlib import Path

# Make the package importable when running from anywhere.
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

from fulladdmax_mcp import logging_config as lc  # noqa: E402


# ---------------------------------------------------------------------------
# Tiny test harness (no pytest dep)
# ---------------------------------------------------------------------------

_PASS = 0
_FAIL = 0
_FAILURES: list[str] = []


def _ok(msg: str) -> None:
    global _PASS
    _PASS += 1
    print(f"  PASS  {msg}")


def _fail(msg: str, detail: str = "") -> None:
    global _FAIL
    _FAIL += 1
    _FAILURES.append(f"{msg} :: {detail}" if detail else msg)
    print(f"  FAIL  {msg}")
    if detail:
        for line in detail.splitlines():
            print(f"        {line}")


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _check(cond: bool, msg: str, detail: str = "") -> bool:
    if cond:
        _ok(msg)
    else:
        _fail(msg, detail)
    return cond


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_env() -> None:
    """Erase every FULLADDMAX_LOG_* env var for the test."""
    for k in list(os.environ):
        if k.startswith("FULLADDMAX_LOG_"):
            del os.environ[k]


def _find_handler(logger: logging.Logger, klass: type) -> logging.Handler | None:
    for h in logger.handlers:
        if isinstance(h, klass):
            return h
    return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_defaults() -> None:
    _section("configure_logging: defaults")
    _strip_env()
    pkg = lc.configure_logging()
    root = logging.getLogger()
    _check(root.level == logging.INFO, "root level == INFO")
    _check(pkg.level == logging.INFO, "package level == INFO")
    # Default handler on root is a StreamHandler pointing at stderr
    h = _find_handler(root, logging.StreamHandler)
    _check(h is not None, "root has a StreamHandler")
    _check(
        h is not None and getattr(h, "stream", None) is sys.stderr,
        "default stream is stderr",
    )
    # Formatter is the text format (not JsonFormatter)
    _check(
        h is not None and not isinstance(h.formatter, lc.JsonFormatter),
        "default formatter is NOT JsonFormatter",
    )


def test_level_override() -> None:
    _section("configure_logging: --log-level DEBUG")
    _strip_env()
    lc.configure_logging(level="DEBUG")
    _check(
        logging.getLogger().level == logging.DEBUG,
        "root level == DEBUG after override",
    )


def test_json_format() -> None:
    _section("configure_logging: --log-format json")
    _strip_env()
    # Point at a StringIO so we can read what the handler produced.
    buf = io.StringIO()
    # Manually swap in a StringIO stream after the call.
    lc.configure_logging(fmt="json")
    root = logging.getLogger()
    h = _find_handler(root, logging.StreamHandler)
    _check(h is not None, "handler installed")
    _check(
        h is not None and isinstance(h.formatter, lc.JsonFormatter),
        "JsonFormatter installed on handler",
    )
    # Actually emit a record and verify it parses as JSON.
    if h is not None and isinstance(h.formatter, lc.JsonFormatter):
        h.stream = buf
        # Use a child logger so we hit "fulladdmax-mcp.*"
        child = lc.get_logger("llm")
        child.info("hello world")
        line = buf.getvalue().strip()
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as e:
            _fail("JSON line parses", f"{e}: {line!r}")
            return
        _check(rec["message"] == "hello world", "message field correct")
        _check(rec["level"] == "INFO", "level field is INFO")
        _check(rec["logger"] == "fulladdmax-mcp.llm", "logger field has dotted path")
        _check("timestamp" in rec, "timestamp field present")


def test_extra_fields_in_json() -> None:
    _section("JsonFormatter: extra=... fields merged into record")
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(lc.JsonFormatter())
    rec_logger = logging.getLogger("fulladdmax-mcp.test-extras")
    rec_logger.handlers = [h]
    rec_logger.propagate = False
    rec_logger.info("op done", extra={"task": "abc", "elapsed_ms": 42})
    rec = json.loads(buf.getvalue().strip())
    _check(rec.get("task") == "abc", "extra 'task' merged")
    _check(rec.get("elapsed_ms") == 42, "extra 'elapsed_ms' merged")
    _check("message" in rec, "message field still present alongside extras")


def test_exc_info_in_json() -> None:
    _section("JsonFormatter: exc_info becomes a string")
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(lc.JsonFormatter())
    rec_logger = logging.getLogger("fulladdmax-mcp.test-exc")
    rec_logger.handlers = [h]
    rec_logger.propagate = False
    try:
        raise ValueError("boom")
    except ValueError:
        rec_logger.exception("oh no")
    rec = json.loads(buf.getvalue().strip())
    _check("exc_info" in rec, "exc_info field present")
    _check(isinstance(rec["exc_info"], str), "exc_info is a string")
    _check("ValueError: boom" in rec["exc_info"], "exc_info contains traceback")


def test_file_output() -> None:
    _section("configure_logging: --log-file writes to file")
    _strip_env()
    with tempfile.TemporaryDirectory() as tmp:
        log_path = str(Path(tmp) / "test.log")
        try:
            lc.configure_logging(file_path=log_path)
            lc.get_logger("test").warning("written to file")
            # Force flush + close by reconfiguring (which closes the
            # previous handler in our idempotent teardown).
            lc.configure_logging(file_path=log_path)
            assert Path(log_path).exists(), "log file was created"
            body = Path(log_path).read_text(encoding="utf-8")
            _check("written to file" in body, "warning line landed in file")
            _check("fulladdmax-mcp.test" in body, "logger name in file line")
        finally:
            logging.shutdown()


def test_file_rotation() -> None:
    _section("configure_logging: rotating file handler")
    _strip_env()
    with tempfile.TemporaryDirectory() as tmp:
        log_path = str(Path(tmp) / "rotate.log")
        try:
            # Set a tiny max_bytes so rotation triggers quickly.
            lc.configure_logging(file_path=log_path, max_bytes=256, backup_count=2)
            # Write enough lines to overflow 256 bytes.
            big = "x" * 100
            for _ in range(10):
                lc.get_logger("rot").info(big)
            # Force flush + close so stat() reads the right size on Windows.
            logging.shutdown()
            main_size = Path(log_path).stat().st_size
            rot1 = Path(log_path + ".1")
            _check(main_size <= 512, f"main file size bounded ({main_size} bytes)")
            _check(rot1.exists(), "rotated backup .1 was created")
        finally:
            logging.shutdown()


def test_idempotency() -> None:
    _section("configure_logging: idempotent (no handler stacking)")
    _strip_env()
    lc.configure_logging()
    n1 = len(logging.getLogger().handlers)
    lc.configure_logging()
    lc.configure_logging()
    n2 = len(logging.getLogger().handlers)
    _check(n1 == n2 == 1, f"handler count stable after re-configure ({n1} -> {n2})")


def test_env_var_precedence() -> None:
    _section("env var precedence (CLI > env > default)")
    _strip_env()
    # 1. env var only
    os.environ["FULLADDMAX_LOG_LEVEL"] = "DEBUG"
    os.environ["FULLADDMAX_LOG_FORMAT"] = "json"
    pkg = lc.configure_logging()
    _check(
        logging.getLogger().level == logging.DEBUG,
        "env FULLADDMAX_LOG_LEVEL=DEBUG applied",
    )
    h = _find_handler(logging.getLogger(), logging.StreamHandler)
    _check(
        h is not None and isinstance(h.formatter, lc.JsonFormatter),
        "env FULLADDMAX_LOG_FORMAT=json applied",
    )

    # 2. CLI override beats env
    pkg = lc.configure_logging(level="ERROR", fmt="text")
    _check(
        logging.getLogger().level == logging.ERROR,
        "CLI level=ERROR beats env DEBUG",
    )
    h = _find_handler(logging.getLogger(), logging.StreamHandler)
    _check(
        h is not None and not isinstance(h.formatter, lc.JsonFormatter),
        "CLI fmt=text beats env json",
    )

    # 3. defaults when neither CLI nor env
    _strip_env()
    lc.configure_logging()
    _check(
        logging.getLogger().level == logging.INFO,
        "falls back to default INFO when no env / no CLI",
    )


def test_validation() -> None:
    _section("configure_logging: input validation")
    _strip_env()
    try:
        lc.configure_logging(level="BOGUS")
    except ValueError as e:
        _ok("bad level rejected with ValueError")
        _check("BOGUS" in str(e), "error message mentions the bad value")
    else:
        _fail("bad level was accepted (should have raised)")

    try:
        lc.configure_logging(fmt="yaml")
    except ValueError as e:
        _ok("bad format rejected with ValueError")
    else:
        _fail("bad format was accepted (should have raised)")


def test_get_logger_paths() -> None:
    _section("get_logger: dotted path normalisation")
    _strip_env()
    a = lc.get_logger()  # bare → "fulladdmax-mcp"
    b = lc.get_logger("llm")
    c = lc.get_logger("fulladdmax-mcp.llm")  # already prefixed → unchanged
    d = logging.getLogger("fulladdmax-mcp.dotted.path")
    _check(a.name == "fulladdmax-mcp", "bare get_logger → fulladdmax-mcp")
    _check(b.name == "fulladdmax-mcp.llm", "get_logger('llm') → fulladdmax-mcp.llm")
    _check(c.name == "fulladdmax-mcp.llm", "idempotent prefix")
    _check(d.name == "fulladdmax-mcp.dotted.path", "raw dotted path preserved")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> int:
    test_defaults()
    test_level_override()
    test_json_format()
    test_extra_fields_in_json()
    test_exc_info_in_json()
    test_file_output()
    test_file_rotation()
    test_idempotency()
    test_env_var_precedence()
    test_validation()
    test_get_logger_paths()

    print()
    print("=" * 70)
    print(f"Summary: {_PASS} passed, {_FAIL} failed")
    print("=" * 70)
    if _FAIL:
        print("\nFailures:")
        for f in _FAILURES:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
