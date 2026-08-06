"""
社交内容网站兼容性测试

测试覆盖：
- 知乎、微博、小红书
- 首页访问、搜索、内容提取、登录态检测
"""
import pytest
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from tests.evaluation.test_runner import EvaluationRunner
from scripts.eval_config import get_websites_by_priority


class TestZhihu:
    """知乎兼容性测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def config(self):
        configs = get_websites_by_priority("P0")
        return next((c for c in configs if c.name == "知乎"), None)

    def test_navigate_homepage(self, runner, config):
        scenario = {"id": "ZHIHU-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_search(self, runner, config):
        scenario = {"id": "ZHIHU-02", "name": "搜索查询", "action": "search", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_results(self, runner, config):
        scenario = {"id": "ZHIHU-03", "name": "结果提取", "action": "extract", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_click_detail(self, runner, config):
        scenario = {"id": "ZHIHU-04", "name": "问题详情页", "action": "click_detail", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_answers(self, runner, config):
        scenario = {"id": "ZHIHU-05", "name": "回答提取", "action": "extract_answers", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_check_login(self, runner, config):
        scenario = {"id": "ZHIHU-06", "name": "登录态检测", "action": "check_login", "dimension": "反检测能力"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, config):
        result = runner.run_website(config, mock_mode=True)
        assert result.website_name == "知乎"
        assert len(result.scenarios) == 6
        assert result.overall_score > 0


class TestWeibo:
    """微博兼容性测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def config(self):
        configs = get_websites_by_priority("P1")
        return next((c for c in configs if c.name == "微博"), None)

    def test_navigate_homepage(self, runner, config):
        scenario = {"id": "WB-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_hot(self, runner, config):
        scenario = {"id": "WB-02", "name": "热搜提取", "action": "extract_list", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_search(self, runner, config):
        scenario = {"id": "WB-03", "name": "搜索查询", "action": "search", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_weibo_list(self, runner, config):
        scenario = {"id": "WB-04", "name": "微博列表提取", "action": "extract_list", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_comments(self, runner, config):
        scenario = {"id": "WB-05", "name": "评论提取", "action": "extract_comments", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_check_login(self, runner, config):
        scenario = {"id": "WB-06", "name": "登录态检测", "action": "check_login", "dimension": "反检测能力"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, config):
        result = runner.run_website(config, mock_mode=True)
        assert result.website_name == "微博"
        assert len(result.scenarios) == 6
        assert result.overall_score > 0


class TestXiaohongshu:
    """小红书兼容性测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def config(self):
        configs = get_websites_by_priority("P1")
        return next((c for c in configs if c.name == "小红书"), None)

    def test_navigate_homepage(self, runner, config):
        scenario = {"id": "XHS-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_search(self, runner, config):
        scenario = {"id": "XHS-02", "name": "搜索笔记", "action": "search", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_list(self, runner, config):
        scenario = {"id": "XHS-03", "name": "列表提取", "action": "extract_list", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_click_detail(self, runner, config):
        scenario = {"id": "XHS-04", "name": "详情页访问", "action": "click_detail", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_note(self, runner, config):
        scenario = {"id": "XHS-05", "name": "笔记内容提取", "action": "extract_note", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_check_login(self, runner, config):
        scenario = {"id": "XHS-06", "name": "登录态检测", "action": "check_login", "dimension": "反检测能力"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, config):
        result = runner.run_website(config, mock_mode=True)
        assert result.website_name == "小红书"
        assert len(result.scenarios) == 6
        assert result.overall_score > 0


class TestSocialIntegration:
    """社交内容集成测试"""

    def test_all_social_sites(self):
        configs = get_websites_by_priority("P0")
        social_sites = [c for c in configs if c.category == "社交媒体"]
        assert len(social_sites) >= 1

        runner = EvaluationRunner(config={"delay_between_sites": 0})
        for config in social_sites:
            result = runner.run_website(config, mock_mode=True)
            assert result.overall_score > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
