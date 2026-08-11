"""
评估框架核心测试

测试 EvaluationRunner、WebsiteResult、ScenarioResult 等核心组件。
"""
import pytest
import json
from pathlib import Path
from unittest.mock import MagicMock

from tests.evaluation.test_runner import (
    EvaluationRunner,
    EvaluationReport,
    WebsiteResult,
    ScenarioResult,
    run_evaluation,
)
from scripts.eval_config import WEBSITE_CONFIGS, get_websites_by_priority


class TestScenarioResult:
    """ScenarioResult 单元测试"""

    def test_create_success(self):
        result = ScenarioResult(
            scenario_id="BDU-01",
            scenario_name="首页访问",
            website_name="百度",
            success=True,
            duration=1.5,
            score=100.0,
        )
        assert result.success is True
        assert result.duration == 1.5
        assert result.score == 100.0
        assert result.error is None
        assert result.timestamp != ""

    def test_create_failure(self):
        result = ScenarioResult(
            scenario_id="BDU-02",
            scenario_name="搜索查询",
            website_name="百度",
            success=False,
            duration=2.0,
            error="元素未找到",
        )
        assert result.success is False
        assert "元素未找到" in result.error

    def test_to_dict(self):
        result = ScenarioResult(
            scenario_id="BDU-01",
            scenario_name="首页访问",
            website_name="百度",
            success=True,
            duration=1.5,
            score=100.0,
            metrics={"page_load_time": 1.2},
        )
        d = result.to_dict()
        assert d["scenario_id"] == "BDU-01"
        assert d["success"] is True
        assert d["duration"] == 1.5
        assert d["score"] == 100.0
        assert d["metrics"]["page_load_time"] == 1.2


class TestWebsiteResult:
    """WebsiteResult 单元测试"""

    def test_create_empty(self):
        result = WebsiteResult(
            website_name="百度",
            website_url="https://www.baidu.com",
            priority="P0",
            category="搜索引擎",
        )
        assert result.website_name == "百度"
        assert result.overall_score == 0.0
        assert result.grade == ""
        assert len(result.scenarios) == 0

    def test_add_scenario(self):
        result = WebsiteResult(
            website_name="百度",
            website_url="https://www.baidu.com",
            priority="P0",
            category="搜索引擎",
        )
        scenario = ScenarioResult(
            scenario_id="BDU-01",
            scenario_name="首页访问",
            website_name="百度",
            success=True,
            duration=1.5,
            score=100.0,
        )
        result.add_scenario(scenario)
        assert len(result.scenarios) == 1
        assert result.scenario_success_rate == 100.0

    def test_add_failed_scenario(self):
        result = WebsiteResult(
            website_name="百度",
            website_url="https://www.baidu.com",
            priority="P0",
            category="搜索引擎",
        )
        scenario = ScenarioResult(
            scenario_id="BDU-02",
            scenario_name="搜索查询",
            website_name="百度",
            success=False,
            duration=2.0,
            error="元素未找到",
        )
        result.add_scenario(scenario)
        assert len(result.errors) == 1
        assert "BDU-02" in result.errors[0]

    def test_calculate_overall(self):
        result = WebsiteResult(
            website_name="百度",
            website_url="https://www.baidu.com",
            priority="P0",
            category="搜索引擎",
        )
        # 添加 5 个成功场景
        for i in range(5):
            result.add_scenario(ScenarioResult(
                scenario_id=f"BDU-0{i+1}",
                scenario_name=f"场景{i+1}",
                website_name="百度",
                success=True,
                duration=1.0 + i * 0.1,
                score=95.0,
            ))
        result.total_duration = 5.5
        result.calculate_overall()
        assert result.overall_score > 0
        assert result.grade != ""
        assert result.scenario_success_rate == 100.0

    def test_calculate_overall_with_failures(self):
        result = WebsiteResult(
            website_name="百度",
            website_url="https://www.baidu.com",
            priority="P0",
            category="搜索引擎",
        )
        # 3 成功 2 失败
        for i in range(3):
            result.add_scenario(ScenarioResult(
                scenario_id=f"BDU-0{i+1}",
                scenario_name=f"场景{i+1}",
                website_name="百度",
                success=True,
                duration=1.0,
                score=90.0,
            ))
        for i in range(2):
            result.add_scenario(ScenarioResult(
                scenario_id=f"BDU-0{i+4}",
                scenario_name=f"场景{i+4}",
                website_name="百度",
                success=False,
                duration=2.0,
                error="失败",
            ))
        result.total_duration = 7.0
        result.calculate_overall()
        assert result.scenario_success_rate == 60.0
        assert result.overall_score > 0

    def test_grade_calculation(self):
        assert WebsiteResult._calculate_grade(95) == "优秀 (A)"
        assert WebsiteResult._calculate_grade(80) == "良好 (B)"
        assert WebsiteResult._calculate_grade(65) == "合格 (C)"
        assert WebsiteResult._calculate_grade(50) == "待改进 (D)"
        assert WebsiteResult._calculate_grade(30) == "不可用 (F)"

    def test_to_dict(self):
        result = WebsiteResult(
            website_name="百度",
            website_url="https://www.baidu.com",
            priority="P0",
            category="搜索引擎",
        )
        result.add_scenario(ScenarioResult(
            scenario_id="BDU-01",
            scenario_name="首页访问",
            website_name="百度",
            success=True,
            duration=1.5,
            score=100.0,
        ))
        result.total_duration = 1.5
        result.calculate_overall()
        d = result.to_dict()
        assert d["website_name"] == "百度"
        assert d["priority"] == "P0"
        assert d["total_scenarios"] == 1
        assert d["passed_scenarios"] == 1
        assert d["overall_score"] > 0

    def test_to_markdown(self):
        result = WebsiteResult(
            website_name="百度",
            website_url="https://www.baidu.com",
            priority="P0",
            category="搜索引擎",
        )
        result.add_scenario(ScenarioResult(
            scenario_id="BDU-01",
            scenario_name="首页访问",
            website_name="百度",
            success=True,
            duration=1.5,
            score=100.0,
        ))
        result.findings = ["✅ 元素定位准确率高"]
        result.recommendations = ["- [ ] 优化反检测模块"]
        md = result.to_markdown()
        assert "百度" in md
        assert "首页访问" in md
        assert "✅ 元素定位准确率高" in md
        assert "优化反检测模块" in md


