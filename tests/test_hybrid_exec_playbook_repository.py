"""
tests/test_hybrid_exec_playbook_repository.py

对应 next_doc/generative_capability_raw_result_and_hybrid_merge_plan.md 第3节：
PlaybookRepository —— skill 档 playbook（步骤说明文档）的独立版本化存储。

用例形状刻意与 tests/test_hybrid_exec.py::TestScriptRepository 对称，验证
两个仓库接口同构、行为一致，同时验证两者各自落在独立目录、互不干扰。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.hybrid_exec.playbook_repository import PlaybookRepository
from mini_agent.hybrid_exec.repository import ScriptRepository


class TestPlaybookRepository(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = PlaybookRepository(Path(self._tmp.name), retire_after_consecutive_fail=3)

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_active_playbook_initially(self):
        self.assertIsNone(self.repo.get_active_playbook("t1"))

    def test_save_and_get_active(self):
        rec = self.repo.save_new_version("t1", "# 步骤说明\n1. 打开首页\n2. 提取标题\n", "agent_explorer")
        self.assertEqual(rec.version, 1)
        active = self.repo.get_active_playbook("t1")
        self.assertIsNotNone(active)
        self.assertEqual(active.version, 1)
        self.assertEqual(active.status, "active")
        self.assertEqual(
            self.repo.load_content("t1", 1),
            "# 步骤说明\n1. 打开首页\n2. 提取标题\n",
        )

    def test_new_version_supersedes_old(self):
        self.repo.save_new_version("t1", "v1 说明", "agent_explorer")
        rec2 = self.repo.save_revised_version("t1", "v2 说明（修订版）", "agent_repairer")
        self.assertEqual(rec2.version, 2)
        active = self.repo.get_active_playbook("t1")
        self.assertEqual(active.version, 2)
        versions = self.repo.list_versions("t1")
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0].status, "superseded")
        self.assertEqual(versions[1].status, "active")

    def test_record_success_resets_consecutive_fail(self):
        self.repo.save_new_version("t1", "说明", "agent_explorer")
        self.repo.record_failure("t1", 1, "boom")
        self.repo.record_success("t1", 1)
        rec = self.repo.get_active_playbook("t1")
        self.assertEqual(rec.consecutive_fail, 0)
        self.assertEqual(rec.success_count, 1)
        self.assertEqual(rec.fail_count, 1)

    def test_auto_retire_after_consecutive_failures(self):
        self.repo.save_new_version("t1", "说明", "agent_explorer")
        self.repo.record_failure("t1", 1, "err1")
        self.repo.record_failure("t1", 1, "err2")
        self.repo.record_failure("t1", 1, "err3")  # 达到阈值 3
        self.assertIsNone(self.repo.get_active_playbook("t1"))
        versions = self.repo.list_versions("t1")
        self.assertEqual(versions[0].status, "retired")

    def test_manual_retire(self):
        self.repo.save_new_version("t1", "说明", "agent_explorer")
        self.repo.retire("t1", 1, "手动退役")
        self.assertIsNone(self.repo.get_active_playbook("t1"))

    def test_playbook_file_uses_md_suffix(self):
        self.repo.save_new_version("t1", "说明", "agent_explorer")
        path = self.repo.get_playbook_path("t1", 1)
        self.assertEqual(path.suffix, ".md")
        self.assertTrue(path.exists())

    def test_playbook_and_script_repository_use_independent_directories(self):
        """同一个 task_id 在两个仓库里的版本历史完全独立，互不干扰
        （对应用户确认的开放问题：playbook 单独设计一套版本化目录，
        不复用 ScriptRepository 的 <task_id>/v{n}.py 布局）。"""
        base = Path(self._tmp.name)
        script_repo = ScriptRepository(base / "scripts")
        playbook_repo = PlaybookRepository(base / "playbooks")

        script_repo.save_new_version("shared-task-id", "def run(ctx): return 'ok'", "llm_explorer")
        playbook_repo.save_new_version("shared-task-id", "# 步骤说明", "agent_explorer")

        self.assertIsNotNone(script_repo.get_active_script("shared-task-id"))
        self.assertIsNotNone(playbook_repo.get_active_playbook("shared-task-id"))

        script_path = script_repo.get_script_path("shared-task-id", 1)
        playbook_path = playbook_repo.get_playbook_path("shared-task-id", 1)
        self.assertNotEqual(script_path.parent, playbook_path.parent)
        self.assertEqual(script_path.suffix, ".py")
        self.assertEqual(playbook_path.suffix, ".md")

    def test_record_upgrade_attempt_writes_timestamp_without_touching_success_fail(self):
        """对应 next_doc/generative_capability_three_tier_improvement_plan.md
        阶段二：record_upgrade_attempt 只写 last_upgrade_attempt_at，不影响
        success_count/fail_count/consecutive_fail 等 playbook 自身的执行
        成败统计。"""
        self.repo.save_new_version("t1", "说明", "agent_explorer")
        self.repo.record_success("t1", 1)

        before = self.repo.get_active_playbook("t1")
        self.assertIsNone(before.last_upgrade_attempt_at)

        self.repo.record_upgrade_attempt("t1", 1)

        after = self.repo.get_active_playbook("t1")
        self.assertIsNotNone(after.last_upgrade_attempt_at)
        self.assertEqual(after.success_count, before.success_count)
        self.assertEqual(after.fail_count, before.fail_count)
        self.assertEqual(after.consecutive_fail, before.consecutive_fail)


if __name__ == "__main__":
    unittest.main()
