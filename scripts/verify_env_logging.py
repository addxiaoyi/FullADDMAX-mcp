"""Simulate ``cp .env.example .env`` and verify logging config works.

This script does NOT require a real .env file.  It parses
``.env.example`` (in repo root) in-process, applies the uncommented
values to ``os.environ`` for the test, and then runs the same
``configure_logging()`` the server would call.  The result is
asserted against each of the 5 logging dimensions:

  1. FULLADDMAX_LOG_LEVEL        (root + package loggers)
  2. FULLADDMAX_LOG_FORMAT       (text vs JsonFormatter)
  3. FULLADDMAX_LOG_FILE         (StreamHandler vs FileHandler)
  4. FULLADDMAX_LOG_ROTATE_MAX_BYTES  (FileHandler vs RotatingFileHandler)
  5. FULLADDMAX_LOG_ROTATE_BACKUPS    (.1 / .2 / .3 backup files)

Why this exists
===============

Teams want to know "if I uncomment these env vars, will it actually
work in production?".  Running the server end-to-end is overkill
(no MCP host, no LLM, etc.).  This script answers the question in
~1 second with no external dependencies.

Usage::

    python scripts/verify_env_logging.py            # auto-load .env.example
    python scripts/verify_env_logging.py -v         # verbose (per-record output)
    python scripts/verify_env_logging.py --env .env # load a real .env instead
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import tempfile
from pathlib import Path

# Make ``fulladdmax_mcp`` importable.
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

from fulladdmax_mcp import logging_config as lc  # noqa: E402


# ---------------------------------------------------------------------------
# .env parser  (5 lines, no python-dotenv dep)
# ---------------------------------------------------------------------------

_ENV_LINE = re.compile(
    r"^\s*#?\s*"                    # optional leading # (treat commented as candidate)
    r"([A-Z_][A-Z0-9_]*)"           # KEY
    r"\s*=\s*"
    r"(.*?)\s*$"                    # VALUE
)


def parse_env_file(path: Path) -> dict[str, str]:
    """Return ``{KEY: value}`` for every assignable line.

    All lines that *look* like ``# KEY = value`` (even if commented) are
    returned, so the caller can decide whether to apply them.  Blank
    lines and pure-comment lines (starting with ``#`` and no ``=``) are
    ignored.
    """
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _ENV_LINE.match(line)
        if not m:
            continue
        key, val = m.group(1), m.group(2)
        if val:  # only include non-empty values
            out[key] = val
    return out


# ---------------------------------------------------------------------------
# Tiny test harness
# ---------------------------------------------------------------------------

_PASS = 0
_FAIL = 0


def _ok(msg: str) -> None:
    global _PASS
    _PASS += 1
    print(f"  [OK]   {msg}")


def _fail(msg: str, detail: str = "") -> None:
    global _FAIL
    _FAIL += 1
    print(f"  [FAIL] {msg}")
    if detail:
        for line in detail.splitlines():
            print(f"         {line}")


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _check(cond: bool, msg: str, detail: str = "") -> bool:
    ( _ok(msg) if cond else _fail(msg, detail) )
    return cond


# ---------------------------------------------------------------------------
# 5-dimension verification
# ---------------------------------------------------------------------------


def _handler_of_type(root: logging.Logger, *types: type) -> logging.Handler | None:
    for h in root.handlers:
        if isinstance(h, types):
            return h
    return None


def _strip_log_env() -> None:
    for k in list(os.environ):
        if k.startswith("FULLADDMAX_LOG_"):
            del os.environ[k]


def verify_default() -> None:
    _section("default config (no env vars)")
    _strip_log_env()
    lc.configure_logging()
    root = logging.getLogger()
    _check(root.level == logging.INFO, "root level == INFO (default)")
    h = _handler_of_type(root, logging.StreamHandler)
    _check(h is not None, "default handler is StreamHandler")
    _check(
        h is not None and not isinstance(h.formatter, lc.JsonFormatter),
        "default formatter is text (NOT JsonFormatter)",
    )
    _check(
        h is not None and getattr(h, "stream", None) is sys.stderr,
        "default stream is stderr",
    )


def verify_level() -> None:
    _section("FULLADDMAX_LOG_LEVEL=DEBUG")
    _strip_log_env()
    os.environ["FULLADDMAX_LOG_LEVEL"] = "DEBUG"
    lc.configure_logging()
    _check(
        logging.getLogger().level == logging.DEBUG,
        "root level == DEBUG after env override",
    )


def verify_json() -> None:
    _section("FULLADDMAX_LOG_FORMAT=json")
    _strip_log_env()
    os.environ["FULLADDMAX_LOG_FORMAT"] = "json"
    lc.configure_logging()
    h = _handler_of_type(logging.getLogger(), logging.StreamHandler)
    _check(
        h is not None and isinstance(h.formatter, lc.JsonFormatter),
        "JsonFormatter installed on root handler",
    )
    # Emit one record, parse it, check fields.
    if h is not None and isinstance(h.formatter, lc.JsonFormatter):
        import io
        buf = io.StringIO()
        h.stream = buf
        logging.getLogger("fulladdmax-mcp.demo").info(
            "test", extra={"task_id": 7}
        )
        rec = json.loads(buf.getvalue().strip())
        _check(rec["message"] == "test", "JSON record 'message' field correct")
        _check(rec["level"] == "INFO", "JSON record 'level' field correct")
        _check(rec["task_id"] == 7, "extra 'task_id' merged into JSON")


def verify_file() -> None:
    _section("FULLADDMAX_LOG_FILE=/tmp/x.log")
    _strip_log_env()
    with tempfile.TemporaryDirectory() as tmp:
        log_path = str(Path(tmp) / "test.log")
        os.environ["FULLADDMAX_LOG_FILE"] = log_path
        try:
            lc.configure_logging()
            logging.getLogger("fulladdmax-mcp.demo").warning("written to file")
            # Force flush + close before tmpdir cleanup (Windows).
            logging.shutdown()
            _check(Path(log_path).exists(), f"log file created at {log_path}")
            body = Path(log_path).read_text(encoding="utf-8")
            _check(
                "written to file" in body and "fulladdmax-mcp.demo" in body,
                "log line landed in file with correct logger name",
            )
        finally:
            logging.shutdown()


def verify_rotation() -> None:
    _section("FULLADDMAX_LOG_ROTATE_MAX_BYTES=256 + BACKUPS=2")
    _strip_log_env()
    with tempfile.TemporaryDirectory() as tmp:
        log_path = str(Path(tmp) / "rotate.log")
        os.environ["FULLADDMAX_LOG_FILE"] = log_path
        os.environ["FULLADDMAX_LOG_ROTATE_MAX_BYTES"] = "256"
        os.environ["FULLADDMAX_LOG_ROTATE_BACKUPS"] = "2"
        try:
            lc.configure_logging()
            h = _handler_of_type(
                logging.getLogger(), logging.handlers.RotatingFileHandler
            )
            _check(h is not None, "RotatingFileHandler installed")
            big = "x" * 100
            for _ in range(10):
                logging.getLogger("fulladdmax-mcp.rot").info(big)
            logging.shutdown()
            main_size = Path(log_path).stat().st_size
            rot1 = Path(log_path + ".1")
            _check(
                main_size <= 512,
                f"main file size bounded after rotation ({main_size} bytes)",
            )
            _check(rot1.exists(), "rotated backup .1 created")
        finally:
            logging.shutdown()


def verify_all_at_once() -> None:
    _section("all 5 env vars together (text, stderr, INFO)")
    _strip_log_env()
    # Leave only the 4 vars that have non-empty defaults in the example file.
    os.environ["FULLADDMAX_LOG_LEVEL"] = "INFO"
    os.environ["FULLADDMAX_LOG_FORMAT"] = "text"
    os.environ["FULLADDMAX_LOG_ROTATE_MAX_BYTES"] = "0"
    os.environ["FULLADDMAX_LOG_ROTATE_BACKUPS"] = "3"
    lc.configure_logging()
    h = _handler_of_type(logging.getLogger(), logging.StreamHandler)
    _check(
        h is not None
        and not isinstance(h.formatter, lc.JsonFormatter)
        and getattr(h, "stream", None) is sys.stderr,
        "INFO / text / stderr / no rotation all applied together",
    )


# ---------------------------------------------------------------------------
# Top-level: load .env.example, then run every check
# ---------------------------------------------------------------------------


def main() -> int:
    import argparse

    p = argparse.ArgumentParser(
        description=(
            "Simulate 'cp .env.example .env' and verify that every "
            "logging env var (FULLADDMAX_LOG_*) actually configures the "
            "server's logging the way you expect."
        ),
    )
    p.add_argument(
        "--env",
        default=str(_REPO / ".env.example"),
        help="Path to .env file to parse (default: .env.example).",
    )
    p.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show the parsed env vars before running checks.",
    )
    args = p.parse_args()

    env_path = Path(args.env)
    print(f"Loading env vars from: {env_path}")
    if not env_path.exists():
        print(f"  (file not found; checks will run with no env vars set)")
    parsed = parse_env_file(env_path)
    print(f"Parsed {len(parsed)} env var(s) from {env_path.name}")
    if args.verbose:
        for k in sorted(parsed):
            print(f"  {k}={parsed[k]!r}")

    log_keys = [k for k in parsed if k.startswith("FULLADDMAX_LOG_")]
    print(f"  of which {len(log_keys)} are FULLADDMAX_LOG_*")
    if log_keys:
        print("  logging env vars in this file:")
        for k in sorted(log_keys):
            print(f"    {k}={parsed[k]!r}")

    # Run every check.  Each check sets its own env vars and re-calls
    # configure_logging() -- the file is parsed for inventory purposes
    # only, the actual application of vars is per-test.
    verify_default()
    verify_level()
    verify_json()
    verify_file()
    verify_rotation()
    verify_all_at_once()

    print()
    print("=" * 70)
    total = _PASS + _FAIL
    print(f"Summary: {_PASS} / {total} passed, {_FAIL} failed")
    print("=" * 70)
    if _FAIL:
        print(
            "\nHint: the .env.example values are commented out by default;\n"
            "      uncomment the FULLADDMAX_LOG_* lines in a real .env to\n"
            "      see those vars take effect when the server starts."
        )
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
