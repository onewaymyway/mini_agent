"""tests/test_lineage_view.py — "谱系"视图（self_awareness_identity_evolution_plan.md §2.3）专属单测。"""

from __future__ import annotations

import unittest
from pathlib import Path

from mini_agent.storage.paths import AgentPaths
from mini_agent.evolution.state_repo import StateRepo
from mini_agent.evolution.lineage_view import compute_lineage_view


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


class TestComputeLineageView(unittest.TestCase):
    def test_no_commits_returns_empty_view(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            view = compute_lineage_view(paths)
            self.assertEqual(view.active_variants, [])
            self.assertEqual(view.merged_variants, [])
            self.assertEqual(view.discarded_variants, [])
            self.assertTrue(view.discarded_note)  # 说明文案始终存在

    def test_active_evolve_branch_detected(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = StateRepo(Path(tmp))
            repo.apply(changes={"a.txt": "1"}, message="init", meta={}, tier="T0")
            repo._run_git(["branch", "evolve/2026-08-30-try-x"])

            paths = _make_paths(tmp)
            view = compute_lineage_view(paths)

            branches = [v["branch"] for v in view.active_variants]
            self.assertIn("evolve/2026-08-30-try-x", branches)

    def test_non_evolve_branch_not_counted_as_active(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = StateRepo(Path(tmp))
            repo.apply(changes={"a.txt": "1"}, message="init", meta={}, tier="T0")
            repo._run_git(["branch", "some-other-branch"])

            paths = _make_paths(tmp)
            view = compute_lineage_view(paths)
            branches = [v["branch"] for v in view.active_variants]
            self.assertNotIn("some-other-branch", branches)

    def test_merged_variant_detected_from_merge_commit_subject(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = StateRepo(Path(tmp))
            repo.apply(changes={"a.txt": "1"}, message="init", meta={}, tier="T0")

            # 创建一个 evolve 分支并在其上提交，然后合并回主分支（走真实的
            # merge_branch()，而不是手写 commit message 模拟）
            main_branch = repo.current_branch()
            repo._run_git(["checkout", "-b", "evolve/2026-08-30-feature-y"])
            (Path(tmp) / "b.txt").write_text("2", encoding="utf-8")
            repo._run_git(["add", "b.txt"])
            repo._run_git(["commit", "-m", "[T0] add b"])
            repo._run_git(["checkout", main_branch])
            repo.merge_branch("evolve/2026-08-30-feature-y", into=main_branch)

            paths = _make_paths(tmp)
            view = compute_lineage_view(paths)

            merged_branches = [v["branch"] for v in view.merged_variants]
            self.assertIn("evolve/2026-08-30-feature-y", merged_branches)
            # 合并后分支应该已经不在 active_variants 里（merge_branch 默认删除源分支）
            active_branches = [v["branch"] for v in view.active_variants]
            self.assertNotIn("evolve/2026-08-30-feature-y", active_branches)

    def test_tiers_extracted_from_commit_subjects(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = StateRepo(Path(tmp))
            repo.apply(changes={"a.txt": "1"}, message="init", meta={}, tier="T0")
            main_branch = repo.current_branch()
            repo._run_git(["checkout", "-b", "evolve/2026-08-30-multi-tier"])
            (Path(tmp) / "b.txt").write_text("2", encoding="utf-8")
            repo._run_git(["add", "b.txt"])
            repo._run_git(["commit", "-m", "[T2] risky change"])
            repo._run_git(["checkout", main_branch])

            paths = _make_paths(tmp)
            view = compute_lineage_view(paths)
            entry = next(v for v in view.active_variants if v["branch"] == "evolve/2026-08-30-multi-tier")
            self.assertIn("T2", entry["tiers"])


if __name__ == "__main__":
    unittest.main()
