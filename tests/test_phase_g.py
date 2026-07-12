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


@pytest.fixture
def real_paths(tmp_path):
    """真实 AgentPaths（而非窄接口的 MockPaths）——本文件后半部分新增的
    load_capability_map/_from_work_index/_from_lesson_review/事件总线相关
    测试都要用到 workdir_memory/workdir_work_index/system_events 等 MockPaths
    没有实现的属性。"""
    from mini_agent.storage.paths import AgentPaths
    return AgentPaths(tmp_path)


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


# ════════════════════════════════════════════════════════════════════════════════
# 回归测试：信号2（_from_work_index）与信号3（_from_lesson_review）的既有 bug
#
# 信号2：thread.thread_id 不存在（真实字段是 id）导致构造 description 时
# AttributeError；thread.last_activity_at 也不存在，getattr 静默回退成 0.0，
# 使"是否最近有活动"的判断永远为 False。
#
# 信号3：LessonGroup.meets_t1_threshold/meets_t2_t3_threshold 是 @property，
# 此前当方法调用（多了一对括号），对 bool 值再调用 () 必然 TypeError；
# 同时依赖的 lesson_review.scan_lesson_groups(paths) 函数此前根本不存在。
#
# 两个信号都被外层 except Exception 静默吞掉，从写下来就没能真正产出过候选。
# ════════════════════════════════════════════════════════════════════════════════

class TestSoftGoalDeriverWorkIndexSignal:
    """信号2（_from_work_index）修复后的端到端验证。"""

    def _make_deriver(self, real_paths):
        from mini_agent.evolution.soft_goal_deriver import SoftGoalDeriver

        class _FakeAutonomyCfg:
            exploration_min_calls_threshold = 2
            already_explored_cooldown_days = 30.0
            novelty_weight = 0.5

        class _FakeCfg:
            autonomy = _FakeAutonomyCfg()

        return SoftGoalDeriver(real_paths, _FakeCfg())

    def _write_work_index(self, real_paths, threads: list):
        import json

        real_paths.workdir_dir.mkdir(parents=True, exist_ok=True)
        real_paths.workdir_work_index.write_text(
            json.dumps({"work_threads": [t.to_dict() for t in threads]}), encoding="utf-8",
        )

    def test_stale_workthread_produces_candidate_without_exception(self, real_paths):
        from mini_agent.perception.workdir_knowledge import WorkThread

        stale_thread = WorkThread(
            id="wt1", title="认证模块",
            next_suggested="继续修复认证模块的边界情况",
            started_at=time.time() - 40 * 86400,  # 40天前，超过 STALE_WORKTHREAD_DAYS(30)
        )
        self._write_work_index(real_paths, [stale_thread])

        deriver = self._make_deriver(real_paths)
        candidates = deriver._from_work_index()  # 修复前这里必然 AttributeError

        assert len(candidates) == 1
        assert candidates[0].source_tag == "workthread"
        assert "wt1" in candidates[0].description  # 用的是 thread.id，不是不存在的 thread_id
        assert "继续修复认证模块" in candidates[0].title

    def test_fresh_workthread_produces_no_candidate(self, real_paths):
        """[回归] 此前 last_activity_at 恒为 0.0，任何有 next_suggested 的
        thread 都会被误判为 stale。修复后，刚创建（started_at 接近现在）
        的 thread 不应该触发候选。"""
        from mini_agent.perception.workdir_knowledge import WorkThread

        fresh_thread = WorkThread(
            id="wt2", title="缓存模块",
            next_suggested="优化缓存命中率",
            started_at=time.time(),  # 刚刚创建
        )
        self._write_work_index(real_paths, [fresh_thread])

        deriver = self._make_deriver(real_paths)
        candidates = deriver._from_work_index()
        assert len(candidates) == 0

    def test_no_next_suggested_produces_no_candidate(self, real_paths):
        from mini_agent.perception.workdir_knowledge import WorkThread

        thread = WorkThread(
            id="wt3", title="无待办", next_suggested="",
            started_at=time.time() - 40 * 86400,
        )
        self._write_work_index(real_paths, [thread])

        deriver = self._make_deriver(real_paths)
        candidates = deriver._from_work_index()
        assert len(candidates) == 0


