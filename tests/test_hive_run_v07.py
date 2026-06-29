"""v0.7 hive_run tests: role resolution + output signature.

Covers the two new helpers in :mod:`fulladdmax_mcp.handlers.agent`:

* :func:`_resolve_departments` — accepts list[str] (v0.6 legacy)
  or list[{name, persona, system_prompt}] (v0.7 new).
* :func:`_sign_worker_output` — annotates each worker section
  with role / timing / sha256 / wave metadata.
"""

from __future__ import annotations

import json
import re

import pytest
import respx
from httpx import Response

from fulladdmax_mcp import context as ctx_mod
from fulladdmax_mcp.handlers.agent import (
    _DEFAULT_MINISTRIES,
    _resolve_departments,
    _sign_worker_output,
)


# ---------------------------------------------------------------------------
# _resolve_departments
# ---------------------------------------------------------------------------

def test_resolve_departments_empty_returns_classical_six():
    """Empty / None input falls back to the 6 classical ministries."""
    m = _resolve_departments(None)
    assert len(m) == len(_DEFAULT_MINISTRIES)
    assert m[0]["name"].startswith("吏部")

    m2 = _resolve_departments([])
    assert [x["name"] for x in m2] == [x["name"] for x in _DEFAULT_MINISTRIES]


def test_resolve_departments_str_legacy_fuzzy_match():
    """list[str] (v0.6 API) still works — fuzzy-matches classical names."""
    # Exact match
    m = _resolve_departments(["吏部 (Personnel)"])
    assert m[0]["name"] == "吏部 (Personnel)"
    assert m[0]["persona"] == ""

    # Fuzzy partial match (the "吏" alone should hit 吏部)
    m2 = _resolve_departments(["吏部"])
    assert any(x["name"].startswith("吏部") for x in m2)

    # Unknown name → generic system prompt
    m3 = _resolve_departments(["星舰舰长"])
    assert m3[0]["name"] == "星舰舰长"
    assert "星舰舰长" in m3[0]["system"]
    assert m3[0]["persona"] == ""


def test_resolve_departments_dict_shape_with_system_prompt():
    """list[dict] (v0.7 API): system_prompt is passed through verbatim."""
    m = _resolve_departments([
        {
            "name": "UI设计师",
            "persona": "负责视觉/动效/图标",
            "system_prompt": "你是 STARQL UI 设计师,只输出中文 Markdown。",
        },
        {
            "role": "测试员",                  # 'role' alias for 'name'
            "persona": "负责功能/性能测试",
            "system_prompt": "你是 STARQL 测试员,先列 checklist 再写脚本。",
        },
    ])
    assert len(m) == 2
    assert m[0]["name"] == "UI设计师"
    assert m[0]["system"] == "你是 STARQL UI 设计师,只输出中文 Markdown。"
    assert m[0]["persona"] == "负责视觉/动效/图标"
    assert m[1]["name"] == "测试员"           # role → name alias works
    assert "checklist" in m[1]["system"]


def test_resolve_departments_dict_aliases_system_field():
    """`system` (no _prompt) is also accepted as alias for system_prompt."""
    m = _resolve_departments([
        {"name": "架构师", "system": "你是首席架构师,只做技术决策。"},
    ])
    assert m[0]["system"] == "你是首席架构师,只做技术决策。"
    # And the generic fallback works when neither field is given.
    m2 = _resolve_departments([{"name": "清洁工"}])
    assert "清洁工" in m2[0]["system"]


def test_resolve_departments_dict_skips_empty_name():
    """Dict items with no name are silently skipped."""
    m = _resolve_departments([
        {"persona": "no name here"},   # → skipped
        {"name": "   "},               # whitespace-only → skipped
        {"name": "有效角色"},
    ])
    assert len(m) == 1
    assert m[0]["name"] == "有效角色"


# ---------------------------------------------------------------------------
# _sign_worker_output
# ---------------------------------------------------------------------------

