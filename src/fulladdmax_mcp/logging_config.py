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
import sys
from typing import Any

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


__all__ = [
    "DEFAULT_TEXT_FORMAT",
    "DEFAULT_JSON_FIELDS",
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
]
