"""tests/test_goal_execution_spec_versioning.py

覆盖 next_doc/goal_output_directory_and_execution_phase_redesign_plan.md
Stage 2（GoalExecutionSpec 版本历史归档）新增/修改的能力：
    - SubDirectory.retention / naming_pattern 字段（含 from_dict 向后兼容）
    - save_spec() 落盘 spec/SPEC.md、spec/SPEC.json
    - save_spec() 在覆盖前把旧版本归档进 spec/history/
    - list_spec_history()

不重复覆盖 tests/test_goal_execution_spec.py 已有的草稿生成/修订/确认流程。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mini_agent.perception import goal_execution_spec as ges
from mini_agent.evolution import output_workspace as ow
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(Path(tmp))


class TestSubDirectoryNewFields(unittest.TestCase):
    def test_defaults_are_unbounded_and_empty_pattern(self):
        s = ges.SubDirectory(name="reports", purpose="报告")
        self.assertEqual(s.retention, "unbounded")
        self.assertEqual(s.naming_pattern, "")

    def test_round_trip_to_dict_from_dict(self):
        s = ges.SubDirectory(
            name="data", purpose="数据", retention="append",
            naming_pattern="YYYY-MM-DD_<主题>.md",
        )
        restored = ges.SubDirectory.from_dict(s.to_dict())
        self.assertEqual(restored, s)

    def test_from_dict_invalid_retention_falls_back_to_unbounded(self):
        restored = ges.SubDirectory.from_dict({"name": "x", "retention": "bogus"})
        self.assertEqual(restored.retention, "unbounded")

    def test_from_dict_missing_fields_backward_compatible(self):
        """旧的已保存 spec 文件里 sub_directories 只有 name/purpose，缺省
        字段应兜底为 unbounded/空字符串，不抛异常。"""
        restored = ges.SubDirectory.from_dict({"name": "legacy", "purpose": "旧数据"})
        self.assertEqual(restored.retention, "unbounded")
        self.assertEqual(restored.naming_pattern, "")

    def test_render_summary_includes_retention(self):
        spec = ges.GoalExecutionSpec(
            goal_id="g1",
            sub_directories=[ges.SubDirectory(name="reports", purpose="报告", retention="latest_only")],
        )
        text = spec.render_summary_for_user()
        self.assertIn("latest_only", text)

    def test_render_prompt_block_includes_retention(self):
        spec = ges.GoalExecutionSpec(
            goal_id="g1",
            deliverables=[ges.Deliverable(name="d1")],
            sub_directories=[ges.SubDirectory(name="data", purpose="数据", retention="append")],
        )
        text = spec.render_prompt_block()
        self.assertIn("append", text)


class TestSaveSpecSnapshot(unittest.TestCase):
    def test_save_spec_writes_spec_md_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            spec = ges.GoalExecutionSpec(goal_id="g1", version=1, confirmed=True)
            ges.save_spec(paths, "g1", spec)

            spec_dir = ow.goal_spec_dir(paths, "g1")
            self.assertTrue((spec_dir / "SPEC.md").exists())
            self.assertTrue((spec_dir / "SPEC.json").exists())

            data = json.loads((spec_dir / "SPEC.json").read_text(encoding="utf-8"))
            self.assertEqual(data["version"], 1)

    def test_authoritative_json_still_written(self):
        """spec/SPEC.json 快照是附加能力，权威存储路径
        （.agent/goal_execution_specs/<goal_id>.json）行为不变。"""
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            spec = ges.GoalExecutionSpec(goal_id="g1", version=1)
            p = ges.save_spec(paths, "g1", spec)
            self.assertTrue(p.exists())
            loaded = ges.load_spec(paths, "g1")
            self.assertEqual(loaded.version, 1)

    def test_first_save_creates_no_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            spec = ges.GoalExecutionSpec(goal_id="g1", version=1)
            ges.save_spec(paths, "g1", spec)
            history_dir = ow.goal_spec_dir(paths, "g1") / "history"
            self.assertFalse(history_dir.exists() and any(history_dir.iterdir()))

    def test_second_save_archives_prior_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            spec_v1 = ges.GoalExecutionSpec(goal_id="g1", version=1, confirmed=True, confirmed_at=1000.0)
            ges.save_spec(paths, "g1", spec_v1)

            spec_v2 = ges.GoalExecutionSpec(goal_id="g1", version=2, confirmed=True, confirmed_at=2000.0)
            ges.save_spec(paths, "g1", spec_v2)

            history = ges.list_spec_history(paths, "g1")
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["version"], 1)

            # 当前 SPEC.json 应为最新版本
            spec_dir = ow.goal_spec_dir(paths, "g1")
            current = json.loads((spec_dir / "SPEC.json").read_text(encoding="utf-8"))
            self.assertEqual(current["version"], 2)

    def test_three_versions_produce_two_history_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            for v in (1, 2, 3):
                spec = ges.GoalExecutionSpec(goal_id="g1", version=v, confirmed=True, confirmed_at=float(v * 1000))
                ges.save_spec(paths, "g1", spec)
            history = ges.list_spec_history(paths, "g1")
            self.assertEqual(len(history), 2)
            versions = sorted(h["version"] for h in history)
            self.assertEqual(versions, [1, 2])

    def test_list_spec_history_empty_when_no_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self.assertEqual(ges.list_spec_history(paths, "no_such_goal"), [])

    def test_save_spec_does_not_raise_when_workspace_dir_unwritable(self):
        """快照/归档逻辑失败不应影响权威存储写入——用一个会在
        _write_spec_snapshot 内部抛异常的场景验证 save_spec 本身不崩溃。
        这里通过 monkeypatch goal_spec_dir 的落点为一个已存在的同名普通
        文件（导致 mkdir 失败）来模拟。"""
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            base = ow.goal_output_base_dir(paths, "g1")
            base.mkdir(parents=True, exist_ok=True)
            # 提前用一个文件占住 spec/ 应该在的路径
            (base / "spec").write_text("not a directory", encoding="utf-8")

            spec = ges.GoalExecutionSpec(goal_id="g1", version=1)
            # 不应抛异常
            p = ges.save_spec(paths, "g1", spec)
            self.assertTrue(p.exists())
            loaded = ges.load_spec(paths, "g1")
            self.assertEqual(loaded.version, 1)


if __name__ == "__main__":
    unittest.main()
