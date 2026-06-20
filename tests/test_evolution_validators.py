"""
tests/test_evolution_validators.py — Stage 2.2 验证

对应 self_evolution_implementation_plan.md Stage 2.2：
  按 tier 分层的校验函数（T0 schema / T1 加载校验 / T2 lint+单测 / T3 复用 T2），
  以及 validators_for_tier() 的 tier → 校验函数集合映射。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mini_agent.evolution.validators import (
    validate_t0_schema,
    validate_t1_load,
    validate_t2_lint,
    validate_t2_existing_tests,
    validate_t3,
    validators_for_tier,
    TIER_VALIDATORS,
)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path


# ── T0：schema 校验 ──────────────────────────────────────────────────────────

def test_t0_valid_json_passes(root):
    result = validate_t0_schema(root, {"profile.json": '{"key": "value"}'})
    assert result.ok


def test_t0_invalid_json_fails(root):
    result = validate_t0_schema(root, {"profile.json": "{not valid json"})
    assert not result.ok
    assert "profile.json" in result.reason


def test_t0_valid_jsonl_passes(root):
    content = '{"a": 1}\n{"b": 2}\n'
    result = validate_t0_schema(root, {"memory.jsonl": content})
    assert result.ok


def test_t0_invalid_jsonl_line_fails(root):
    content = '{"a": 1}\nnot json\n{"b": 2}\n'
    result = validate_t0_schema(root, {"memory.jsonl": content})
    assert not result.ok
    assert "第 2 行" in result.reason


def test_t0_jsonl_skips_blank_lines(root):
    content = '{"a": 1}\n\n{"b": 2}\n'
    result = validate_t0_schema(root, {"memory.jsonl": content})
    assert result.ok


def test_t0_ignores_non_json_files(root):
    result = validate_t0_schema(root, {"README.md": "not json at all, but that's fine"})
    assert result.ok


def test_t0_ignores_deletions(root):
    result = validate_t0_schema(root, {"profile.json": None})
    assert result.ok


def test_t0_multiple_files_all_checked(root):
    result = validate_t0_schema(root, {
        "a.json": '{"ok": true}',
        "b.json": "{broken",
    })
    assert not result.ok


# ── T1：声明式资产加载校验 ────────────────────────────────────────────────────

_VALID_SKILL = """---
name: bash-rm-safety
description: Avoid destructive rm without confirmation
---

# bash-rm-safety

