"""Obsidian vault integration.

Read and write notes in an Obsidian vault (a directory of plain Markdown
files with optional YAML frontmatter). Pure stdlib, zero dependencies —
all frontmatter / file-glob / path-safety logic is hand-written.

Vault object
------------

A :class:`Vault` is bound to a single root directory. Every operation
takes a path that is **relative to the vault root** and is checked
against the root to prevent ``..`` / symlink traversal. Absolute paths
are rejected. Non-Markdown files are ignored.

Tools exposed
-------------

The five functions below are registered with the agent tool registry
(via :func:`register_tool`) and are also exposed as MCP tools on the
server. Each takes a ``vault_path`` argument so a single server can
serve many vaults in one session.

* :func:`obsidian_list_notes`
* :func:`obsidian_read_note`
* :func:`obsidian_search_notes`
* :func:`obsidian_write_note`
* :func:`obsidian_append_note`
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .errors import FullADDMAXError

log = logging.getLogger(__name__)

MAX_NOTE_BYTES = 5 * 1024 * 1024  # 5 MB — reject larger notes early
DEFAULT_SEARCH_LIMIT = 50


# ---------------------------------------------------------------------------
# Custom error
# ---------------------------------------------------------------------------


class VaultError(FullADDMAXError):
    """Raised on any vault access problem (missing path, traversal, etc)."""


# ---------------------------------------------------------------------------
# Frontmatter parsing (zero-dep, hand-rolled, but real)
# ---------------------------------------------------------------------------


_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(?P<yaml>.*?)\n---\s*\n?(?P<body>.*)", re.DOTALL
)


@dataclass
class Frontmatter:
    """A parsed frontmatter block + the rest of the note body."""

    fields: dict[str, Any] = field(default_factory=dict)
    body: str = ""

    def to_markdown(self) -> str:
        if not self.fields:
            # No frontmatter -> body is the whole note
            return self.body
        yaml_text = _dump_simple_yaml(self.fields)
        if not yaml_text.endswith("\n"):
            yaml_text += "\n"
        return f"---\n{yaml_text}---\n{self.body}"


def parse_note(text: str) -> Frontmatter:
    """Parse a Markdown note into frontmatter + body.

    * If the note starts with ``---\\n ... \\n---\\n`` we extract the
      YAML and treat the rest as the body.
    * If not, ``fields`` is empty and ``body`` is the whole text.
    """
    if not text.startswith("---"):
        return Frontmatter(body=text)
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return Frontmatter(body=text)
    yaml_text = m.group("yaml")
    body = m.group("body")
    fields = _parse_simple_yaml(yaml_text)
    return Frontmatter(fields=fields, body=body)


# A small but real YAML subset, sufficient for the frontmatter people
# actually write in Obsidian: scalar values, lists, nested mappings,
# quoted strings, block-style and flow-style, comments. We do NOT aim
# to support the full YAML 1.2 spec; if a vault contains pathological
# frontmatter the parser will raise VaultError.
#
# Implementation strategy: line-based scanner with a tiny indent stack.
# Works for 99% of Obsidian notes; the rest we surface as a clear error.


_INDENT = "  "  # Obsidian / YAML default indent


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse a YAML frontmatter block. Raises :class:`VaultError` on
    syntax errors. Supports:

    * key: value
    * key: "double quoted" / 'single quoted'
    * key: 123 / -1.5 / true / false / null
    * key: [a, b, c]   (flow list)
    * key:\\n  - item\\n  - item   (block list)
    * nested mappings (2-space indent)
    * comments (# ...) and blank lines
    """
    # Normalize line endings, drop trailing whitespace, keep \n
    lines = [ln.rstrip() for ln in text.splitlines()]
    out, _ = _yaml_parse_block(lines, 0, 0)
    if not isinstance(out, dict):
        raise VaultError("frontmatter top-level must be a mapping")
    return out


