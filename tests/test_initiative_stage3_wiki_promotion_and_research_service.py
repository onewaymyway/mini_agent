"""[next_doc/initiative_systems_unification_plan.md 阶段三] 单元测试：

1. growth_advisor.promote_growth_report_to_wiki() —— 报告回写 wiki。
2. adopt_candidate_as_goal(cfg=...) —— wiki_promotion_on_adopt_enabled
   开关：默认关闭 no-op，开启后落地成 Goal 时自动回写。
3. evolution/research_service.py —— 抽取出的共享调研服务：
   filter_compliance_text() 与 capability_learning.apply_compliance_filter()
   行为一致（回归保证抽取没有改变外部可观察行为）。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.config.models import GrowthAdvisorConfig
from mini_agent.evolution import growth_advisor as ga
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


def _accepted_candidate_with_report(paths, title="数据分析"):
    backlog = ga.GrowthBacklog(paths)
    cand = backlog.add_or_merge(
        title=title,
        rationale="你最近经常聊到这个方向",
        evidence_refs=["e1", "e2", "e3"],
        min_evidence_count=3,
        max_pending=10,
        dismissed_cooldown_days=30,
    )
    backlog.set_status(cand.candidate_id, ga.STATUS_ACCEPTED)
    report = ga.GrowthReport(
        report_id="r1",
        candidate_id=cand.candidate_id,
        title=title,
        slug="data-analysis",
        summary="调研摘要正文",
        body_path=str(Path(paths.workdir_dir) / "growth_reports" / "r1.md"),
    )
    body_path = Path(report.body_path)
    body_path.parent.mkdir(parents=True, exist_ok=True)
    body_path.write_text("# 报告正文", encoding="utf-8")
    ga._append_jsonl(paths.growth_reports_index_path, report.to_dict())
    backlog.attach_report(cand.candidate_id, report.report_id)
    return backlog.get(cand.candidate_id), report


class TestPromoteGrowthReportToWiki(unittest.TestCase):
    def test_writes_wiki_page_with_user_growth_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cand, report = _accepted_candidate_with_report(paths)

            page_id = ga.promote_growth_report_to_wiki(paths, cand, report, goal_id="g1")

            self.assertEqual(page_id, f"growth_{cand.candidate_id}")
            page_path = paths.wiki_type_dir("topic") / f"{page_id}.md"
            self.assertTrue(page_path.exists())
            text = page_path.read_text(encoding="utf-8")
            self.assertIn("user_growth", text)
            self.assertIn("调研摘要正文", text)
            self.assertIn("g1", text)


class TestAdoptCandidateAsGoalWikiPromotion(unittest.TestCase):
    def test_disabled_by_default_no_wiki_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cand, _report = _accepted_candidate_with_report(paths)
            cfg = GrowthAdvisorConfig()
            self.assertFalse(cfg.wiki_promotion_on_adopt_enabled)

            ga.adopt_candidate_as_goal(paths, cand, cfg=cfg)

            page_path = paths.wiki_type_dir("topic") / f"growth_{cand.candidate_id}.md"
            self.assertFalse(page_path.exists())

    def test_no_cfg_passed_no_wiki_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cand, _report = _accepted_candidate_with_report(paths)

            ga.adopt_candidate_as_goal(paths, cand)  # cfg=None，与改动前调用方式一致

            page_path = paths.wiki_type_dir("topic") / f"growth_{cand.candidate_id}.md"
            self.assertFalse(page_path.exists())

    def test_enabled_writes_wiki_on_adopt(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cand, _report = _accepted_candidate_with_report(paths)
            cfg = GrowthAdvisorConfig(wiki_promotion_on_adopt_enabled=True)

            goal = ga.adopt_candidate_as_goal(paths, cand, cfg=cfg)

            page_path = paths.wiki_type_dir("topic") / f"growth_{cand.candidate_id}.md"
            self.assertTrue(page_path.exists())
            self.assertIn(goal.id, page_path.read_text(encoding="utf-8"))

    def test_wiki_write_failure_does_not_block_goal_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cand, _report = _accepted_candidate_with_report(paths)
            cfg = GrowthAdvisorConfig(wiki_promotion_on_adopt_enabled=True)

            import mini_agent.evolution.growth_advisor as ga_mod
            original = ga_mod.promote_growth_report_to_wiki
            ga_mod.promote_growth_report_to_wiki = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom"))
            try:
                goal = ga.adopt_candidate_as_goal(paths, cand, cfg=cfg)
            finally:
                ga_mod.promote_growth_report_to_wiki = original

            self.assertIsNotNone(goal)


class TestResearchServiceExtractionParity(unittest.TestCase):
    """抽取 evolution/research_service.py 之后，capability_learning 里
    保留的对外签名（`apply_compliance_filter`/`is_disclaimer_required_track`）
    行为必须与抽取前完全一致——这里直接对比两边在同样输入下的输出。"""

    def test_filter_compliance_text_matches_capability_apply_compliance_filter(self):
        from mini_agent.evolution import capability_learning as cl
        from mini_agent.evolution import research_service as rs

        track = cl.CapabilityTrack(
            track_id="cap_x", title="股票分析能力", persona_desc="", wiki_tag="stock",
        )
        results = [
            {"summary": "建议买入这只股票，目标价100元。技术面上看趋势向好。"},
            {"summary": "这是一段完全正常的方法论描述。"},
        ]

        filtered_a, any_filtered_a, disclaimer_a = cl.apply_compliance_filter(results, track)
        filtered_b, any_filtered_b, disclaimer_b = rs.filter_compliance_text(
            results, domain_hint=f"{track.title} {track.persona_desc} {track.wiki_tag}",
        )

        self.assertEqual(filtered_a, filtered_b)
        self.assertEqual(any_filtered_a, any_filtered_b)
        self.assertEqual(disclaimer_a, disclaimer_b)

    def test_capability_learning_delegates_to_research_service(self):
        """确认 capability_learning.apply_compliance_filter 内部真的走了
        research_service（不是两套并行维护的重复实现）。"""
        import inspect
        from mini_agent.evolution import capability_learning as cl

        src = inspect.getsource(cl.apply_compliance_filter)
        self.assertIn("research_service", src)


if __name__ == "__main__":
    unittest.main()
