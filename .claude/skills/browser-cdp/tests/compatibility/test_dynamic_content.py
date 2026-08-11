"""
动态内容加载兼容性测试

测试覆盖：
- AJAX加载、无限滚动、SPA路由
- 懒加载、动态渲染、WebSocket
"""
import pytest
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from tests.evaluation.test_runner import EvaluationRunner
from scripts.eval_config import get_websites_by_priority


class TestDynamicContent:
    """动态内容加载兼容性测试 - 以知乎为例（SPA）"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def config(self):
        configs = get_websites_by_priority("P0")
        return next((c for c in configs if c.name == "知乎"), None)

    def test_01_ajax_load(self, runner, config):
        """DYNAMIC-01: AJAX加载"""
        scenario = {"id": "DYNAMIC-01", "name": "AJAX加载", "action": "ajax_load", "dimension": "稳定性"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_02_infinite_scroll(self, runner, config):
        """DYNAMIC-02: 无限滚动"""
        scenario = {"id": "DYNAMIC-02", "name": "无限滚动", "action": "infinite_scroll", "dimension": "稳定性"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_03_lazy_load(self, runner, config):
        """DYNAMIC-03: 懒加载"""
        scenario = {"id": "DYNAMIC-03", "name": "懒加载", "action": "lazy_load", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_04_spa_route(self, runner, config):
        """DYNAMIC-04: SPA路由"""
        scenario = {"id": "DYNAMIC-04", "name": "SPA路由", "action": "spa_route", "dimension": "稳定性"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_05_dynamic_render(self, runner, config):
        """DYNAMIC-05: 动态渲染"""
        scenario = {"id": "DYNAMIC-05", "name": "动态渲染", "action": "dynamic_render", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_06_websocket(self, runner, config):
        """DYNAMIC-06: WebSocket"""
        scenario = {"id": "DYNAMIC-06", "name": "WebSocket", "action": "websocket", "dimension": "实时性"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_07_dom_update(self, runner, config):
        """DYNAMIC-07: DOM更新"""
        scenario = {"id": "DYNAMIC-07", "name": "DOM更新", "action": "dom_update", "dimension": "稳定性"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_08_async_data(self, runner, config):
        """DYNAMIC-08: 异步数据"""
        scenario = {"id": "DYNAMIC-08", "name": "异步数据", "action": "async_data", "dimension": "抓取成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_09_shadow_dom(self, runner, config):
        """DYNAMIC-09: Shadow DOM"""
        scenario = {"id": "DYNAMIC-09", "name": "Shadow DOM", "action": "shadow_dom", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_10_full_evaluation(self, runner, config):
        """完整动态内容评估"""
        result = runner.run_website(config, mock_mode=True)
        assert result.website_name == "知乎"
        assert len(result.scenarios) > 0
        assert result.overall_score > 0


class TestDynamicContentIntegration:
    """动态内容加载集成测试"""

    def test_all_spa_sites(self):
        """测试所有SPA网站"""
        configs = get_websites_by_priority("P0")
        # 使用 tech_stack 字段判断是否为 SPA
        spa_sites = [c for c in configs if "SPA" in c.tech_stack or "动态" in c.tech_stack]
        assert len(spa_sites) >= 1

        runner = EvaluationRunner(config={"delay_between_sites": 0})
        for config in spa_sites:
            result = runner.run_website(config, mock_mode=True)
            assert result.overall_score > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
