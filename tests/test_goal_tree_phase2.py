"""
tests/test_goal_tree_phase2.py — 目标树系统阶段二（自动分解）测试

覆盖 next_doc/goal_tree_system_plan.md §4.2：
  - GoalBacklog.append_decompose_candidates / accept_candidate / reject_candidate / get_tree
  - GoalTreeDecomposer 的 prompt 拼装、候选解析、节奏治理、accept/reject 全流程
  - find_stale_nodes_for_scan() / find_parent_needing_decompose_after_completion()
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.perception.goal_tree_decomposer import (
    GoalTreeDecomposer,
    MIN_DECOMPOSE_INTERVAL_SECONDS,
    find_parent_needing_decompose_after_completion,
    find_stale_nodes_for_scan,
)
from mini_agent.storage.paths import AgentPaths


class _FakeLLMHelper:
    """最小 llm_helper 替身：只实现 .ask(prompt) -> str。"""

    def __init__(self, response: str = "", raise_error: bool = False):
        self.response = response
        self.raise_error = raise_error
        self.calls: list[str] = []

    def ask(self, prompt: str, **kwargs) -> str:
        self.calls.append(prompt)
        if self.raise_error:
            raise RuntimeError("llm boom")
        return self.response


def _make_backlog(tmp) -> GoalBacklog:
    paths = AgentPaths(Path(tmp))
    return GoalBacklog(paths)


class TestGoalBacklogCandidateHelpers(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.backlog = _make_backlog(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_append_and_accept_candidate(self):
        root = self.backlog.add_node("ultimate", "根")
        domain = self.backlog.add_node("domain", "事业", parent_id=root.id)
        ok = self.backlog.append_decompose_candidates(domain.id, [
            {"id": "cand_1", "title": "做个项目", "description": "desc", "level": "stage"},
        ])
        self.assertTrue(ok)

        self.backlog.load()
        reloaded = self.backlog.get(domain.id)
        self.assertEqual(len(reloaded.decompose_candidates), 1)

        new_node = self.backlog.accept_candidate(domain.id, "cand_1")
        self.assertIsNotNone(new_node)
        self.assertEqual(new_node.level, "stage")
        self.assertEqual(new_node.parent_id, domain.id)

        self.backlog.load()
        reloaded_domain = self.backlog.get(domain.id)
        self.assertEqual(reloaded_domain.decompose_candidates, [])
        self.assertIn(new_node.id, reloaded_domain.children_ids)

    def test_accept_candidate_with_overrides(self):
        root = self.backlog.add_node("ultimate", "根")
        domain = self.backlog.add_node("domain", "健康", parent_id=root.id)
        self.backlog.append_decompose_candidates(domain.id, [
            {"id": "cand_1", "title": "原标题", "description": "原描述", "level": "stage"},
        ])
        new_node = self.backlog.accept_candidate(
            domain.id, "cand_1", overrides={"title": "新标题"},
        )
        self.assertEqual(new_node.title, "新标题")
        self.assertEqual(new_node.description, "原描述")

    def test_accept_candidate_rejects_invalid_level(self):
        root = self.backlog.add_node("ultimate", "根")
        domain = self.backlog.add_node("domain", "健康", parent_id=root.id)
        self.backlog.append_decompose_candidates(domain.id, [
            {"id": "cand_1", "title": "x", "level": "objective"},  # domain 下不能直接挂 objective
        ])
        result = self.backlog.accept_candidate(domain.id, "cand_1")
        self.assertIsNone(result)

    def test_accept_candidate_missing_returns_none(self):
        root = self.backlog.add_node("ultimate", "根")
        result = self.backlog.accept_candidate(root.id, "does_not_exist")
        self.assertIsNone(result)

    def test_reject_candidate_removes_and_returns_dict(self):
        root = self.backlog.add_node("ultimate", "根")
        self.backlog.append_decompose_candidates(root.id, [
            {"id": "cand_1", "title": "标题", "level": "domain"},
        ])
        removed = self.backlog.reject_candidate(root.id, "cand_1")
        self.assertEqual(removed["title"], "标题")
        self.backlog.load()
        self.assertEqual(self.backlog.get(root.id).decompose_candidates, [])

    def test_reject_candidate_missing_returns_none(self):
        root = self.backlog.add_node("ultimate", "根")
        self.assertIsNone(self.backlog.reject_candidate(root.id, "nope"))

    def test_get_tree_builds_nested_structure(self):
        root = self.backlog.add_node("ultimate", "根")
        domain = self.backlog.add_node("domain", "事业", parent_id=root.id)
        self.backlog.add_node("stage", "第一阶段", parent_id=domain.id)

        tree = self.backlog.get_tree()
        self.assertEqual(tree["node"].id, root.id)
        self.assertEqual(len(tree["children"]), 1)
        self.assertEqual(tree["children"][0]["node"].id, domain.id)
        self.assertEqual(len(tree["children"][0]["children"]), 1)

    def test_get_tree_missing_root_returns_none(self):
        self.assertIsNone(self.backlog.get_tree())
        self.assertIsNone(self.backlog.get_tree("nope"))


class TestGoalTreeDecomposerPromptAndParsing(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.backlog = _make_backlog(self._tmpdir.name)
        self.decomposer = GoalTreeDecomposer(self.backlog._paths, self.backlog)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_build_prompt_includes_ancestors_and_children(self):
        root = self.backlog.add_node("ultimate", "我的人生目标")
        domain = self.backlog.add_node("domain", "事业", parent_id=root.id)
        self.backlog.add_node("stage", "已有阶段", parent_id=domain.id)

        prompt = self.decomposer.build_prompt(domain)
        self.assertIn("我的人生目标", prompt)
        self.assertIn("事业", prompt)
        self.assertIn("已有阶段", prompt)

    def test_parse_candidates_basic(self):
        root = self.backlog.add_node("ultimate", "根")
        domain = self.backlog.add_node("domain", "事业", parent_id=root.id)
        text = "阶段一｜先做A｜stage\n阶段二｜再做B｜stage"
        candidates = self.decomposer._parse_candidates(text, domain)
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0]["title"], "阶段一")
        self.assertEqual(candidates[0]["level"], "stage")

    def test_parse_candidates_ascii_pipe_also_works(self):
        root = self.backlog.add_node("ultimate", "根")
        domain = self.backlog.add_node("domain", "事业", parent_id=root.id)
        text = "阶段一|先做A|stage"
        candidates = self.decomposer._parse_candidates(text, domain)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["description"], "先做A")

    def test_parse_candidates_invalid_level_falls_back_to_default(self):
        root = self.backlog.add_node("ultimate", "根")
        domain = self.backlog.add_node("domain", "事业", parent_id=root.id)
        text = "阶段一｜描述｜objective"  # domain 的下一层不能是 objective，应回退到 stage
        candidates = self.decomposer._parse_candidates(text, domain)
        self.assertEqual(candidates[0]["level"], "stage")

    def test_parse_candidates_dedupes_against_existing_children(self):
        root = self.backlog.add_node("ultimate", "根")
        domain = self.backlog.add_node("domain", "事业", parent_id=root.id)
        self.backlog.add_node("stage", "已有阶段", parent_id=domain.id)
        text = "已有阶段｜重复的｜stage\n新阶段｜不重复｜stage"
        candidates = self.decomposer._parse_candidates(text, domain)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["title"], "新阶段")

    def test_parse_candidates_dedupes_against_rejected(self):
        root = self.backlog.add_node("ultimate", "根")
        domain = self.backlog.add_node("domain", "事业", parent_id=root.id)
        self.decomposer.record_rejected_topic(domain.id, "被拒过的主题")
        text = "被拒过的主题｜desc｜stage\n新主题｜desc｜stage"
        candidates = self.decomposer._parse_candidates(text, domain)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["title"], "新主题")

    def test_parse_candidates_caps_at_max(self):
        root = self.backlog.add_node("ultimate", "根")
        domain = self.backlog.add_node("domain", "事业", parent_id=root.id)
        lines = [f"候选{i}｜desc｜stage" for i in range(10)]
        candidates = self.decomposer._parse_candidates("\n".join(lines), domain)
        self.assertLessEqual(len(candidates), 5)

    def test_parse_candidates_empty_text(self):
        root = self.backlog.add_node("ultimate", "根")
        self.assertEqual(self.decomposer._parse_candidates("", root), [])
        self.assertEqual(self.decomposer._parse_candidates("   \n  ", root), [])


class TestGoalTreeDecomposerRhythmGovernance(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.backlog = _make_backlog(self._tmpdir.name)
        self.decomposer = GoalTreeDecomposer(self.backlog._paths, self.backlog)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_should_decompose_none_when_clean(self):
        root = self.backlog.add_node("ultimate", "根")
        self.assertIsNone(self.decomposer.should_decompose(root))

    def test_should_decompose_skips_when_candidates_pending(self):
        root = self.backlog.add_node("ultimate", "根")
        self.backlog.append_decompose_candidates(root.id, [{"id": "c1", "title": "x"}])
        self.backlog.load()
        node = self.backlog.get(root.id)
        reason = self.decomposer.should_decompose(node)
        self.assertIsNotNone(reason)
        self.assertIn("未处理", reason)

    def test_should_decompose_skips_within_min_interval(self):
        root = self.backlog.add_node("ultimate", "根")
        self.decomposer.record_attempt(root.id)
        reason = self.decomposer.should_decompose(root)
        self.assertIsNotNone(reason)

    def test_should_decompose_allows_after_interval(self):
        root = self.backlog.add_node("ultimate", "根")
        self.decomposer.record_attempt(root.id)
        # 手动改写状态文件伪造"很久以前"触发过
        import json
        data = json.loads(self.decomposer._state_path.read_text(encoding="utf-8"))
        data[root.id] = time.time() - MIN_DECOMPOSE_INTERVAL_SECONDS - 10
        self.decomposer._state_path.write_text(json.dumps(data), encoding="utf-8")
        self.assertIsNone(self.decomposer.should_decompose(root))


class TestGoalTreeDecomposerDecompose(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.backlog = _make_backlog(self._tmpdir.name)
        self.decomposer = GoalTreeDecomposer(self.backlog._paths, self.backlog)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_decompose_happy_path_writes_candidates(self):
        root = self.backlog.add_node("ultimate", "根")
        domain = self.backlog.add_node("domain", "事业", parent_id=root.id)
        helper = _FakeLLMHelper(response="阶段一｜先做A｜stage\n阶段二｜再做B｜stage")

        candidates = self.decomposer.decompose(domain.id, llm_helper=helper)
        self.assertEqual(len(candidates), 2)
        self.assertEqual(len(helper.calls), 1)

        self.backlog.load()
        reloaded = self.backlog.get(domain.id)
        self.assertEqual(len(reloaded.decompose_candidates), 2)

    def test_decompose_missing_node_returns_empty(self):
        helper = _FakeLLMHelper(response="x｜y｜stage")
        self.assertEqual(self.decomposer.decompose("nope", llm_helper=helper), [])
        self.assertEqual(len(helper.calls), 0)

    def test_decompose_llm_error_returns_empty_but_records_attempt(self):
        root = self.backlog.add_node("ultimate", "根")
        helper = _FakeLLMHelper(raise_error=True)
        result = self.decomposer.decompose(root.id, llm_helper=helper)
        self.assertEqual(result, [])
        # attempt 已记录，节奏治理生效
        reason = self.decomposer.should_decompose(root)
        self.assertIsNotNone(reason)

    def test_decompose_skipped_when_candidates_already_pending(self):
        root = self.backlog.add_node("ultimate", "根")
        self.backlog.append_decompose_candidates(root.id, [{"id": "c1", "title": "existing"}])
        helper = _FakeLLMHelper(response="x｜y｜domain")
        result = self.decomposer.decompose(root.id, llm_helper=helper)
        self.assertEqual(result, [])
        self.assertEqual(len(helper.calls), 0)

    def test_decompose_force_bypasses_pending_check(self):
        root = self.backlog.add_node("ultimate", "根")
        self.backlog.append_decompose_candidates(root.id, [{"id": "c1", "title": "existing"}])
        helper = _FakeLLMHelper(response="新候选｜desc｜domain")
        result = self.decomposer.decompose(root.id, llm_helper=helper, force=True)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(helper.calls), 1)

    def test_decompose_empty_llm_output_returns_empty(self):
        root = self.backlog.add_node("ultimate", "根")
        helper = _FakeLLMHelper(response="")
        result = self.decomposer.decompose(root.id, llm_helper=helper)
        self.assertEqual(result, [])


class TestGoalTreeDecomposerAcceptRejectFlow(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.backlog = _make_backlog(self._tmpdir.name)
        self.decomposer = GoalTreeDecomposer(self.backlog._paths, self.backlog)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_reject_candidate_records_dedupe_and_removes(self):
        root = self.backlog.add_node("ultimate", "根")
        self.backlog.append_decompose_candidates(root.id, [
            {"id": "cand_1", "title": "某主题", "level": "domain"},
        ])
        ok = self.decomposer.reject_candidate(root.id, "cand_1")
        self.assertTrue(ok)

        self.backlog.load()
        self.assertEqual(self.backlog.get(root.id).decompose_candidates, [])

        rejected_keys = self.decomposer._load_rejected_keys()
        self.assertIn(f"{root.id}:{self._norm('某主题')}", rejected_keys)

    @staticmethod
    def _norm(title):
        from mini_agent.evolution.objective_outcome_tracker import normalize_title_key
        return normalize_title_key(title)

    def test_reject_candidate_missing_returns_false(self):
        root = self.backlog.add_node("ultimate", "根")
        self.assertFalse(self.decomposer.reject_candidate(root.id, "nope"))

    def test_rejected_topic_excluded_from_future_parse(self):
        root = self.backlog.add_node("ultimate", "根")
        self.backlog.append_decompose_candidates(root.id, [
            {"id": "cand_1", "title": "重复主题", "level": "domain"},
        ])
        self.decomposer.reject_candidate(root.id, "cand_1")

        self.backlog.load()
        node = self.backlog.get(root.id)
        text = "重复主题｜desc｜domain\n全新主题｜desc｜domain"
        candidates = self.decomposer._parse_candidates(text, node)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["title"], "全新主题")


class TestFindStaleNodesForScan(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.backlog = _make_backlog(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_finds_leafless_stale_structural_node(self):
        root = self.backlog.add_node("ultimate", "根")
        self.backlog.load()
        node = self.backlog.get(root.id)
        node.last_touched_at = time.time() - 20 * 86400
        self.backlog.save()

        stale = find_stale_nodes_for_scan(self.backlog, stale_days=14)
        self.assertEqual([n.id for n in stale], [root.id])

    def test_excludes_node_with_active_children(self):
        root = self.backlog.add_node("ultimate", "根")
        self.backlog.add_node("domain", "事业", parent_id=root.id)
        self.backlog.load()
        node = self.backlog.get(root.id)
        node.last_touched_at = time.time() - 20 * 86400
        self.backlog.save()

        stale = find_stale_nodes_for_scan(self.backlog, stale_days=14)
        self.assertEqual(stale, [])

    def test_excludes_recently_touched_node(self):
        self.backlog.add_node("ultimate", "根")
        stale = find_stale_nodes_for_scan(self.backlog, stale_days=14)
        self.assertEqual(stale, [])

    def test_excludes_objective_level(self):
        root = self.backlog.add_node("ultimate", "根")
        domain = self.backlog.add_node("domain", "d", parent_id=root.id)
        stage = self.backlog.add_node("stage", "s", parent_id=domain.id)
        goal = self.backlog.add_node("goal", "g", parent_id=stage.id)
        obj = self.backlog.add_node("objective", "o", parent_id=goal.id)
        self.backlog.load()
        for nid in (root.id, domain.id, stage.id, goal.id, obj.id):
            n = self.backlog.get(nid)
            n.last_touched_at = time.time() - 20 * 86400
        self.backlog.save()

        stale = find_stale_nodes_for_scan(self.backlog, stale_days=14)
        stale_ids = {n.id for n in stale}
        self.assertNotIn(obj.id, stale_ids)
        # goal 没有 active 子节点（objective 未 completed 但仍是 active，因此 goal 其实有 active 子节点）
        self.assertNotIn(goal.id, stale_ids)


class TestFindParentNeedingDecomposeAfterCompletion(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.backlog = _make_backlog(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_returns_parent_when_last_active_child_completed(self):
        root = self.backlog.add_node("ultimate", "根")
        domain = self.backlog.add_node("domain", "事业", parent_id=root.id)
        stage = self.backlog.add_node("stage", "阶段", parent_id=domain.id)
        self.backlog.update_fields(stage.id, status="completed")

        parent = find_parent_needing_decompose_after_completion(self.backlog, stage.id)
        self.assertIsNotNone(parent)
        self.assertEqual(parent.id, domain.id)

    def test_returns_none_when_siblings_still_active(self):
        root = self.backlog.add_node("ultimate", "根")
        domain = self.backlog.add_node("domain", "事业", parent_id=root.id)
        stage1 = self.backlog.add_node("stage", "阶段1", parent_id=domain.id)
        self.backlog.add_node("stage", "阶段2", parent_id=domain.id)
        self.backlog.update_fields(stage1.id, status="completed")

        parent = find_parent_needing_decompose_after_completion(self.backlog, stage1.id)
        self.assertIsNone(parent)

    def test_returns_none_when_no_parent(self):
        root = self.backlog.add_node("ultimate", "根")
        parent = find_parent_needing_decompose_after_completion(self.backlog, root.id)
        self.assertIsNone(parent)

    def test_returns_none_for_unknown_node(self):
        parent = find_parent_needing_decompose_after_completion(self.backlog, "nope")
        self.assertIsNone(parent)


if __name__ == "__main__":
    unittest.main()
