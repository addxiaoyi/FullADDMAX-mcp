"""One-command installer for FullADDMAX-mcp into supported AI IDEs.

NOTE: This file is a mirror of ``scripts/install_to_ide.py``. They must be
kept in sync. The ``scripts/`` copy is convenient for ``python scripts/...``
during development; this ``src/`` copy is what gets installed as the
``fulladdmax-install`` console script by ``pip install -e .``.

Detects which AI IDEs are installed on the current machine and writes the
correct MCP server entry into each one's configuration file. Supports:

    * Claude Desktop        (claude_desktop_config.json)
    * Cursor                (~/.cursor/mcp.json)
    * Trae                  (~/.trae/mcp.json)
    * Continue.dev          (~/.continue/config.json or config.yaml)
    * Codex CLI             (~/.codex/config.toml)

Usage::

    # Interactive: detect installed IDEs and ask which to configure
    fulladdmax-install

    # Explicit
    fulladdmax-install --ide claude,cursor,codex \\
        --base-url https://api.openai.com/v1 \\
        --api-key sk-... \\
        --model gpt-4o-mini

    # HTTP transport (point the IDE at a running HTTP server)
    fulladdmax-install --ide cursor --url http://127.0.0.1:8000/mcp

    # Dry run
    fulladdmax-install --ide claude --dry-run

    # Remove the entry
    fulladdmax-install --ide claude --uninstall
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

IDE = Literal["claude", "cursor", "trae", "continue", "codex"]

ALL_IDES: tuple[IDE, ...] = ("claude", "cursor", "trae", "continue", "codex")


@dataclass
class InstallResult:
    ide: str
    config_path: Path
    status: Literal["installed", "updated", "skipped", "uninstalled", "error", "missing"]
    message: str = ""


@dataclass
class Installer:
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    server_name: str = "fulladdmax"
    timeout: float = 60.0
    max_tokens: int = 2048
    # If set, the IDE entry uses {url: ...} instead of {command, env}
    http_url: str | None = None
    uninstall: bool = False
    dry_run: bool = False

    # ----- IDE detection ---------------------------------------------------

    def detect_installed_ides(self) -> dict[IDE, Path]:
        """Return a map of detected IDEs to the config file path that
        *would* be written. Missing files are still returned (with a hint)
        so the caller can opt to create them.
        """
        home = Path.home()
        results: dict[IDE, Path] = {}

        if sys.platform == "win32":
            appdata = Path(os.environ.get("APPDATA", str(home / "AppData/Roaming")))
            claude = appdata / "Claude" / "claude_desktop_config.json"
        elif sys.platform == "darwin":
            claude = home / "Library/Application Support/Claude/claude_desktop_config.json"
        else:
            claude = home / ".config/Claude/claude_desktop_config.json"
        results["claude"] = claude

        # Cursor / Trae / Continue / Codex: ~/.X/mcp.json (or config.{json,yaml,toml})
        results["cursor"] = home / ".cursor" / "mcp.json"
        results["trae"] = home / ".trae" / "mcp.json"
        results["continue"] = _first_existing(
            home / ".continue" / "config.yaml",
            home / ".continue" / "config.json",
        )
        results["codex"] = home / ".codex" / "config.toml"
        return results

    # ----- Per-IDE writers ------------------------------------------------

    def install(self, ide: IDE) -> InstallResult:
        if ide not in ALL_IDES:
            return InstallResult(ide, Path(), "error", f"unsupported IDE: {ide}")

        paths = self.detect_installed_ides()
        path = paths[ide]
        try:
            if ide == "claude":
                return self._install_json(
                    ide=ide, path=path,
                    container_key="mcpServers",
                    entry=self._claude_entry(),
                )
            if ide == "cursor":
                return self._install_json(
                    ide=ide, path=path,
                    container_key="mcpServers",
                    entry=self._cursor_entry(),
                )
            if ide == "trae":
                return self._install_json(
                    ide=ide, path=path,
                    container_key="mcpServers",
                    entry=self._cursor_entry(),  # Trae shares Cursor's schema
                )
            if ide == "continue":
                return self._install_continue(path)
            if ide == "codex":
                return self._install_codex(path)
        except Exception as e:  # noqa: BLE001
            return InstallResult(ide, path, "error", f"{type(e).__name__}: {e}")
        return InstallResult(ide, path, "error", "unreachable")

    # ----- helpers --------------------------------------------------------

    def _claude_entry(self) -> dict[str, Any]:
        if self.http_url:
            return {"url": self.http_url}
        return {
            "command": "fulladdmax-mcp",
            "env": {
                "FULLADDMAX_BASE_URL": self.base_url,
                "FULLADDMAX_API_KEY": self.api_key,
                "FULLADDMAX_MODEL": self.model,
                "FULLADDMAX_TIMEOUT": str(self.timeout),
                "FULLADDMAX_MAX_TOKENS": str(self.max_tokens),
            },
        }

    def _cursor_entry(self) -> dict[str, Any]:
        # Cursor / Trae schema: same as Claude
        return self._claude_entry()

    def _install_json(
        self,
        *,
        ide: IDE,
        path: Path,
        container_key: str,
        entry: dict[str, Any],
    ) -> InstallResult:
        if self.uninstall:
            target: dict[str, Any] = {}
        else:
            target = entry

        if path.exists():
            try:
                raw = path.read_text(encoding="utf-8")
                cfg: dict[str, Any] = json.loads(raw) if raw.strip() else {}
            except (json.JSONDecodeError, OSError) as e:
                return InstallResult(ide, path, "error", f"failed to read: {e}")
        else:
            cfg = {}

        container = cfg.setdefault(container_key, {})
        if not isinstance(container, dict):
            return InstallResult(ide, path, "error", f"{container_key!r} is not an object")

        status: Literal["installed", "updated", "skipped", "uninstalled"]
        if self.uninstall:
            if self.server_name in container:
                container.pop(self.server_name)
                status = "uninstalled"
            else:
                return InstallResult(ide, path, "skipped", f"'{self.server_name}' not present")
        else:
            status = "updated" if self.server_name in container else "installed"
            container[self.server_name] = target

        if not self.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return InstallResult(ide, path, status, f"container_key={container_key}")

    def _install_continue(self, path: Path) -> InstallResult:
        if self.uninstall:
            target_entry: dict[str, Any] | None = None
        else:
            target_entry = self._cursor_entry()

        if path.suffix == ".yaml" or path.suffix == ".yml":
            return self._install_continue_yaml(path, target_entry)
        # default to JSON
        if not path.exists() and (Path.home() / ".continue" / "config.yaml").exists():
            path = Path.home() / ".continue" / "config.yaml"
            return self._install_continue_yaml(path, target_entry)
        return self._install_continue_json(path, target_entry)

    def _install_continue_json(
        self, path: Path, entry: dict[str, Any] | None
    ) -> InstallResult:
        if path.exists():
            try:
                raw = path.read_text(encoding="utf-8")
                cfg: dict[str, Any] = json.loads(raw) if raw.strip() else {}
            except (json.JSONDecodeError, OSError) as e:
                return InstallResult("continue", path, "error", f"failed to read: {e}")
        else:
            cfg = {}

        servers = cfg.setdefault("experimental", {}).setdefault(
            "modelContextProtocolServers", []
        )
        if not isinstance(servers, list):
            return InstallResult("continue", path, "error", "modelContextProtocolServers is not a list")

        if self.uninstall:
            new_servers = [s for s in servers if not (isinstance(s, dict) and s.get("name") == self.server_name)]
            if len(new_servers) == len(servers):
                return InstallResult("continue", path, "skipped", f"'{self.server_name}' not present")
            cfg["experimental"]["modelContextProtocolServers"] = new_servers
            status: Literal["installed", "updated", "skipped", "uninstalled"] = "uninstalled"
        else:
            assert entry is not None
            existing_idx = next(
                (i for i, s in enumerate(servers) if isinstance(s, dict) and s.get("name") == self.server_name),
                None,
            )
            if existing_idx is None:
                servers.append({"name": self.server_name, **entry})
                status = "installed"
            else:
                servers[existing_idx] = {"name": self.server_name, **entry}
                status = "updated"

        if not self.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return InstallResult("continue", path, status)

    def _install_continue_yaml(
        self, path: Path, entry: dict[str, Any] | None
    ) -> InstallResult:
        # Hand-rolled minimal YAML manipulation: only touches the
        # ``mcpServers:`` block, leaves other keys alone. We do not
        # depend on PyYAML to keep the installer zero-dep.
        if path.exists():
            text = path.read_text(encoding="utf-8")
        else:
            text = ""

        new_block_lines = _yaml_block_for_server(self.server_name, entry, self.uninstall)
        new_text = _replace_yaml_mcp_block(text, new_block_lines, uninstall=self.uninstall)

        if not self.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(new_text, encoding="utf-8")
        return InstallResult("continue", path, "updated" if not self.uninstall else "uninstalled")

    def _install_codex(self, path: Path) -> InstallResult:
        if path.exists():
            text = path.read_text(encoding="utf-8")
        else:
            text = ""

        block = _toml_block_for_server(self.server_name, self._cursor_entry(), self.uninstall)
        if self.uninstall:
            new_text = _remove_toml_table(text, self.server_name)
            status: Literal["installed", "updated", "skipped", "uninstalled"] = "uninstalled"
        else:
            new_text = _replace_toml_table(text, self.server_name, block)
            status = "installed" if self.server_name not in text else "updated"

        if not self.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(new_text, encoding="utf-8")
        return InstallResult("codex", path, status)


# ---------------------------------------------------------------------------
# YAML helpers (no PyYAML dependency)
# ---------------------------------------------------------------------------


def _yaml_block_for_server(
    name: str, entry: dict[str, Any] | None, uninstall: bool
) -> list[str]:
    """Render a YAML block for one ``mcpServers:`` entry (2-space indent)."""
    if uninstall or entry is None:
        # Sentinel: empty mapping will be filtered out by the replace step.
        return [f"  - name: {name}"]

    lines = [f"  - name: {name}"]
    if "url" in entry:
        lines.append(f"    url: {entry['url']}")
    else:
        lines.append(f"    command: {entry.get('command', 'fulladdmax-mcp')}")
        env = entry.get("env", {})
        if env:
            lines.append("    env:")
            for k, v in env.items():
                # Quote value to be safe
                lines.append(f"      {k}: \"{v}\"")
    return lines


def _replace_yaml_mcp_block(
    text: str, new_block_lines: list[str], uninstall: bool = False
) -> str:
    """Replace (or insert) a single ``- name: <server>`` entry under
    ``mcpServers:`` in a YAML file. Leaves other content untouched.

    When ``uninstall`` is True the matching entry is removed and
    ``new_block_lines`` is ignored.
    """
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    found_mcp_servers = False
    inserted = False
    target_name = new_block_lines[0].split("name:", 1)[1].strip() if new_block_lines else None
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped == "mcpServers:" or stripped.startswith("mcpServers:"):
            found_mcp_servers = True
            out.append(line)
            i += 1
            # copy existing entries until indent drops back to 0
            while i < len(lines):
                cur = lines[i]
                if not cur.strip():
                    out.append(cur)
                    i += 1
                    continue
                leading = len(cur) - len(cur.lstrip())
                if leading <= 0:
                    break
                # is this the entry we're replacing?
                if target_name and cur.strip().startswith(f"- name: {target_name}"):
                    # skip until indent drops back
                    j = i + 1
                    while j < len(lines):
                        nxt = lines[j]
                        if not nxt.strip():
                            j += 1
                            continue
                        if (len(nxt) - len(nxt.lstrip())) <= 2:
                            break
                        j += 1
                    i = j
                else:
                    out.append(cur)
                    i += 1
            # insert new block
            if not inserted:
                out.extend(new_block_lines)
                inserted = True
            continue
        out.append(line)
        i += 1

    if not found_mcp_servers:
        if out and out[-1].strip():
            out.append("")
        out.append("mcpServers:")
        out.extend(new_block_lines)

    if not inserted and not uninstall:
        # entry was already there and we replaced it; nothing to add
        pass

    return "\n".join(out) + ("\n" if text.endswith("\n") or not text else "")


# ---------------------------------------------------------------------------
# TOML helpers (no tomli_w dependency)
# ---------------------------------------------------------------------------


def _toml_block_for_server(name: str, entry: dict[str, Any], uninstall: bool) -> str:
    if uninstall or not entry:
        return ""
    lines = [f'[[mcp_servers]]\nname = "{name}"']
    if "url" in entry:
        lines.append(f'url = "{entry["url"]}"')
    else:
        lines.append(f'command = "{entry.get("command", "fulladdmax-mcp")}"')
        env = entry.get("env", {})
        if env:
            env_str = ", ".join(f'{k} = "{v}"' for k, v in env.items())
            lines.append(f"env = {{ {env_str} }}")
    return "\n".join(lines) + "\n"


def _replace_toml_table(text: str, name: str, new_block: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    replaced = False
    while i < len(lines):
        line = lines[i]
        if line.strip() == "[[mcp_servers]]":
            # peek at the next non-empty line for `name = "..."`
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and lines[j].strip().startswith("name") and f'"{name}"' in lines[j]:
                # skip the whole table until next [[ ... ]] or end
                i = j
                while i < len(lines):
                    if lines[i].strip().startswith("[["):
                        break
                    i += 1
                replaced = True
                if new_block:
                    out.append(new_block.rstrip())
                    out.append("")
                continue
        out.append(line)
        i += 1
    if not replaced:
        if out and out[-1].strip():
            out.append("")
        out.append(new_block.rstrip())
        out.append("")
    return "\n".join(out) + ("\n" if text.endswith("\n") or not text else "")


def _remove_toml_table(text: str, name: str) -> str:
    return _replace_toml_table(text, name, "")


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def _first_existing(*paths: Path) -> Path:
    for p in paths:
        if p.exists():
            return p
    return paths[0]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fulladdmax-install",
        description=(
            "Install/uninstall FullADDMAX-mcp into Claude Desktop, Cursor, Trae, "
            "Continue.dev, and/or Codex CLI configuration files."
        ),
    )
    p.add_argument(
        "--ide",
        default="auto",
        help=(
            "Comma-separated list of IDEs to configure. "
            f"One of: {','.join(ALL_IDES)}. Default 'auto' detects installed IDEs."
        ),
    )
    p.add_argument("--base-url", default="https://api.openai.com/v1", help="OpenAI-compatible base URL.")
    p.add_argument("--api-key", default=os.environ.get("FULLADDMAX_API_KEY", ""), help="API key.")
    p.add_argument("--model", default=os.environ.get("FULLADDMAX_MODEL", "gpt-4o-mini"), help="Model name.")
    p.add_argument("--timeout", type=float, default=60.0, help="Per-request timeout in seconds.")
    p.add_argument("--max-tokens", type=int, default=2048, help="Max tokens per LLM response.")
    p.add_argument("--server-name", default="fulladdmax", help="Name to register the server under.")
    p.add_argument(
        "--url",
        default=None,
        help=(
            "If set, write a URL-based entry pointing at a running HTTP server "
            "(e.g. http://127.0.0.1:8000/mcp). Overrides --base-url/--api-key/--model."
        ),
    )
    p.add_argument("--uninstall", action="store_true", help="Remove the server entry instead of installing.")
    p.add_argument("--dry-run", action="store_true", help="Print what would happen without writing files.")
    p.add_argument("--list", action="store_true", help="List detected IDEs and config paths, then exit.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    installer = Installer(
        base_url=args.base_url,
        api_key=args.api_key,
        model=args.model,
        server_name=args.server_name,
        timeout=args.timeout,
        max_tokens=args.max_tokens,
        http_url=args.url,
        uninstall=args.uninstall,
        dry_run=args.dry_run,
    )
    detected = installer.detect_installed_ides()

    if args.list:
        for ide, path in detected.items():
            mark = "✓" if path.exists() else "·"
            print(f"  {mark} {ide:<8} {path}")
        return 0

    if args.ide == "auto":
        ides: list[IDE] = [ide for ide, path in detected.items() if path.exists() or ide in {"claude", "cursor", "codex"}]
        if not ides:
            print("No supported IDE detected. Pass --ide <name> explicitly.", file=sys.stderr)
            return 1
    else:
        ides = []
        for raw in args.ide.split(","):
            ide = raw.strip().lower()
            if ide not in ALL_IDES:
                print(f"Unknown IDE: {ide!r}. Valid: {','.join(ALL_IDES)}", file=sys.stderr)
                return 2
            ides.append(ide)

    if not installer.uninstall and not installer.http_url and not installer.api_key:
        print(
            "WARNING: --api-key is empty and --url is not set. The IDE will register the server "
            "but every workflow tool will fail with 'LLM not configured' until you set env.",
            file=sys.stderr,
        )

    results = [installer.install(ide) for ide in ides]

    rc = 0
    for r in results:
        suffix = f" ({r.message})" if r.message else ""
        action = "[DRY-RUN] " if args.dry_run else ""
        print(f"  {action}{r.ide:<8} {r.status:<11} {r.config_path}{suffix}")
        if r.status == "error":
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
