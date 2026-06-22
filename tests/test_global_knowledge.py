"""
tests/test_global_knowledge.py — Stage 5 验证（W3：Global 知识层）

对应 self_evolution_stage4plus_plan.md Stage 5：
  - 5.1 self_profile.json（ensure_self_profile / load_self_profile /
        update_self_profile_on_session_end）
  - 5.2 projects_index.json（register_or_touch_project / refresh_dormant_status）
  - 5.3 activity_log.jsonl（append_activity_log / load_recent_activity）
  - 5.4 cross_project_index.json（scan_cross_project_patterns /
        merge_cross_project_patterns / update_cross_project_capability_map）

所有测试通过 monkeypatch Path.home() 隔离真实 ~/.agent/ 目录，避免污染
执行测试的机器（global_dir 直接使用 Path.home()，没有依赖注入入口，
这是测试本身需要负责隔离的地方，而不是生产代码该改的地方——生产代码里
"agent 的 home 在哪"本身就应该是真实的 Path.home()）。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from mini_agent.storage.paths import AgentPaths
from mini_agent.perception.memory_store import MemoryEntry
from mini_agent.perception.global_knowledge import (
    project_id_for,
    SelfIdentity,
    SelfAssessment,
    OperatingState,
    ResourceBudget,
    EvolutionState,
    SelfProfile,
    load_self_profile,
    save_self_profile,
    ensure_self_profile,
    update_self_profile_on_session_end,
    ProjectIndexEntry,
    ProjectsIndex,
    load_projects_index,
    save_projects_index,
    register_or_touch_project,
    refresh_dormant_status,
    append_activity_log,
    load_recent_activity,
    CrossProjectPattern,
    SkillPromotionRecord,
    CrossProjectIndex,
    load_cross_project_index,
    save_cross_project_index,
    scan_cross_project_patterns,
    merge_cross_project_patterns,
    update_cross_project_capability_map,
)


@pytest.fixture
def home_dir(tmp_path: Path, monkeypatch) -> Path:
    """隔离的虚拟 home 目录，~/.agent/ 实际落在 tmp_path 下。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    return root


@pytest.fixture
def paths(home_dir, project_root: Path) -> AgentPaths:
    return AgentPaths(project_root=project_root)


# ════════════════════════════════════════════════════════════════════════════
# project_id_for
# ════════════════════════════════════════════════════════════════════════════

class TestProjectIdFor:

    def test_stable_across_calls(self, project_root):
        assert project_id_for(project_root) == project_id_for(project_root)

    def test_different_paths_different_ids(self, tmp_path):
        a = tmp_path / "proj_a"
        b = tmp_path / "proj_b"
        a.mkdir()
        b.mkdir()
        assert project_id_for(a) != project_id_for(b)

    def test_same_name_different_path_different_id(self, tmp_path):
        a = tmp_path / "x" / "mini_agent"
        b = tmp_path / "y" / "mini_agent"
        a.mkdir(parents=True)
        b.mkdir(parents=True)
        assert project_id_for(a) != project_id_for(b)

    def test_id_starts_with_proj_prefix(self, project_root):
        assert project_id_for(project_root).startswith("proj_")


# ════════════════════════════════════════════════════════════════════════════
# 5.1 self_profile.json
# ════════════════════════════════════════════════════════════════════════════

