"""
复杂表单交互兼容性测试

测试覆盖：
- 12306、好大夫在线、汽车之家
- 多字段表单、日期选择、下拉筛选、提交验证
"""
import pytest
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from tests.evaluation.test_runner import EvaluationRunner
from scripts.eval_config import get_websites_by_priority


class Test12306Form:
    """12306 表单交互测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def config(self):
        configs = get_websites_by_priority("P0")
        return next((c for c in configs if c.name == "12306铁路购票"), None)

    def test_navigate_homepage(self, runner, config):
        scenario = {"id": "TRAIN-FORM-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_fill_departure(self, runner, config):
        scenario = {"id": "TRAIN-FORM-02", "name": "填写出发地", "action": "fill_form", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_fill_destination(self, runner, config):
        scenario = {"id": "TRAIN-FORM-03", "name": "填写目的地", "action": "fill_form", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_select_date(self, runner, config):
        scenario = {"id": "TRAIN-FORM-04", "name": "选择日期", "action": "choose_date", "dimension": "交互成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_submit_search(self, runner, config):
        scenario = {"id": "TRAIN-FORM-05", "name": "提交查询", "action": "submit_form", "dimension": "交互成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_results(self, runner, config):
        scenario = {"id": "TRAIN-FORM-06", "name": "结果提取", "action": "extract_list", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, config):
        result = runner.run_website(config, mock_mode=True)
        assert result.website_name == "12306铁路购票"
        assert result.overall_score > 0


class TestHaodfForm:
    """好大夫在线表单交互测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def config(self):
        configs = get_websites_by_priority("P0")
        return next((c for c in configs if c.name == "好大夫在线"), None)

    def test_navigate_homepage(self, runner, config):
        scenario = {"id": "HAODF-FORM-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_search_doctor(self, runner, config):
        scenario = {"id": "HAODF-FORM-02", "name": "医生搜索", "action": "search", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_select_department(self, runner, config):
        scenario = {"id": "HAODF-FORM-03", "name": "选择科室", "action": "select_dropdown", "dimension": "交互成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_filter_by_level(self, runner, config):
        scenario = {"id": "HAODF-FORM-04", "name": "筛选职称", "action": "apply_filter", "dimension": "交互成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_doctor_list(self, runner, config):
        scenario = {"id": "HAODF-FORM-05", "name": "医生列表提取", "action": "extract_list", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, config):
        result = runner.run_website(config, mock_mode=True)
        assert result.website_name == "好大夫在线"
        assert result.overall_score > 0


class TestAutohomeForm:
    """汽车之家表单交互测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def config(self):
        configs = get_websites_by_priority("P0")
        return next((c for c in configs if c.name == "汽车之家"), None)

    def test_navigate_homepage(self, runner, config):
        scenario = {"id": "AUTO-FORM-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_search_car(self, runner, config):
        scenario = {"id": "AUTO-FORM-02", "name": "车型搜索", "action": "search", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_select_brand(self, runner, config):
        scenario = {"id": "AUTO-FORM-03", "name": "选择品牌", "action": "select_dropdown", "dimension": "交互成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_filter_price_range(self, runner, config):
        scenario = {"id": "AUTO-FORM-04", "name": "价格筛选", "action": "apply_filter", "dimension": "交互成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_car_list(self, runner, config):
        scenario = {"id": "AUTO-FORM-05", "name": "车型列表提取", "action": "extract_list", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, config):
        result = runner.run_website(config, mock_mode=True)
        assert result.website_name == "汽车之家"
        assert result.overall_score > 0


class TestFormIntegration:
    """表单交互集成测试"""

    def test_all_form_sites(self):
        configs = get_websites_by_priority("P0")
        form_sites = [c for c in configs if c.category in ["交通出行", "医疗健康", "汽车消费"]]
        assert len(form_sites) >= 3

        runner = EvaluationRunner(config={"delay_between_sites": 0})
        for config in form_sites:
            result = runner.run_website(config, mock_mode=True)
            assert result.overall_score > 0
            assert result.scenario_success_rate >= 70


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