class TestSoftGoalDeriverLessonReviewSignal:
    """信号3（_from_lesson_review）修复后的端到端验证。"""

    def _make_deriver(self, real_paths):
        from mini_agent.evolution.soft_goal_deriver import SoftGoalDeriver

        class _FakeAutonomyCfg:
            exploration_min_calls_threshold = 2
            already_explored_cooldown_days = 30.0
            novelty_weight = 0.5

        class _FakeCfg:
            autonomy = _FakeAutonomyCfg()

        return SoftGoalDeriver(real_paths, _FakeCfg())

    def _add_lessons(self, real_paths, trigger: str, count: int):
        from mini_agent.perception.memory_store import MemoryStore, MemoryEntry

        store = MemoryStore(real_paths.workdir_memory)
        for i in range(count):
            store.add(MemoryEntry(
                session_id=f"s{i}", summary="lesson", key_outcomes=[], tags=["lesson"],
                model="t", entry_type="lesson", trigger=trigger, outcome="something happened",
                source="self_reflection",
            ))
        return store

    def test_high_frequency_lesson_group_produces_candidate(self, real_paths):
        # T1_MIN_OCCURRENCE/T1_MIN_SESSIONS 门槛见 lesson_review.py，
        # 每条 lesson 来自不同 session_id，数量给足够冗余确保达标。
        self._add_lessons(real_paths, "数据库连接超时反复出现", count=5)

        deriver = self._make_deriver(real_paths)
        candidates = deriver._from_lesson_review()  # 修复前这里必然 TypeError/ImportError

        assert len(candidates) == 1
        assert candidates[0].source_tag == "lesson"
        assert "数据库连接超时反复出现" in candidates[0].description

    def test_low_frequency_lesson_group_produces_no_candidate(self, real_paths):
        self._add_lessons(real_paths, "偶发的小问题", count=1)

        deriver = self._make_deriver(real_paths)
        candidates = deriver._from_lesson_review()
        assert len(candidates) == 0

    def test_scan_lesson_groups_helper_exists_and_works(self, real_paths):
        """scan_lesson_groups(real_paths) 此前根本不存在（纯 ImportError）。"""
        from mini_agent.perception.lesson_review import scan_lesson_groups

        self._add_lessons(real_paths, "网络请求偶尔失败", count=3)
        groups = scan_lesson_groups(real_paths)
        assert len(groups) == 1
        assert groups[0].total_occurrence == 3


