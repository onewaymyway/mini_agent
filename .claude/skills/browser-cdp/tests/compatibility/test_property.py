"""
房产平台兼容性测试

测试覆盖：
- 链家、安居客
- 首页访问、房源搜索、列表提取、详情页、地图交互
"""
import pytest
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from tests.evaluation.test_runner import EvaluationRunner
from scripts.eval_config import get_websites_by_priority


class TestLianjia:
    """链家兼容性测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def config(self):
        configs = get_websites_by_priority("P2")
        return next((c for c in configs if c.name == "链家"), None)

    def test_navigate_homepage(self, runner, config):
        scenario = {"id": "LJ-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_search(self, runner, config):
        scenario = {"id": "LJ-02", "name": "房源搜索", "action": "search", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_list(self, runner, config):
        scenario = {"id": "LJ-03", "name": "列表提取", "action": "extract_list", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_click_detail(self, runner, config):
        scenario = {"id": "LJ-04", "name": "详情页访问", "action": "click_detail", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_house(self, runner, config):
        scenario = {"id": "LJ-05", "name": "房源信息提取", "action": "extract_house", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, config):
        result = runner.run_website(config, mock_mode=True)
        assert result.website_name == "链家"
        assert len(result.scenarios) == 5
        assert result.overall_score > 0


class TestAnjuke:
    """安居客兼容性测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def config(self):
        configs = get_websites_by_priority("P2")
        return next((c for c in configs if c.name == "安居客"), None)

    def test_navigate_homepage(self, runner, config):
        scenario = {"id": "AJK-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_search(self, runner, config):
        scenario = {"id": "AJK-02", "name": "房源搜索", "action": "search", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_list(self, runner, config):
        scenario = {"id": "AJK-03", "name": "列表提取", "action": "extract_list", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_click_detail(self, runner, config):
        scenario = {"id": "AJK-04", "name": "详情页访问", "action": "click_detail", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_switch_map(self, runner, config):
        scenario = {"id": "AJK-05", "name": "地图交互", "action": "switch_map", "dimension": "交互成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, config):
        result = runner.run_website(config, mock_mode=True)
        assert result.website_name == "安居客"
        assert len(result.scenarios) == 5
        assert result.overall_score > 0


class TestPropertyIntegration:
    """房产平台集成测试"""

    def test_all_property_sites(self):
        configs = get_websites_by_priority("P2")
        property_sites = [c for c in configs if c.category == "房产平台"]
        assert len(property_sites) >= 2

        runner = EvaluationRunner(config={"delay_between_sites": 0})
        for config in property_sites:
            result = runner.run_website(config, mock_mode=True)
            assert result.overall_score > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
