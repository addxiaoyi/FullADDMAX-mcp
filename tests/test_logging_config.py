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
# init_logging() tests
# ---------------------------------------------------------------------------


def _write_env_file(path: Path, lines: list[str]) -> None:
    """Write a test .env file with UTF-8 (for the emoji comments)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_init_logging_no_args() -> None:
    _section("init_logging(): no .env, no args -> defaults")
    _strip_env()
    # Run from a tempdir so the auto-detect '.env' lookup finds nothing.
    cwd = tempfile.mkdtemp()
    old_cwd = os.getcwd()
    try:
        os.chdir(cwd)
        pkg = lc.init_logging(env_file="auto")
        _check(
            logging.getLogger().level == logging.INFO,
            "defaults to INFO when no .env / no env / no CLI",
        )
        h = _find_handler(logging.getLogger(), logging.StreamHandler)
        _check(
            h is not None and not isinstance(h.formatter, lc.JsonFormatter),
            "defaults to text formatter",
        )
    finally:
        os.chdir(old_cwd)


def test_init_logging_explicit_env_file() -> None:
    _section("init_logging(env_file=<path>) loads that .env")
    _strip_env()
    with tempfile.TemporaryDirectory() as tmp:
        env_path = Path(tmp) / "fixture.env"
        _write_env_file(env_path, [
            "FULLADDMAX_LOG_LEVEL=DEBUG",
            "FULLADDMAX_LOG_FORMAT=json",
            "# this comment should be skipped",
        ])
        pkg = lc.init_logging(env_file=env_path)
        _check(
            logging.getLogger().level == logging.DEBUG,
            "DEBUG applied from .env (explicit path)",
        )
        h = _find_handler(logging.getLogger(), logging.StreamHandler)
        _check(
            h is not None and isinstance(h.formatter, lc.JsonFormatter),
            "JsonFormatter applied from .env (explicit path)",
        )


def test_init_logging_env_file_none() -> None:
    _section("init_logging(env_file='none') skips .env loading")
    _strip_env()
    # Drop a .env in cwd to prove it's NOT picked up.
    cwd = tempfile.mkdtemp()
    old_cwd = os.getcwd()
    try:
        Path(cwd, ".env").write_text(
            "FULLADDMAX_LOG_LEVEL=DEBUG\n", encoding="utf-8"
        )
        os.chdir(cwd)
        lc.init_logging(env_file="none")
        _check(
            logging.getLogger().level == logging.INFO,
            "level stays INFO when env_file='none' (ignores cwd .env)",
        )
    finally:
        os.chdir(old_cwd)


def test_init_logging_cli_over_env() -> None:
    _section("init_logging(level='ERROR', env_file=...) : CLI > .env")
    _strip_env()
    with tempfile.TemporaryDirectory() as tmp:
        env_path = Path(tmp) / "fixture.env"
        _write_env_file(env_path, [
            "FULLADDMAX_LOG_LEVEL=DEBUG",
            "FULLADDMAX_LOG_FORMAT=text",
        ])
        # CLI passes level=ERROR, but .env says DEBUG -> CLI wins.
        lc.init_logging(level="ERROR", env_file=env_path)
        _check(
            logging.getLogger().level == logging.ERROR,
            "CLI level=ERROR beats .env DEBUG",
        )


def test_init_logging_shell_env_over_dotenv() -> None:
    _section("init_logging: shell env > .env (setdefault semantics)")
    _strip_env()
    with tempfile.TemporaryDirectory() as tmp:
        env_path = Path(tmp) / "fixture.env"
        _write_env_file(env_path, [
            "FULLADDMAX_LOG_LEVEL=DEBUG",
        ])
        # Shell pre-sets the var BEFORE init_logging runs (simulating
        # a teammate who has 'export FULLADDMAX_LOG_LEVEL=ERROR' in
        # their shell rc).
        os.environ["FULLADDMAX_LOG_LEVEL"] = "ERROR"
        try:
            lc.init_logging(env_file=env_path)
            _check(
                logging.getLogger().level == logging.ERROR,
                "shell-set ERROR wins over .env's DEBUG",
            )
        finally:
            del os.environ["FULLADDMAX_LOG_LEVEL"]


def test_init_logging_multiple_files() -> None:
    _section("init_logging(env_file=[a, b]) tries each in order")
    _strip_env()
    with tempfile.TemporaryDirectory() as tmp:
        a = Path(tmp) / "a.env"
        b = Path(tmp) / "b.env"
        _write_env_file(a, ["FULLADDMAX_LOG_LEVEL=DEBUG"])
        _write_env_file(b, ["FULLADDMAX_LOG_FORMAT=json"])
        # First file wins for each key (setdefault semantics).
        lc.init_logging(env_file=[a, b])
        _check(
            logging.getLogger().level == logging.DEBUG,
            "level from a.env applied",
        )
        h = _find_handler(logging.getLogger(), logging.StreamHandler)
        _check(
            h is not None and isinstance(h.formatter, lc.JsonFormatter),
            "format from b.env applied",
        )


def test_init_logging_auto_default_paths() -> None:
    _section("init_logging(env_file='auto') tries DEFAULT_ENV_PATHS")
    _strip_env()
    cwd = tempfile.mkdtemp()
    old_cwd = os.getcwd()
    try:
        # Drop a .env in cwd (the first DEFAULT_ENV_PATHS entry).
        Path(cwd, ".env").write_text(
            "FULLADDMAX_LOG_LEVEL=DEBUG\n", encoding="utf-8"
        )
        os.chdir(cwd)
        lc.init_logging(env_file="auto")
        _check(
            logging.getLogger().level == logging.DEBUG,
            "auto-detected cwd ./.env applied",
        )
    finally:
        os.chdir(old_cwd)


def test_init_logging_returns_package_logger() -> None:
    _section("init_logging() return value")
    _strip_env()
    with tempfile.TemporaryDirectory() as tmp:
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp)
            pkg = lc.init_logging(env_file="none")
            _check(
                isinstance(pkg, logging.Logger),
                "return value is a logging.Logger",
            )
            _check(
                pkg.name == "fulladdmax-mcp",
                "return value is the fulladdmax-mcp package logger",
            )
        finally:
            os.chdir(old_cwd)


def test_init_logging_idempotent() -> None:
    _section("init_logging() idempotency (no handler stacking)")
    _strip_env()
    with tempfile.TemporaryDirectory() as tmp:
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp)
            lc.init_logging(env_file="none")
            n1 = len(logging.getLogger().handlers)
            lc.init_logging(env_file="none")
            lc.init_logging(env_file="none")
            n2 = len(logging.getLogger().handlers)
            _check(n1 == n2 == 1, f"handler count stable ({n1} -> {n2})")
        finally:
            os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# init_logging() edge-case tests (env_file=None / [] / exception paths)
# ---------------------------------------------------------------------------


def test_init_logging_env_file_none_literal() -> None:
    _section("init_logging(env_file=None) — None is a valid skip-sentinel")
    _strip_env()
    # Drop a .env in cwd to prove it's NOT picked up.
    cwd = tempfile.mkdtemp()
    old_cwd = os.getcwd()
    try:
        Path(cwd, ".env").write_text(
            "FULLADDMAX_LOG_LEVEL=DEBUG\n", encoding="utf-8"
        )
        os.chdir(cwd)
        # None should behave identically to "none" (skip .env loading).
        lc.init_logging(env_file=None)
        _check(
            logging.getLogger().level == logging.INFO,
            "env_file=None -> level stays INFO (skips cwd .env)",
        )
    finally:
        os.chdir(old_cwd)


def test_init_logging_env_file_empty_iterables() -> None:
    _section("init_logging(env_file=[] or ()) — empty iterables are no-ops")
    _strip_env()
    cwd = tempfile.mkdtemp()
    old_cwd = os.getcwd()
    try:
        # Drop a .env in cwd to prove neither branch picks it up.
        Path(cwd, ".env").write_text(
            "FULLADDMAX_LOG_LEVEL=DEBUG\n", encoding="utf-8"
        )
        os.chdir(cwd)
        lc.init_logging(env_file=[])
        _check(
            logging.getLogger().level == logging.INFO,
            "env_file=[]  -> level stays INFO (no .env loaded)",
        )
        lc.init_logging(env_file=())
        _check(
            logging.getLogger().level == logging.INFO,
            "env_file=()  -> level stays INFO (no .env loaded)",
        )
        # And no .env-loaded key in os.environ either.
        _check(
            "FULLADDMAX_LOG_LEVEL" not in os.environ,
            "FULLADDMAX_LOG_LEVEL was not injected by empty-iterable call",
        )
    finally:
        os.chdir(old_cwd)


def test_init_logging_env_file_nonexistent_single_path() -> None:
    _section("init_logging(env_file=<missing path>) — silently skipped")
    _strip_env()
    with tempfile.TemporaryDirectory() as tmp:
        ghost = Path(tmp) / "definitely_does_not_exist.env"
        # Sanity: confirm the file does not exist.
        _check(not ghost.exists(), "fixture: missing file confirmed absent")
        # Should NOT raise; should fall through to defaults.
        try:
            lc.init_logging(env_file=ghost)
        except Exception as e:  # noqa: BLE001
            _fail("init_logging crashed on nonexistent path", repr(e))
            return
        _check(
            logging.getLogger().level == logging.INFO,
            "missing path -> level stays INFO (no crash, no env var)",
        )
        _check(
            "FULLADDMAX_LOG_LEVEL" not in os.environ,
            "missing path did not pollute os.environ",
        )


def test_init_logging_env_file_iterable_with_missing_paths() -> None:
    _section("init_logging(env_file=[existing, missing]) — only existing loaded")
    _strip_env()
    with tempfile.TemporaryDirectory() as tmp:
        real = Path(tmp) / "real.env"
        ghost1 = Path(tmp) / "ghost1.env"
        ghost2 = Path(tmp) / "ghost2.env"
        _write_env_file(real, ["FULLADDMAX_LOG_FORMAT=json"])
        # Mix: 1 existing + 2 missing in arbitrary order.
        try:
            lc.init_logging(env_file=[ghost1, real, ghost2])
        except Exception as e:  # noqa: BLE001
            _fail("init_logging crashed on iterable with missing paths", repr(e))
            return
        h = _find_handler(logging.getLogger(), logging.StreamHandler)
        _check(
            h is not None and isinstance(h.formatter, lc.JsonFormatter),
            "real.env's JSON format still applied despite 2 missing neighbours",
        )


def test_init_logging_env_file_path_is_directory() -> None:
    _section("init_logging(env_file=<directory>) — silently skipped, no crash")
    _strip_env()
    with tempfile.TemporaryDirectory() as tmp:
        # tmpdir itself is a directory, not a file.  Should not raise.
        try:
            lc.init_logging(env_file=tmp)
        except Exception as e:  # noqa: BLE001
            _fail("init_logging crashed on directory path", repr(e))
            return
        _check(
            "FULLADDMAX_LOG_LEVEL" not in os.environ,
            "directory path did not pollute os.environ",
        )


def test_init_logging_env_file_malformed_lines_ignored() -> None:
    _section("init_logging: malformed .env lines are silently ignored")
    _strip_env()
    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "garbage.env"
        # Mix of: valid, malformed, weird-but-parseable, empty, comment.
        _write_env_file(bad, [
            "FULLADDMAX_LOG_LEVEL=ERROR",          # valid -> applied
            "",                                    # empty line
            "# this is a pure comment",            # pure comment
            "=no_key_on_left",                     # missing key (regex reject)
            "123STARTS_WITH_DIGIT=value",          # key must start with [A-Z_]
            "good_key_lower=value",                # key regex is [A-Z_] only!
            "  FULLADDMAX_LOG_FORMAT=json  ",      # valid (whitespace trimmed)
            "trailing=line  # not really a comment",  # no comment stripping
            "KEY_WITH_NO_VALUE=",                  # value empty -> skipped (if val)
        ])
        try:
            lc.init_logging(env_file=bad)
        except Exception as e:  # noqa: BLE001
            _fail("init_logging crashed on malformed .env", repr(e))
            return
        # 1 valid line: FULLADDMAX_LOG_LEVEL=ERROR
        _check(
            logging.getLogger().level == logging.ERROR,
            "valid FULLADDMAX_LOG_LEVEL=ERROR still applied",
        )
        # 1 valid line: FULLADDMAX_LOG_FORMAT=json (whitespace trimmed)
        h = _find_handler(logging.getLogger(), logging.StreamHandler)
        _check(
            h is not None and isinstance(h.formatter, lc.JsonFormatter),
            "valid FULLADDMAX_LOG_FORMAT=json still applied (whitespace stripped)",
        )
        # Malformed keys must NOT pollute os.environ.
        for bad_key in ("NO_KEY_ON_LEFT", "123STARTS_WITH_DIGIT",
                        "GOOD_KEY_LOWER", "TRAILING"):
            _check(
                bad_key not in os.environ,
                f"malformed key {bad_key!r} was correctly rejected",
            )


def test_init_logging_env_file_empty_file() -> None:
    _section("init_logging(env_file=<empty file>) — no-op, no crash")
    _strip_env()
    with tempfile.TemporaryDirectory() as tmp:
        empty = Path(tmp) / "empty.env"
        _write_env_file(empty, [])  # 0 lines
        try:
            lc.init_logging(env_file=empty)
        except Exception as e:  # noqa: BLE001
            _fail("init_logging crashed on empty .env", repr(e))
            return
        _check(
            logging.getLogger().level == logging.INFO,
            "empty file -> level stays INFO",
        )


def test_parse_env_file_missing_returns_empty_dict() -> None:
    _section("_parse_env_file(missing) — returns {} instead of raising")
    try:
        out = lc._parse_env_file("/this/path/definitely/does/not/exist.env")
    except Exception as e:  # noqa: BLE001
        _fail("_parse_env_file raised on missing path", repr(e))
        return
    _check(out == {}, "_parse_env_file returns empty dict for missing file")


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
    test_init_logging_no_args()
    test_init_logging_explicit_env_file()
    test_init_logging_env_file_none()
    test_init_logging_cli_over_env()
    test_init_logging_shell_env_over_dotenv()
    test_init_logging_multiple_files()
    test_init_logging_auto_default_paths()
    test_init_logging_returns_package_logger()
    test_init_logging_idempotent()
    test_init_logging_env_file_none_literal()
    test_init_logging_env_file_empty_iterables()
    test_init_logging_env_file_nonexistent_single_path()
    test_init_logging_env_file_iterable_with_missing_paths()
    test_init_logging_env_file_path_is_directory()
    test_init_logging_env_file_malformed_lines_ignored()
    test_init_logging_env_file_empty_file()
    test_parse_env_file_missing_returns_empty_dict()

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