class TestGoalCandidateUnvalidatedEventFlow:
    """事件总线第四条链路：goal.candidate_unvalidated 完整闭环。"""

    def _make_deriver(self, real_paths):
        from mini_agent.evolution.soft_goal_deriver import SoftGoalDeriver

        class _FakeAutonomyCfg:
            exploration_min_calls_threshold = 2
            already_explored_cooldown_days = 30.0
            novelty_weight = 0.5

        class _FakeCfg:
            autonomy = _FakeAutonomyCfg()

        return SoftGoalDeriver(real_paths, _FakeCfg())

    def test_workthread_candidate_tagged_and_event_published(self, real_paths):
        from mini_agent.perception.goal_backlog import GoalBacklog
        from mini_agent.evolution.soft_goal_deriver import _DeriveCandidate
        from mini_agent.perception import system_events as se

        backlog = GoalBacklog(real_paths)
        deriver = self._make_deriver(real_paths)
        candidates = [_DeriveCandidate(
            title="做一件事", description="desc", source_tag="workthread",
            priority=20, urgency=1.0,
        )]
        new_goals = deriver.commit_goals(candidates, backlog)

        assert len(new_goals) == 1
        assert "needs_review" in new_goals[0].tags
        assert new_goals[0].status == "active"

        events = se.poll_since(real_paths, consumer_name="peek", tiers=["tick"], advance_cursor=False)
        matched = [e for e in events if e.event_type == "goal.candidate_unvalidated"]
        assert len(matched) == 1
        assert matched[0].payload["goal_id"] == new_goals[0].id
        assert matched[0].payload["source_tag"] == "workthread"

    def test_capability_candidate_not_tagged_needs_review(self, real_paths):
        """capability 类候选走 ExplorationSandbox 验证，不应该被打
        needs_review 标签，也不应该发布 goal.candidate_unvalidated 事件。"""
        from mini_agent.perception.goal_backlog import GoalBacklog
        from mini_agent.evolution.soft_goal_deriver import _DeriveCandidate
        from mini_agent.perception import system_events as se

        backlog = GoalBacklog(real_paths)
        deriver = self._make_deriver(real_paths)
        candidates = [_DeriveCandidate(
            title="探索能力X", description="desc", source_tag="capability",
            priority=15, urgency=1.0,
        )]
        new_goals = deriver.commit_goals(candidates, backlog)

        assert "needs_review" not in new_goals[0].tags
        events = se.poll_since(real_paths, consumer_name="peek2", tiers=["tick"], advance_cursor=False)
        matched = [e for e in events if e.event_type == "goal.candidate_unvalidated"]
        assert len(matched) == 0

    def test_review_downgrades_goal_when_workthread_gone(self, real_paths):
        from mini_agent.perception.goal_backlog import GoalBacklog
        from mini_agent.evolution.soft_goal_deriver import _DeriveCandidate

        backlog = GoalBacklog(real_paths)
        deriver = self._make_deriver(real_paths)
        candidates = [_DeriveCandidate(
            title="做一件已经不存在的事", description="desc", source_tag="workthread",
            priority=20, urgency=1.0,
        )]
        new_goals = deriver.commit_goals(candidates, backlog)
        goal_id = new_goals[0].id

        # work_index 里没有任何 WorkThread → 复核判定"已不存在"
        processed = deriver.review_unvalidated_candidates(backlog)
        assert processed == 1

        node = backlog.get(goal_id)
        assert node.status == "paused"
        assert "review_failed" in node.tags
        assert "needs_review" not in node.tags
        assert "自动复核" in node.progress_notes

    def test_review_keeps_goal_when_workthread_still_stale(self, real_paths):
        import json
        from mini_agent.perception.goal_backlog import GoalBacklog
        from mini_agent.evolution.soft_goal_deriver import _DeriveCandidate
        from mini_agent.perception.workdir_knowledge import WorkThread

        thread = WorkThread(
            id="wt1", title="认证模块",
            next_suggested="继续修复认证模块",
            started_at=time.time() - 40 * 86400,
        )
        real_paths.workdir_dir.mkdir(parents=True, exist_ok=True)
        real_paths.workdir_work_index.write_text(
            json.dumps({"work_threads": [thread.to_dict()]}), encoding="utf-8",
        )

        backlog = GoalBacklog(real_paths)
        deriver = self._make_deriver(real_paths)
        candidates = [_DeriveCandidate(
            title="继续修复认证模块"[:80], description="desc", source_tag="workthread",
            priority=20, urgency=1.0,
        )]
        new_goals = deriver.commit_goals(candidates, backlog)
        goal_id = new_goals[0].id

        processed = deriver.review_unvalidated_candidates(backlog)
        assert processed == 1

        node = backlog.get(goal_id)
        assert node.status == "active"
        assert "needs_review" not in node.tags
        assert "review_failed" not in node.tags

    def test_review_is_idempotent(self, real_paths):
        from mini_agent.perception.goal_backlog import GoalBacklog
        from mini_agent.evolution.soft_goal_deriver import _DeriveCandidate

        backlog = GoalBacklog(real_paths)
        deriver = self._make_deriver(real_paths)
        candidates = [_DeriveCandidate(
            title="某候选", description="desc", source_tag="lesson",
            priority=20, urgency=1.0,
        )]
        deriver.commit_goals(candidates, backlog)

        first = deriver.review_unvalidated_candidates(backlog)
        second = deriver.review_unvalidated_candidates(backlog)
        assert first == 1
        assert second == 0  # 游标已推进，不重复处理
