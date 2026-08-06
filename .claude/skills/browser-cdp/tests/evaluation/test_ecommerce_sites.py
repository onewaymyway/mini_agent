"""
电商平台评估测试

测试覆盖：
- 首页访问与加载
- 商品搜索
- 列表提取
- 详情页访问
- 价格提取
- 分页浏览
- 拼团信息提取
"""
import pytest
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from tests.evaluation.test_runner import EvaluationRunner
from scripts.eval_config import get_websites_by_priority


class TestTaobao:
    """淘宝评估测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def taobao_config(self):
        configs = get_websites_by_priority("P1")
        return next((c for c in configs if c.name == "淘宝"), None)

    def test_navigate_homepage(self, runner, taobao_config):
        scenario = {"id": "TB-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, taobao_config, mock_mode=True)
        assert result.success is True

    def test_search_products(self, runner, taobao_config):
        scenario = {"id": "TB-02", "name": "搜索商品", "action": "search", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, taobao_config, mock_mode=True)
        assert result.success is True

    def test_extract_list(self, runner, taobao_config):
        scenario = {"id": "TB-03", "name": "列表提取", "action": "extract_list", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, taobao_config, mock_mode=True)
        assert result.success is True

    def test_click_detail(self, runner, taobao_config):
        scenario = {"id": "TB-04", "name": "详情页访问", "action": "click_detail", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, taobao_config, mock_mode=True)
        assert result.success is True

    def test_extract_price(self, runner, taobao_config):
        scenario = {"id": "TB-05", "name": "价格提取", "action": "extract_price", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, taobao_config, mock_mode=True)
        assert result.success is True

    def test_pagination(self, runner, taobao_config):
        scenario = {"id": "TB-06", "name": "分页浏览", "action": "paginate", "dimension": "稳定性"}
        result = runner._run_scenario(scenario, taobao_config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, taobao_config):
        result = runner.run_website(taobao_config, mock_mode=True)
        assert result.website_name == "淘宝"
        assert len(result.scenarios) == 6
        assert result.overall_score > 0


class TestJD:
    """京东评估测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def jd_config(self):
        configs = get_websites_by_priority("P1")
        return next((c for c in configs if c.name == "京东"), None)

    def test_navigate_homepage(self, runner, jd_config):
        scenario = {"id": "JD-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, jd_config, mock_mode=True)
        assert result.success is True

    def test_search_products(self, runner, jd_config):
        scenario = {"id": "JD-02", "name": "搜索商品", "action": "search", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, jd_config, mock_mode=True)
        assert result.success is True

    def test_extract_list(self, runner, jd_config):
        scenario = {"id": "JD-03", "name": "列表提取", "action": "extract_list", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, jd_config, mock_mode=True)
        assert result.success is True

    def test_click_detail(self, runner, jd_config):
        scenario = {"id": "JD-04", "name": "详情页访问", "action": "click_detail", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, jd_config, mock_mode=True)
        assert result.success is True

    def test_extract_price(self, runner, jd_config):
        scenario = {"id": "JD-05", "name": "价格提取", "action": "extract_price", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, jd_config, mock_mode=True)
        assert result.success is True

    def test_extract_specs(self, runner, jd_config):
        scenario = {"id": "JD-06", "name": "规格提取", "action": "extract_specs", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, jd_config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, jd_config):
        result = runner.run_website(jd_config, mock_mode=True)
        assert result.website_name == "京东"
        assert len(result.scenarios) == 6
        assert result.overall_score > 0


class TestPDD:
    """拼多多评估测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def pdd_config(self):
        configs = get_websites_by_priority("P1")
        return next((c for c in configs if c.name == "拼多多"), None)

    def test_navigate_homepage(self, runner, pdd_config):
        scenario = {"id": "PDD-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, pdd_config, mock_mode=True)
        assert result.success is True

    def test_search_products(self, runner, pdd_config):
        scenario = {"id": "PDD-02", "name": "商品搜索", "action": "search", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, pdd_config, mock_mode=True)
        assert result.success is True

    def test_extract_list(self, runner, pdd_config):
        scenario = {"id": "PDD-03", "name": "列表提取", "action": "extract_list", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, pdd_config, mock_mode=True)
        assert result.success is True

    def test_click_detail(self, runner, pdd_config):
        scenario = {"id": "PDD-04", "name": "详情页访问", "action": "click_detail", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, pdd_config, mock_mode=True)
        assert result.success is True

    def test_extract_group_price(self, runner, pdd_config):
        scenario = {"id": "PDD-05", "name": "拼团价格提取", "action": "extract_group_price", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, pdd_config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, pdd_config):
        result = runner.run_website(pdd_config, mock_mode=True)
        assert result.website_name == "拼多多"
        assert len(result.scenarios) == 5
        assert result.overall_score > 0


class TestEcommerceIntegration:
    """电商平台集成测试"""

    def test_all_ecommerce_sites(self):
        configs = get_websites_by_priority("P1")
        ecommerce_sites = [c for c in configs if c.category == "电商平台"]
        assert len(ecommerce_sites) >= 3

        runner = EvaluationRunner(config={"delay_between_sites": 0})
        for config in ecommerce_sites:
            result = runner.run_website(config, mock_mode=True)
            assert result.overall_score > 0
            assert result.scenario_success_rate >= 70


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
