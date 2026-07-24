"""
tests/test_evolution_proposal_risk_track_i.py

覆盖 next_doc/kanban_and_autonomy_improvement_plan.md Track I
（进化提案分级自治）：

- StateRepo.commits_on_branch()：正确返回分支相对 base 的独有 commit。
- StateRepo.merge_branch()：成功合并、合并后删除源分支、冲突时中止并
  抛出 StateRepoError、目标分支不存在时报错。
- evolution/proposal_risk.classify_proposal_risk()：
  - 纯文档改动（T1、路径匹配 next_doc/*.md）→ "low"。
  - 改动了非文档路径（比如 src/ 下的代码）→ "high"。
  - 命中 T2/T3 tier → "high"，即使路径看起来像文档。
  - eval_result.json 显示回归 → "high"。
  - eval_result.json 显示无回归 / 不提供 → 不影响本应判定为 "low" 的结果。
  - 分支相对 base 没有独有 commit → "high"（无法分级，保守处理）。

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_evolution_proposal_risk_track_i.py -q
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mini_agent.evolution.state_repo import StateRepo, StateRepoError
from mini_agent.evolution.proposal_risk import classify_proposal_risk


class TestStateRepoCommitsOnBranch(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.repo = StateRepo(self.root)
        # 主分支先有一个 commit，作为 base。
        self.repo.apply(changes={"README.md": "base\n"}, message="init", meta={}, tier="T0")
        self.main_branch = self.repo.current_branch()

    def tearDown(self):
        self._tmpdir.cleanup()

    def _make_branch_with_commit(self, branch: str, path: str, content: str, tier: str = "T1") -> None:
        self.repo.create_branch(branch)
        self.repo._run_git(["checkout", branch])
        self.repo.apply(changes={path: content}, message=f"add {path}", meta={}, tier=tier)
        self.repo._run_git(["checkout", self.main_branch])

    def test_commits_on_branch_returns_only_branch_unique_commits(self):
        self._make_branch_with_commit("evolve/x", "next_doc/x.md", "hello\n")
        commits = self.repo.commits_on_branch("evolve/x", base=self.main_branch)
        self.assertEqual(len(commits), 1)
        self.assertIn("next_doc/x.md", commits[0].files)

    def test_commits_on_branch_empty_when_no_unique_commits(self):
        self.repo.create_branch("evolve/empty")
        commits = self.repo.commits_on_branch("evolve/empty", base=self.main_branch)
        self.assertEqual(commits, [])


class TestStateRepoMergeBranch(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.repo = StateRepo(self.root)
        self.repo.apply(changes={"README.md": "base\n"}, message="init", meta={}, tier="T0")
        self.main_branch = self.repo.current_branch()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_merge_success_deletes_branch_by_default(self):
        self.repo.create_branch("evolve/feat")
        self.repo._run_git(["checkout", "evolve/feat"])
        self.repo.apply(changes={"next_doc/feat.md": "content\n"}, message="feat doc", meta={}, tier="T1")
        self.repo._run_git(["checkout", self.main_branch])

        commit_hash = self.repo.merge_branch("evolve/feat")
        self.assertTrue(commit_hash)
        self.assertTrue((self.root / "next_doc" / "feat.md").exists())
        self.assertNotIn("evolve/feat", self.repo.list_branches())

    def test_merge_keeps_branch_when_delete_after_false(self):
        self.repo.create_branch("evolve/keep")
        self.repo._run_git(["checkout", "evolve/keep"])
        self.repo.apply(changes={"next_doc/keep.md": "content\n"}, message="keep doc", meta={}, tier="T1")
        self.repo._run_git(["checkout", self.main_branch])

        self.repo.merge_branch("evolve/keep", delete_after=False)
        self.assertIn("evolve/keep", self.repo.list_branches())

    def test_merge_nonexistent_branch_raises(self):
        with self.assertRaises(StateRepoError):
            self.repo.merge_branch("evolve/does-not-exist")

    def test_merge_conflict_aborts_and_raises(self):
        # 主分支和 evolve 分支都改同一个文件的同一行，制造冲突。
        self.repo.apply(changes={"conflict.txt": "line-main\n"}, message="main edit", meta={}, tier="T0")
        self.repo.create_branch("evolve/conflict", base=f"{self.main_branch}~1")
        self.repo._run_git(["checkout", "evolve/conflict"])
        self.repo.apply(changes={"conflict.txt": "line-branch\n"}, message="branch edit", meta={}, tier="T1")
        self.repo._run_git(["checkout", self.main_branch])

        with self.assertRaises(StateRepoError):
            self.repo.merge_branch("evolve/conflict", delete_after=False)
        # 冲突应被自动 abort，仓库回到干净状态（没有遗留的合并中间态）。
        status = self.repo._run_git(["status", "--porcelain"])
        self.assertEqual(status.stdout.strip(), "")


class TestClassifyProposalRisk(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.repo = StateRepo(self.root)
        self.repo.apply(changes={"README.md": "base\n"}, message="init", meta={}, tier="T0")
        self.main_branch = self.repo.current_branch()

    def tearDown(self):
        self._tmpdir.cleanup()

    def _branch(self, name: str, path: str, content: str, tier: str) -> None:
        self.repo.create_branch(name)
        self.repo._run_git(["checkout", name])
        self.repo.apply(changes={path: content}, message=f"add {path}", meta={}, tier=tier)
        self.repo._run_git(["checkout", self.main_branch])

    def test_docs_only_t1_is_low_risk(self):
        self._branch("evolve/docs", "next_doc/note.md", "hello\n", tier="T1")
        result = classify_proposal_risk(self.repo, "evolve/docs", base=self.main_branch)
        self.assertEqual(result.risk, "low")
        self.assertEqual(result.max_tier, "T1")

    def test_code_change_is_high_risk(self):
        self._branch("evolve/code", "src/mini_agent/foo.py", "x = 1\n", tier="T1")
        result = classify_proposal_risk(self.repo, "evolve/code", base=self.main_branch)
        self.assertEqual(result.risk, "high")

    def test_t2_tier_forces_high_risk_even_if_path_looks_like_docs(self):
        self._branch("evolve/t2doc", "next_doc/note2.md", "hello\n", tier="T2")
        result = classify_proposal_risk(self.repo, "evolve/t2doc", base=self.main_branch)
        self.assertEqual(result.risk, "high")
        self.assertEqual(result.max_tier, "T2")

    def test_no_unique_commits_is_high_risk(self):
        self.repo.create_branch("evolve/empty")
        result = classify_proposal_risk(self.repo, "evolve/empty", base=self.main_branch)
        self.assertEqual(result.risk, "high")
        self.assertEqual(result.commit_count, 0)

    def test_eval_regression_forces_high_risk(self):
        self._branch("evolve/regressed", "next_doc/note3.md", "hello\n", tier="T1")
        eval_path = self.root / "eval_result.json"
        eval_path.write_text(json.dumps({
            "summary": {
                "with_skill": {"tool_failure_rate": 0.5, "scenarios_ok": 3},
                "without_skill": {"tool_failure_rate": 0.1, "scenarios_ok": 5},
            }
        }), encoding="utf-8")
        result = classify_proposal_risk(
            self.repo, "evolve/regressed", base=self.main_branch, eval_result_path=eval_path,
        )
        self.assertEqual(result.risk, "high")
        self.assertTrue(result.eval_regression)

    def test_eval_no_regression_stays_low_risk(self):
        self._branch("evolve/clean_eval", "next_doc/note4.md", "hello\n", tier="T1")
        eval_path = self.root / "eval_result.json"
        eval_path.write_text(json.dumps({
            "summary": {
                "with_skill": {"tool_failure_rate": 0.05, "scenarios_ok": 5},
                "without_skill": {"tool_failure_rate": 0.1, "scenarios_ok": 5},
            }
        }), encoding="utf-8")
        result = classify_proposal_risk(
            self.repo, "evolve/clean_eval", base=self.main_branch, eval_result_path=eval_path,
        )
        self.assertEqual(result.risk, "low")
        self.assertFalse(result.eval_regression)

    def test_missing_eval_file_does_not_block_low_risk(self):
        self._branch("evolve/no_eval", "next_doc/note5.md", "hello\n", tier="T1")
        result = classify_proposal_risk(
            self.repo, "evolve/no_eval", base=self.main_branch,
            eval_result_path=self.root / "does_not_exist.json",
        )
        self.assertEqual(result.risk, "low")
        self.assertIsNone(result.eval_regression)


if __name__ == "__main__":
    unittest.main()
