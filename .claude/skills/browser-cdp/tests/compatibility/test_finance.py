"""
金融数据兼容性测试

测试覆盖：
- 东方财富、雪球
- 首页访问、股票搜索、实时数据、历史数据、图表验证
"""
import pytest
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from tests.evaluation.test_runner import EvaluationRunner
from scripts.eval_config import get_websites_by_priority


class TestEastmoney:
    """东方财富兼容性测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def config(self):
        configs = get_websites_by_priority("P3")
        return next((c for c in configs if c.name == "东方财富"), None)

    def test_navigate_homepage(self, runner, config):
        scenario = {"id": "EM-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_search_stock(self, runner, config):
        scenario = {"id": "EM-02", "name": "股票搜索", "action": "search_stock", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_realtime(self, runner, config):
        scenario = {"id": "EM-03", "name": "实时数据提取", "action": "extract_realtime", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_history(self, runner, config):
        scenario = {"id": "EM-04", "name": "历史数据提取", "action": "extract_history", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_check_chart(self, runner, config):
        scenario = {"id": "EM-05", "name": "图表验证", "action": "check_chart", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, config):
        result = runner.run_website(config, mock_mode=True)
        assert result.website_name == "东方财富"
        assert len(result.scenarios) == 5
        assert result.overall_score > 0


class TestXueqiu:
    """雪球兼容性测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def config(self):
        configs = get_websites_by_priority("P3")
        return next((c for c in configs if c.name == "雪球"), None)

    def test_navigate_homepage(self, runner, config):
        scenario = {"id": "XQ-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_search_stock(self, runner, config):
        scenario = {"id": "XQ-02", "name": "股票搜索", "action": "search_stock", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_realtime(self, runner, config):
        scenario = {"id": "XQ-03", "name": "实时数据提取", "action": "extract_realtime", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_click_discuss(self, runner, config):
        scenario = {"id": "XQ-04", "name": "讨论区访问", "action": "click_discuss", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_posts(self, runner, config):
        scenario = {"id": "XQ-05", "name": "帖子提取", "action": "extract_posts", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, config):
        result = runner.run_website(config, mock_mode=True)
        assert result.website_name == "雪球"
        assert len(result.scenarios) == 5
        assert result.overall_score > 0


class TestFinanceIntegration:
    """金融数据集成测试"""

    def test_all_finance_sites(self):
        configs = get_websites_by_priority("P3")
        finance_sites = [c for c in configs if c.category in ["金融数据", "金融社区"]]
        assert len(finance_sites) >= 2

        runner = EvaluationRunner(config={"delay_between_sites": 0})
        for config in finance_sites:
            result = runner.run_website(config, mock_mode=True)
            assert result.overall_score > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
