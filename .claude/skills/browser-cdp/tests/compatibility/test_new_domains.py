"""
新增网站兼容性测试

测试覆盖：
- 豆瓣、抖音、快手（P0 新增）
- 携程、美团（P1 新增）
"""
import pytest
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from tests.evaluation.test_runner import EvaluationRunner
from scripts.eval_config import get_websites_by_priority, WEBSITE_CONFIGS


class TestDouban:
    """豆瓣兼容性测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def config(self):
        return next((c for c in WEBSITE_CONFIGS if c.name == "豆瓣"), None)

    def test_navigate_homepage(self, runner, config):
        scenario = {"id": "DB-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_search(self, runner, config):
        scenario = {"id": "DB-02", "name": "搜索查询", "action": "search", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_list(self, runner, config):
        scenario = {"id": "DB-03", "name": "列表提取", "action": "extract_list", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_click_detail(self, runner, config):
        scenario = {"id": "DB-04", "name": "详情页访问", "action": "click_detail", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_reviews(self, runner, config):
        scenario = {"id": "DB-05", "name": "评价提取", "action": "extract_reviews", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, config):
        result = runner.run_website(config, mock_mode=True)
        assert result.website_name == "豆瓣"
        assert result.overall_score > 0


class TestDouyin:
    """抖音兼容性测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def config(self):
        return next((c for c in WEBSITE_CONFIGS if c.name == "抖音"), None)

    def test_navigate_homepage(self, runner, config):
        scenario = {"id": "DY-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_search(self, runner, config):
        scenario = {"id": "DY-02", "name": "搜索查询", "action": "search", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_infinite_scroll(self, runner, config):
        scenario = {"id": "DY-03", "name": "无限滚动", "action": "infinite_scroll", "dimension": "稳定性"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_list(self, runner, config):
        scenario = {"id": "DY-04", "name": "列表提取", "action": "extract_list", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, config):
        result = runner.run_website(config, mock_mode=True)
        assert result.website_name == "抖音"
        assert result.overall_score > 0


class TestKuaishou:
    """快手兼容性测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def config(self):
        return next((c for c in WEBSITE_CONFIGS if c.name == "快手"), None)

    def test_navigate_homepage(self, runner, config):
        scenario = {"id": "KS-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_search(self, runner, config):
        scenario = {"id": "KS-02", "name": "搜索查询", "action": "search", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_infinite_scroll(self, runner, config):
        scenario = {"id": "KS-03", "name": "无限滚动", "action": "infinite_scroll", "dimension": "稳定性"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_list(self, runner, config):
        scenario = {"id": "KS-04", "name": "列表提取", "action": "extract_list", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, config):
        result = runner.run_website(config, mock_mode=True)
        assert result.website_name == "快手"
        assert result.overall_score > 0


class TestCtrip:
    """携程兼容性测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def config(self):
        return next((c for c in WEBSITE_CONFIGS if c.name == "携程"), None)

    def test_navigate_homepage(self, runner, config):
        scenario = {"id": "CT-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_search_flight(self, runner, config):
        scenario = {"id": "CT-02", "name": "机票搜索", "action": "search_flight", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_fill_form(self, runner, config):
        scenario = {"id": "CT-03", "name": "填写表单", "action": "fill_form", "dimension": "交互成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_results(self, runner, config):
        scenario = {"id": "CT-04", "name": "结果提取", "action": "extract_list", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, config):
        result = runner.run_website(config, mock_mode=True)
        assert result.website_name == "携程"
        assert result.overall_score > 0


class TestMeituan:
    """美团兼容性测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def config(self):
        return next((c for c in WEBSITE_CONFIGS if c.name == "美团"), None)

    def test_navigate_homepage(self, runner, config):
        scenario = {"id": "MT-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_search(self, runner, config):
        scenario = {"id": "MT-02", "name": "商户搜索", "action": "search", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_apply_filter(self, runner, config):
        scenario = {"id": "MT-03", "name": "应用筛选", "action": "apply_filter", "dimension": "交互成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_list(self, runner, config):
        scenario = {"id": "MT-04", "name": "列表提取", "action": "extract_list", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, config):
        result = runner.run_website(config, mock_mode=True)
        assert result.website_name == "美团"
        assert result.overall_score > 0


class TestNewDomainsIntegration:
    """新增网站集成测试"""

    def test_all_new_domains(self):
        new_domains = ["豆瓣", "抖音", "快手", "携程", "美团"]
        runner = EvaluationRunner(config={"delay_between_sites": 0})
        
        for name in new_domains:
            config = next((c for c in WEBSITE_CONFIGS if c.name == name), None)
            if config:
                result = runner.run_website(config, mock_mode=True)
                assert result.overall_score > 0, f"{name} 评估失败"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