class TestSelfProfile:

    def test_no_file_returns_none(self, paths):
        assert load_self_profile(paths) is None

    def test_ensure_creates_file_with_defaults(self, paths):
        profile = ensure_self_profile(paths)
        assert paths.global_self_profile.is_file()
        assert profile.version == 1
        assert profile.operating_state.autonomy_level == "passive"

    def test_ensure_idempotent_does_not_reset_existing(self, paths):
        profile1 = ensure_self_profile(paths)
        profile1.identity.purpose = "custom purpose"
        save_self_profile(paths, profile1)
        profile2 = ensure_self_profile(paths)
        assert profile2.identity.purpose == "custom purpose"

    def test_round_trip_preserves_all_sections(self, paths):
        profile = SelfProfile(
            version=1,
            identity=SelfIdentity(purpose="p", core_constraints_ref="ref", created_at=1.0),
            self_assessment=SelfAssessment(
                strengths=["python"], weak_areas=["bash"],
                confidence_by_domain={"python": 0.8}, last_assessed_at=2.0,
            ),
            operating_state=OperatingState(
                autonomy_level="assisted", active_project="/p", last_active_at=3.0,
                total_sessions_lifetime=5, total_projects_worked=2,
            ),
            resource_budget=ResourceBudget(daily_token_budget=1000, used_today=50),
            evolution_state=EvolutionState(
                pending_evolve_branches=["evolve/x"], last_reflection_at=4.0,
                lifetime_lessons_generated=3, lifetime_skills_proposed=1,
                lifetime_skills_approved=1,
            ),
        )
        save_self_profile(paths, profile)
        loaded = load_self_profile(paths)
        assert loaded.identity.purpose == "p"
        assert loaded.self_assessment.strengths == ["python"]
        assert loaded.operating_state.autonomy_level == "assisted"
        assert loaded.resource_budget.used_today == 50
        assert loaded.evolution_state.pending_evolve_branches == ["evolve/x"]

    def test_invalid_autonomy_level_falls_back_to_passive(self, paths):
        save_self_profile(paths, SelfProfile())  # write default
        raw = paths.global_self_profile.read_text(encoding="utf-8")
        import json
        data = json.loads(raw)
        data["operating_state"]["autonomy_level"] = "bogus_level"
        paths.global_self_profile.write_text(json.dumps(data), encoding="utf-8")
        loaded = load_self_profile(paths)
        assert loaded.operating_state.autonomy_level == "passive"

    def test_corrupted_file_does_not_raise(self, paths):
        paths.global_self_profile.parent.mkdir(parents=True, exist_ok=True)
        paths.global_self_profile.write_text("{not valid json", encoding="utf-8")
        assert load_self_profile(paths) is None

    def test_update_on_session_end_increments_total_sessions(self, paths):
        update_self_profile_on_session_end(paths, active_project="/p1")
        profile2 = update_self_profile_on_session_end(paths, active_project="/p1")
        assert profile2.operating_state.total_sessions_lifetime == 2

    def test_update_on_session_end_sets_active_project(self, paths):
        profile = update_self_profile_on_session_end(paths, active_project="/p1")
        assert profile.operating_state.active_project == "/p1"

    def test_update_on_session_end_updates_last_active_at(self, paths):
        profile1 = update_self_profile_on_session_end(paths, active_project="/p1")
        time.sleep(0.01)
        profile2 = update_self_profile_on_session_end(paths, active_project="/p1")
        assert profile2.operating_state.last_active_at > profile1.operating_state.last_active_at

    def test_update_on_session_end_accumulates_tokens(self, paths):
        update_self_profile_on_session_end(paths, active_project="/p1", tokens_used=100)
        profile2 = update_self_profile_on_session_end(paths, active_project="/p1", tokens_used=50)
        assert profile2.resource_budget.used_today == 150

    def test_update_on_session_end_resets_tokens_on_new_calendar_day(self, paths):
        import datetime as _dt
        update_self_profile_on_session_end(paths, active_project="/p1", tokens_used=100)

        # 手动把 last_active_at 改写为"昨天"，模拟跨日
        profile = load_self_profile(paths)
        yesterday = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=1)
        profile.operating_state.last_active_at = yesterday.timestamp()
        save_self_profile(paths, profile)

        profile2 = update_self_profile_on_session_end(paths, active_project="/p1", tokens_used=50)
        assert profile2.resource_budget.used_today == 50

    def test_update_on_session_end_first_token_usage_does_not_inherit_stale_baseline(self, paths):
        """首次产生 tokens_used 时（self_profile 刚创建，last_active_at=0），
        即便磁盘上 used_today 已有陈旧数值，也应该视为新的一天重新计数。"""
        profile = ensure_self_profile(paths)
        profile.resource_budget.used_today = 9999  # 模拟陈旧/手工写入的脏数据
        save_self_profile(paths, profile)

        profile2 = update_self_profile_on_session_end(paths, active_project="/p1", tokens_used=10)
        assert profile2.resource_budget.used_today == 10

    def test_self_assessment_prompt_block_empty_when_no_data(self):
        assert SelfAssessment().to_prompt_block() == ""

    def test_self_assessment_prompt_block_includes_strengths(self):
        block = SelfAssessment(strengths=["python refactoring"]).to_prompt_block()
        assert "python refactoring" in block
        assert "Self-assessment" in block


