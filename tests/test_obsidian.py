"""Tests for the Obsidian vault integration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fulladdmax_mcp.obsidian import (
    Frontmatter,
    Vault,
    VaultError,
    _dump_simple_yaml,
    append_note_tool,
    list_notes_tool,
    parse_note,
    read_note_tool,
    search_notes_tool,
    write_note_tool,
)


# ---------------------------------------------------------------------------
# Frontmatter parser
# ---------------------------------------------------------------------------


def test_parse_note_no_frontmatter():
    fm = parse_note("hello world\n")
    assert fm.fields == {}
    assert fm.body == "hello world\n"


def test_parse_note_simple_frontmatter():
    text = "---\ntitle: My Note\ntags: work\n---\n# heading\nbody\n"
    fm = parse_note(text)
    assert fm.fields == {"title": "My Note", "tags": "work"}
    assert fm.body == "# heading\nbody\n"


def test_parse_note_quoted_string():
    text = '---\ntitle: "Hello: World"\n---\nbody\n'
    fm = parse_note(text)
    assert fm.fields == {"title": "Hello: World"}


def test_parse_note_numbers_and_bools():
    text = "---\ncount: 42\nratio: 1.5\nactive: true\ndraft: false\nnothing: null\n---\nbody"
    fm = parse_note(text)
    assert fm.fields == {
        "count": 42,
        "ratio": 1.5,
        "active": True,
        "draft": False,
        "nothing": None,
    }


def test_parse_note_block_list():
    text = "---\ntags:\n  - python\n  - mcp\n  - obsidian\n---\nbody"
    fm = parse_note(text)
    assert fm.fields == {"tags": ["python", "mcp", "obsidian"]}


def test_parse_note_flow_list():
    text = '---\ntags: [a, b, "c d"]\n---\nbody'
    fm = parse_note(text)
    assert fm.fields == {"tags": ["a", "b", "c d"]}


def test_parse_note_nested_mapping():
    text = "---\nauthor:\n  name: Alice\n  email: a@example.com\n---\nbody"
    fm = parse_note(text)
    assert fm.fields == {
        "author": {"name": "Alice", "email": "a@example.com"}
    }


def test_parse_note_with_comments():
    text = "---\n# a comment\ntitle: Note  # inline comment\n---\nbody"
    fm = parse_note(text)
    assert fm.fields == {"title": "Note"}


def test_parse_note_block_scalar():
    text = "---\ndescription: |\n  multi\n  line\n  body\n---\nactual"
    fm = parse_note(text)
    assert fm.fields["description"] == "multi\nline\nbody"
    assert fm.body == "actual"


def test_parse_note_unicode():
    text = "---\ntitle: 中文标题\ntags: [笔记, 工具]\n---\n正文"
    fm = parse_note(text)
    assert fm.fields == {"title": "中文标题", "tags": ["笔记", "工具"]}
    assert fm.body == "正文"


def test_parse_note_roundtrip():
    text = "---\ntitle: t\ncount: 3\ntags: [a, b]\n---\nbody"
    fm = parse_note(text)
    rendered = fm.to_markdown()
    fm2 = parse_note(rendered)
    assert fm2.fields == fm.fields
    assert fm2.body == fm.body


def test_dump_simple_yaml_list():
    text = _dump_simple_yaml({"tags": ["a", "b", "c"]})
    parsed = parse_note("---\n" + text + "---\nx")
    assert parsed.fields == {"tags": ["a", "b", "c"]}


def test_dump_simple_yaml_nested_dict():
    text = _dump_simple_yaml({"author": {"name": "A", "age": 1}})
    parsed = parse_note("---\n" + text + "---\nx")
    assert parsed.fields == {"author": {"name": "A", "age": 1}}


def test_dump_simple_yaml_quotes_special_chars():
    text = _dump_simple_yaml({"title": 'has: colon and "quote"'})
    parsed = parse_note("---\n" + text + "---\nx")
    assert parsed.fields["title"] == 'has: colon and "quote"'


def test_parse_note_malformed_raises():
    with pytest.raises(VaultError, match="syntax error"):
        parse_note("---\nnot yaml at all\n---\nbody")


# ---------------------------------------------------------------------------
# Vault safety
# ---------------------------------------------------------------------------


def test_vault_init_missing_path(tmp_path):
    with pytest.raises(VaultError, match="does not exist"):
        Vault(tmp_path / "nope")


def test_safe_resolve_rejects_absolute(tmp_path):
    v = Vault(tmp_path)
    for bad in ("/etc/passwd", "C:/Windows", "\\\\server\\share"):
        with pytest.raises(VaultError, match="absolute paths|escape"):
            v._safe_resolve(bad)


def test_safe_resolve_rejects_traversal(tmp_path):
    v = Vault(tmp_path)
    for bad in ("../etc/passwd", "foo/../../bar", ".."):
        with pytest.raises(VaultError, match="traversal|escape"):
            v._safe_resolve(bad)


def test_safe_resolve_allows_nested(tmp_path):
    v = Vault(tmp_path)
    target = v._safe_resolve("Daily/2026-06-26.md")
    assert target == (tmp_path / "Daily" / "2026-06-26.md").resolve()


# ---------------------------------------------------------------------------
# Vault CRUD
# ---------------------------------------------------------------------------


def _make_vault(tmp_path: Path) -> Vault:
    v = Vault(tmp_path)
    (tmp_path / "Daily").mkdir()
    (tmp_path / "Daily" / "2026-06-25.md").write_text(
        "---\ntags: [work, daily]\n---\n# 2026-06-25\n\nold entry\n", encoding="utf-8"
    )
    (tmp_path / "Projects").mkdir()
    (tmp_path / "Projects" / "alpha.md").write_text(
        "---\nstatus: active\n---\n# Alpha\n\nbody\n", encoding="utf-8"
    )
    (tmp_path / "Projects" / "beta.md").write_text(
        "# Beta\n\nno frontmatter body\n", encoding="utf-8"
    )
    (tmp_path / "ignore.txt").write_text("not a note", encoding="utf-8")
    return v


def test_vault_list_notes(tmp_path):
    v = _make_vault(tmp_path)
    notes = v.list_notes()
    assert sorted(notes) == [
        "Daily/2026-06-25.md",
        "Projects/alpha.md",
        "Projects/beta.md",
    ]


def test_vault_list_notes_in_folder(tmp_path):
    v = _make_vault(tmp_path)
    notes = v.list_notes(folder="Projects")
    assert sorted(notes) == ["Projects/alpha.md", "Projects/beta.md"]


def test_vault_list_notes_ignores_non_md(tmp_path):
    v = _make_vault(tmp_path)
    notes = v.list_notes()
    assert all(n.endswith(".md") for n in notes)


def test_vault_list_notes_respects_limit(tmp_path):
    v = _make_vault(tmp_path)
    notes = v.list_notes(limit=1)
    assert len(notes) == 1


def test_vault_read_note_with_frontmatter(tmp_path):
    v = _make_vault(tmp_path)
    note = v.read_note("Projects/alpha.md")
    assert note.frontmatter == {"status": "active"}
    assert "# Alpha" in note.body


def test_vault_read_note_without_frontmatter(tmp_path):
    v = _make_vault(tmp_path)
    note = v.read_note("Projects/beta.md")
    assert note.frontmatter == {}
    assert "no frontmatter" in note.body


def test_vault_read_note_missing(tmp_path):
    v = _make_vault(tmp_path)
    with pytest.raises(VaultError, match="does not exist"):
        v.read_note("nope.md")


def test_vault_write_note_creates(tmp_path):
    v = Vault(tmp_path)
    result = v.write_note(
        "Daily/2026-06-26.md",
        body="today's entry",
        frontmatter={"tags": ["work"]},
    )
    assert "created" in result
    text = (tmp_path / "Daily" / "2026-06-26.md").read_text(encoding="utf-8")
    assert "today's entry" in text
    assert "tags:" in text


def test_vault_write_note_fails_if_exists(tmp_path):
    v = _make_vault(tmp_path)
    with pytest.raises(VaultError, match="already exists"):
        v.write_note("Projects/alpha.md", body="new", overwrite=False)


def test_vault_write_note_overwrite_preserves_frontmatter(tmp_path):
    v = _make_vault(tmp_path)
    v.write_note("Projects/alpha.md", body="new body", overwrite=True)
    note = v.read_note("Projects/alpha.md")
    assert note.frontmatter == {"status": "active"}
    assert note.body == "new body"


def test_vault_append_note_creates(tmp_path):
    v = Vault(tmp_path)
    result = v.append_note("Daily/today.md", "first line")
    assert "created" in result
    text = (tmp_path / "Daily" / "today.md").read_text(encoding="utf-8")
    assert "first line" in text


def test_vault_append_note_to_existing(tmp_path):
    v = _make_vault(tmp_path)
    v.append_note("Projects/alpha.md", "## addition\nmore")
    text = (tmp_path / "Projects" / "alpha.md").read_text(encoding="utf-8")
    assert "# Alpha" in text
    assert "## addition" in text
    assert "status: active" in text  # frontmatter preserved


def test_vault_append_note_rejects_non_md(tmp_path):
    v = Vault(tmp_path)
    with pytest.raises(VaultError, match="only .md"):
        v.append_note("notes.txt", "x")


def test_vault_write_note_rejects_non_md(tmp_path):
    v = Vault(tmp_path)
    with pytest.raises(VaultError, match="only .md"):
        v.write_note("foo/bar.txt", body="x")


# ---------------------------------------------------------------------------
# Vault search
# ---------------------------------------------------------------------------


def test_vault_search_finds_in_body(tmp_path):
    v = _make_vault(tmp_path)
    results = v.search_notes("alpha")
    assert any("alpha.md" in r["path"] for r in results)


def test_vault_search_finds_in_frontmatter(tmp_path):
    v = _make_vault(tmp_path)
    results = v.search_notes("work")
    assert any("Daily/2026-06-25.md" in r["path"] for r in results)


def test_vault_search_case_insensitive(tmp_path):
    v = _make_vault(tmp_path)
    results = v.search_notes("ALPHA", case_sensitive=False)
    assert results
    results_strict = v.search_notes("ALPHA", case_sensitive=True)
    assert not results_strict


def test_vault_search_scoped_to_folder(tmp_path):
    v = _make_vault(tmp_path)
    results = v.search_notes("body", folder="Projects")
    paths = {r["path"] for r in results}
    assert paths == {"Projects/alpha.md", "Projects/beta.md"}


def test_vault_search_returns_snippet(tmp_path):
    v = _make_vault(tmp_path)
    results = v.search_notes("frontmatter")
    assert results
    assert "snippet" in results[0]
    assert "frontmatter" in results[0]["snippet"].lower()


# ---------------------------------------------------------------------------
# Tool wrappers (end-to-end through MCP / agent surface)
# ---------------------------------------------------------------------------


def test_list_notes_tool(tmp_path):
    _make_vault(tmp_path)
    out = list_notes_tool(str(tmp_path))
    assert "Daily/2026-06-25.md" in out
    assert "Projects/alpha.md" in out


def test_list_notes_tool_in_subfolder(tmp_path):
    _make_vault(tmp_path)
    out = list_notes_tool(str(tmp_path), folder="Projects")
    assert "alpha.md" in out
    assert "beta.md" in out
    assert "Daily" not in out


def test_list_notes_tool_empty(tmp_path):
    Vault(tmp_path)
    out = list_notes_tool(str(tmp_path))
    assert "No notes" in out


def test_list_notes_tool_missing_vault(tmp_path):
    with pytest.raises(VaultError, match="does not exist"):
        list_notes_tool(str(tmp_path / "nope"))


def test_read_note_tool(tmp_path):
    _make_vault(tmp_path)
    out = read_note_tool(str(tmp_path), "Projects/alpha.md")
    assert "# Projects/alpha.md" in out
    assert "## Frontmatter" in out
    assert "status: active" in out
    assert "## Body" in out
    assert "# Alpha" in out


def test_search_notes_tool(tmp_path):
    _make_vault(tmp_path)
    out = search_notes_tool(str(tmp_path), "alpha")
    assert "Projects/alpha.md" in out
    assert "match(es)" in out


def test_search_notes_tool_no_match(tmp_path):
    _make_vault(tmp_path)
    out = search_notes_tool(str(tmp_path), "zzznotfoundzzz")
    assert "No matches" in out


def test_write_note_tool_creates(tmp_path):
    _make_vault(tmp_path)
    out = write_note_tool(
        str(tmp_path),
        "Daily/2026-06-26.md",
        body="new note body",
        frontmatter_json='{"tags": ["work"], "status": "draft"}',
    )
    assert "created" in out
    text = (tmp_path / "Daily" / "2026-06-26.md").read_text(encoding="utf-8")
    assert "new note body" in text
    assert "status: draft" in text


def test_write_note_tool_rejects_existing(tmp_path):
    _make_vault(tmp_path)
    with pytest.raises(VaultError, match="already exists"):
        write_note_tool(str(tmp_path), "Projects/alpha.md", body="x")


def test_write_note_tool_overwrite(tmp_path):
    _make_vault(tmp_path)
    out = write_note_tool(
        str(tmp_path),
        "Projects/alpha.md",
        body="replaced",
        frontmatter_json='{"status": "done"}',
        overwrite=True,
    )
    assert "updated" in out
    text = (tmp_path / "Projects" / "alpha.md").read_text(encoding="utf-8")
    assert "replaced" in text
    assert "status: done" in text


def test_write_note_tool_rejects_bad_json(tmp_path):
    _make_vault(tmp_path)
    with pytest.raises(VaultError, match="not valid JSON"):
        write_note_tool(
            str(tmp_path),
            "new.md",
            body="x",
            frontmatter_json="not json",
        )


def test_write_note_tool_rejects_non_object_json(tmp_path):
    _make_vault(tmp_path)
    with pytest.raises(VaultError, match="JSON object"):
        write_note_tool(
            str(tmp_path),
            "new.md",
            body="x",
            frontmatter_json="[1,2,3]",
        )


def test_append_note_tool(tmp_path):
    _make_vault(tmp_path)
    out = append_note_tool(str(tmp_path), "Projects/alpha.md", "## new section")
    assert "appended" in out
    text = (tmp_path / "Projects" / "alpha.md").read_text(encoding="utf-8")
    assert "## new section" in text
    assert "status: active" in text  # frontmatter preserved


def test_append_note_tool_creates(tmp_path):
    Vault(tmp_path)
    out = append_note_tool(str(tmp_path), "Daily/today.md", "first line")
    assert "created" in out


# ---------------------------------------------------------------------------
# Path-traversal on the tool surface (defence in depth)
# ---------------------------------------------------------------------------


def test_tools_reject_traversal(tmp_path):
    _make_vault(tmp_path)
    with pytest.raises(VaultError, match="traversal|escape"):
        read_note_tool(str(tmp_path), "../etc/passwd")


def test_tools_reject_absolute_in_arg(tmp_path):
    _make_vault(tmp_path)
    with pytest.raises(VaultError, match="absolute paths"):
        read_note_tool(str(tmp_path), "/etc/passwd")