def _yaml_parse_block(lines: list[str], start: int, base_indent: int) -> tuple[Any, int]:
    """Parse one mapping block starting at ``lines[start]``. Returns
    ``(value, next_line_index)``.

    Terminates when we hit a line with indent <= base_indent (or EOF).
    """
    if start >= len(lines):
        return {}, start
    first = lines[start]
    first_indent = len(first) - len(first.lstrip(" "))
    if first_indent < base_indent:
        return {}, start
    if first_indent > base_indent:
        raise VaultError(
            f"YAML indent error at line {start + 1}: "
            f"got {first_indent} spaces, expected <= {base_indent}"
        )
    if not first.strip() or first.lstrip().startswith("#"):
        return _yaml_parse_block(lines, start + 1, base_indent)

    result: dict[str, Any] = {}
    i = start
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent < base_indent:
            break
        if indent > base_indent:
            raise VaultError(
                f"YAML indent error at line {i + 1}: "
                f"got {indent} spaces, expected <= {base_indent}"
            )
        stripped = line.lstrip(" ")
        if not stripped.startswith("-"):
            # key: value
            if ":" not in stripped:
                raise VaultError(
                    f"YAML syntax error at line {i + 1}: expected 'key: value', got {stripped!r}"
                )
            key, _, value_part = stripped.partition(":")
            key = key.strip()
            value_part = value_part.strip()
            if not key:
                raise VaultError(f"YAML syntax error at line {i + 1}: empty key")
            i += 1
            if value_part == "":
                # value is on the next line(s), at indent > base_indent
                if i < len(lines):
                    nxt = lines[i]
                    nxt_indent = len(nxt) - len(nxt.lstrip(" "))
                    nxt_stripped = nxt.strip()
                    if nxt_indent > base_indent:
                        if nxt_stripped.startswith("- "):
                            val, i = _yaml_parse_list(lines, i, nxt_indent)
                        else:
                            val, i = _yaml_parse_block(lines, i, nxt_indent)
                        result[key] = val
                        continue
                result[key] = None
            elif value_part == "|":
                # block scalar (literal); join subsequent indented lines
                block_indent = indent + 2
                parts: list[str] = []
                while i < len(lines):
                    nxt = lines[i]
                    nxt_indent = len(nxt) - len(nxt.lstrip(" "))
                    if not nxt.strip():
                        parts.append("")
                        i += 1
                        continue
                    if nxt_indent < block_indent:
                        break
                    parts.append(nxt[block_indent:])
                    i += 1
                while parts and parts[-1] == "":
                    parts.pop()
                result[key] = "\n".join(parts)
            elif value_part.startswith("[") and value_part.endswith("]"):
                # flow list
                result[key] = _yaml_parse_flow_list(value_part)
            elif value_part.startswith("{") and value_part.endswith("}"):
                # flow map — best-effort JSON-ish
                import json

                try:
                    result[key] = json.loads(value_part)
                except json.JSONDecodeError as e:
                    raise VaultError(
                        f"YAML flow map at line {i}: not valid JSON: {e}"
                    ) from e
            else:
                result[key] = _yaml_scalar(value_part)
        else:
            raise VaultError(
                f"YAML list item at top level is not allowed at line {i + 1}"
            )
    return result, i


def _yaml_parse_list(lines: list[str], start: int, base_indent: int) -> tuple[list, int]:
    """Parse a block-style YAML list. Each item starts with ``- `` at
    ``base_indent``."""
    items: list[Any] = []
    i = start
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent < base_indent:
            break
        if indent > base_indent:
            raise VaultError(
                f"YAML list indent error at line {i + 1}"
            )
        stripped = line.lstrip(" ")
        if not stripped.startswith("- "):
            break
        rest = stripped[2:]
        if rest == "":
            # nested mapping follows
            i += 1
            if i < len(lines):
                nxt = lines[i]
                nxt_indent = len(nxt) - len(nxt.lstrip(" "))
                nxt_stripped = nxt.strip()
                if nxt_indent > base_indent and not nxt_stripped.startswith("- "):
                    val, i = _yaml_parse_block(lines, i, nxt_indent)
                    items.append(val)
                    continue
            items.append(None)
            continue
        if ":" in rest and not rest.startswith('"') and not rest.startswith("'"):
            # inline mapping start: - key: value
            # We synthesise "key: value" and parse as a block whose
            # first line is at the current indent.
            synthesized = " " * (base_indent + 2) + rest
            # Insert into a synthetic single-key block
            sub_block = _yaml_parse_block([synthesized], 0, base_indent + 2)
            items.append(sub_block[0] if isinstance(sub_block, tuple) else sub_block)
            # If the rest spans multiple lines, continue reading.
            i += 1
            if i < len(lines):
                nxt = lines[i]
                nxt_indent = len(nxt) - len(nxt.lstrip(" "))
                if nxt_indent > base_indent + 2 and nxt.strip():
                    val, i = _yaml_parse_block(lines, i, nxt_indent)
                    # Merge the trailing keys into the dict
                    if items and isinstance(items[-1], dict):
                        items[-1].update(val)
                    else:
                        items.append(val)
            continue
        # plain scalar
        items.append(_yaml_scalar(rest))
        i += 1
    return items, i