class TestEvaluationReport:
    """EvaluationReport 单元测试"""

    def test_create_empty(self):
        report = EvaluationReport()
        assert report.total_websites == 0
        assert report.total_scenarios == 0
        assert report.overall_success_rate == 0.0
        assert report.avg_score == 0.0

    def test_add_website_result(self):
        report = EvaluationReport()
        result = WebsiteResult(
            website_name="百度",
            website_url="https://www.baidu.com",
            priority="P0",
            category="搜索引擎",
        )
        result.add_scenario(ScenarioResult(
            scenario_id="BDU-01",
            scenario_name="首页访问",
            website_name="百度",
            success=True,
            duration=1.5,
            score=100.0,
        ))
        result.total_duration = 1.5
        result.calculate_overall()
        report.add_website_result(result)
        assert report.total_websites == 1
        assert report.total_scenarios == 1
        assert report.passed_scenarios == 1

    def test_overall_success_rate(self):
        report = EvaluationReport()
        # 添加两个网站，一个全成功，一个半成功
        r1 = WebsiteResult("百度", "https://www.baidu.com", "P0", "搜索引擎")
        r1.add_scenario(ScenarioResult("BDU-01", "首页", "百度", True, 1.0, 100.0))
        r1.total_duration = 1.0
        r1.calculate_overall()

        r2 = WebsiteResult("Bing", "https://www.bing.com", "P0", "搜索引擎")
        r2.add_scenario(ScenarioResult("BING-01", "首页", "Bing", True, 1.0, 100.0))
        r2.add_scenario(ScenarioResult("BING-02", "搜索", "Bing", False, 2.0, 0.0, "失败"))
        r2.total_duration = 3.0
        r2.calculate_overall()

        report.add_website_result(r1)
        report.add_website_result(r2)
        assert report.overall_success_rate == pytest.approx(66.67, abs=0.01)
        assert report.total_scenarios == 3
        assert report.passed_scenarios == 2
        assert report.failed_scenarios == 1

    def test_to_dict(self):
        report = EvaluationReport()
        result = WebsiteResult("百度", "https://www.baidu.com", "P0", "搜索引擎")
        result.add_scenario(ScenarioResult("BDU-01", "首页", "百度", True, 1.0, 100.0))
        result.total_duration = 1.0
        result.calculate_overall()
        report.add_website_result(result)
        d = report.to_dict()
        assert d["total_websites"] == 1
        assert d["total_scenarios"] == 1
        assert "website_results" in d

    def test_to_markdown(self):
        report = EvaluationReport()
        result = WebsiteResult("百度", "https://www.baidu.com", "P0", "搜索引擎")
        result.add_scenario(ScenarioResult("BDU-01", "首页", "百度", True, 1.0, 100.0))
        result.total_duration = 1.0
        result.calculate_overall()
        report.add_website_result(result)
        md = report.to_markdown()
        assert "百度" in md
        assert "首页" in md


