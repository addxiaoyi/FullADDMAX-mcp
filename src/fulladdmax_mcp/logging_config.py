"""Centralised, configurable logging for FullADDMAX-mcp.

Three independent dimensions, each configurable via CLI flag, env var,
or default:

  1. **Level**     -- DEBUG / INFO / WARNING / ERROR (default: INFO)
  2. **Format**    -- ``text`` (human) / ``json`` (machine, for log aggregators)
  3. **Output**    -- stderr (default) / file path (optional rotation)

Precedence (highest first):  CLI flag > env var > built-in default.

Examples
========

CLI::

    fulladdmax-mcp --log-level DEBUG --log-format json
    fulladdmax-mcp --log-file /var/log/fulladdmax-mcp.log \\
                    --log-rotate-max-bytes 10485760 \\
                    --log-rotate-backups 5
    fulladdmax-mcp panel --serve --log-format text

Env vars (set in the host's environment / MCP config)::

    FULLADDMAX_LOG_LEVEL=DEBUG
    FULLADDMAX_LOG_FORMAT=json
    FULLADDMAX_LOG_FILE=/var/log/fulladdmax-mcp.log
    FULLADDMAX_LOG_ROTATE_MAX_BYTES=10485760
    FULLADDMAX_LOG_ROTATE_BACKUPS=5

Programmatic::

    from fulladdmax_mcp.logging_config import configure_logging
    configure_logging(level="DEBUG", fmt="json", file_path="/tmp/x.log")
    # ... any getLogger("fulladdmax_mcp.*") picks up the new config
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

#: Default text format (matches the historical format we shipped with).
DEFAULT_TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

#: Default JSON record fields.  Extras (extra={...}) are merged in.
DEFAULT_JSON_FIELDS: tuple[str, ...] = (
    "timestamp", "level", "logger", "message",
)

#: Env-var names.  Centralised so the README stays in sync.
ENV_LEVEL = "FULLADDMAX_LOG_LEVEL"
ENV_FORMAT = "FULLADDMAX_LOG_FORMAT"
ENV_FILE = "FULLADDMAX_LOG_FILE"
ENV_ROTATE_MAX_BYTES = "FULLADDMAX_LOG_ROTATE_MAX_BYTES"
ENV_ROTATE_BACKUPS = "FULLADDMAX_LOG_ROTATE_BACKUPS"

#: Valid values for ``--log-format``.
VALID_FORMATS: tuple[str, ...] = ("text", "json")
#: Valid values for ``--log-level`` (matches Python's level names).
VALID_LEVELS: tuple[str, ...] = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


# ---------------------------------------------------------------------------
# init_logging() sentinels for the ``env_file`` argument
# ---------------------------------------------------------------------------

#: Sentinel: auto-detect a .env file at well-known locations.
ENV_FILE_AUTO = "auto"
#: Sentinel: skip .env loading entirely (caller manages env vars itself).
ENV_FILE_NONE = "none"

#: Default locations tried by ``env_file=ENV_FILE_AUTO`` (in order).
DEFAULT_ENV_PATHS: tuple[str, ...] = (
    ".env",
    "~/.fulladdmax-mcp/.env",
    "~/.config/fulladdmax-mcp/.env",
)


# ---------------------------------------------------------------------------
# Resolver: CLI > env > default
# ---------------------------------------------------------------------------


def _resolve(cli_value: str | None, env_name: str, default: str) -> str:
    """Return the first non-empty value in (cli, env, default) order."""
    if cli_value is not None and str(cli_value) != "":
        return str(cli_value)
    env_val = os.environ.get(env_name, "").strip()
    if env_val:
        return env_val
    return default


def _resolve_int(cli_value: int | None, env_name: str, default: int) -> int:
    if cli_value is not None and cli_value > 0:
        return int(cli_value)
    env_val = os.environ.get(env_name, "").strip()
    if env_val:
        try:
            v = int(env_val)
            if v > 0:
                return v
        except ValueError:
            pass
    return default


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


class JsonFormatter(logging.Formatter):
    """Render a LogRecord as a single-line JSON object.

    Always emits ``DEFAULT_JSON_FIELDS``; any user-supplied
    ``extra={"foo": ...}`` to the log call is merged in.

    Strips ANSI / exception traceback formatting into a ``exc_info`` string
    so each line stays parseable.
    """

    def __init__(self, fields: tuple[str, ...] = DEFAULT_JSON_FIELDS) -> None:
        super().__init__()
        self._fields = fields

    def format(self, record: logging.LogRecord) -> str:
        # Build a flat dict; ``getattr`` so missing extras don't crash.
        out: dict[str, Any] = {}
        out["timestamp"] = self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z")
        out["level"] = record.levelname
        out["logger"] = record.name
        out["message"] = record.getMessage()
        # Standard exception info → string (not the default multi-line repr).
        if record.exc_info:
            out["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            out["stack_info"] = record.stack_info
        # Merge in any extra={...} fields the caller passed in.
        for key, val in record.__dict__.items():
            if key in (
                "name", "msg", "args", "levelname", "levelno", "pathname",
                "filename", "module", "exc_info", "exc_text", "stack_info",
                "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process", "message",
                "asctime", "taskName",
            ):
                continue
            if key.startswith("_"):
                continue
            out[key] = val
        # Sort keys for stable diffs in CI / log files.
        return json.dumps({k: out[k] for k in sorted(out)}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def configure_logging(
    level: str | None = None,
    *,
    fmt: str | None = None,
    file_path: str | None = None,
    max_bytes: int | None = None,
    backup_count: int | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> logging.Logger:
    """Reconfigure the root logger for FullADDMAX-mcp.

    Idempotent: calling this twice does NOT stack handlers.  All existing
    handlers on the root logger (and on the ``fulladdmax-mcp`` named
    logger) are torn down first.

    Parameters
    ----------
    level:
        DEBUG / INFO / WARNING / ERROR / CRITICAL.  Resolved from
        ``FULLADDMAX_LOG_LEVEL`` if None.
    fmt:
        ``text`` or ``json``.  Resolved from ``FULLADDMAX_LOG_FORMAT`` if
        None.
    file_path:
        Optional output file.  Resolved from ``FULLADDMAX_LOG_FILE`` if
        None.  ``None`` means stderr.
    max_bytes:
        File rotation threshold.  ``0`` / None disables rotation.
        Resolved from ``FULLADDMAX_LOG_ROTATE_MAX_BYTES``.
    backup_count:
        Number of rotated files to keep.  Default 3.
    cli_overrides:
        Optional dict of ``{"level": ..., "fmt": ..., "file_path": ...,
        "max_bytes": ..., "backup_count": ...}`` to pass through (the CLI
        arg parser uses this so a single ``configure_logging(**args)``
        call covers all flags).

    Returns
    -------
    The ``fulladdmax-mcp`` package logger (the one ``logging.getLogger``
    returns for ``__name__ == "fulladdmax-mcp"`` and any sub-logger).
    """
    overrides = cli_overrides or {}
    level = _resolve(overrides.get("level", level), ENV_LEVEL, "INFO").upper()
    fmt = _resolve(overrides.get("fmt", fmt), ENV_FORMAT, "text").lower()
    file_path = _resolve(overrides.get("file_path", file_path), ENV_FILE, "")
    max_bytes = _resolve_int(
        overrides.get("max_bytes", max_bytes), ENV_ROTATE_MAX_BYTES, 0
    )
    backup_count = _resolve_int(
        overrides.get("backup_count", backup_count), ENV_ROTATE_BACKUPS, 3
    )

    if level not in VALID_LEVELS:
        raise ValueError(
            f"invalid log level {level!r}; valid: {VALID_LEVELS}"
        )
    if fmt not in VALID_FORMATS:
        raise ValueError(
            f"invalid log format {fmt!r}; valid: {VALID_FORMATS}"
        )

    # 1. Tear down any existing handlers on the root + package loggers
    #    so this function is idempotent.
    for name in ("", "fulladdmax-mcp"):
        target = logging.getLogger(name)
        for h in list(target.handlers):
            target.removeHandler(h)
            try:
                h.close()
            except Exception:  # noqa: BLE001
                pass

    # 2. Build the new handler.
    formatter: logging.Formatter
    if fmt == "json":
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(DEFAULT_TEXT_FORMAT)

    handler: logging.Handler
    if file_path:
        # Rotating file handler if max_bytes > 0, else plain FileHandler.
        if max_bytes > 0:
            handler = logging.handlers.RotatingFileHandler(
                file_path,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
        else:
            handler = logging.FileHandler(file_path, encoding="utf-8")
    else:
        handler = logging.StreamHandler(stream=sys.stderr)

    handler.setFormatter(formatter)

    # 3. Install the handler ONLY on the root logger.  The package
    #    logger (and any ``fulladdmax-mcp.*`` sub-logger) inherits it
    #    via propagation, which is the standard Python logging pattern.
    #    Adding the handler to both would cause duplicate output because
    #    propagation re-fires on every ancestor.
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(level)

    pkg = logging.getLogger("fulladdmax-mcp")
    # No direct handler here; just set the level and ensure sub-loggers
    # inherit the root config.
    pkg.setLevel(level)
    pkg.propagate = True

    return pkg


def get_logger(name: str | None = None) -> logging.Logger:
    """Convenience: ``getLogger("fulladdmax-mcp" + ("." + name if name else ""))``."""
    if name is None or name == "":
        return logging.getLogger("fulladdmax-mcp")
    if name.startswith("fulladdmax-mcp"):
        return logging.getLogger(name)
    return logging.getLogger(f"fulladdmax-mcp.{name}")


# ---------------------------------------------------------------------------
# .env file parser + init_logging()
# ---------------------------------------------------------------------------

#: Regex used by :func:`_parse_env_file`.  Matches both ``KEY=value`` and
#: ``# KEY=value`` so we can also pick up commented-out examples for
#: inventory purposes.
_ENV_LINE_RE = re.compile(
    r"^\s*#?\s*"                    # optional leading # (commented candidate)
    r"([A-Z_][A-Z0-9_]*)"           # KEY
    r"\s*=\s*"
    r"(.*?)\s*$"                    # VALUE (non-greedy, trailing ws trimmed)
)


def _parse_env_file(path: str | Path) -> dict[str, str]:
    """Parse a .env file, returning ``{KEY: value}`` for assignable lines.

    Accepts both ``# KEY=value`` (commented) and ``KEY=value`` (active).
    Lines that are pure comments (no ``=``) are ignored.  Returns
    ``{}`` if the file doesn't exist.
    """
    p = Path(path).expanduser()
    out: dict[str, str] = {}
    if not p.exists() or not p.is_file():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        m = _ENV_LINE_RE.match(line)
        if not m:
            continue
        key, val = m.group(1), m.group(2)
        if val:  # only include non-empty values
            out[key] = val
    return out


def _load_env_files(
    env_file: str | Path | Iterable[str | Path] | None,
) -> list[Path]:
    """Resolve ``env_file`` argument to a list of files actually loaded.

    Rules:
      * ``ENV_FILE_NONE`` / ``None``  -> load nothing
      * ``ENV_FILE_AUTO``             -> try :data:`DEFAULT_ENV_PATHS`
      * a single path string / Path   -> try that one
      * iterable of paths             -> try each

    Files that don't exist are silently skipped.  Loaded values are
    written to ``os.environ`` ONLY for keys that are not already set
    (i.e. an explicit shell env var always wins over .env).
    """
    if env_file is None or env_file == ENV_FILE_NONE:
        return []
    if env_file == ENV_FILE_AUTO:
        candidates: Iterable[str | Path] = DEFAULT_ENV_PATHS
    elif isinstance(env_file, (str, Path)):
        candidates = [env_file]
    else:
        candidates = list(env_file)

    loaded: list[Path] = []
    for c in candidates:
        p = Path(c).expanduser()
        if not (p.exists() and p.is_file()):
            continue
        parsed = _parse_env_file(p)
        for k, v in parsed.items():
            # Shell env wins over .env: don't overwrite.
            os.environ.setdefault(k, v)
        loaded.append(p)
    return loaded


def init_logging(
    *,
    level: str | None = None,
    fmt: str | None = None,
    file_path: str | None = None,
    max_bytes: int | None = None,
    backup_count: int | None = None,
    env_file: str | Path | Iterable[str | Path] | None = ENV_FILE_AUTO,
) -> logging.Logger:
    """One-shot initialization for the FullADDMAX-mcp package logger.

    What it does, in order:

      1. Loads ``.env`` from disk (auto-detected or explicit).  The
         ``.env`` file only fills in env vars that are NOT already set
         in the calling shell -- so explicit shell exports always win.
      2. Calls :func:`configure_logging` with the union of explicit
         args + ``FULLADDMAX_LOG_*`` env vars + built-in defaults.

    Precedence (highest first):  explicit arg > env var > .env file
    value > built-in default.  ``.env`` is loaded with
    ``os.environ.setdefault`` so it can never stomp a more specific
    value.

    Parameters
    ----------
    level, fmt, file_path, max_bytes, backup_count:
        Same as :func:`configure_logging`.  Pass any of these to
        override the env-var values for the current process.
    env_file:
        ``"auto"`` (default) tries :data:`DEFAULT_ENV_PATHS`.
        ``"none"`` / ``None`` skips .env loading entirely.
        A path / Path loads that specific file.
        An iterable of paths loads each in order (later files do NOT
        override earlier ones because of ``setdefault``).

    Returns
    -------
    The configured ``fulladdmax-mcp`` package logger.

    Examples
    --------
    Library use (the common case)::

        from fulladdmax_mcp.logging_config import init_logging
        log = init_logging()              # auto-loads .env
        log.info("started", extra={"v": 1})

    CLI override (server entry point)::

        from fulladdmax_mcp.logging_config import init_logging
        log = init_logging(
            level=args.log_level,          # CLI wins over .env
            fmt=args.log_format,
            file_path=args.log_file,
            max_bytes=args.log_rotate_max_bytes,
            backup_count=args.log_rotate_backups,
            env_file="none",               # don't auto-load; caller chose CLI
        )

    Tests that need a clean env::

        from fulladdmax_mcp.logging_config import init_logging
        # explicitly load a fixture file
        log = init_logging(env_file="tests/fixtures/test.env")
    """
    _load_env_files(env_file)
    return configure_logging(
        level=level,
        fmt=fmt,
        file_path=file_path,
        max_bytes=max_bytes,
        backup_count=backup_count,
    )


__all__ = [
    "DEFAULT_TEXT_FORMAT",
    "DEFAULT_JSON_FIELDS",
    "DEFAULT_ENV_PATHS",
    "ENV_FILE_AUTO",
    "ENV_FILE_NONE",
    "ENV_LEVEL",
    "ENV_FORMAT",
    "ENV_FILE",
    "ENV_ROTATE_MAX_BYTES",
    "ENV_ROTATE_BACKUPS",
    "VALID_FORMATS",
    "VALID_LEVELS",
    "JsonFormatter",
    "configure_logging",
    "get_logger",
    "init_logging",
]
