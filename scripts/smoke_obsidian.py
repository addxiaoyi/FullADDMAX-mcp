"""Manual end-to-end smoke test for the Obsidian integration.

Builds a fake vault, runs each of the 5 public tools, and asserts
basic invariants. Exits non-zero on any failure.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

from fulladdmax_mcp.obsidian import (
    append_note_tool,
    list_notes_tool,
    read_note_tool,
    search_notes_tool,
    write_note_tool,
)


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="famobs_"))
    try:
        (root / "Daily").mkdir()
        (root / "Projects").mkdir()
        (root / "Daily" / "2026-06-25.md").write_text(
            """# 2026-06-25

## 任务
- 完成 FullADDMAX-mcp v0.3.0

## 笔记
- function calling 已加
- HTTP transport 已加
""",
            encoding="utf-8",
        )
        (root / "Projects" / "roadmap.md").write_text(
            """---
status: active
tags: [mcp, agent]
---
# 项目路线图

## 进行中
- function calling
- 自定义工具
""",
            encoding="utf-8",
        )

        v = str(root)
        print("=== list_notes ===")
        out = list_notes_tool(v)
        print(out)
        assert "Daily/2026-06-25.md" in out
        assert "Projects/roadmap.md" in out

        print("\n=== search 'function calling' ===")
        out = search_notes_tool(v, "function calling")
        print(out)
        assert "Daily/2026-06-25.md" in out
        assert "Projects/roadmap.md" in out

        print("\n=== read Daily ===")
        out = read_note_tool(v, "Daily/2026-06-25.md")
        print(out)
        assert "# Daily/2026-06-25.md" in out
        assert "function calling" in out

        print("\n=== read Projects/roadmap.md (with frontmatter) ===")
        out = read_note_tool(v, "Projects/roadmap.md")
        print(out)
        assert "## Frontmatter" in out
        assert "status: active" in out
        assert "tags: [mcp, agent]" in out

        print("\n=== append ===")
        out = append_note_tool(v, "Daily/2026-06-25.md", "## 增量笔记\n- obsidian 集成完成")
        print(out)
        assert "appended" in out
        text = (root / "Daily" / "2026-06-25.md").read_text(encoding="utf-8")
        assert "obsidian 集成完成" in text
        # 之前的 2026-06-25 笔记没 frontmatter
        assert "## 任务" in text

        print("\n=== write a new note with frontmatter ===")
        out = write_note_tool(
            v,
            "Daily/2026-06-26.md",
            body="今天开始 obsidian 集成",
            frontmatter_json='{"tags": ["work", "obsidian"], "status": "draft"}',
        )
        print(out)
        assert "created" in out
        new_text = (root / "Daily" / "2026-06-26.md").read_text(encoding="utf-8")
        assert "今天开始 obsidian 集成" in new_text
        assert "status: draft" in new_text
        assert "tags:" in new_text

        print("\n=== search restricted to Projects ===")
        out = search_notes_tool(v, "function calling", folder="Projects")
        print(out)
        assert "Projects/roadmap.md" in out
        assert "Daily/2026-06-25.md" not in out

        print("\nALL SMOKE CHECKS PASSED ✅")
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
