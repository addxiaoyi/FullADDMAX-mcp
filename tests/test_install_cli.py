"""Tests for the one-command IDE installer."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from fulladdmax_mcp import _install_cli as cli
from fulladdmax_mcp._install_cli import (
    Installer,
    _replace_toml_table,
    _replace_yaml_mcp_block,
    _toml_block_for_server,
    _yaml_block_for_server,
    main,
)


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Make ``Path.home()`` and ``$APPDATA`` (on Windows) point at ``tmp_path``."""
    monkeypatch.setattr(cli.Path, "home", lambda: tmp_path)
    if sys.platform == "win32":
        monkeypatch.setenv("APPDATA", str(tmp_path / "AppData/Roaming"))
    return tmp_path


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def test_detect_installed_ides_returns_all(fake_home):
    detected = Installer().detect_installed_ides()
    assert set(detected.keys()) == {"claude", "cursor", "trae", "continue", "codex"}
    for ide, p in detected.items():
        assert isinstance(p, Path)


# ---------------------------------------------------------------------------
# JSON installers (Claude / Cursor / Trae)
# ---------------------------------------------------------------------------


def test_install_claude_creates_file(fake_home):
    installer = Installer(api_key="sk-test", model="m-1")
    res = installer.install("claude")
    assert res.status in ("installed", "updated")
    assert res.config_path.exists()
    cfg = json.loads(res.config_path.read_text(encoding="utf-8"))
    assert "fulladdmax" in cfg["mcpServers"]
    entry = cfg["mcpServers"]["fulladdmax"]
    assert entry["command"] == "fulladdmax-mcp"
    assert entry["env"]["FULLADDMAX_API_KEY"] == "sk-test"
    assert entry["env"]["FULLADDMAX_MODEL"] == "m-1"


def test_install_cursor_uses_cursor_schema(fake_home):
    installer = Installer(api_key="sk-abc")
    res = installer.install("cursor")
    assert res.config_path.exists()
    cfg = json.loads(res.config_path.read_text(encoding="utf-8"))
    assert "fulladdmax" in cfg["mcpServers"]


def test_install_preserves_existing_keys(fake_home):
    path = fake_home / ".cursor" / "mcp.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "other-server": {"command": "other", "env": {}},
                }
            }
        ),
        encoding="utf-8",
    )
    installer = Installer(api_key="sk-test")
    res = installer.install("cursor")
    assert res.status == "installed"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    assert "other-server" in cfg["mcpServers"]
    assert "fulladdmax" in cfg["mcpServers"]


def test_update_when_already_present(fake_home):
    installer = Installer(api_key="sk-old")
    installer.install("claude")
    installer2 = Installer(api_key="sk-new")
    res = installer2.install("claude")
    assert res.status == "updated"
    cfg = json.loads(res.config_path.read_text(encoding="utf-8"))
    assert cfg["mcpServers"]["fulladdmax"]["env"]["FULLADDMAX_API_KEY"] == "sk-new"


def test_uninstall_removes_entry(fake_home):
    installer = Installer(api_key="sk-test")
    installer.install("claude")
    installer2 = Installer(uninstall=True)
    res = installer2.install("claude")
    assert res.status == "uninstalled"
    cfg = json.loads(res.config_path.read_text(encoding="utf-8"))
    assert "fulladdmax" not in cfg["mcpServers"]


def test_uninstall_skips_when_absent(fake_home):
    installer = Installer(uninstall=True)
    res = installer.install("claude")
    assert res.status == "skipped"


def test_install_http_mode_uses_url(fake_home):
    installer = Installer(http_url="http://127.0.0.1:8000/mcp")
    res = installer.install("cursor")
    cfg = json.loads(res.config_path.read_text(encoding="utf-8"))
    assert cfg["mcpServers"]["fulladdmax"] == {"url": "http://127.0.0.1:8000/mcp"}


def test_dry_run_does_not_write(fake_home):
    installer = Installer(api_key="sk-test", dry_run=True)
    res = installer.install("claude")
    assert res.status == "installed"
    assert not res.config_path.exists()


# ---------------------------------------------------------------------------
# Continue YAML helpers
# ---------------------------------------------------------------------------


def test_yaml_block_for_server_with_env():
    block = _yaml_block_for_server(
        "fulladdmax",
        {"command": "fulladdmax-mcp", "env": {"FULLADDMAX_API_KEY": "sk-x"}},
        uninstall=False,
    )
    text = "\n".join(block)
    assert "  - name: fulladdmax" in text
    assert "command: fulladdmax-mcp" in text
    assert 'FULLADDMAX_API_KEY: "sk-x"' in text


def test_yaml_block_for_server_with_url():
    block = _yaml_block_for_server(
        "fulladdmax", {"url": "http://x/mcp"}, uninstall=False
    )
    text = "\n".join(block)
    assert "url: http://x/mcp" in text


