"""
tests/test_workdir_knowledge.py — Stage 4 验证（W2：Workdir 知识层）

对应 self_evolution_stage4plus_plan.md Stage 4：
  - 4.1 project.json（ensure_project_meta / load_project_meta）
  - 4.2 timeline.jsonl（append_timeline_entry / load_recent_timeline）
  - 4.3 work_index.json（WorkThread CRUD + relate_session_to_work_thread 启发式）
  - 4.4 open_threads.json（add_open_thread / import_unresolved_from_manifest /
        get_high_priority_open_threads）
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from mini_agent.storage.paths import AgentPaths
from mini_agent.perception.workdir_knowledge import (
    ProjectMeta,
    ensure_project_meta,
    load_project_meta,
    capture_environment_fingerprint,
    detect_environment_drift,
    append_timeline_entry,
    load_recent_timeline,
    WorkThread,
    load_work_index,
    save_work_index,
    get_active_work_threads,
    find_work_thread,
    upsert_work_thread,
    relate_session_to_work_thread,
    OpenThread,
    load_open_threads,
    save_open_threads,
    add_open_thread,
    import_unresolved_from_manifest,
    get_high_priority_open_threads,
    KnowledgeIndexEntry,
    load_knowledge_index,
    save_knowledge_index,
    upsert_knowledge_index_entry,
    search_knowledge_index,
    read_knowledge_section,
)


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def paths(project_root: Path) -> AgentPaths:
    return AgentPaths(project_root=project_root)


# ════════════════════════════════════════════════════════════════════════════
# 4.1 project.json
# ════════════════════════════════════════════════════════════════════════════

class TestProjectMeta:

    def test_no_file_returns_none(self, paths):
        assert load_project_meta(paths) is None

    def test_first_call_creates_file(self, paths, project_root):
        meta = ensure_project_meta(paths, project_root)
        assert paths.workdir_project_meta.is_file()
        assert meta.total_sessions == 1
        assert meta.name == project_root.name

    def test_second_call_increments_total_sessions(self, paths, project_root):
        ensure_project_meta(paths, project_root)
        meta2 = ensure_project_meta(paths, project_root)
        assert meta2.total_sessions == 2

    def test_second_call_updates_last_active(self, paths, project_root):
        meta1 = ensure_project_meta(paths, project_root)
        time.sleep(0.01)
        meta2 = ensure_project_meta(paths, project_root)
        assert meta2.last_active > meta1.last_active

    def test_fallback_name_used_on_first_create(self, paths, project_root):
        meta = ensure_project_meta(paths, project_root, fallback_name="custom-name")
        assert meta.name == "custom-name"

    def test_persisted_across_loads(self, paths, project_root):
        ensure_project_meta(paths, project_root)
        loaded = load_project_meta(paths)
        assert loaded is not None
        assert loaded.total_sessions == 1

    def test_to_prompt_block_includes_name(self, paths, project_root):
        meta = ensure_project_meta(paths, project_root, fallback_name="my-proj")
        block = meta.to_prompt_block()
        assert "my-proj" in block
        assert "Project identity" in block

    def test_to_prompt_block_empty_meta_still_has_header(self):
        meta = ProjectMeta()
        block = meta.to_prompt_block()
        assert "Project identity" in block

    def test_corrupted_file_does_not_raise(self, paths):
        paths.workdir_project_meta.parent.mkdir(parents=True, exist_ok=True)
        paths.workdir_project_meta.write_text("{not valid json", encoding="utf-8")
        assert load_project_meta(paths) is None


# ════════════════════════════════════════════════════════════════════════════
# 4.2 timeline.jsonl
# ════════════════════════════════════════════════════════════════════════════

class TestTimeline:

    def test_no_file_returns_empty(self, paths):
        assert load_recent_timeline(paths) == []

    def test_append_creates_file(self, paths):
        append_timeline_entry(paths, "s1", 5.0, "theme1", ["out1"], 2)
        assert paths.workdir_timeline.is_file()

    def test_append_then_load(self, paths):
        append_timeline_entry(paths, "s1", 5.0, "theme1", ["out1"], 2)
        entries = load_recent_timeline(paths)
        assert len(entries) == 1
        assert entries[0]["sid"] == "s1"
        assert entries[0]["theme"] == "theme1"
        assert entries[0]["key_outcomes"] == ["out1"]
        assert entries[0]["task_count"] == 2
        assert entries[0]["status"] == "done"

    def test_multiple_appends_preserve_order(self, paths):
        append_timeline_entry(paths, "s1", 1.0, "first", [], 0)
        append_timeline_entry(paths, "s2", 2.0, "second", [], 0)
        append_timeline_entry(paths, "s3", 3.0, "third", [], 0)
        entries = load_recent_timeline(paths)
        assert [e["sid"] for e in entries] == ["s1", "s2", "s3"]

    def test_limit_returns_most_recent(self, paths):
        for i in range(5):
            append_timeline_entry(paths, f"s{i}", 1.0, f"theme{i}", [], 0)
        entries = load_recent_timeline(paths, limit=2)
        assert [e["sid"] for e in entries] == ["s3", "s4"]

    def test_malformed_line_is_skipped(self, paths):
        append_timeline_entry(paths, "s1", 1.0, "good", [], 0)
        with open(paths.workdir_timeline, "a", encoding="utf-8") as f:
            f.write("not valid json\n")
        append_timeline_entry(paths, "s2", 1.0, "also good", [], 0)
        entries = load_recent_timeline(paths)
        assert [e["sid"] for e in entries] == ["s1", "s2"]


# ════════════════════════════════════════════════════════════════════════════
# 4.3 work_index.json
# ════════════════════════════════════════════════════════════════════════════

class TestWorkIndex:

    def test_no_file_returns_empty(self, paths):
        assert load_work_index(paths) == []
        assert get_active_work_threads(paths) == []

    def test_upsert_creates_new(self, paths):
        t = WorkThread(id="wt_1", title="实现 W2")
        upsert_work_thread(paths, t)
        threads = load_work_index(paths)
        assert len(threads) == 1
        assert threads[0].id == "wt_1"

    def test_upsert_overwrites_existing(self, paths):
        upsert_work_thread(paths, WorkThread(id="wt_1", title="v1"))
        upsert_work_thread(paths, WorkThread(id="wt_1", title="v2", status="done"))
        threads = load_work_index(paths)
        assert len(threads) == 1
        assert threads[0].title == "v2"
        assert threads[0].status == "done"

    def test_find_work_thread_by_id(self, paths):
        upsert_work_thread(paths, WorkThread(id="wt_1", title="A"))
        upsert_work_thread(paths, WorkThread(id="wt_2", title="B"))
        found = find_work_thread(paths, "wt_2")
        assert found is not None
        assert found.title == "B"

    def test_find_work_thread_missing_returns_none(self, paths):
        assert find_work_thread(paths, "nonexistent") is None

    def test_get_active_work_threads_filters_by_status(self, paths):
        upsert_work_thread(paths, WorkThread(id="wt_1", title="A", status="active"))
        upsert_work_thread(paths, WorkThread(id="wt_2", title="B", status="done"))
        upsert_work_thread(paths, WorkThread(id="wt_3", title="C", status="paused"))
        active = get_active_work_threads(paths)
        assert [t.id for t in active] == ["wt_1"]

    # ── last_activity_at（system-events-bus-guide.md 第8节遗留项）───────────

    def test_last_activity_at_defaults_close_to_now(self, paths):
        before = time.time()
        upsert_work_thread(paths, WorkThread(id="wt_1", title="A"))
        loaded = find_work_thread(paths, "wt_1")
        assert loaded.last_activity_at >= before

    def test_upsert_refreshes_last_activity_at_even_if_started_at_unchanged(self, paths):
        """持续被推进的 thread，started_at 不变，但 last_activity_at 应该跟着更新——
        这正是修复前用 started_at 近似时会误判"长期无进展"的场景。"""
        old_ts = time.time() - 40 * 86400
        upsert_work_thread(paths, WorkThread(id="wt_1", title="v1", started_at=old_ts))
        first = find_work_thread(paths, "wt_1")
        assert first.started_at == old_ts

        before_second_upsert = time.time()
        upsert_work_thread(paths, WorkThread(id="wt_1", title="v2", started_at=old_ts))
        second = find_work_thread(paths, "wt_1")
        assert second.started_at == old_ts  # 创建时间没变
        assert second.last_activity_at >= before_second_upsert  # 但活跃时间刷新了

    def test_last_activity_at_backward_compat_missing_field_falls_back_to_started_at(self, paths):
        """老数据（没有 last_activity_at 字段）加载时应回退到 started_at，
        而不是报错或变成 0。"""
        import json

        old_ts = time.time() - 10 * 86400
        paths.workdir_dir.mkdir(parents=True, exist_ok=True)
        legacy = {
            "id": "wt_legacy", "title": "老数据", "status": "active",
            "started_at": old_ts, "related_sessions": [], "cumulative_progress": "",
            "open_questions": [], "next_suggested": "", "related_goal_id": None,
        }
        paths.workdir_work_index.write_text(
            json.dumps({"work_threads": [legacy]}), encoding="utf-8",
        )
        loaded = find_work_thread(paths, "wt_legacy")
        assert loaded is not None
        assert loaded.last_activity_at == old_ts

    def test_related_sessions_round_trip(self, paths):
        t = WorkThread(id="wt_1", title="A", related_sessions=["s1", "s2"])
        upsert_work_thread(paths, t)
        loaded = find_work_thread(paths, "wt_1")
        assert loaded.related_sessions == ["s1", "s2"]

    # ── relate_session_to_work_thread 启发式 ────────────────────────────────

    def test_relate_no_active_threads_returns_none(self, paths):
        result = relate_session_to_work_thread(paths, "s1", "some goal text")
        assert result is None

    def test_relate_title_substring_match(self, paths):
        upsert_work_thread(paths, WorkThread(id="wt_1", title="自我进化机制实现", status="active"))
        result = relate_session_to_work_thread(paths, "s1", "继续推进自我进化机制实现的工作")
        assert result is not None
        assert result.id == "wt_1"
        assert "s1" in result.related_sessions

    def test_relate_does_not_duplicate_session_id(self, paths):
        upsert_work_thread(paths, WorkThread(
            id="wt_1", title="自我进化机制实现", status="active", related_sessions=["s1"],
        ))
        result = relate_session_to_work_thread(paths, "s1", "自我进化机制实现")
        assert result.related_sessions.count("s1") == 1

    def test_relate_no_match_returns_none(self, paths):
        # save_work_index 写入后 last_updated 是"现在"，所以"近期触达"判据会命中；
        # 用 relation_days=0 让时间窗口宽度为 0，配合一个不匹配标题的 goal 文本，
        # 验证两个判据都不命中时返回 None。
        upsert_work_thread(paths, WorkThread(id="wt_1", title="完全不相关的标题", status="active"))
        result = relate_session_to_work_thread(
            paths, "s1", "无关的目标文本", relation_days=0.0,
        )
        assert result is None

    def test_relate_does_not_create_new_thread(self, paths):
        """纯启发式不应该新建 WorkThread——返回 None 但不应往 work_index 写入新条目。"""
        result = relate_session_to_work_thread(paths, "s1", "全新的工作内容")
        assert result is None
        assert load_work_index(paths) == []


# ════════════════════════════════════════════════════════════════════════════
# 4.4 open_threads.json
# ════════════════════════════════════════════════════════════════════════════

class TestOpenThreads:

    def test_no_file_returns_empty(self, paths):
        assert load_open_threads(paths) == []

    def test_add_open_thread_basic(self, paths):
        item = add_open_thread(paths, "标题", "sess1")
        assert item.id == "ot_001"
        assert item.title == "标题"
        assert item.discovered_in == "sess1"
        assert item.type == "question"        # 默认值
        assert item.priority == "medium"       # 默认值
        assert item.status == "open"

    def test_add_open_thread_invalid_type_falls_back(self, paths):
        item = add_open_thread(paths, "t", "s1", type="not_a_real_type")
        assert item.type == "question"

    def test_add_open_thread_invalid_priority_falls_back(self, paths):
        item = add_open_thread(paths, "t", "s1", priority="urgent!!")
        assert item.priority == "medium"

    def test_add_open_thread_ids_increment(self, paths):
        i1 = add_open_thread(paths, "a", "s1")
        i2 = add_open_thread(paths, "b", "s1")
        i3 = add_open_thread(paths, "c", "s1")
        assert [i1.id, i2.id, i3.id] == ["ot_001", "ot_002", "ot_003"]

    def test_persisted_across_loads(self, paths):
        add_open_thread(paths, "标题", "sess1", type="bug", priority="high")
        items = load_open_threads(paths)
        assert len(items) == 1
        assert items[0].type == "bug"
        assert items[0].priority == "high"

    def test_import_unresolved_creates_items(self, paths):
        created = import_unresolved_from_manifest(
            paths, "sess1", ["还差一个edge case", "需要补充测试"],
        )
        assert len(created) == 2
        for item in created:
            assert item.type == "tech_debt"
            assert item.priority == "medium"
            assert item.discovered_in == "sess1"

    def test_import_unresolved_empty_list_noop(self, paths):
        created = import_unresolved_from_manifest(paths, "sess1", [])
        assert created == []
        assert load_open_threads(paths) == []

    def test_import_unresolved_skips_blank_entries(self, paths):
        created = import_unresolved_from_manifest(paths, "sess1", ["", "  ", "real one"])
        assert len(created) == 1
        assert created[0].description == "real one"

    def test_import_unresolved_appends_to_existing(self, paths):
        add_open_thread(paths, "existing", "sess0")
        import_unresolved_from_manifest(paths, "sess1", ["new issue"])
        items = load_open_threads(paths)
        assert len(items) == 2

    def test_get_high_priority_filters_correctly(self, paths):
        add_open_thread(paths, "low one", "s1", priority="low")
        add_open_thread(paths, "high one", "s1", priority="high")
        add_open_thread(paths, "medium one", "s1", priority="medium")
        result = get_high_priority_open_threads(paths)
        assert len(result) == 1
        assert result[0].title == "high one"

    def test_get_high_priority_excludes_resolved(self, paths):
        add_open_thread(paths, "resolved high", "s1", priority="high")
        all_items = load_open_threads(paths)
        all_items[0].status = "resolved"
        save_open_threads(paths, all_items)
        result = get_high_priority_open_threads(paths)
        assert result == []

    def test_get_high_priority_respects_limit(self, paths):
        for i in range(10):
            add_open_thread(paths, f"item{i}", "s1", priority="high")
        result = get_high_priority_open_threads(paths, limit=3)
        assert len(result) == 3

    def test_corrupted_file_does_not_raise(self, paths):
        paths.workdir_open_threads.parent.mkdir(parents=True, exist_ok=True)
        paths.workdir_open_threads.write_text("{broken", encoding="utf-8")
        assert load_open_threads(paths) == []


# ════════════════════════════════════════════════════════════════════════════
# 12.2 横向加固：environment_fingerprint / detect_environment_drift
# ════════════════════════════════════════════════════════════════════════════

class TestEnvironmentFingerprint:

    def test_capture_returns_expected_keys(self, project_root):
        fp = capture_environment_fingerprint(project_root)
        assert set(fp.keys()) == {"python_version", "os", "key_deps", "captured_at"}
        assert fp["python_version"]
        assert fp["os"]
        assert isinstance(fp["key_deps"], dict)

    def test_no_pyproject_results_in_empty_key_deps(self, project_root):
        fp = capture_environment_fingerprint(project_root)
        assert fp["key_deps"] == {}

    def test_pyproject_dependencies_are_resolved(self, project_root):
        (project_root / "pyproject.toml").write_text(
            '[project]\ndependencies = [\n  "pytest",\n]\n', encoding="utf-8",
        )
        fp = capture_environment_fingerprint(project_root)
        assert "pytest" in fp["key_deps"]

    def test_pyproject_with_unresolvable_dep_is_skipped(self, project_root):
        (project_root / "pyproject.toml").write_text(
            '[project]\ndependencies = [\n  "this-package-does-not-exist-xyz",\n]\n',
            encoding="utf-8",
        )
        fp = capture_environment_fingerprint(project_root)
        assert "this-package-does-not-exist-xyz" not in fp["key_deps"]

    def test_ensure_project_meta_populates_fingerprint(self, paths, project_root):
        meta = ensure_project_meta(paths, project_root)
        assert meta.environment_fingerprint
        assert "python_version" in meta.environment_fingerprint

    def test_ensure_project_meta_refreshes_fingerprint_on_repeat_calls(self, paths, project_root):
        meta1 = ensure_project_meta(paths, project_root)
        meta2 = ensure_project_meta(paths, project_root)
        assert meta2.environment_fingerprint["captured_at"] >= meta1.environment_fingerprint["captured_at"]


class TestDetectEnvironmentDrift:

    def test_empty_old_fp_returns_no_drift(self):
        assert detect_environment_drift({}, {"python_version": "3.12"}) == []

    def test_identical_fingerprints_no_drift(self):
        fp = {"python_version": "3.12", "os": "Linux", "key_deps": {"a": "1.0"}}
        assert detect_environment_drift(fp, dict(fp)) == []

    def test_python_version_change_detected(self):
        old = {"python_version": "3.11", "os": "Linux", "key_deps": {}}
        new = {"python_version": "3.12", "os": "Linux", "key_deps": {}}
        drift = detect_environment_drift(old, new)
        assert any("python_version" in d for d in drift)

    def test_os_change_detected(self):
        old = {"python_version": "3.12", "os": "Linux", "key_deps": {}}
        new = {"python_version": "3.12", "os": "Darwin", "key_deps": {}}
        drift = detect_environment_drift(old, new)
        assert any("os:" in d for d in drift)

    def test_key_dep_version_change_detected(self):
        old = {"python_version": "3.12", "os": "Linux", "key_deps": {"anthropic": "0.30.0"}}
        new = {"python_version": "3.12", "os": "Linux", "key_deps": {"anthropic": "0.40.0"}}
        drift = detect_environment_drift(old, new)
        assert any("anthropic" in d for d in drift)

    def test_new_dep_added_detected(self):
        old = {"python_version": "3.12", "os": "Linux", "key_deps": {}}
        new = {"python_version": "3.12", "os": "Linux", "key_deps": {"newpkg": "1.0"}}
        drift = detect_environment_drift(old, new)
        assert any("newpkg" in d for d in drift)


# ════════════════════════════════════════════════════════════════════════════
# 14.1 横向加固：knowledge_index.json
# ════════════════════════════════════════════════════════════════════════════

class TestKnowledgeIndex:

    def test_no_file_returns_empty(self, paths):
        assert load_knowledge_index(paths) == []

    def test_upsert_creates_new_entry(self, paths):
        entry = upsert_knowledge_index_entry(paths, heading="架构决策", summary="选了方案A")
        assert entry.id == "kn_001"
        assert entry.heading == "架构决策"
        assert entry.summary == "选了方案A"

    def test_persisted_across_loads(self, paths):
        upsert_knowledge_index_entry(paths, heading="标题", topic="storage")
        entries = load_knowledge_index(paths)
        assert len(entries) == 1
        assert entries[0].topic == "storage"

    def test_upsert_same_heading_updates_not_duplicates(self, paths):
        upsert_knowledge_index_entry(paths, heading="标题", summary="v1")
        upsert_knowledge_index_entry(paths, heading="标题", summary="v2")
        entries = load_knowledge_index(paths)
        assert len(entries) == 1
        assert entries[0].summary == "v2"

    def test_upsert_keeps_id_stable_across_updates(self, paths):
        e1 = upsert_knowledge_index_entry(paths, heading="标题", summary="v1")
        e2 = upsert_knowledge_index_entry(paths, heading="标题", summary="v2")
        assert e1.id == e2.id

    def test_upsert_preserves_unset_fields_on_update(self, paths):
        upsert_knowledge_index_entry(paths, heading="标题", topic="mcp", decision_type="architecture")
        updated = upsert_knowledge_index_entry(paths, heading="标题", summary="new summary")
        # topic/decision_type 未在第二次调用中传入，应保留原值
        assert updated.topic == "mcp"
        assert updated.decision_type == "architecture"

    def test_affected_modules_round_trip(self, paths):
        upsert_knowledge_index_entry(
            paths, heading="标题", affected_modules=["a.py", "b.py"],
        )
        entries = load_knowledge_index(paths)
        assert entries[0].affected_modules == ["a.py", "b.py"]

    def test_ids_increment_across_different_headings(self, paths):
        e1 = upsert_knowledge_index_entry(paths, heading="标题1")
        e2 = upsert_knowledge_index_entry(paths, heading="标题2")
        assert e1.id != e2.id

    def test_corrupted_file_does_not_raise(self, paths):
        paths.workdir_knowledge_index.parent.mkdir(parents=True, exist_ok=True)
        paths.workdir_knowledge_index.write_text("{broken", encoding="utf-8")
        assert load_knowledge_index(paths) == []


# ════════════════════════════════════════════════════════════════════════════
# 检索侧补全：search_knowledge_index / read_knowledge_section
# ════════════════════════════════════════════════════════════════════════════
#
# 补的是设计文档 8.4 节"knowledge.md 相关段落，按本次 session 意图检索后
# 注入"那一项——此前 update_knowledge() 只把 knowledge.md 写进去、
# upsert_knowledge_index_entry() 维护了索引，但没有任何函数把索引或正文
# 读出来供 agent 按需检索，这里是补上的读取/检索侧。

class TestSearchKnowledgeIndex:

    def test_empty_index_returns_empty(self, paths):
        assert search_knowledge_index(paths, "任意查询") == []

    def test_empty_query_returns_empty(self, paths):
        upsert_knowledge_index_entry(paths, heading="标题", summary="内容")
        assert search_knowledge_index(paths, "") == []

    def test_finds_matching_entry(self, paths):
        upsert_knowledge_index_entry(
            paths, heading="数据库选型", summary="选择了 SQLite 而不是 Postgres",
            topic="storage",
        )
        upsert_knowledge_index_entry(
            paths, heading="鉴权方案", summary="使用 JWT token", topic="auth",
        )
        results = search_knowledge_index(paths, "SQLite Postgres 数据库")
        assert len(results) >= 1
        assert results[0][0].heading == "数据库选型"

    def test_results_sorted_by_score_descending(self, paths):
        upsert_knowledge_index_entry(paths, heading="A", summary="数据库 数据库 数据库连接池")
        upsert_knowledge_index_entry(paths, heading="B", summary="提到一次数据库")
        results = search_knowledge_index(paths, "数据库")
        scores = [s for _, s in results]
        assert scores == sorted(scores, reverse=True)

    def test_unrelated_query_returns_empty(self, paths):
        upsert_knowledge_index_entry(paths, heading="数据库选型", summary="SQLite vs Postgres")
        assert search_knowledge_index(paths, "量子计算机外星人") == []

    def test_k_limits_results(self, paths):
        for i in range(5):
            upsert_knowledge_index_entry(paths, heading=f"标题{i}", summary="共同关键词 架构 决策")
        results = search_knowledge_index(paths, "架构 决策", k=2)
        assert len(results) <= 2

    def test_topic_filter_narrows_candidates(self, paths):
        upsert_knowledge_index_entry(paths, heading="A", summary="集成方式说明", topic="mcp")
        upsert_knowledge_index_entry(paths, heading="B", summary="集成方式说明", topic="auth")
        results = search_knowledge_index(paths, "集成方式", topic="mcp")
        assert all(e.topic == "mcp" for e, _ in results)
        assert any(e.heading == "A" for e, _ in results)
        assert not any(e.heading == "B" for e, _ in results)

    def test_topic_filter_with_no_matching_topic_returns_empty(self, paths):
        upsert_knowledge_index_entry(paths, heading="A", summary="任意内容", topic="mcp")
        assert search_knowledge_index(paths, "任意内容", topic="nonexistent") == []

    def test_searches_affected_modules_field(self, paths):
        upsert_knowledge_index_entry(
            paths, heading="MCP 重构", summary="去掉了 SDK 依赖",
            affected_modules=["mcp/manager.py", "mcp/client.py"],
        )
        results = search_knowledge_index(paths, "manager")
        assert len(results) >= 1
        assert results[0][0].heading == "MCP 重构"


class TestReadKnowledgeSection:

    def test_no_file_returns_none(self, paths):
        assert read_knowledge_section(paths, "任意标题") is None

    def test_missing_heading_returns_none(self, paths):
        paths.workdir_knowledge_md.parent.mkdir(parents=True, exist_ok=True)
        paths.workdir_knowledge_md.write_text("## 已有标题\n\n内容\n", encoding="utf-8")
        assert read_knowledge_section(paths, "不存在的标题") is None

    def test_returns_section_content(self, paths):
        paths.workdir_knowledge_md.parent.mkdir(parents=True, exist_ok=True)
        paths.workdir_knowledge_md.write_text(
            "## 数据库选型\n\n选择了 SQLite。\n\n## 鉴权方案\n\n使用 JWT。\n",
            encoding="utf-8",
        )
        content = read_knowledge_section(paths, "数据库选型")
        assert content == "选择了 SQLite。"

    def test_stops_at_next_heading(self, paths):
        paths.workdir_knowledge_md.parent.mkdir(parents=True, exist_ok=True)
        paths.workdir_knowledge_md.write_text(
            "## 第一节\n\n第一节内容\n\n## 第二节\n\n第二节内容\n",
            encoding="utf-8",
        )
        content = read_knowledge_section(paths, "第一节")
        assert content == "第一节内容"
        assert "第二节" not in content

    def test_reads_last_section_to_end_of_file(self, paths):
        paths.workdir_knowledge_md.parent.mkdir(parents=True, exist_ok=True)
        paths.workdir_knowledge_md.write_text(
            "## 第一节\n\n内容A\n\n## 最后一节\n\n内容B\n",
            encoding="utf-8",
        )
        content = read_knowledge_section(paths, "最后一节")
        assert content == "内容B"

    def test_stops_at_h1_heading_too(self, paths):
        paths.workdir_knowledge_md.parent.mkdir(parents=True, exist_ok=True)
        paths.workdir_knowledge_md.write_text(
            "## 某节\n\n节内容\n\n# 一级标题\n\n后面的内容\n",
            encoding="utf-8",
        )
        content = read_knowledge_section(paths, "某节")
        assert content == "节内容"