def _yaml_parse_flow_list(text: str) -> list[Any]:
    """Parse ``[a, b, "c d", 1, true]`` — best-effort split on top-level
    commas respecting brackets/quotes.
    """
    import json

    inner = text[1:-1].strip()
    if not inner:
        return []
    # Wrap strings in quotes that aren't already
    parts: list[str] = []
    buf = ""
    in_str: str | None = None
    depth = 0
    for ch in inner:
        if in_str:
            buf += ch
            if ch == in_str and (not buf.endswith("\\" + in_str)):
                in_str = None
            continue
        if ch in ('"', "'"):
            in_str = ch
            buf += ch
            continue
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(buf.strip())
            buf = ""
            continue
        buf += ch
    if buf.strip():
        parts.append(buf.strip())
    result: list[Any] = []
    for p in parts:
        try:
            result.append(json.loads(p))
        except json.JSONDecodeError:
            result.append(p)
    return result


def _yaml_scalar(text: str) -> Any:
    """Parse a YAML scalar — quote handling, bool/null/number, else str."""
    s = text.strip()
    # Strip trailing inline comment (only outside quotes)
    if not (s.startswith('"') or s.startswith("'")):
        s = _strip_inline_comment(s)
    if s.startswith('"') and s.endswith('"') and len(s) >= 2:
        return s[1:-1].replace('\\"', '"').replace("\\n", "\n")
    if s.startswith("'") and s.endswith("'") and len(s) >= 2:
        return s[1:-1].replace("''", "'")
    if s in ("true", "True", "TRUE"):
        return True
    if s in ("false", "False", "FALSE"):
        return False
    if s in ("null", "Null", "NULL", "~"):
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _strip_inline_comment(s: str) -> str:
    """Remove a trailing ``# ...`` from a scalar value, respecting
    quoted segments. The scalar must not start with a quote (caller's
    job to check).
    """
    in_str: str | None = None
    for i, ch in enumerate(s):
        if in_str:
            if ch == in_str:
                in_str = None
            continue
        if ch in ('"', "'"):
            in_str = ch
            continue
        if ch == "#":
            return s[:i].rstrip()
    return s


def _dump_simple_yaml(d: dict[str, Any], indent: int = 0) -> str:
    """Render a dict back to YAML. Lists of simple scalars use flow
    style ``[a, b, c]``; lists containing complex values fall back to
    block style. Nested dicts are emitted as a 2-space-indented block.
    """
    out_lines: list[str] = []
    pad = " " * indent
    for k, v in d.items():
        if isinstance(v, list):
            if all(not isinstance(item, (dict, list)) for item in v):
                # flow style keeps the note compact and human-editable
                out_lines.append(f"{pad}{k}: [{', '.join(_yaml_dump_scalar(x) for x in v)}]")
            else:
                out_lines.append(f"{pad}{k}:")
                for item in v:
                    if isinstance(item, (dict, list)):
                        raise VaultError(
                            f"cannot dump nested {type(item).__name__} at key {k!r} (round-trip unsupported)"
                        )
                    out_lines.append(f"{pad}  - {_yaml_dump_scalar(item)}")
        elif isinstance(v, dict):
            out_lines.append(f"{pad}{k}:")
            out_lines.append(_dump_simple_yaml(v, indent + 2).rstrip("\n"))
        else:
            out_lines.append(f"{pad}{k}: {_yaml_dump_scalar(v)}")
    return "\n".join(out_lines) + "\n"


