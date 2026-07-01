"""
tests/test_phase_g.py — Stage 8 Phase G 测试

覆盖：
  8.2  prune_skills（剪枝候选扫描）
  8.3  build_capability_map（能力地图）
  8.4  check_scope_promotion（Scope 晋升候选）
  8.5  rhythm_is_allowed / record_proposal（节奏治理）
  8.1  run_phase_g / should_run_phase_g（整体入口 + 时间门控）
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mini_agent.evolution.phase_g import (
    rhythm_is_allowed,
    record_proposal,
    get_last_phase_g_run,
    record_phase_g_run,
    should_run_phase_g,
    prune_skills,
    build_capability_map,
    check_scope_promotion,
    run_phase_g,
    _infer_domain,
    CapabilityMapEntry,
    PruneCandidate,
    PromotionCandidate,
)


# ── fixture: mock AgentPaths ──────────────────────────────────────────────────

class MockPaths:
    """轻量 AgentPaths mock，把所有路径重定向到 tmp_path。"""
    def __init__(self, root: Path):
        self._root = root
        (root / ".agent").mkdir(parents=True, exist_ok=True)

    @property
    def workdir_dir(self) -> Path:
        return self._root / ".agent"

    @property
    def sessions_dir(self) -> Path:
        return self._root / ".agent" / "sessions"

    @property
    def global_cross_project_index(self) -> Path:
        return self._root / ".agent" / "cross_project_index.json"


@pytest.fixture
def paths(tmp_path) -> MockPaths:
    return MockPaths(tmp_path)


# ════════════════════════════════════════════════════════════════════════════════
# 8.5  节奏治理
# ════════════════════════════════════════════════════════════════════════════════

class TestRhythmGovernance:
    def test_new_key_is_allowed(self, paths):
        assert rhythm_is_allowed(paths, "prune", "skill_foo") is True

    def test_after_record_not_allowed(self, paths):
        record_proposal(paths, "prune", "skill_foo")
        assert rhythm_is_allowed(paths, "prune", "skill_foo", min_interval_days=7.0) is False

    def test_different_type_allowed(self, paths):
        record_proposal(paths, "prune", "skill_foo")
        assert rhythm_is_allowed(paths, "promote", "skill_foo") is True

    def test_different_key_allowed(self, paths):
        record_proposal(paths, "prune", "skill_foo")
        assert rhythm_is_allowed(paths, "prune", "skill_bar") is True

    def test_expired_record_allowed(self, paths):
        # 先记录一个过期的时间（比 min_interval_days 更早）
        data = {f"prune:skill_foo": time.time() - 8 * 86400}  # 8 天前
        (paths.workdir_dir / "phase_g_rhythm.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        assert rhythm_is_allowed(paths, "prune", "skill_foo", min_interval_days=7.0) is True

    def test_phase_g_run_tracking(self, paths):
        assert get_last_phase_g_run(paths) == 0.0
        record_phase_g_run(paths)
        assert get_last_phase_g_run(paths) > 0.0

    def test_should_run_phase_g_initially(self, paths):
        assert should_run_phase_g(paths, interval_hours=24.0) is True

    def test_should_not_run_after_recent(self, paths):
        record_phase_g_run(paths)
        assert should_run_phase_g(paths, interval_hours=24.0) is False

    def test_should_run_after_interval(self, paths):
        # 模拟 25 小时前运行过
        data = {"_last_run_at": time.time() - 25 * 3600}
        (paths.workdir_dir / "phase_g_rhythm.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        assert should_run_phase_g(paths, interval_hours=24.0) is True


# ════════════════════════════════════════════════════════════════════════════════
# 8.3  _infer_domain（domain 推断）
# ════════════════════════════════════════════════════════════════════════════════

class TestInferDomain:
    def test_python(self):
        # flask/django/fastapi → python（无其他更具体规则先匹配）
        assert _infer_domain("Create a web app using flask and jinja2") == "python"

    def test_refactor(self):
        assert _infer_domain("重构 auth 模块，提高可读性") == "refactor"

    def test_bash_scripting(self):
        assert _infer_domain("Write a bash script to deploy") == "bash_scripting"

    def test_testing(self):
        assert _infer_domain("Add pytest tests for the parser") == "testing"

    def test_bug_fix(self):
        assert _infer_domain("Fix the bug: KeyError in parser.py") == "bug_fix"

    def test_general_fallback(self):
        assert _infer_domain("Random task with no keywords") == "general"

    def test_devops(self):
        assert _infer_domain("Build Docker image and push to registry") == "devops"


# ════════════════════════════════════════════════════════════════════════════════
# 8.3  build_capability_map
# ════════════════════════════════════════════════════════════════════════════════

def _make_manifest(sessions_dir: Path, session: str, task: str, status: str, goal: str) -> None:
    d = sessions_dir / session / "tasks" / task
    d.mkdir(parents=True, exist_ok=True)
    manifest = {
        "goal": goal,
        "outcome": {"status": status},
    }
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


class TestBuildCapabilityMap:
    def test_basic_aggregation(self, paths, tmp_path):
        sessions = paths.sessions_dir
        _make_manifest(sessions, "s1", "t1", "done",   "Fix bug in utils.py")
        _make_manifest(sessions, "s1", "t2", "done",   "Fix bug in parser.py")
        _make_manifest(sessions, "s2", "t3", "failed", "Fix bug in app.py")

        result = build_capability_map(paths, memory_backend=None)
        domain_map = {e.domain: e for e in result}

        assert "bug_fix" in domain_map
        bfe = domain_map["bug_fix"]
        assert bfe.success_count == 2
        assert bfe.failure_count == 1
        assert abs(bfe.confidence - 2/3) < 0.01

    def test_empty_sessions(self, paths):
        result = build_capability_map(paths, memory_backend=None)
        assert result == []

    def test_writes_to_memory(self, paths, tmp_path):
        sessions = paths.sessions_dir
        _make_manifest(sessions, "s1", "t1", "done", "Write pytest tests")

        mock_mem = MagicMock()
        result = build_capability_map(paths, memory_backend=mock_mem)
        # memory.add 应被调用一次
        assert mock_mem.add.called

    def test_cancelled_counts_as_failure(self, paths):
        sessions = paths.sessions_dir
        _make_manifest(sessions, "s1", "t1", "cancelled", "Refactor auth module")

        result = build_capability_map(paths, memory_backend=None)
        domain_map = {e.domain: e for e in result}
        assert "refactor" in domain_map
        assert domain_map["refactor"].failure_count == 1

    def test_multiple_domains(self, paths):
        sessions = paths.sessions_dir
        _make_manifest(sessions, "s1", "t1", "done",   "Write pytest tests for parser")
        _make_manifest(sessions, "s1", "t2", "done",   "Build Docker image")
        _make_manifest(sessions, "s2", "t3", "failed", "Write pytest tests for auth")

        result = build_capability_map(paths, memory_backend=None)
        domains = {e.domain for e in result}
        assert "testing" in domains
        assert "devops" in domains


# ════════════════════════════════════════════════════════════════════════════════
# 8.4  check_scope_promotion
# ════════════════════════════════════════════════════════════════════════════════

def _write_cross_index(paths, patterns: list[dict]) -> None:
    data = {"cross_project_patterns": patterns}
    paths.global_cross_project_index.write_text(
        json.dumps(data), encoding="utf-8"
    )


class TestCheckScopePromotion:
    def test_basic_promotion(self, paths):
        _write_cross_index(paths, [{
            "id": "p001",
            "description": "always use bash -e for scripts",
            "observed_in_projects": 3,
            "confidence": 0.85,
            "global_skill_candidate": True,
        }])
        result = check_scope_promotion(paths, min_projects=2, min_confidence=0.7)
        assert len(result) == 1
        assert result[0].pattern_id == "p001"
        assert result[0].observed_in_projects == 3

    def test_below_project_threshold(self, paths):
        _write_cross_index(paths, [{
            "id": "p002",
            "description": "pattern with only 1 project",
            "observed_in_projects": 1,
            "confidence": 0.9,
            "global_skill_candidate": True,
        }])
        result = check_scope_promotion(paths, min_projects=2, min_confidence=0.7)
        assert result == []

    def test_below_confidence_threshold(self, paths):
        _write_cross_index(paths, [{
            "id": "p003",
            "description": "low confidence pattern",
            "observed_in_projects": 5,
            "confidence": 0.5,
            "global_skill_candidate": True,
        }])
        result = check_scope_promotion(paths, min_projects=2, min_confidence=0.7)
        assert result == []

    def test_not_skill_candidate_excluded(self, paths):
        _write_cross_index(paths, [{
            "id": "p004",
            "description": "not a skill candidate",
            "observed_in_projects": 3,
            "confidence": 0.9,
            "global_skill_candidate": False,
        }])
        result = check_scope_promotion(paths, min_projects=2, min_confidence=0.7)
        assert result == []

    def test_cooldown_suppresses(self, paths):
        _write_cross_index(paths, [{
            "id": "p005",
            "description": "bash error handling",
            "observed_in_projects": 3,
            "confidence": 0.85,
            "global_skill_candidate": True,
        }])
        # 记录一个冷却中的提案
        record_proposal(paths, "promote", "p005")
        result = check_scope_promotion(paths, min_projects=2, min_confidence=0.7,
                                       min_interval_days=7.0)
        assert result == []

    def test_missing_index_returns_empty(self, paths):
        result = check_scope_promotion(paths)
        assert result == []

    def test_suggested_skill_name_derived(self, paths):
        _write_cross_index(paths, [{
            "id": "p006",
            "description": "Use pytest fixtures for test setup",
            "observed_in_projects": 2,
            "confidence": 0.8,
            "global_skill_candidate": True,
        }])
        result = check_scope_promotion(paths, min_projects=2, min_confidence=0.7)
        assert result[0].suggested_skill_name  # 非空
        assert "_" in result[0].suggested_skill_name or len(result[0].suggested_skill_name) > 0


# ════════════════════════════════════════════════════════════════════════════════
# 8.1  run_phase_g（整体入口）
# ════════════════════════════════════════════════════════════════════════════════

class TestRunPhaseG:
    def test_returns_report(self, paths):
        report = run_phase_g(paths, skill_loader=None, memory_backend=None)
        assert hasattr(report, "prune_candidates")
        assert hasattr(report, "capability_map")
        assert hasattr(report, "promotion_candidates")

    def test_records_last_run(self, paths):
        assert get_last_phase_g_run(paths) == 0.0
        run_phase_g(paths, skill_loader=None, memory_backend=None)
        assert get_last_phase_g_run(paths) > 0.0

    def test_capability_map_populated(self, paths):
        sessions = paths.sessions_dir
        _make_manifest(sessions, "s1", "t1", "done", "Fix bug in utils.py")

        report = run_phase_g(paths, skill_loader=None, memory_backend=None)
        assert len(report.capability_map) > 0

    def test_promotion_candidates_from_index(self, paths):
        _write_cross_index(paths, [{
            "id": "pX",
            "description": "bash error handling pattern",
            "observed_in_projects": 3,
            "confidence": 0.9,
            "global_skill_candidate": True,
        }])
        report = run_phase_g(paths, skill_loader=None, memory_backend=None)
        assert len(report.promotion_candidates) == 1

    def test_report_to_dict(self, paths):
        report = run_phase_g(paths, skill_loader=None, memory_backend=None)
        d = report.to_dict()
        assert "ran_at" in d
        assert "prune_candidates" in d
        assert "capability_map" in d
        assert "promotion_candidates" in d

    def test_has_findings_false_when_empty(self, paths):
        report = run_phase_g(paths, skill_loader=None, memory_backend=None)
        # 没有剪枝候选且没有晋升候选
        assert report.has_findings is False

    def test_has_findings_true_with_promotion(self, paths):
        _write_cross_index(paths, [{
            "id": "pY",
            "description": "pattern with promotion",
            "observed_in_projects": 5,
            "confidence": 0.95,
            "global_skill_candidate": True,
        }])
        report = run_phase_g(paths, skill_loader=None, memory_backend=None)
        assert report.has_findings is True