# ════════════════════════════════════════════════════════════════════════════
# 5.2 projects_index.json
# ════════════════════════════════════════════════════════════════════════════

class TestProjectsIndex:

    def test_no_file_returns_empty(self, paths):
        index = load_projects_index(paths)
        assert index.projects == []
        assert index.active_project_id is None

    def test_register_new_project(self, paths, project_root):
        entry = register_or_touch_project(paths, project_root)
        assert paths.global_projects_index.is_file()
        assert entry.total_sessions == 1
        assert entry.status == "active"

    def test_register_sets_active_project_id(self, paths, project_root):
        entry = register_or_touch_project(paths, project_root)
        index = load_projects_index(paths)
        assert index.active_project_id == entry.id

    def test_touch_existing_increments_sessions(self, paths, project_root):
        register_or_touch_project(paths, project_root)
        entry2 = register_or_touch_project(paths, project_root)
        assert entry2.total_sessions == 2

    def test_two_different_projects_two_entries(self, paths, tmp_path):
        root_a = tmp_path / "a"
        root_b = tmp_path / "b"
        root_a.mkdir()
        root_b.mkdir()
        register_or_touch_project(paths, root_a)
        register_or_touch_project(paths, root_b)
        index = load_projects_index(paths)
        assert len(index.projects) == 2

    def test_register_updates_self_profile_total_projects_worked(self, paths, tmp_path):
        root_a = tmp_path / "a"
        root_b = tmp_path / "b"
        root_a.mkdir()
        root_b.mkdir()
        register_or_touch_project(paths, root_a)
        register_or_touch_project(paths, root_b)
        profile = load_self_profile(paths)
        assert profile.operating_state.total_projects_worked == 2

    def test_touching_existing_project_does_not_double_count_projects_worked(self, paths, project_root):
        register_or_touch_project(paths, project_root)
        register_or_touch_project(paths, project_root)
        profile = load_self_profile(paths)
        assert profile.operating_state.total_projects_worked == 1

    def test_dormant_detection_marks_old_project(self, paths, project_root):
        register_or_touch_project(paths, project_root)
        index = load_projects_index(paths)
        index.projects[0].last_active = time.time() - 31 * 86400
        save_projects_index(paths, index)
        changed = refresh_dormant_status(paths, dormant_after_days=30.0)
        assert changed == 1
        index2 = load_projects_index(paths)
        assert index2.projects[0].status == "dormant"

    def test_dormant_detection_skips_recent_project(self, paths, project_root):
        register_or_touch_project(paths, project_root)
        changed = refresh_dormant_status(paths, dormant_after_days=30.0)
        assert changed == 0
        index = load_projects_index(paths)
        assert index.projects[0].status == "active"

    def test_touching_dormant_project_revives_it(self, paths, project_root):
        register_or_touch_project(paths, project_root)
        index = load_projects_index(paths)
        index.projects[0].status = "dormant"
        index.projects[0].last_active = time.time() - 60 * 86400
        save_projects_index(paths, index)

        register_or_touch_project(paths, project_root)
        index2 = load_projects_index(paths)
        assert index2.projects[0].status == "active"

    def test_corrupted_file_returns_empty(self, paths):
        paths.global_projects_index.parent.mkdir(parents=True, exist_ok=True)
        paths.global_projects_index.write_text("{not valid", encoding="utf-8")
        index = load_projects_index(paths)
        assert index.projects == []


# ════════════════════════════════════════════════════════════════════════════
# 5.3 activity_log.jsonl
# ════════════════════════════════════════════════════════════════════════════