def _yaml_dump_scalar(v: Any) -> str:
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if any(ch in s for ch in [":", "#", '"', "'", "\n", "["]):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


# ---------------------------------------------------------------------------
# Vault
# ---------------------------------------------------------------------------


@dataclass
class Note:
    """A single note's parsed contents."""

    path: str  # relative to vault root, with forward slashes
    frontmatter: dict[str, Any]
    body: str

    def render(self) -> str:
        return Frontmatter(fields=self.frontmatter, body=self.body).to_markdown()


class Vault:
    """A read/write handle on an Obsidian vault directory.

    All paths are relative to ``root`` and use forward slashes. The
    constructor resolves the root to its absolute, real path so symlink
    and ``..`` traversal are detected by :meth:`_safe_resolve`.
    """

    def __init__(self, root: str | Path) -> None:
        root_path = Path(root).expanduser()
        if not root_path.exists():
            raise VaultError(f"vault root does not exist: {root_path}")
        if not root_path.is_dir():
            raise VaultError(f"vault root is not a directory: {root_path}")
        self.root = root_path.resolve()

    # ---- internal -------------------------------------------------------

    def _safe_resolve(self, rel_path: str) -> Path:
        """Resolve ``rel_path`` against the vault root and verify it
        stays inside the root. Raises :class:`VaultError` on any escape
        attempt.
        """
        if not rel_path:
            raise VaultError("path is empty")
        # Reject absolute paths outright — we never want to touch the
        # real filesystem outside the vault.
        if rel_path.startswith("/") or rel_path.startswith("\\"):
            raise VaultError(f"absolute paths are not allowed: {rel_path!r}")
        if re.match(r"^[A-Za-z]:[\\/]", rel_path):
            raise VaultError(f"absolute paths are not allowed: {rel_path!r}")
        # Normalize separators
        normalized = rel_path.replace("\\", "/").lstrip("/")
        # Disallow .. segments
        for segment in normalized.split("/"):
            if segment in ("..",):
                raise VaultError(f"path traversal not allowed: {rel_path!r}")
        target = (self.root / normalized).resolve()
        # Resolve root fresh each time to defeat symlink swaps
        root_real = self.root.resolve()
        try:
            target.relative_to(root_real)
        except ValueError as e:
            raise VaultError(
                f"path escapes vault root: {rel_path!r}"
            ) from e
        return target

    # ---- read -----------------------------------------------------------

    def list_notes(self, folder: str = "", limit: int = 500) -> list[str]:
        """Return relative paths of all ``.md`` notes under ``folder``
        (recursively). ``folder=""`` means the entire vault.
        """
        if limit < 1:
            raise VaultError("limit must be >= 1")
        base = self._safe_resolve(folder) if folder else self.root
        if not base.exists():
            raise VaultError(f"folder does not exist: {folder!r}")
        if not base.is_dir():
            raise VaultError(f"not a folder: {folder!r}")
        results: list[str] = []
        for p in base.rglob("*.md"):
            try:
                rel = p.resolve().relative_to(self.root.resolve())
            except ValueError:
                continue
            results.append(rel.as_posix())
            if len(results) >= limit:
                break
        results.sort()
        return results

    def read_note(self, path: str) -> Note:
        target = self._safe_resolve(path)
        if not target.exists():
            raise VaultError(f"note does not exist: {path!r}")
        if not target.is_file():
            raise VaultError(f"not a file: {path!r}")
        size = target.stat().st_size
        if size > MAX_NOTE_BYTES:
            raise VaultError(
                f"note is too large ({size} bytes, max {MAX_NOTE_BYTES}): {path!r}"
            )
        text = target.read_text(encoding="utf-8")
        fm = parse_note(text)
        return Note(
            path=path.replace("\\", "/").lstrip("/"),
            frontmatter=fm.fields,
            body=fm.body,
        )

    def search_notes(
        self,
        keyword: str,
        folder: str = "",
        case_sensitive: bool = False,
        limit: int = DEFAULT_SEARCH_LIMIT,
    ) -> list[dict[str, str]]:
        """Find notes whose body or frontmatter contains ``keyword``.

        Returns a list of ``{path, snippet}`` dicts. The snippet is a
        ±60-char window around the first match.
        """
        if not keyword:
            raise VaultError("keyword is empty")
        if limit < 1:
            raise VaultError("limit must be >= 1")
        notes = self.list_notes(folder=folder, limit=10000)
        out: list[dict[str, str]] = []
        kw = keyword if case_sensitive else keyword.lower()
        for rel in notes:
            note = self.read_note(rel)
            haystack = note.body
            if not case_sensitive:
                haystack = haystack.lower()
            idx = haystack.find(kw)
            if idx < 0:
                # also search frontmatter
                fm_text = "\n".join(f"{k}: {v}" for k, v in note.frontmatter.items())
                if not case_sensitive:
                    fm_text = fm_text.lower()
                idx = fm_text.find(kw)
                if idx < 0:
                    continue
                # snippet from frontmatter
                start = max(0, idx - 30)
                end = min(len(fm_text), idx + len(kw) + 30)
                snippet = fm_text[start:end].replace("\n", " ")
            else:
                start = max(0, idx - 30)
                end = min(len(note.body), idx + len(kw) + 30)
                snippet = note.body[start:end].replace("\n", " ")
            out.append({"path": rel, "snippet": snippet})
            if len(out) >= limit:
                break
        return out

    # ---- write ----------------------------------------------------------

    def write_note(
        self,
        path: str,
        body: str,
        frontmatter: dict[str, Any] | None = None,
        overwrite: bool = False,
    ) -> str:
        """Create or overwrite a note.

        - If ``frontmatter`` is None and the file already exists, the
          existing frontmatter is preserved.
        - If ``frontmatter`` is None and the file does not exist, the
          note is created without a frontmatter block.
        - If ``overwrite`` is False and the file already exists, raises
          :class:`VaultError`. (Use :meth:`append_note` to add content.)
        """
        target = self._safe_resolve(path)
        if not path.lower().endswith(".md"):
            raise VaultError("only .md notes are supported")
        existed = target.exists()
        if existed and not overwrite:
            raise VaultError(
                f"note already exists: {path!r} (pass overwrite=True to replace)"
            )
        if frontmatter is None and existed:
            # preserve existing frontmatter
            existing = self.read_note(path)
            frontmatter = existing.frontmatter

        target.parent.mkdir(parents=True, exist_ok=True)
        rendered = Frontmatter(fields=frontmatter or {}, body=body).to_markdown()
        target.write_text(rendered, encoding="utf-8")
        action = "updated" if existed else "created"
        log.info("vault %s: %s %s", self.root, action, path)
        return f"{action}: {path} ({len(rendered)} bytes)"

    def append_note(self, path: str, content: str) -> str:
        """Append text to a note's body, creating the note (and any
        missing parent folders) if it does not exist. Frontmatter is
        preserved if present.
        """
        target = self._safe_resolve(path)
        if not path.lower().endswith(".md"):
            raise VaultError("only .md notes are supported")
        if target.exists():
            existing = self.read_note(path)
            new_body = existing.body.rstrip("\n") + "\n\n" + content.lstrip("\n")
            rendered = Frontmatter(
                fields=existing.frontmatter, body=new_body
            ).to_markdown()
            target.write_text(rendered, encoding="utf-8")
            return f"appended: {path} ({len(content)} bytes added)"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content.lstrip("\n"), encoding="utf-8")
        return f"created: {path} ({len(content)} bytes)"


