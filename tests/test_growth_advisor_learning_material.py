"""tests/test_growth_advisor_learning_material.py — 覆盖 next_doc/
growth_advisor_autonomous_search_and_material_improvement_plan.md 方向
"报告与学习素材分层"：

  1. `generate_learning_material()` 规则模板兜底路径：始终产出非空
     `learning_path`/`resources`/`first_task`，正文落盘、索引落盘、
     候选的 `material_id` 被回填。
  2. LLM 路径：解析出结构化 JSON 时采用 LLM 内容；解析失败/异常/空
     响应/字段不完整时静默退回规则模板。
  3. 素材可以基于已有报告生成（复用报告 `summary` 作为背景），也可以
     独立生成（不传 `report`）。
  4. `list_materials()`/`get_material_by_id()` 基本读取行为。
  5. `GrowthLearningMaterial`/`GrowthCandidate.material_id` 的
     `to_dict()`/`from_dict()` 序列化兼容性（旧数据缺字段时的默认值）。
  6. CLI `/growth material <id>`：候选没有素材时生成并展示；已有素材
     时直接展示，不重复生成。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.cli.commands import growth_cmd
from mini_agent.evolution import growth_advisor as ga
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


def _candidate(title: str = "数据分析") -> "ga.GrowthCandidate":
    return ga.GrowthCandidate(
        candidate_id="c1", title=title, rationale="因为持续投入证据充分",
        evidence_count=5, confidence=0.6,
    )


class TestGenerateLearningMaterialTemplateFallback(unittest.TestCase):
    def test_template_fallback_produces_nonempty_structured_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            material = ga.generate_learning_material(paths, _candidate())
            self.assertEqual(material.source, "template")
            self.assertTrue(material.learning_path)
            self.assertTrue(material.resources)
            self.assertTrue(material.first_task)
            self.assertTrue(Path(material.body_path).exists())
            body = Path(material.body_path).read_text(encoding="utf-8")
            self.assertIn("学习路径", body)
            self.assertIn("资源清单", body)
            self.assertIn("现在就可以做的第一件事", body)

    def test_persists_to_index_and_backfills_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            cand = backlog.add_or_merge(
                title="数据分析", rationale="r", evidence_refs=["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            material = ga.generate_learning_material(paths, cand)
            rows = ga.list_materials(paths)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].material_id, material.material_id)

            updated = backlog.get(cand.candidate_id)
            self.assertEqual(updated.material_id, material.material_id)


class TestGenerateLearningMaterialLLMPath(unittest.TestCase):
    def test_llm_structured_response_is_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)

            def llm_helper(prompt):
                return (
                    '{"learning_path": ["先读文档", "跑一个例子", "记录问题"], '
                    '"resources": ["官方文档", "社区案例"], '
                    '"first_task": "跑通一个最小示例"}'
                )

            material = ga.generate_learning_material(paths, _candidate(), llm_helper=llm_helper)
            self.assertEqual(material.source, "llm")
            self.assertEqual(material.learning_path, ["先读文档", "跑一个例子", "记录问题"])
            self.assertEqual(material.resources, ["官方文档", "社区案例"])
            self.assertEqual(material.first_task, "跑通一个最小示例")

    def test_llm_response_wrapped_in_code_fence_is_parsed(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)

            def llm_helper(prompt):
                return (
                    "```json\n"
                    '{"learning_path": ["A", "B"], "resources": ["R1"], "first_task": "任务"}\n'
                    "```"
                )

            material = ga.generate_learning_material(paths, _candidate(), llm_helper=llm_helper)
            self.assertEqual(material.source, "llm")
            self.assertEqual(material.learning_path, ["A", "B"])

    def test_llm_invalid_json_falls_back_to_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)

            def llm_helper(prompt):
                return "这不是 JSON"

            material = ga.generate_learning_material(paths, _candidate(), llm_helper=llm_helper)
            self.assertEqual(material.source, "template")
            self.assertTrue(material.learning_path)
            self.assertTrue(material.first_task)

    def test_llm_missing_required_fields_falls_back_to_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)

            def llm_helper(prompt):
                # 缺 first_task
                return '{"learning_path": ["A"], "resources": []}'

            material = ga.generate_learning_material(paths, _candidate(), llm_helper=llm_helper)
            self.assertEqual(material.source, "template")

    def test_llm_exception_falls_back_to_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)

            def llm_helper(prompt):
                raise RuntimeError("boom")

            material = ga.generate_learning_material(paths, _candidate(), llm_helper=llm_helper)
            self.assertEqual(material.source, "template")
            self.assertTrue(material.learning_path)

    def test_llm_empty_response_falls_back_to_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)

            def llm_helper(prompt):
                return ""

            material = ga.generate_learning_material(paths, _candidate(), llm_helper=llm_helper)
            self.assertEqual(material.source, "template")


class TestGenerateLearningMaterialBasedOnReport(unittest.TestCase):
    def test_material_reuses_report_summary_as_background(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cand = _candidate()

            def report_llm(prompt):
                return "报告正文"

            report = ga.generate_growth_report(paths, cand, llm_helper=report_llm)
            material = ga.generate_learning_material(paths, cand, report=report)
            self.assertEqual(material.based_on_report_id, report.report_id)
            body = Path(material.body_path).read_text(encoding="utf-8")
            self.assertIn(report.summary, body)

    def test_material_without_report_uses_candidate_rationale(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cand = _candidate()
            material = ga.generate_learning_material(paths, cand)
            self.assertIsNone(material.based_on_report_id)
            body = Path(material.body_path).read_text(encoding="utf-8")
            self.assertIn(cand.rationale, body)


class TestListAndGetMaterial(unittest.TestCase):
    def test_get_material_by_id_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            material = ga.generate_learning_material(paths, _candidate())
            fetched = ga.get_material_by_id(paths, material.material_id)
            self.assertEqual(fetched.material_id, material.material_id)
            self.assertEqual(fetched.learning_path, material.learning_path)

    def test_get_material_by_id_unknown_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self.assertIsNone(ga.get_material_by_id(paths, "does-not-exist"))

    def test_list_materials_empty_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self.assertEqual(ga.list_materials(paths), [])


class TestSerializationCompat(unittest.TestCase):
    def test_material_to_dict_from_dict_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            material = ga.generate_learning_material(paths, _candidate())
            d = material.to_dict()
            restored = ga.GrowthLearningMaterial.from_dict(d)
            self.assertEqual(restored, material)

    def test_candidate_material_id_defaults_to_none_for_legacy_data(self):
        legacy = {
            "candidate_id": "c1", "title": "t", "rationale": "r",
        }
        cand = ga.GrowthCandidate.from_dict(legacy)
        self.assertIsNone(cand.material_id)


class TestGrowthCmdMaterialSubcommand(unittest.TestCase):
    class _Agent:
        def __init__(self, paths):
            self._paths = paths
            self.cfg = object()

    def test_generates_and_prints_material_when_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            cand = backlog.add_or_merge(
                title="数据分析", rationale="r", evidence_refs=["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            growth_cmd.handle_growth_cmd(
                ["material", cand.candidate_id], agent=self._Agent(paths),
            )
            updated = backlog.get(cand.candidate_id)
            self.assertIsNotNone(updated.material_id)

    def test_reuses_existing_material_without_regenerating(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            cand = backlog.add_or_merge(
                title="数据分析", rationale="r", evidence_refs=["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            material = ga.generate_learning_material(paths, cand)
            growth_cmd.handle_growth_cmd(
                ["material", cand.candidate_id], agent=self._Agent(paths),
            )
            self.assertEqual(len(ga.list_materials(paths)), 1)
            self.assertEqual(ga.list_materials(paths)[0].material_id, material.material_id)

    def test_unknown_candidate_reports_error_without_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            growth_cmd.handle_growth_cmd(
                ["material", "does-not-exist"], agent=self._Agent(paths),
            )


if __name__ == "__main__":
    unittest.main()