def test_replace_yaml_mcp_block_inserts_new():
    text = "name: foo\nmcpServers:\n  - name: other\n    command: other\n"
    new_lines = _yaml_block_for_server(
        "fulladdmax", {"command": "fulladdmax-mcp"}, uninstall=False
    )
    out = _replace_yaml_mcp_block(text, new_lines)
    assert "- name: fulladdmax" in out
    assert "- name: other" in out
    assert "command: fulladdmax-mcp" in out


def test_replace_yaml_mcp_block_replaces_existing():
    text = (
        "mcpServers:\n"
        "  - name: fulladdmax\n"
        "    command: old-cmd\n"
        "  - name: other\n"
        "    command: other\n"
    )
    new_lines = _yaml_block_for_server(
        "fulladdmax", {"command": "new-cmd"}, uninstall=False
    )
    out = _replace_yaml_mcp_block(text, new_lines)
    assert "new-cmd" in out
    assert "old-cmd" not in out
    assert "- name: other" in out


def test_replace_yaml_mcp_block_appends_when_no_mcp_servers():
    text = "name: foo\n"
    new_lines = _yaml_block_for_server(
        "fulladdmax", {"command": "x"}, uninstall=False
    )
    out = _replace_yaml_mcp_block(text, new_lines)
    assert "mcpServers:" in out
    assert "- name: fulladdmax" in out


def test_replace_yaml_mcp_block_uninstall():
    text = (
        "mcpServers:\n"
        "  - name: fulladdmax\n"
        "    command: x\n"
        "  - name: other\n"
        "    command: y\n"
    )
    new_lines = _yaml_block_for_server("fulladdmax", None, uninstall=True)
    out = _replace_yaml_mcp_block(text, new_lines)
    assert "command: x" not in out
    assert "command: y" in out


# ---------------------------------------------------------------------------
# Codex TOML helpers
# ---------------------------------------------------------------------------


def test_toml_block_for_server_with_env():
    block = _toml_block_for_server(
        "fulladdmax",
        {"command": "fulladdmax-mcp", "env": {"FULLADDMAX_API_KEY": "sk-x"}},
        uninstall=False,
    )
    assert '[[mcp_servers]]' in block
    assert 'name = "fulladdmax"' in block
    assert 'command = "fulladdmax-mcp"' in block
    assert 'FULLADDMAX_API_KEY = "sk-x"' in block


def test_toml_block_for_server_with_url():
    block = _toml_block_for_server("fulladdmax", {"url": "http://x/mcp"}, uninstall=False)
    assert 'url = "http://x/mcp"' in block


def test_replace_toml_table_inserts_new():
    text = 'other_key = "x"\n'
    block = _toml_block_for_server(
        "fulladdmax", {"command": "fulladdmax-mcp"}, uninstall=False
    )
    out = _replace_toml_table(text, "fulladdmax", block)
    assert "[[mcp_servers]]" in out
    assert 'name = "fulladdmax"' in out


def test_replace_toml_table_replaces_existing():
    text = (
        '[[mcp_servers]]\n'
        'name = "fulladdmax"\n'
        'command = "old"\n'
        '\n'
        '[[mcp_servers]]\n'
        'name = "other"\n'
        'command = "other-cmd"\n'
    )
    block = _toml_block_for_server(
        "fulladdmax", {"command": "new"}, uninstall=False
    )
    out = _replace_toml_table(text, "fulladdmax", block)
    assert "command = \"new\"" in out
    assert "command = \"old\"" not in out
    assert "other-cmd" in out  # untouched


def test_remove_toml_table():
    text = (
        '[[mcp_servers]]\n'
        'name = "fulladdmax"\n'
        'command = "x"\n'
        '\n'
        '[[mcp_servers]]\n'
        'name = "other"\n'
        'command = "y"\n'
    )
    out = _replace_toml_table(text, "fulladdmax", "")
    assert "name = \"fulladdmax\"" not in out
    assert "name = \"other\"" in out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_list(fake_home, capsys):
    rc = main(["--list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "claude" in out
    assert "cursor" in out
    assert "codex" in out


def test_cli_explicit_ide(fake_home, capsys):
    rc = main(["--ide", "cursor", "--api-key", "sk-test", "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "cursor" in out
    assert "DRY-RUN" in out


def test_cli_unknown_ide(fake_home, capsys):
    rc = main(["--ide", "webrtc"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "Unknown IDE" in err


def test_cli_uninstall(fake_home, capsys):
    main(["--ide", "cursor", "--api-key", "sk-test"])
    rc = main(["--ide", "cursor", "--uninstall"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "uninstalled" in out


def test_cli_warns_when_no_key(fake_home, capsys, monkeypatch):
    monkeypatch.delenv("FULLADDMAX_API_KEY", raising=False)
    rc = main(["--ide", "cursor"])
    err = capsys.readouterr().err
    assert rc == 0
    assert "WARNING" in err
    assert "api-key" in err.lower()
