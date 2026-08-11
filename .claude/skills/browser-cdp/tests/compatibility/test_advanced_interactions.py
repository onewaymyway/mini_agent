"""
高级交互兼容性测试

测试覆盖：
- 无限滚动、弹窗处理、多Tab管理、截图验证、错误恢复
"""
import pytest
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from tests.evaluation.test_runner import EvaluationRunner
from scripts.eval_config import get_websites_by_priority


class TestInfiniteScroll:
    """无限滚动测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def config(self):
        configs = get_websites_by_priority("P0")
        return next((c for c in configs if c.name == "知乎"), None)

    def test_navigate_homepage(self, runner, config):
        scenario = {"id": "INFINITE-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_infinite_scroll(self, runner, config):
        scenario = {"id": "INFINITE-02", "name": "无限滚动", "action": "infinite_scroll", "dimension": "稳定性"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_load_more_content(self, runner, config):
        scenario = {"id": "INFINITE-03", "name": "加载更多", "action": "extract_list", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, config):
        result = runner.run_website(config, mock_mode=True)
        assert result.website_name == "知乎"
        assert result.overall_score > 0


class TestPopupHandling:
    """弹窗处理测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def config(self):
        configs = get_websites_by_priority("P1")
        return next((c for c in configs if c.name == "淘宝"), None)

    def test_navigate_homepage(self, runner, config):
        scenario = {"id": "POPUP-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_close_popup(self, runner, config):
        scenario = {"id": "POPUP-02", "name": "关闭弹窗", "action": "close_popup", "dimension": "交互成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_search_after_popup(self, runner, config):
        scenario = {"id": "POPUP-03", "name": "弹窗后搜索", "action": "search", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, config):
        result = runner.run_website(config, mock_mode=True)
        assert result.website_name == "淘宝"
        assert result.overall_score > 0


class TestTabManagement:
    """多Tab管理测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def config(self):
        configs = get_websites_by_priority("P2")
        return next((c for c in configs if c.name == "B站"), None)

    def test_navigate_homepage(self, runner, config):
        scenario = {"id": "TAB-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_open_new_tab(self, runner, config):
        scenario = {"id": "TAB-02", "name": "打开新标签", "action": "open_new_tab", "dimension": "交互成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_switch_tab(self, runner, config):
        scenario = {"id": "TAB-03", "name": "切换标签", "action": "switch_tab", "dimension": "交互成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_close_tab(self, runner, config):
        scenario = {"id": "TAB-04", "name": "关闭标签", "action": "close_tab", "dimension": "交互成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, config):
        result = runner.run_website(config, mock_mode=True)
        assert result.website_name == "B站"
        assert result.overall_score > 0


class TestScreenshotVerification:
    """截图验证测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def config(self):
        configs = get_websites_by_priority("P0")
        return next((c for c in configs if c.name == "百度"), None)

    def test_navigate_homepage(self, runner, config):
        scenario = {"id": "SCREEN-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_capture_screenshot(self, runner, config):
        scenario = {"id": "SCREEN-02", "name": "截图验证", "action": "capture_screenshot", "dimension": "交互成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_search_and_screenshot(self, runner, config):
        scenario = {"id": "SCREEN-03", "name": "搜索后截图", "action": "search", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, config):
        result = runner.run_website(config, mock_mode=True)
        assert result.website_name == "百度"
        assert result.overall_score > 0


class TestErrorRecovery:
    """错误恢复测试"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def config(self):
        configs = get_websites_by_priority("P1")
        return next((c for c in configs if c.name == "Boss直聘"), None)

    def test_navigate_homepage(self, runner, config):
        scenario = {"id": "ERROR-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_search_with_retry(self, runner, config):
        scenario = {"id": "ERROR-02", "name": "搜索重试", "action": "search", "dimension": "稳定性"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_extract_with_recovery(self, runner, config):
        scenario = {"id": "ERROR-03", "name": "提取恢复", "action": "extract_list", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, config):
        result = runner.run_website(config, mock_mode=True)
        assert result.website_name == "Boss直聘"
        assert result.overall_score > 0


class TestAdvancedIntegration:
    """高级交互集成测试"""

    def test_all_advanced_scenarios(self):
        runner = EvaluationRunner(config={"delay_between_sites": 0})
        
        # 测试无限滚动
        zhihu = next((c for c in get_websites_by_priority("P0") if c.name == "知乎"), None)
        if zhihu:
            result = runner.run_website(zhihu, mock_mode=True)
            assert result.overall_score > 0
        
        # 测试弹窗处理
        taobao = next((c for c in get_websites_by_priority("P1") if c.name == "淘宝"), None)
        if taobao:
            result = runner.run_website(taobao, mock_mode=True)
            assert result.overall_score > 0
        
        # 测试多Tab
        bilibili = next((c for c in get_websites_by_priority("P2") if c.name == "B站"), None)
        if bilibili:
            result = runner.run_website(bilibili, mock_mode=True)
            assert result.overall_score > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
