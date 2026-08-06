"""
新闻资讯网站兼容性测试

测试覆盖：
- 新浪新闻、网易新闻、财联社
- 首页访问、列表提取、详情页、正文提取、分页
"""
import pytest
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from tests.evaluation.test_runner import EvaluationRunner
from scripts.eval_config import get_websites_by_priority


class TestSinaNews:
    """新浪新闻兼容性测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def config(self):
        configs = get_websites_by_priority("P0")
        return next((c for c in configs if c.name == "新浪新闻"), None)

    def test_navigate_homepage(self, runner, config):
        scenario = {"id": "SINA-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_list(self, runner, config):
        scenario = {"id": "SINA-02", "name": "列表提取", "action": "extract_list", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_click_detail(self, runner, config):
        scenario = {"id": "SINA-03", "name": "详情页访问", "action": "click_detail", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_article(self, runner, config):
        scenario = {"id": "SINA-04", "name": "正文提取", "action": "extract_article", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_pagination(self, runner, config):
        scenario = {"id": "SINA-05", "name": "分页浏览", "action": "paginate", "dimension": "稳定性"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, config):
        result = runner.run_website(config, mock_mode=True)
        assert result.website_name == "新浪新闻"
        assert len(result.scenarios) == 5
        assert result.overall_score > 0


class TestNeteaseNews:
    """网易新闻兼容性测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def config(self):
        configs = get_websites_by_priority("P0")
        return next((c for c in configs if c.name == "网易新闻"), None)

    def test_navigate_homepage(self, runner, config):
        scenario = {"id": "WY-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_list(self, runner, config):
        scenario = {"id": "WY-02", "name": "列表提取", "action": "extract_list", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_click_detail(self, runner, config):
        scenario = {"id": "WY-03", "name": "详情页访问", "action": "click_detail", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_article(self, runner, config):
        scenario = {"id": "WY-04", "name": "正文提取", "action": "extract_article", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_comments(self, runner, config):
        scenario = {"id": "WY-05", "name": "评论提取", "action": "extract_comments", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, config):
        result = runner.run_website(config, mock_mode=True)
        assert result.website_name == "网易新闻"
        assert len(result.scenarios) == 5
        assert result.overall_score > 0


class TestCLS:
    """财联社兼容性测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def config(self):
        configs = get_websites_by_priority("P1")
        return next((c for c in configs if c.name == "财联社"), None)

    def test_navigate_homepage(self, runner, config):
        scenario = {"id": "CLS-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_list(self, runner, config):
        scenario = {"id": "CLS-02", "name": "快讯列表提取", "action": "extract_list", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_click_detail(self, runner, config):
        scenario = {"id": "CLS-03", "name": "专题页访问", "action": "click_detail", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_realtime(self, runner, config):
        scenario = {"id": "CLS-04", "name": "实时行情提取", "action": "extract_realtime", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_pagination(self, runner, config):
        scenario = {"id": "CLS-05", "name": "分页浏览", "action": "paginate", "dimension": "稳定性"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, config):
        result = runner.run_website(config, mock_mode=True)
        assert result.website_name == "财联社"
        assert len(result.scenarios) == 5
        assert result.overall_score > 0


class TestNewsIntegration:
    """新闻资讯集成测试"""

    def test_all_news_sites(self):
        configs = get_websites_by_priority("P0")
        news_sites = [c for c in configs if c.category == "新闻资讯"]
        assert len(news_sites) >= 2

        runner = EvaluationRunner(config={"delay_between_sites": 0})
        for config in news_sites:
            result = runner.run_website(config, mock_mode=True)
            assert result.overall_score > 0
            assert result.scenario_success_rate >= 80


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
