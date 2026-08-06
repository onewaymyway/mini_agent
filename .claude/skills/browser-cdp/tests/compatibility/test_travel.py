"""
旅行平台兼容性测试

测试覆盖：
- 飞猪
- 首页访问、机票搜索、列表提取、价格提取、酒店搜索
"""
import pytest
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from tests.evaluation.test_runner import EvaluationRunner
from scripts.eval_config import get_websites_by_priority


class TestFliggy:
    """飞猪兼容性测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def config(self):
        configs = get_websites_by_priority("P3")
        return next((c for c in configs if c.name == "飞猪"), None)

    def test_navigate_homepage(self, runner, config):
        scenario = {"id": "FP-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_search_flight(self, runner, config):
        scenario = {"id": "FP-02", "name": "机票搜索", "action": "search_flight", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_list(self, runner, config):
        scenario = {"id": "FP-03", "name": "列表提取", "action": "extract_list", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_price(self, runner, config):
        scenario = {"id": "FP-04", "name": "价格提取", "action": "extract_price", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_switch_hotel(self, runner, config):
        scenario = {"id": "FP-05", "name": "酒店搜索", "action": "switch_hotel", "dimension": "交互成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, config):
        result = runner.run_website(config, mock_mode=True)
        assert result.website_name == "飞猪"
        assert len(result.scenarios) == 5
        assert result.overall_score > 0


class TestTravelIntegration:
    """旅行平台集成测试"""

    def test_all_travel_sites(self):
        configs = get_websites_by_priority("P3")
        travel_sites = [c for c in configs if c.category == "旅行平台"]
        assert len(travel_sites) >= 1

        runner = EvaluationRunner(config={"delay_between_sites": 0})
        for config in travel_sites:
            result = runner.run_website(config, mock_mode=True)
            assert result.overall_score > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
