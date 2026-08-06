"""
综合评估测试

测试覆盖所有优先级网站，验证评估框架的完整性和一致性。
"""
import json
import pytest
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from tests.evaluation.test_runner import EvaluationRunner, EvaluationReport, WebsiteResult, ScenarioResult
from scripts.eval_config import WEBSITE_CONFIGS, get_websites_by_priority


class TestAllPriorities:
    """所有优先级网站测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    def test_p0_websites(self, runner):
        """P0 级网站评估"""
        configs = get_websites_by_priority("P0")
        assert len(configs) == 14
        for config in configs:
            result = runner.run_website(config, mock_mode=True)
            assert result.overall_score >= 75, f"{config.name} P0 级评分应 >= 75"
            assert result.scenario_success_rate >= 80, f"{config.name} P0 级成功率应 >= 80%"

    def test_p1_websites(self, runner):
        """P1 级网站评估"""
        configs = get_websites_by_priority("P1")
        assert len(configs) == 9
        for config in configs:
            result = runner.run_website(config, mock_mode=True)
            assert result.overall_score >= 65, f"{config.name} P1 级评分应 >= 65"
            assert result.scenario_success_rate >= 70, f"{config.name} P1 级成功率应 >= 70%"

    def test_p2_websites(self, runner):
        """P2 级网站评估"""
        configs = get_websites_by_priority("P2")
        assert len(configs) == 5
        for config in configs:
            result = runner.run_website(config, mock_mode=True)
            assert result.overall_score >= 55, f"{config.name} P2 级评分应 >= 55"
            assert result.scenario_success_rate >= 60, f"{config.name} P2 级成功率应 >= 60%"

    def test_p3_websites(self, runner):
        """P3 级网站评估"""
        configs = get_websites_by_priority("P3")
        assert len(configs) == 4
        for config in configs:
            result = runner.run_website(config, mock_mode=True)
            assert result.overall_score >= 50, f"{config.name} P3 级评分应 >= 50"
            assert result.scenario_success_rate >= 50, f"{config.name} P3 级成功率应 >= 50%"


class TestBatchExecution:
    """批量执行测试"""

    def test_batch_p0(self):
        """批量执行 P0 网站"""
        configs = get_websites_by_priority("P0")
        runner = EvaluationRunner(config={"delay_between_sites": 0})
        report = runner.run_batch(configs, mock_mode=True)
        assert report.total_websites == 14
        assert report.total_scenarios == 72  # 14 websites × ~5 scenarios
        assert report.overall_success_rate == 100.0

    def test_batch_all(self):
        """批量执行所有网站"""
        runner = EvaluationRunner(config={"delay_between_sites": 0})
        report = runner.run_batch(WEBSITE_CONFIGS, mock_mode=True)
        assert report.total_websites == 32
        assert report.total_scenarios > 50
        assert report.avg_score > 0

    def test_batch_by_category(self):
        """按分类批量执行"""
        from scripts.eval_config import get_websites_by_category
        search_sites = get_websites_by_category("搜索引擎")
        runner = EvaluationRunner(config={"delay_between_sites": 0})
        report = runner.run_batch(search_sites, mock_mode=True)
        assert report.total_websites == len(search_sites)


class TestReportGeneration:
    """报告生成测试"""

    def test_json_report(self, tmp_path):
        """JSON 报告生成"""
        configs = get_websites_by_priority("P0")[:2]
        runner = EvaluationRunner(config={"delay_between_sites": 0})
        report = runner.run_batch(configs, mock_mode=True)
        output_dir = tmp_path / "json_report"
        runner.save_report(report, output_dir)

        json_files = list(output_dir.glob("*.json"))
        assert len(json_files) >= 1

        # 验证 JSON 格式
        with open(json_files[0], "r", encoding="utf-8") as f:
            data = json.load(f)
            assert "total_websites" in data
            assert "website_results" in data

    def test_markdown_report(self, tmp_path):
        """Markdown 报告生成"""
        configs = get_websites_by_priority("P0")[:2]
        runner = EvaluationRunner(config={"delay_between_sites": 0})
        report = runner.run_batch(configs, mock_mode=True)
        output_dir = tmp_path / "md_report"
        runner.save_report(report, output_dir)

        md_files = list(output_dir.glob("*.md"))
        assert len(md_files) >= 1

        # 验证 Markdown 内容
        with open(md_files[0], "r", encoding="utf-8") as f:
            content = f.read()
            assert "评估汇总报告" in content
            assert "百度" in content

    def test_website_detail_reports(self, tmp_path):
        """各网站详细报告生成"""
        configs = get_websites_by_priority("P0")[:1]
        runner = EvaluationRunner(config={"delay_between_sites": 0})
        report = runner.run_batch(configs, mock_mode=True)
        output_dir = tmp_path / "detail_report"
        runner.save_report(report, output_dir)

        # 检查网站目录
        website_dir = output_dir / "百度"
        assert website_dir.exists()
        assert (website_dir / "百度_detail.json").exists()
        assert (website_dir / "百度_detail.md").exists()


class TestEdgeCases:
    """边界情况测试"""

    def test_empty_website_config(self):
        """空配置处理"""
        result = WebsiteResult(
            website_name="测试",
            website_url="https://test.com",
            priority="P0",
            category="测试",
        )
        result.calculate_overall()
        assert result.overall_score == 0.0
        assert result.grade == "不可用 (F)"

    def test_all_scenarios_fail(self):
        """所有场景失败"""
        runner = EvaluationRunner(config={"delay_between_sites": 0})
        configs = get_websites_by_priority("P0")[:1]
        # 模拟所有场景失败
        for config in configs:
            result = WebsiteResult(
                website_name=config.name,
                website_url=config.url,
                priority=config.priority,
                category=config.category,
            )
            for scenario in config.scenarios:
                result.add_scenario(ScenarioResult(
                    scenario_id=scenario["id"],
                    scenario_name=scenario["name"],
                    website_name=config.name,
                    success=False,
                    duration=1.0,
                    error="模拟失败",
                ))
            result.total_duration = len(config.scenarios)
            result.calculate_overall()
            assert result.scenario_success_rate == 0.0
            assert result.grade == "不可用 (F)"

    def test_partial_scenario_failure(self):
        """部分场景失败"""
        runner = EvaluationRunner(config={"delay_between_sites": 0})
        configs = get_websites_by_priority("P0")[:1]
        for config in configs:
            result = runner.run_website(config, mock_mode=True)
            # 正常情况应该大部分成功
            assert result.scenario_success_rate > 0


class TestPerformance:
    """性能测试"""

    def test_batch_execution_time(self):
        """批量执行时间"""
        import time
        configs = get_websites_by_priority("P0")
        runner = EvaluationRunner(config={"delay_between_sites": 0})
        start = time.time()
        report = runner.run_batch(configs, mock_mode=True)
        elapsed = time.time() - start
        # 4 个 P0 网站应在 10 秒内完成
        assert elapsed < 10, f"批量执行耗时 {elapsed:.2f}s 超过 10s"
        assert report.total_duration < 10

    def test_concurrent_scenario_execution(self):
        """场景并发执行测试"""
        runner = EvaluationRunner(config={"delay_between_sites": 0})
        configs = get_websites_by_priority("P0")
        # 验证所有场景都能执行
        for config in configs:
            for scenario in config.scenarios:
                result = runner._run_scenario(scenario, config, mock_mode=True)
                assert result is not None
                assert result.scenario_id == scenario["id"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