Always confirm before running rm -rf.
"""


def test_t1_valid_skill_md_passes(root):
    result = validate_t1_load(root, {"skills/bash-rm-safety/SKILL.md": _VALID_SKILL})
    assert result.ok


def test_t1_empty_skill_md_fails(root):
    result = validate_t1_load(root, {"skills/empty/SKILL.md": ""})
    assert not result.ok
    assert "内容为空" in result.reason


def test_t1_skill_without_frontmatter_still_parses_via_fallback(root):
    """SkillLoader._parse_skill 对缺失 frontmatter 的内容有 fallback 逻辑
    （用目录名做 name，首行做 description），因此只要非空就应该通过。"""
    content = "# My Skill\n\nThis skill does something useful.\n"
    result = validate_t1_load(root, {"skills/my-skill/SKILL.md": content})
    assert result.ok


def test_t1_flat_layout_skill_md_validated(root):
    """扁平布局 skills/<name>.md（非嵌套 SKILL.md）也应该被识别并校验。"""
    result = validate_t1_load(root, {"skills/my-skill.md": _VALID_SKILL})
    assert result.ok


def test_t1_agent_profile_with_name_passes(root):
    content = "---\nname: evolution-agent\nrole_type: evolution\n---\nYou are an evolution agent.\n"
    result = validate_t1_load(root, {".agent/agents/evolution-agent.md": content})
    assert result.ok


def test_t1_agent_profile_missing_name_fails(root):
    content = "---\nrole_type: evolution\n---\nbody\n"
    result = validate_t1_load(root, {".agent/agents/bad.md": content})
    assert not result.ok
    assert "缺少必填字段 name" in result.reason


def test_t1_agent_profile_missing_frontmatter_fails(root):
    content = "no frontmatter here, just text"
    result = validate_t1_load(root, {".agent/agents/bad.md": content})
    assert not result.ok
    assert "缺少 YAML frontmatter" in result.reason


def test_t1_permissions_json_reuses_t0_schema(root):
    result = validate_t1_load(root, {"permissions.json": "{not valid"})
    assert not result.ok


def test_t1_permissions_json_valid_passes(root):
    result = validate_t1_load(root, {"permissions.json": '{"allow": []}'})
    assert result.ok


def test_t1_unrelated_file_not_checked(root):
    result = validate_t1_load(root, {"src/foo.py": "this is not even valid python !!!"})
    assert result.ok  # T1 不管 .py 文件，那是 T2 的职责


def test_t1_ignores_deletions(root):
    result = validate_t1_load(root, {"skills/foo/SKILL.md": None})
    assert result.ok


# ── T2：lint（语法检查 / ruff） ───────────────────────────────────────────────

def test_t2_valid_python_syntax_passes(root):
    result = validate_t2_lint(root, {"foo.py": "def f():\n    return 1\n"})
    assert result.ok


def test_t2_invalid_python_syntax_fails(root):
    result = validate_t2_lint(root, {"foo.py": "def f(:\n    pass"})
    assert not result.ok
    assert "语法错误" in result.reason


def test_t2_ignores_non_python_files(root):
    result = validate_t2_lint(root, {"README.md": "not python, that's fine"})
    assert result.ok


def test_t2_ignores_deletions(root):
    result = validate_t2_lint(root, {"foo.py": None})
    assert result.ok


def test_t2_multiple_python_files_all_checked(root):
    result = validate_t2_lint(root, {
        "good.py": "x = 1\n",
        "bad.py": "def f(:\n  pass",
    })
    assert not result.ok


# ── T2：现有单测跑通 ──────────────────────────────────────────────────────────

def test_t2_tests_skip_when_no_tests_dir(root):
    """root 下没有 tests/ 目录时（例如非 mini_agent 项目布局），应该放行而不是报错。"""
    result = validate_t2_existing_tests(root, {"x.py": "pass"})
    assert result.ok


def test_t2_tests_pass_for_passing_suite(tmp_path):
    """构造一个最小的、必定通过的 pytest 项目，验证 T2 单测校验确实会真的执行 pytest。"""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_trivial.py").write_text("def test_ok():\n    assert True\n")
    result = validate_t2_existing_tests(tmp_path, {"x.py": "pass"})
    assert result.ok


def test_t2_tests_fail_for_failing_suite(tmp_path):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_broken.py").write_text("def test_fail():\n    assert False\n")
    result = validate_t2_existing_tests(tmp_path, {"x.py": "pass"})
    assert not result.ok
    assert "单测" in result.reason


# ── T3：复用 T2 全部校验项 ────────────────────────────────────────────────────

def test_t3_runs_lint_first_and_fails_fast(root):
    """T3 应该先跑 lint，语法错误时不需要等单测跑完才报错。"""
    result = validate_t3(root, {"foo.py": "def f(:\n  pass"})
    assert not result.ok
    assert "语法错误" in result.reason


def test_t3_passes_when_lint_and_tests_pass(tmp_path):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    result = validate_t3(tmp_path, {"foo.py": "x = 1\n"})
    assert result.ok


# ── validators_for_tier() ─────────────────────────────────────────────────────

def test_validators_for_tier_t0():
    fns = validators_for_tier("T0")
    assert fns == [validate_t0_schema]


def test_validators_for_tier_t1():
    fns = validators_for_tier("T1")
    assert fns == [validate_t0_schema, validate_t1_load]


def test_validators_for_tier_t2():
    fns = validators_for_tier("T2")
    assert fns == [validate_t2_lint, validate_t2_existing_tests]


def test_validators_for_tier_t3():
    fns = validators_for_tier("T3")
    assert fns == [validate_t3]


def test_validators_for_tier_unknown_falls_back_to_t3():
    fns = validators_for_tier("not-a-real-tier")
    assert fns == [validate_t3]


def test_tier_validators_covers_all_known_tiers():
    assert set(TIER_VALIDATORS.keys()) == {"T0", "T1", "T2", "T3"}


# ── 与 StateRepo.apply() 集成（auto_validators） ─────────────────────────────

def test_validators_integrate_with_state_repo_apply(tmp_path):
    from mini_agent.evolution.state_repo import StateRepo

    repo = StateRepo(tmp_path)
    result = repo.apply(
        changes={"skills/foo/SKILL.md": _VALID_SKILL},
        message="add skill",
        meta={},
        tier="T1",
        validators=validators_for_tier("T1"),
    )
    assert result.ok

    result2 = repo.apply(
        changes={"skills/bad/SKILL.md": ""},
        message="add bad skill",
        meta={},
        tier="T1",
        validators=validators_for_tier("T1"),
    )
    assert not result2.ok
    assert not (tmp_path / "skills/bad/SKILL.md").exists()
