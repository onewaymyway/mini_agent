"""
招聘平台兼容性测试

测试覆盖：
- Boss直聘、拉勾
- 首页访问、职位搜索、列表提取、详情页、公司信息
"""
import pytest
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from tests.evaluation.test_runner import EvaluationRunner
from scripts.eval_config import get_websites_by_priority


class TestBossZhipin:
    """Boss直聘兼容性测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def config(self):
        configs = get_websites_by_priority("P1")
        return next((c for c in configs if c.name == "Boss直聘"), None)

    def test_navigate_homepage(self, runner, config):
        scenario = {"id": "ZP-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_search(self, runner, config):
        scenario = {"id": "ZP-02", "name": "职位搜索", "action": "search", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_list(self, runner, config):
        scenario = {"id": "ZP-03", "name": "列表提取", "action": "extract_list", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_click_detail(self, runner, config):
        scenario = {"id": "ZP-04", "name": "详情页访问", "action": "click_detail", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_job(self, runner, config):
        scenario = {"id": "ZP-05", "name": "职位信息提取", "action": "extract_job", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_check_anti_crawl(self, runner, config):
        scenario = {"id": "ZP-06", "name": "反爬检测", "action": "check_anti_crawl", "dimension": "反检测能力"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, config):
        result = runner.run_website(config, mock_mode=True)
        assert result.website_name == "Boss直聘"
        assert len(result.scenarios) == 6
        assert result.overall_score > 0


class TestLagou:
    """拉勾兼容性测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def config(self):
        configs = get_websites_by_priority("P1")
        return next((c for c in configs if c.name == "拉勾"), None)

    def test_navigate_homepage(self, runner, config):
        scenario = {"id": "LG-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_search(self, runner, config):
        scenario = {"id": "LG-02", "name": "职位搜索", "action": "search", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_list(self, runner, config):
        scenario = {"id": "LG-03", "name": "列表提取", "action": "extract_list", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_click_detail(self, runner, config):
        scenario = {"id": "LG-04", "name": "详情页访问", "action": "click_detail", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_company(self, runner, config):
        scenario = {"id": "LG-05", "name": "公司信息提取", "action": "extract_company", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, config):
        result = runner.run_website(config, mock_mode=True)
        assert result.website_name == "拉勾"
        assert len(result.scenarios) == 5
        assert result.overall_score > 0


class TestJobIntegration:
    """招聘平台集成测试"""

    def test_all_job_sites(self):
        configs = get_websites_by_priority("P1")
        job_sites = [c for c in configs if c.category == "招聘平台"]
        assert len(job_sites) >= 2

        runner = EvaluationRunner(config={"delay_between_sites": 0})
        for config in job_sites:
            result = runner.run_website(config, mock_mode=True)
            assert result.overall_score > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