def test_sign_worker_output_real_parallel_format():
    """Verify signing against the ACTUAL output of parallel.run().

    parallel.run() emits ``## Task #<N>`` sections separated by
    ``\\n\\n---\\n\\n``.  Each section must get exactly one
    signature line right after the ``## Task #<N>`` header.
    """
    # Simulate the real output format (what parallel.run() returns).
    body = (
        "## Task #1\n"
        "UI designer output here.\n"
        "\n---\n\n"
        "## Task #2\n"
        "Dev output here.\n"
        "\n---\n\n"
        "## Task #3 (ERROR)\n"
        "tester failed\n"
    )
    ministries = [
        {"name": "UI设计师", "angle": "", "system": "", "persona": ""},
        {"name": "开发者",   "angle": "", "system": "", "persona": ""},
        {"name": "测试员",   "angle": "", "system": "", "persona": ""},
    ]
    out = _sign_worker_output(body, ministries, wave=1, waves_total=2, elapsed_ms=1234)

    # Each task section should now have a signature line.
    sig_lines = [ln for ln in out.splitlines() if ln.startswith("> 🏷️")]
    assert len(sig_lines) == 3, f"expected 3 sigs, got {len(sig_lines)}:\n{out}"

    # Verify the role labels match the caller's names.
    assert "UI设计师" in sig_lines[0]
    assert "开发者"   in sig_lines[1]
    assert "测试员"   in sig_lines[2]

    # Verify the sha256 digest is present (8 hex chars).
    for line in sig_lines:
        m = re.search(r"sha256:([0-9a-f]{8})", line)
        assert m is not None, f"missing sha256 in: {line!r}"
        assert m.group(1) != "0" * 8, f"hash shouldn't be all zeros: {line!r}"

    # Verify timing + wave metadata.
    for line in sig_lines:
        assert "⏱️ 1234ms" in line
        assert "🌊 wave 1/2" in line


def test_sign_worker_output_fallback_when_no_marker():
    """When the body has no ``## Task #`` marker, a single trailer is appended."""
    body = "raw LLM output with no recognizable markers at all"
    ministries = [{"name": "孤胆", "angle": "", "system": "", "persona": ""}]
    out = _sign_worker_output(body, ministries, wave=2, waves_total=3, elapsed_ms=99)

    sig_lines = [ln for ln in out.splitlines() if ln.startswith("> 🏷️")]
    assert len(sig_lines) == 1
    assert "(merged)" in sig_lines[0]
    assert "⏱️ 99ms" in sig_lines[0]
    assert "🌊 wave 2/3" in sig_lines[0]


def test_sign_worker_output_preserves_original_content():
    """Signing must NEVER modify the existing content — only insert new lines."""
    body = (
        "## Task #1\n"
        "important content A\n"
        "## Task #2\n"
        "important content B\n"
    )
    ministries = [
        {"name": "A", "angle": "", "system": "", "persona": ""},
        {"name": "B", "angle": "", "system": "", "persona": ""},
    ]
    out = _sign_worker_output(body, ministries, wave=1, waves_total=1, elapsed_ms=0)

    # Original content must still be present verbatim.
    assert "important content A" in out
    assert "important content B" in out
    assert "## Task #1" in out
    assert "## Task #2" in out

    # The signature lines should appear RIGHT AFTER the task headers.
    lines = out.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("## Task #"):
            assert lines[i + 1].startswith("> 🏷️"), (
                f"sig should be at line {i+1}, got: {lines[i+1]!r}"
            )


def test_sign_worker_output_different_hashes_for_different_bodies():
    """Same ministries, different body content → different sha256."""
    ministries = [{"name": "x", "angle": "", "system": "", "persona": ""}]
    body_a = "## Task #1\nalpha"
    body_b = "## Task #1\nbeta"
    out_a = _sign_worker_output(body_a, ministries, 1, 1, 100)
    out_b = _sign_worker_output(body_b, ministries, 1, 1, 100)
    ha = re.search(r"sha256:([0-9a-f]{8})", out_a).group(1)
    hb = re.search(r"sha256:([0-9a-f]{8})", out_b).group(1)
    assert ha != hb


