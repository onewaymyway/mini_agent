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


# ════════════════════════════════════════════════════════════════════════════════
# 回归测试：load_capability_map 缺失 + soft_goal_deriver._from_capability_map
# 被误拼接进 _recently_explored_domains() 导致的 AttributeError（已修复）
#
# 修复前：self._from_capability_map() 调用必然抛 AttributeError（该方法此前
# 没有独立的 def 头，代码是 _recently_explored_domains() 内部 return 之后的
# 死代码），phase_g.load_capability_map 函数本身也完全不存在。
# derive_candidates() 无 try/except 保护，异常只在更外层的
# autonomous_loop._tick_autonomous() 被吞掉——意味着"软目标自动推导"功能
# 从写下来那天起就从未真正产出过候选，且不会被常规测试发现（因为
# derive_candidates() 本身此前没有专门的端到端测试）。
# ════════════════════════════════════════════════════════════════════════════════

class TestLoadCapabilityMap:
    def test_load_capability_map_matches_build_capability_map(self, paths):
        """load_capability_map(paths) 应该是 build_capability_map(paths, None)
        的只读等价物——两者结果应完全一致（同一份统计口径，不产生分歧）。"""
        from mini_agent.evolution.phase_g import load_capability_map

        sessions = paths.sessions_dir
        _make_manifest(sessions, "s1", "t1", "done", "refactor the code")
        _make_manifest(sessions, "s1", "t2", "failed", "refactor the module")
        _make_manifest(sessions, "s1", "t3", "failed", "refactor again")

        via_load = load_capability_map(paths)
        via_build = build_capability_map(paths, memory_backend=None)

        assert [e.domain for e in via_load] == [e.domain for e in via_build]
        assert [e.confidence for e in via_load] == [e.domain and e.confidence for e in via_build]

    def test_capability_name_and_total_calls_aliases(self, paths):
        """soft_goal_deriver.py 用的是 capability_name/total_calls 字段名，
        CapabilityMapEntry 的真实字段是 domain/success_count/failure_count
        ——这两个 property 必须正确桥接，否则 total_calls 会静默 getattr
        回退成 0（不报错，但 novelty/urgency 计算全部错误）。"""
        from mini_agent.evolution.phase_g import load_capability_map

        sessions = paths.sessions_dir
        _make_manifest(sessions, "s1", "t1", "done", "refactor the code")
        _make_manifest(sessions, "s1", "t2", "failed", "refactor the module")

        entries = load_capability_map(paths)
        assert len(entries) == 1
        entry = entries[0]
        assert entry.capability_name == entry.domain == "refactor"
        assert entry.total_calls == entry.success_count + entry.failure_count == 2

    def test_empty_when_no_sessions(self, paths):
        from mini_agent.evolution.phase_g import load_capability_map
        assert load_capability_map(paths) == []


class TestSoftGoalDeriverCapabilitySignal:
    """soft_goal_deriver._from_capability_map()（信号1）修复后的端到端验证。"""

    def _make_deriver(self, paths):
        from mini_agent.evolution.soft_goal_deriver import SoftGoalDeriver

        class _FakeAutonomyCfg:
            exploration_min_calls_threshold = 2
            already_explored_cooldown_days = 30.0
            novelty_weight = 0.5

        class _FakeCfg:
            autonomy = _FakeAutonomyCfg()

        return SoftGoalDeriver(paths, _FakeCfg())

    def test_from_capability_map_is_a_real_bound_method(self, paths):
        """回归防护：确保这个方法不会再次被意外拼接进别的函数体里丢失
        独立的 def 头（那种 bug 不会在 import 阶段报错，只有实际调用才会
        暴露，所以必须显式断言它是 SoftGoalDeriver 自己的方法）。"""
        deriver = self._make_deriver(paths)
        assert hasattr(deriver, "_from_capability_map")
        assert callable(deriver._from_capability_map)
        # 调用不应该抛异常（此前必然 AttributeError）
        result = deriver._from_capability_map()
        assert isinstance(result, list)

    def test_low_confidence_domain_produces_candidate(self, paths):
        sessions = paths.sessions_dir
        # 5 次 refactor 任务，4 次失败 → confidence=0.2，明显低于 CONFIDENCE_LOW
        _make_manifest(sessions, "s1", "t1", "done", "refactor the code")
        for i in range(2, 6):
            _make_manifest(sessions, "s1", f"t{i}", "failed", "refactor again")

        deriver = self._make_deriver(paths)
        candidates = deriver._from_capability_map()

        assert len(candidates) == 1
        assert "refactor" in candidates[0].title
        assert candidates[0].source_tag == "capability"
        assert candidates[0].urgency > 0

    def test_high_confidence_domain_produces_no_candidate(self, paths):
        sessions = paths.sessions_dir
        # 5 次全部成功 → confidence=1.0，不应触发"需要改善可靠性"候选
        for i in range(5):
            _make_manifest(sessions, "s1", f"t{i}", "done", "refactor the code")

        deriver = self._make_deriver(paths)
        candidates = deriver._from_capability_map()
        assert len(candidates) == 0

    def test_derive_candidates_end_to_end_no_exception(self, paths):
        """derive_candidates() 端到端跑通，修复前这里必然抛 AttributeError。"""
        from mini_agent.perception.goal_backlog import GoalBacklog

        sessions = paths.sessions_dir
        _make_manifest(sessions, "s1", "t1", "done", "refactor the code")
        for i in range(2, 6):
            _make_manifest(sessions, "s1", f"t{i}", "failed", "refactor again")

        deriver = self._make_deriver(paths)
        backlog = GoalBacklog(paths)

        cap_candidates, other_candidates = deriver.derive_candidates(backlog)

        assert isinstance(cap_candidates, list)
        assert isinstance(other_candidates, list)
        assert any("refactor" in c.title for c in cap_candidates)