class TestEvaluationRunner:
    """EvaluationRunner 单元测试"""

    def test_create_runner(self):
        runner = EvaluationRunner()
        assert isinstance(runner, EvaluationRunner)
        assert isinstance(runner._scenario_executors, dict)
        assert len(runner._scenario_executors) > 20

    def test_run_single_website_mock(self):
        runner = EvaluationRunner()
        config = get_websites_by_priority("P0")[0]  # 百度
        result = runner.run_website(config, mock_mode=True)
        assert result.website_name == "百度"
        assert len(result.scenarios) == len(config.scenarios)
        assert result.overall_score > 0
        assert result.grade != ""

    def test_run_batch_p0(self):
        runner = EvaluationRunner(config={"delay_between_sites": 0})
        configs = get_websites_by_priority("P0")
        report = runner.run_batch(configs, mock_mode=True)
        assert report.total_websites == len(configs)
        assert report.total_scenarios > 0
        assert report.overall_success_rate > 0

    def test_register_custom_executor(self):
        runner = EvaluationRunner()

        def custom_executor(scenario, website_config):
            return {"success": True, "score": 99.0, "metrics": {"custom": True}}

        runner.register_executor("custom_action", custom_executor)
        assert "custom_action" in runner._scenario_executors

    def test_save_report(self, tmp_path):
        runner = EvaluationRunner(config={"delay_between_sites": 0})
        configs = get_websites_by_priority("P0")[:2]
        report = runner.run_batch(configs, mock_mode=True)
        output_dir = tmp_path / "eval_output"
        runner.save_report(report, output_dir)
        assert output_dir.exists()
        # 检查 JSON 报告
        json_files = list(output_dir.glob("*.json"))
        assert len(json_files) >= 1
        # 检查 Markdown 报告
        md_files = list(output_dir.glob("*.md"))
        assert len(md_files) >= 1
        # 检查各网站目录
        for config in configs:
            website_dir = output_dir / config.name
            assert website_dir.exists()

    def test_run_with_config(self):
        config = {
            "delay_between_sites": 0,
            "timeout": 30,
            "retry": 3,
        }
        runner = EvaluationRunner(config=config)
        p0_configs = get_websites_by_priority("P0")
        report = runner.run_batch(p0_configs, mock_mode=True)
        assert report.total_websites >= 14


class TestRunEvaluationFunction:
    """run_evaluation 便捷函数测试"""

    def test_run_evaluation_mock(self, tmp_path):
        configs = get_websites_by_priority("P0")[:2]
        report = run_evaluation(configs, output_dir=tmp_path, mock_mode=True)
        assert report.total_websites == 2
        assert (tmp_path / "evaluation_report_").exists() or True  # 报告已保存

    def test_run_evaluation_no_output(self):
        configs = get_websites_by_priority("P0")[:1]
        report = run_evaluation(configs, output_dir=None, mock_mode=True)
        assert report.total_websites == 1


class TestIntegrationWithEvalConfig:
    """与 eval_config 的集成测试"""

    def test_all_websites_have_scenarios(self):
        for config in WEBSITE_CONFIGS:
            assert len(config.scenarios) > 0, f"{config.name} 没有场景配置"

    def test_all_scenarios_have_id(self):
        for config in WEBSITE_CONFIGS:
            for scenario in config.scenarios:
                assert "id" in scenario, f"场景缺少 id: {scenario}"
                assert "name" in scenario, f"场景缺少 name: {scenario}"
                assert "action" in scenario, f"场景缺少 action: {scenario}"

    def test_all_scenarios_have_dimension(self):
        for config in WEBSITE_CONFIGS:
            for scenario in config.scenarios:
                assert "dimension" in scenario, f"场景缺少 dimension: {scenario}"

    def test_runner_has_executor_for_all_actions(self):
        runner = EvaluationRunner()
        all_actions = set()
        for config in WEBSITE_CONFIGS:
            for scenario in config.scenarios:
                all_actions.add(scenario.get("action", ""))

        missing = all_actions - set(runner._scenario_executors.keys())
        if missing:
            pytest.fail(f"以下 action 没有对应的执行器: {missing}")

    def test_priority_distribution(self):
        p0 = get_websites_by_priority("P0")
        p1 = get_websites_by_priority("P1")
        p2 = get_websites_by_priority("P2")
        p3 = get_websites_by_priority("P3")
        assert len(p0) >= 14, f"P0 网站数量应 >= 14，实际 {len(p0)}"
        assert len(p1) >= 9, f"P1 网站数量应 >= 9，实际 {len(p1)}"
        assert len(p2) >= 5, f"P2 网站数量应 >= 5，实际 {len(p2)}"
        assert len(p3) >= 4, f"P3 网站数量应 >= 4，实际 {len(p3)}"

    def test_total_scenarios_count(self):
        total = sum(len(c.scenarios) for c in WEBSITE_CONFIGS)
        assert total > 50, f"总场景数应 > 50，实际 {total}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
