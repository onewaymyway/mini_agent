"""
政务服务网站评估测试

测试覆盖：
- 首页访问与加载
- 政务服务搜索
- 事项列表提取
- 详情页访问
- 办事指南提取
- PDF 下载
- 筛选条件测试
"""
import pytest
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from tests.evaluation.test_runner import EvaluationRunner
from scripts.eval_config import get_websites_by_priority


class TestGovServicePlatform:
    """国家政务服务平台评估测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def gov_config(self):
        configs = get_websites_by_priority("P0")
        return next((c for c in configs if c.name == "国家政务服务平台"), None)

    def test_navigate_homepage(self, runner, gov_config):
        scenario = {"id": "GOV-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, gov_config, mock_mode=True)
        assert result.success is True

    def test_search_service(self, runner, gov_config):
        scenario = {"id": "GOV-02", "name": "政务服务搜索", "action": "search", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, gov_config, mock_mode=True)
        assert result.success is True

    def test_extract_list(self, runner, gov_config):
        scenario = {"id": "GOV-03", "name": "事项列表提取", "action": "extract_list", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, gov_config, mock_mode=True)
        assert result.success is True

    def test_click_detail(self, runner, gov_config):
        scenario = {"id": "GOV-04", "name": "详情页访问", "action": "click_detail", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, gov_config, mock_mode=True)
        assert result.success is True

    def test_extract_article(self, runner, gov_config):
        scenario = {"id": "GOV-05", "name": "办事指南提取", "action": "extract_article", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, gov_config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, gov_config):
        result = runner.run_website(gov_config, mock_mode=True)
        assert result.website_name == "国家政务服务平台"
        assert len(result.scenarios) == 5
        assert result.overall_score > 0


class TestGovCN:
    """中国政府网评估测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def gc_config(self):
        configs = get_websites_by_priority("P0")
        return next((c for c in configs if c.name == "中国政府网"), None)

    def test_navigate_homepage(self, runner, gc_config):
        scenario = {"id": "GC-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, gc_config, mock_mode=True)
        assert result.success is True

    def test_search_policy(self, runner, gc_config):
        scenario = {"id": "GC-02", "name": "政策搜索", "action": "search", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, gc_config, mock_mode=True)
        assert result.success is True

    def test_extract_list(self, runner, gc_config):
        scenario = {"id": "GC-03", "name": "政策列表提取", "action": "extract_list", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, gc_config, mock_mode=True)
        assert result.success is True

    def test_click_detail(self, runner, gc_config):
        scenario = {"id": "GC-04", "name": "政策详情访问", "action": "click_detail", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, gc_config, mock_mode=True)
        assert result.success is True

    def test_extract_article(self, runner, gc_config):
        scenario = {"id": "GC-05", "name": "政策正文提取", "action": "extract_article", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, gc_config, mock_mode=True)
        assert result.success is True

    def test_download_pdf(self, runner, gc_config):
        scenario = {"id": "GC-06", "name": "PDF下载", "action": "download_pdf", "dimension": "交互成功率"}
        result = runner._run_scenario(scenario, gc_config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, gc_config):
        result = runner.run_website(gc_config, mock_mode=True)
        assert result.website_name == "中国政府网"
        assert len(result.scenarios) == 6
        assert result.overall_score > 0


class TestCourtDocument:
    """中国裁判文书网评估测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def cw_config(self):
        configs = get_websites_by_priority("P1")
        return next((c for c in configs if c.name == "中国裁判文书网"), None)

    def test_navigate_homepage(self, runner, cw_config):
        scenario = {"id": "CW-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, cw_config, mock_mode=True)
        assert result.success is True

    def test_search_case(self, runner, cw_config):
        scenario = {"id": "CW-02", "name": "案例搜索", "action": "search", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, cw_config, mock_mode=True)
        assert result.success is True

    def test_extract_list(self, runner, cw_config):
        scenario = {"id": "CW-03", "name": "案例列表提取", "action": "extract_list", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, cw_config, mock_mode=True)
        assert result.success is True

    def test_click_detail(self, runner, cw_config):
        scenario = {"id": "CW-04", "name": "案例详情访问", "action": "click_detail", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, cw_config, mock_mode=True)
        assert result.success is True

    def test_extract_article(self, runner, cw_config):
        scenario = {"id": "CW-05", "name": "文书内容提取", "action": "extract_article", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, cw_config, mock_mode=True)
        assert result.success is True

    def test_filter_conditions(self, runner, cw_config):
        scenario = {"id": "CW-06", "name": "筛选条件测试", "action": "paginate", "dimension": "交互成功率"}
        result = runner._run_scenario(scenario, cw_config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, cw_config):
        result = runner.run_website(cw_config, mock_mode=True)
        assert result.website_name == "中国裁判文书网"
        assert len(result.scenarios) == 6
        assert result.overall_score > 0


class TestGovIntegration:
    """政务服务集成测试"""

    def test_all_gov_sites(self):
        configs = get_websites_by_priority("P0")
        gov_sites = [c for c in configs if c.category == "政务服务"]
        assert len(gov_sites) >= 2

        runner = EvaluationRunner(config={"delay_between_sites": 0})
        for config in gov_sites:
            result = runner.run_website(config, mock_mode=True)
            assert result.overall_score > 0
            assert result.scenario_success_rate >= 80


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
