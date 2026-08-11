"""
导航功能兼容性测试

测试覆盖：
- 首页访问、链接点击、返回、前进、刷新
- 标签页管理、书签、历史记录
"""
import pytest
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from tests.evaluation.test_runner import EvaluationRunner
from scripts.eval_config import get_websites_by_priority


class TestNavigation:
    """导航功能兼容性测试 - 以百度为例"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def config(self):
        configs = get_websites_by_priority("P0")
        return next((c for c in configs if c.name == "百度"), None)

    def test_01_navigate_homepage(self, runner, config):
        """NAV-01: 首页访问"""
        scenario = {"id": "NAV-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_02_click_link(self, runner, config):
        """NAV-02: 链接点击"""
        scenario = {"id": "NAV-02", "name": "链接点击", "action": "click", "dimension": "交互成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_03_back(self, runner, config):
        """NAV-03: 返回上一页"""
        scenario = {"id": "NAV-03", "name": "返回", "action": "back", "dimension": "导航成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_04_forward(self, runner, config):
        """NAV-04: 前进"""
        scenario = {"id": "NAV-04", "name": "前进", "action": "forward", "dimension": "导航成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_05_refresh(self, runner, config):
        """NAV-05: 刷新页面"""
        scenario = {"id": "NAV-05", "name": "刷新", "action": "refresh", "dimension": "稳定性"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_06_new_tab(self, runner, config):
        """NAV-06: 新标签页"""
        scenario = {"id": "NAV-06", "name": "新标签页", "action": "new_tab", "dimension": "标签管理"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_07_switch_tab(self, runner, config):
        """NAV-07: 切换标签页"""
        scenario = {"id": "NAV-07", "name": "切换标签", "action": "switch_tab", "dimension": "标签管理"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_08_close_tab(self, runner, config):
        """NAV-08: 关闭标签页"""
        scenario = {"id": "NAV-08", "name": "关闭标签", "action": "close_tab", "dimension": "标签管理"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_09_bookmark(self, runner, config):
        """NAV-09: 添加书签"""
        scenario = {"id": "NAV-09", "name": "添加书签", "action": "bookmark", "dimension": "书签管理"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_10_history(self, runner, config):
        """NAV-10: 历史记录"""
        scenario = {"id": "NAV-10", "name": "历史记录", "action": "history", "dimension": "历史记录"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_11_scroll(self, runner, config):
        """NAV-11: 页面滚动"""
        scenario = {"id": "NAV-11", "name": "页面滚动", "action": "scroll", "dimension": "滚动稳定性"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_12_full_evaluation(self, runner, config):
        """完整导航评估"""
        result = runner.run_website(config, mock_mode=True)
        assert result.website_name == "百度"
        assert len(result.scenarios) > 0
        assert result.overall_score > 0


class TestNavigationIntegration:
    """导航功能集成测试"""

    def test_all_navigation_sites(self):
        """测试所有搜索引擎的导航功能"""
        configs = get_websites_by_priority("P0")
        nav_sites = [c for c in configs if c.category == "搜索引擎"]
        assert len(nav_sites) >= 2

        runner = EvaluationRunner(config={"delay_between_sites": 0})
        for config in nav_sites:
            result = runner.run_website(config, mock_mode=True)
            assert result.overall_score > 0
            assert result.scenario_success_rate >= 80


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
