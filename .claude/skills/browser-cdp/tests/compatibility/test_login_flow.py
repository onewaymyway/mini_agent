"""
登录流程兼容性测试

测试覆盖：
- 登录页面访问、表单填写、登录提交
- 登录失败处理、记住登录态、第三方登录、验证码处理

使用知乎作为测试目标（需要登录的社交媒体网站）
"""
import pytest
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from tests.evaluation.test_runner import EvaluationRunner
from scripts.eval_config import get_websites_by_priority


class TestLoginFlow:
    """登录流程兼容性测试 - 以知乎为例"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def config(self):
        configs = get_websites_by_priority("P0")
        return next((c for c in configs if c.name == "知乎"), None)

    def test_01_navigate_to_login(self, runner, config):
        """LOGIN-01: 导航到登录页面"""
        scenario = {
            "id": "LOGIN-01",
            "name": "导航到登录页面",
            "action": "navigate",
            "dimension": "页面访问成功率"
        }
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_02_fill_username(self, runner, config):
        """LOGIN-02: 填写用户名"""
        scenario = {
            "id": "LOGIN-02",
            "name": "填写用户名",
            "action": "input",
            "dimension": "元素定位准确率"
        }
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_03_fill_password(self, runner, config):
        """LOGIN-03: 填写密码"""
        scenario = {
            "id": "LOGIN-03",
            "name": "填写密码",
            "action": "input",
            "dimension": "元素定位准确率"
        }
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_04_submit_login(self, runner, config):
        """LOGIN-04: 提交登录表单"""
        scenario = {
            "id": "LOGIN-04",
            "name": "提交登录表单",
            "action": "click",
            "dimension": "交互成功率"
        }
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_05_handle_login_error(self, runner, config):
        """LOGIN-05: 处理登录失败"""
        scenario = {
            "id": "LOGIN-05",
            "name": "处理登录失败",
            "action": "check_error",
            "dimension": "错误处理"
        }
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_06_check_remember_me(self, runner, config):
        """LOGIN-06: 记住登录态"""
        scenario = {
            "id": "LOGIN-06",
            "name": "记住登录态",
            "action": "check_remember_me",
            "dimension": "会话管理"
        }
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_07_third_party_login(self, runner, config):
        """LOGIN-07: 第三方登录"""
        scenario = {
            "id": "LOGIN-07",
            "name": "第三方登录",
            "action": "third_party_login",
            "dimension": "交互成功率"
        }
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_08_captcha_input(self, runner, config):
        """LOGIN-08: 验证码输入"""
        scenario = {
            "id": "LOGIN-08",
            "name": "验证码输入",
            "action": "captcha_input",
            "dimension": "反检测能力"
        }
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, config):
        """完整登录流程评估"""
        result = runner.run_website(config, mock_mode=True)
        assert result.website_name == "知乎"
        assert len(result.scenarios) > 0
        assert result.overall_score > 0


class TestLoginIntegration:
    """登录流程集成测试"""

    def test_all_login_sites(self):
        """测试所有需要登录的网站（社交媒体类）"""
        configs = get_websites_by_priority("P0")
        login_sites = [c for c in configs if c.category == "社交媒体"]
        assert len(login_sites) >= 1

        runner = EvaluationRunner(config={"delay_between_sites": 0})
        for config in login_sites:
            result = runner.run_website(config, mock_mode=True)
            assert result.overall_score > 0
            assert result.scenario_success_rate >= 80


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
