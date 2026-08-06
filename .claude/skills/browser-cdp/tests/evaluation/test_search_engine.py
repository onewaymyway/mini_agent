"""
搜索引擎评估测试

测试覆盖：
- 首页访问与加载
- 搜索查询构建与提交
- 搜索结果提取
- 分页浏览
- 自动补全
"""
import pytest
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from tests.evaluation.test_runner import EvaluationRunner, ScenarioResult, WebsiteResult
from scripts.eval_config import get_websites_by_priority


class TestBaiduSearch:
    """百度搜索引擎评估测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def baidu_config(self):
        configs = get_websites_by_priority("P0")
        return next((c for c in configs if c.name == "百度"), None)

    def test_navigate_homepage(self, runner, baidu_config):
        """测试：首页访问"""
        scenario = {"id": "BDU-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, baidu_config, mock_mode=True)
        assert result.success is True
        assert result.scenario_id == "BDU-01"
        assert result.duration > 0

    def test_search_query(self, runner, baidu_config):
        """测试：搜索查询"""
        scenario = {"id": "BDU-02", "name": "搜索查询", "action": "search", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, baidu_config, mock_mode=True)
        assert result.success is True
        assert result.score > 0

    def test_extract_results(self, runner, baidu_config):
        """测试：结果提取"""
        scenario = {"id": "BDU-03", "name": "结果提取", "action": "extract", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, baidu_config, mock_mode=True)
        assert result.success is True

    def test_pagination(self, runner, baidu_config):
        """测试：分页浏览"""
        scenario = {"id": "BDU-04", "name": "分页浏览", "action": "paginate", "dimension": "稳定性"}
        result = runner._run_scenario(scenario, baidu_config, mock_mode=True)
        assert result.success is True

    def test_autocomplete(self, runner, baidu_config):
        """测试：自动补全"""
        scenario = {"id": "BDU-05", "name": "自动补全", "action": "autocomplete", "dimension": "反检测能力"}
        result = runner._run_scenario(scenario, baidu_config, mock_mode=True)
        assert result.success is True

    def test_full_website_evaluation(self, runner, baidu_config):
        """测试：完整网站评估"""
        result = runner.run_website(baidu_config, mock_mode=True)
        assert result.website_name == "百度"
        assert len(result.scenarios) == 5
        assert result.overall_score > 0
        assert result.grade != ""
        assert result.total_duration > 0


class TestBingSearch:
    """Bing 搜索引擎评估测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def bing_config(self):
        configs = get_websites_by_priority("P0")
        return next((c for c in configs if c.name == "Bing"), None)

    def test_navigate_homepage(self, runner, bing_config):
        scenario = {"id": "BING-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, bing_config, mock_mode=True)
        assert result.success is True

    def test_search_query(self, runner, bing_config):
        scenario = {"id": "BING-02", "name": "搜索查询", "action": "search", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, bing_config, mock_mode=True)
        assert result.success is True

    def test_image_search(self, runner, bing_config):
        scenario = {"id": "BING-04", "name": "图片搜索", "action": "switch_tab", "dimension": "交互成功率"}
        result = runner._run_scenario(scenario, bing_config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, bing_config):
        result = runner.run_website(bing_config, mock_mode=True)
        assert result.website_name == "Bing"
        assert len(result.scenarios) == 5
        assert result.overall_score > 0


class TestSearchEngineIntegration:
    """搜索引擎集成测试"""

    def test_all_search_engines(self):
        """测试所有搜索引擎网站"""
        configs = get_websites_by_priority("P0")
        search_engines = [c for c in configs if c.category == "搜索引擎"]
        assert len(search_engines) >= 2

        runner = EvaluationRunner(config={"delay_between_sites": 0})
        for config in search_engines:
            result = runner.run_website(config, mock_mode=True)
            assert result.overall_score > 0, f"{config.name} 评分不应为 0"
            assert result.scenario_success_rate >= 80, f"{config.name} 成功率不应低于 80%"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