# ---------------------------------------------------------------------------
# Tool wrappers (also registered as MCP tools via register_tool)
# ---------------------------------------------------------------------------


def _vault(vault_path: str) -> Vault:
    if not vault_path:
        raise VaultError("vault_path is required")
    return Vault(vault_path)


def list_notes_tool(vault_path: str, folder: str = "", limit: int = 500) -> str:
    """List all ``.md`` notes in an Obsidian vault.

    Args:
        vault_path: Absolute path to the vault root directory.
        folder: Subfolder to scope the listing to (relative to vault
            root, default ``""`` = the whole vault).
        limit: Maximum number of notes to return (default 500).
    """
    notes = _vault(vault_path).list_notes(folder=folder, limit=limit)
    if not notes:
        return f"No notes found in {vault_path}/{folder}".rstrip("/")
    return f"Found {len(notes)} note(s):\n" + "\n".join(f"- {n}" for n in notes)


def read_note_tool(vault_path: str, path: str) -> str:
    """Read a single note from an Obsidian vault, returning its
    frontmatter and body as plain Markdown.

    Args:
        vault_path: Absolute path to the vault root directory.
        path: Note path relative to the vault root, e.g.
            ``"Daily/2026-06-26.md"``.
    """
    note = _vault(vault_path).read_note(path)
    parts: list[str] = [f"# {note.path}"]
    if note.frontmatter:
        parts.append("")
        parts.append("## Frontmatter")
        parts.append("```yaml")
        parts.append(_dump_simple_yaml(note.frontmatter).rstrip("\n"))
        parts.append("```")
    parts.append("")
    parts.append("## Body")
    parts.append(note.body.rstrip("\n") if note.body else "(empty)")
    return "\n".join(parts)


