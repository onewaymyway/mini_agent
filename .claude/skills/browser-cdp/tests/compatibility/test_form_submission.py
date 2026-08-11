"""
表单提交兼容性测试

测试覆盖：
- 表单填写、验证、提交、错误处理
- 文件上传、多选、动态表单
"""
import pytest
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from tests.evaluation.test_runner import EvaluationRunner
from scripts.eval_config import get_websites_by_priority


class TestFormSubmission:
    """表单提交兼容性测试 - 以好大夫在线为例"""

    @pytest.fixture
    def runner(self):
        return EvaluationRunner(config={"delay_between_sites": 0})

    @pytest.fixture
    def config(self):
        configs = get_websites_by_priority("P0")
        return next((c for c in configs if c.name == "好大夫在线"), None)

    def test_01_fill_text_input(self, runner, config):
        """FORM-01: 文本输入"""
        scenario = {"id": "FORM-01", "name": "文本输入", "action": "input", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_02_fill_select(self, runner, config):
        """FORM-02: 下拉选择"""
        scenario = {"id": "FORM-02", "name": "下拉选择", "action": "select", "dimension": "交互成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_03_check_radio(self, runner, config):
        """FORM-03: 单选框"""
        scenario = {"id": "FORM-03", "name": "单选框", "action": "check_radio", "dimension": "交互成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_04_check_checkbox(self, runner, config):
        """FORM-04: 复选框"""
        scenario = {"id": "FORM-04", "name": "复选框", "action": "check_checkbox", "dimension": "交互成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_05_upload_file(self, runner, config):
        """FORM-05: 文件上传"""
        scenario = {"id": "FORM-05", "name": "文件上传", "action": "upload_file", "dimension": "交互成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_06_dynamic_form(self, runner, config):
        """FORM-06: 动态表单"""
        scenario = {"id": "FORM-06", "name": "动态表单", "action": "dynamic_form", "dimension": "稳定性"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_07_validate_form(self, runner, config):
        """FORM-07: 表单验证"""
        scenario = {"id": "FORM-07", "name": "表单验证", "action": "validate", "dimension": "错误处理"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_08_submit_form(self, runner, config):
        """FORM-08: 提交表单"""
        scenario = {"id": "FORM-08", "name": "提交表单", "action": "submit", "dimension": "交互成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_09_handle_error(self, runner, config):
        """FORM-09: 错误处理"""
        scenario = {"id": "FORM-09", "name": "错误处理", "action": "handle_error", "dimension": "错误处理"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_10_auto_fill(self, runner, config):
        """FORM-10: 自动填充"""
        scenario = {"id": "FORM-10", "name": "自动填充", "action": "auto_fill", "dimension": "元素定位准确率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_11_multi_step_form(self, runner, config):
        """FORM-11: 多步表单"""
        scenario = {"id": "FORM-11", "name": "多步表单", "action": "multi_step", "dimension": "稳定性"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_12_date_picker(self, runner, config):
        """FORM-12: 日期选择器"""
        scenario = {"id": "FORM-12", "name": "日期选择", "action": "date_picker", "dimension": "交互成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_13_rich_text(self, runner, config):
        """FORM-13: 富文本编辑"""
        scenario = {"id": "FORM-13", "name": "富文本编辑", "action": "rich_text", "dimension": "交互成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_14_cascader(self, runner, config):
        """FORM-14: 级联选择"""
        scenario = {"id": "FORM-14", "name": "级联选择", "action": "cascader", "dimension": "交互成功率"}
        result = runner._run_scenario(scenario, config, mock_mode=True)
        assert result.success is True

    def test_15_full_evaluation(self, runner, config):
        """完整表单评估"""
        result = runner.run_website(config, mock_mode=True)
        assert result.website_name == "好大夫在线"
        assert len(result.scenarios) > 0
        assert result.overall_score > 0


class TestFormIntegration:
    """表单提交集成测试"""

    def test_all_form_sites(self):
        """测试所有需要表单的网站"""
        configs = get_websites_by_priority("P0")
        form_sites = [c for c in configs if c.category in ["医疗健康", "政务服务", "交通出行"]]
        assert len(form_sites) >= 2

        runner = EvaluationRunner(config={"delay_between_sites": 0})
        for config in form_sites:
            result = runner.run_website(config, mock_mode=True)
            assert result.overall_score > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