class TestActivityLog:

    def test_no_file_returns_empty(self, paths):
        assert load_recent_activity(paths) == []

    def test_append_then_load(self, paths):
        append_activity_log(paths, project_id="proj_x", session_id="s1", theme="t1", duration_min=10.0)
        records = load_recent_activity(paths)
        assert len(records) == 1
        assert records[0]["project_id"] == "proj_x"
        assert records[0]["sid"] == "s1"
        assert records[0]["theme"] == "t1"

    def test_multiple_appends_preserve_order(self, paths):
        append_activity_log(paths, "p1", "s1", "first", 1.0)
        append_activity_log(paths, "p2", "s2", "second", 2.0)
        records = load_recent_activity(paths)
        assert [r["sid"] for r in records] == ["s1", "s2"]

    def test_limit_returns_most_recent(self, paths):
        for i in range(5):
            append_activity_log(paths, "p1", f"s{i}", f"theme{i}", 1.0)
        records = load_recent_activity(paths, limit=2)
        assert [r["sid"] for r in records] == ["s3", "s4"]

    def test_malformed_line_skipped(self, paths):
        append_activity_log(paths, "p1", "s1", "good", 1.0)
        with open(paths.global_activity_log, "a", encoding="utf-8") as f:
            f.write("not valid json\n")
        append_activity_log(paths, "p1", "s2", "also good", 1.0)
        records = load_recent_activity(paths)
        assert [r["sid"] for r in records] == ["s1", "s2"]


# ════════════════════════════════════════════════════════════════════════════
# 5.4 cross_project_index.json
# ════════════════════════════════════════════════════════════════════════════

def _write_lesson(project_root: Path, trigger: str, confidence: float = 0.8, tags=None) -> None:
    """在指定 workdir 的 memory.jsonl 里追加一条 lesson 条目（测试辅助）。"""
    p = AgentPaths(project_root)
    p.workdir_memory.parent.mkdir(parents=True, exist_ok=True)
    entry = MemoryEntry(
        session_id="s1",
        summary="",
        key_outcomes=[],
        tags=tags or [],
        model="test-model",
        entry_type="lesson",
        trigger=trigger,
        confidence=confidence,
    )
    import json as _json
    from dataclasses import asdict
    with open(p.workdir_memory, "a", encoding="utf-8") as f:
        f.write(_json.dumps(asdict(entry), ensure_ascii=False) + "\n")