def search_notes_tool(
    vault_path: str,
    keyword: str,
    folder: str = "",
    case_sensitive: bool = False,
    limit: int = DEFAULT_SEARCH_LIMIT,
) -> str:
    """Search for ``keyword`` across note bodies and frontmatter in
    the vault. Returns paths plus a short snippet for each match.

    Args:
        vault_path: Absolute path to the vault root directory.
        keyword: Substring to search for (non-empty).
        folder: Optional subfolder to scope the search to.
        case_sensitive: If False (default), match case-insensitively.
        limit: Maximum number of matches to return (default 50).
    """
    results = _vault(vault_path).search_notes(
        keyword=keyword,
        folder=folder,
        case_sensitive=case_sensitive,
        limit=limit,
    )
    if not results:
        return f"No matches for {keyword!r} in {vault_path}"
    lines = [f"Found {len(results)} match(es) for {keyword!r}:"]
    for r in results:
        lines.append(f"- **{r['path']}** — …{r['snippet']}…")
    return "\n".join(lines)


def write_note_tool(
    vault_path: str,
    path: str,
    body: str,
    frontmatter_json: str = "",
    overwrite: bool = False,
) -> str:
    """Create or overwrite a note in an Obsidian vault.

    Args:
        vault_path: Absolute path to the vault root directory.
        path: Note path relative to the vault root, e.g.
            ``"Projects/roadmap.md"``. Must end in ``.md``.
        body: The Markdown body of the note (frontmatter is NOT
            included; pass it via ``frontmatter_json``).
        frontmatter_json: Optional JSON object as a string, e.g.
            ``'{"tags": ["work"], "status": "draft"}'``. Leave empty
            to create a note without frontmatter (or to preserve the
            existing frontmatter when ``overwrite=True``).
        overwrite: If False (default) and the note already exists,
            the call fails. Set True to replace.
    """
    import json

    fm: dict[str, Any] | None
    if frontmatter_json.strip():
        try:
            parsed = json.loads(frontmatter_json)
        except json.JSONDecodeError as e:
            raise VaultError(f"frontmatter_json is not valid JSON: {e}") from e
        if not isinstance(parsed, dict):
            raise VaultError("frontmatter_json must decode to a JSON object")
        fm = parsed
    else:
        fm = None
    return _vault(vault_path).write_note(path, body=body, frontmatter=fm, overwrite=overwrite)


def append_note_tool(vault_path: str, path: str, content: str) -> str:
    """Append text to a note's body, creating the note if it does not
    exist. Useful for incrementally building logs, daily notes, or
    research trails.

    Args:
        vault_path: Absolute path to the vault root directory.
        path: Note path relative to the vault root, e.g.
            ``"Daily/2026-06-26.md"``.
        content: Text to append. A blank line is inserted before the
            new content if the existing body is non-empty.
    """
    return _vault(vault_path).append_note(path, content)
