"""
搜索引擎兼容性测试

测试覆盖：
- 百度、Bing
- 首页访问、搜索、结果提取、分页、自动补全
"""
import pytest
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from tests.evaluation.test_runner import EvaluationRunner
from scripts.eval_config import get_websites_by_priority


class TestBaidu:
    """百度兼容性测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def config(self):
        configs = get_websites_by_priority("P0")
        return next((c for c in configs if c.name == "百度"), None)

    def test_navigate_homepage(self, runner, config):
        scenario = {"id": "BDU-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_search(self, runner, config):
        scenario = {"id": "BDU-02", "name": "搜索查询", "action": "search", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_results(self, runner, config):
        scenario = {"id": "BDU-03", "name": "结果提取", "action": "extract", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_pagination(self, runner, config):
        scenario = {"id": "BDU-04", "name": "分页浏览", "action": "paginate", "dimension": "稳定性"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_autocomplete(self, runner, config):
        scenario = {"id": "BDU-05", "name": "自动补全", "action": "autocomplete", "dimension": "反检测能力"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, config):
        result = runner.run_website(config, mock_mode=True)
        assert result.website_name == "百度"
        assert len(result.scenarios) == 5
        assert result.overall_score > 0


class TestBing:
    """Bing 兼容性测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def config(self):
        configs = get_websites_by_priority("P0")
        return next((c for c in configs if c.name == "Bing"), None)

    def test_navigate_homepage(self, runner, config):
        scenario = {"id": "BING-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_search(self, runner, config):
        scenario = {"id": "BING-02", "name": "搜索查询", "action": "search", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_results(self, runner, config):
        scenario = {"id": "BING-03", "name": "结果提取", "action": "extract", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_image_search(self, runner, config):
        scenario = {"id": "BING-04", "name": "图片搜索", "action": "switch_tab", "dimension": "交互成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_video_search(self, runner, config):
        scenario = {"id": "BING-05", "name": "视频搜索", "action": "switch_tab", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, config):
        result = runner.run_website(config, mock_mode=True)
        assert result.website_name == "Bing"
        assert len(result.scenarios) == 5
        assert result.overall_score > 0


class TestSearchIntegration:
    """搜索引擎集成测试"""

    def test_all_search_sites(self):
        configs = get_websites_by_priority("P0")
        search_sites = [c for c in configs if c.category == "搜索引擎"]
        assert len(search_sites) >= 2

        runner = EvaluationRunner(config={"delay_between_sites": 0})
        for config in search_sites:
            result = runner.run_website(config, mock_mode=True)
            assert result.overall_score > 0
            assert result.scenario_success_rate >= 80


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