class TestCrossProjectIndex:

    def test_no_file_returns_empty(self, paths):
        index = load_cross_project_index(paths)
        assert index.cross_project_patterns == []

    def test_round_trip(self, paths):
        index = CrossProjectIndex(
            cross_project_patterns=[
                CrossProjectPattern(
                    id="cpp_001", title="bash rm danger",
                    observed_in_projects=["proj_a", "proj_b"],
                    occurrence_count=4, confidence=0.9,
                    pattern_type="risk", derived_from_lessons=["l1", "l2"],
                    global_skill_candidate=True,
                )
            ],
            skill_promotion_history=[
                SkillPromotionRecord(
                    skill_name="bash-safety", promoted_from="proj_a",
                    promoted_at=1.0, trigger_pattern="cpp_001",
                )
            ],
            cross_project_capability_map={"python": {"confidence": 0.8}},
        )
        save_cross_project_index(paths, index)
        loaded = load_cross_project_index(paths)
        assert loaded.cross_project_patterns[0].title == "bash rm danger"
        assert loaded.skill_promotion_history[0].skill_name == "bash-safety"
        assert loaded.cross_project_capability_map["python"]["confidence"] == 0.8

    def test_scan_finds_cross_project_pattern(self, tmp_path):
        root_a = tmp_path / "proj_a"
        root_b = tmp_path / "proj_b"
        root_a.mkdir()
        root_b.mkdir()
        _write_lesson(root_a, "bash rm -rf 删除了重要文件", confidence=0.9, tags=["risk"])
        _write_lesson(root_b, "bash rm -rf 删除了重要文件", confidence=0.8, tags=["risk"])

        patterns = scan_cross_project_patterns([root_a, root_b])
        assert len(patterns) == 1
        assert len(patterns[0].observed_in_projects) == 2
        assert patterns[0].occurrence_count == 2

    def test_scan_ignores_single_project_pattern(self, tmp_path):
        root_a = tmp_path / "proj_a"
        root_a.mkdir()
        _write_lesson(root_a, "只在一个项目里出现的教训", confidence=0.9)
        _write_lesson(root_a, "完全不同的另一条教训内容", confidence=0.9)

        patterns = scan_cross_project_patterns([root_a])
        assert patterns == []

    def test_scan_handles_missing_memory_file(self, tmp_path):
        root_a = tmp_path / "proj_a"
        root_a.mkdir()
        patterns = scan_cross_project_patterns([root_a])
        assert patterns == []

    def test_scan_global_skill_candidate_when_confidence_high(self, tmp_path):
        root_a = tmp_path / "proj_a"
        root_b = tmp_path / "proj_b"
        root_a.mkdir()
        root_b.mkdir()
        _write_lesson(root_a, "重构前先跑测试可以减少回归问题出现", confidence=0.9)
        _write_lesson(root_b, "重构前先跑测试可以减少回归问题出现", confidence=0.85)

        patterns = scan_cross_project_patterns(
            [root_a, root_b],
            min_projects_for_candidate=2,
            confidence_threshold_for_candidate=0.7,
        )
        assert len(patterns) == 1
        assert patterns[0].global_skill_candidate is True

    def test_scan_not_candidate_when_confidence_low(self, tmp_path):
        root_a = tmp_path / "proj_a"
        root_b = tmp_path / "proj_b"
        root_a.mkdir()
        root_b.mkdir()
        _write_lesson(root_a, "一个不太确定的模式描述文本", confidence=0.3)
        _write_lesson(root_b, "一个不太确定的模式描述文本", confidence=0.2)

        patterns = scan_cross_project_patterns(
            [root_a, root_b],
            confidence_threshold_for_candidate=0.7,
        )
        assert len(patterns) == 1
        assert patterns[0].global_skill_candidate is False

    def test_merge_adds_new_patterns(self, paths, tmp_path):
        root_a = tmp_path / "proj_a"
        root_b = tmp_path / "proj_b"
        root_a.mkdir()
        root_b.mkdir()
        _write_lesson(root_a, "重复出现的风险操作模式描述", confidence=0.9, tags=["risk"])
        _write_lesson(root_b, "重复出现的风险操作模式描述", confidence=0.9, tags=["risk"])

        scanned = scan_cross_project_patterns([root_a, root_b])
        index = merge_cross_project_patterns(paths, scanned)
        assert len(index.cross_project_patterns) == 1

    def test_merge_preserves_promotion_state(self, paths, tmp_path):
        root_a = tmp_path / "proj_a"
        root_b = tmp_path / "proj_b"
        root_a.mkdir()
        root_b.mkdir()
        _write_lesson(root_a, "已经晋升过的模式描述文本", confidence=0.9, tags=["risk"])
        _write_lesson(root_b, "已经晋升过的模式描述文本", confidence=0.9, tags=["risk"])

        scanned = scan_cross_project_patterns([root_a, root_b])
        index = merge_cross_project_patterns(paths, scanned)

        # 模拟晋升流程（Stage 8 范畴）手动标记
        index.cross_project_patterns[0].promoted_to_skill = "bash-safety"
        index.cross_project_patterns[0].promoted_at = time.time()
        save_cross_project_index(paths, index)

        # 再次扫描合并（数据没变化），晋升状态应该保留
        scanned2 = scan_cross_project_patterns([root_a, root_b])
        index2 = merge_cross_project_patterns(paths, scanned2)
        assert index2.cross_project_patterns[0].promoted_to_skill == "bash-safety"

    def test_merge_keeps_stale_entries_not_seen_in_latest_scan(self, paths):
        old_pattern = CrossProjectPattern(
            id="cpp_old", title="old pattern",
            observed_in_projects=["proj_x", "proj_y"],
            derived_from_lessons=["lx", "ly"],
        )
        save_cross_project_index(paths, CrossProjectIndex(cross_project_patterns=[old_pattern]))
        merged = merge_cross_project_patterns(paths, [])
        assert len(merged.cross_project_patterns) == 1
        assert merged.cross_project_patterns[0].id == "cpp_old"

    def test_update_capability_map_writes_back_to_self_profile(self, paths):
        update_cross_project_capability_map(
            paths, {"python_refactoring": {"confidence": 0.85, "sample_projects": 2}},
        )
        index = load_cross_project_index(paths)
        assert index.cross_project_capability_map["python_refactoring"]["confidence"] == 0.85

        profile = load_self_profile(paths)
        assert profile.self_assessment.confidence_by_domain["python_refactoring"] == 0.85

    def test_update_capability_map_empty_dict_is_noop(self, paths):
        save_cross_project_index(paths, CrossProjectIndex(cross_project_capability_map={"x": 1}))
        update_cross_project_capability_map(paths, {})
        index = load_cross_project_index(paths)
        assert index.cross_project_capability_map == {"x": 1}
