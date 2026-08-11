"""
验证码处理兼容性测试

测试覆盖：
- 验证码识别、滑块验证、点选验证
- 验证码刷新、超时处理、绕过检测
"""
import pytest
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from tests.evaluation.test_runner import EvaluationRunner
from scripts.eval_config import get_websites_by_priority


class TestCaptcha:
    """验证码处理兼容性测试 - 以12306为例（强反爬）"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def config(self):
        configs = get_websites_by_priority("P0")
        return next((c for c in configs if c.name == "12306铁路购票"), None)

    def test_01_detect_captcha(self, runner, config):
        """CAPTCHA-01: 检测验证码"""
        scenario = {"id": "CAPTCHA-01", "name": "检测验证码", "action": "detect_captcha", "dimension": "反检测能力"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_02_slide_captcha(self, runner, config):
        """CAPTCHA-02: 滑块验证"""
        scenario = {"id": "CAPTCHA-02", "name": "滑块验证", "action": "slide_captcha", "dimension": "交互成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_03_click_captcha(self, runner, config):
        """CAPTCHA-03: 点选验证"""
        scenario = {"id": "CAPTCHA-03", "name": "点选验证", "action": "click_captcha", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_04_refresh_captcha(self, runner, config):
        """CAPTCHA-04: 刷新验证码"""
        scenario = {"id": "CAPTCHA-04", "name": "刷新验证码", "action": "refresh_captcha", "dimension": "稳定性"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_05_captcha_timeout(self, runner, config):
        """CAPTCHA-05: 验证码超时处理"""
        scenario = {"id": "CAPTCHA-05", "name": "超时处理", "action": "handle_timeout", "dimension": "错误处理"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_06_bypass_detection(self, runner, config):
        """CAPTCHA-06: 绕过检测"""
        scenario = {"id": "CAPTCHA-06", "name": "绕过检测", "action": "bypass_detection", "dimension": "反检测能力"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_full_evaluation(self, runner, config):
        """完整验证码评估"""
        result = runner.run_website(config, mock_mode=True)
        assert result.website_name == "12306铁路购票"
        assert len(result.scenarios) > 0
        assert result.overall_score > 0


class TestCaptchaIntegration:
    """验证码处理集成测试"""

    def test_all_strong_anti_crawl_sites(self):
        """测试所有强反爬网站"""
        configs = get_websites_by_priority("P0")
        # 使用 difficulty 字段判断反爬难度（L3及以上）
        strong_sites = [c for c in configs if c.difficulty in ['L3', 'L4', 'L5'] or 'L3' in c.difficulty or 'L4' in c.difficulty or 'L5' in c.difficulty]
        assert len(strong_sites) >= 1

        runner = EvaluationRunner(config={"delay_between_sites": 0})
        for config in strong_sites:
            result = runner.run_website(config, mock_mode=True)
            assert result.overall_score > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