# ---------------------------------------------------------------------------
# End-to-end: full _hive_run with mocked LLM
# ---------------------------------------------------------------------------

def _chat_response(content: str) -> Response:
    return Response(
        200,
        json={
            "id": "test",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
        },
    )


def _make_fulladdmax_mcp_test_dir():
    """Skip helper — placeholder so the import above doesn't fail.
    The actual conftest.py handles LLM config; nothing to do here.
    """
    pass


async def test_hive_run_dict_persona_reaches_llm(mock_chat, make_response):
    """v0.7 dict departments: each role's system_prompt reaches the LLM.

    With 3 dict departments + waves=1, the LLM should be hit 3 times
    (one per role).  We capture the request bodies and assert that
    each carries the system_prompt from the corresponding dict.
    """
    # Use a fresh hive context so we don't inherit state from other tests.
    ctx_mod.put("hive_depth", 0)

    # The route handler captures the request body so we can inspect it.
    captured_bodies: list[dict] = []

    def _handler(request):
        body = json.loads(request.content)
        captured_bodies.append(body)
        return _chat_response(f"reply for: {body['messages'][0]['content'][:40]}")

    mock_chat.post("/v1/chat/completions").mock(side_effect=_handler)

    from fulladdmax_mcp.handlers.agent import _hive_run

    out = await _hive_run(
        task="为 STARQL 设计 REST API",
        departments=[
            {"name": "UI设计师", "persona": "视觉",
             "system_prompt": "ZZZ-UI-ROLE-MARKER-AAA"},
            {"name": "开发者",   "persona": "后端",
             "system_prompt": "ZZZ-DEV-ROLE-MARKER-BBB"},
            {"name": "测试员",   "persona": "QA",
             "system_prompt": "ZZZ-QA-ROLE-MARKER-CCC"},
        ],
        waves=1,
    )

    # We should have hit the LLM exactly 3 times (one per role).
    assert len(captured_bodies) == 3, (
        f"expected 3 LLM calls, got {len(captured_bodies)}"
    )

    # The user-message for each role should embed the role's name.
    # And the request bodies collectively should contain all 3 system
    # markers — proving the dict system_prompts reached the LLM.
    all_content = " ".join(
        m["content"] for body in captured_bodies for m in body["messages"]
    )
    assert "ZZZ-UI-ROLE-MARKER-AAA"  in all_content
    assert "ZZZ-DEV-ROLE-MARKER-BBB" in all_content
    assert "ZZZ-QA-ROLE-MARKER-CCC"  in all_content

    # The output should include the per-role signature lines.
    sig_lines = [ln for ln in out.splitlines() if ln.startswith("> 🏷️")]
    assert len(sig_lines) == 3
    assert any("UI设计师" in ln for ln in sig_lines)
    assert any("开发者"   in ln for ln in sig_lines)
    assert any("测试员"   in ln for ln in sig_lines)


async def test_hive_run_empty_departments_raises(mock_chat):
    """All-empty departments list raises with the i18n error."""
    ctx_mod.put("hive_depth", 0)
    from fulladdmax_mcp.handlers.agent import _hive_run

    with pytest.raises(ValueError, match="hive_run"):
        await _hive_run(
            task="x",
            departments=[
                {"persona": "no name"},   # skipped
                {"name": "  "},           # skipped
            ],
            waves=1,
        )


async def test_hive_run_waves_range_raises(mock_chat):
    """waves > 20 raises — no silent truncation."""
    ctx_mod.put("hive_depth", 0)
    from fulladdmax_mcp.handlers.agent import _hive_run

    with pytest.raises(ValueError, match="waves"):
        await _hive_run(task="x", waves=21)
