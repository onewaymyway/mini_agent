"""
test_form_submission.py — 表单提交场景测试模板

测试覆盖场景：
- 复杂表单字段填写（文本、邮箱、日期、下拉选择）
- 表单验证与错误处理
- 文件上传功能
- 多步骤表单流程
- 表单数据提取与验证

依赖模块：browser_nav, browser_extract, browser_input, browser_screenshot, browser_console
"""
from __future__ import annotations

import unittest
from unittest.mock import patch, Mock
from pathlib import Path
import sys

# 添加 skill 目录到路径
SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

# 导入基础模板
from templates.base_test_template import BaseBrowserTest
import src.core.browser_launch as browser_launch
import src.core.browser_nav as browser_nav
import src.core.browser_extract as browser_extract
import src.core.browser_input as browser_input
import src.core.browser_screenshot as browser_screenshot
import src.core.browser_console as browser_console


class TestFormSubmission(BaseBrowserTest):
    """表单提交测试用例"""

    def setUp(self):
        super().setUp()
        self._setup_form_mocks()

    def _setup_form_mocks(self):
        """设置表单页面相关的 mock"""
        with patch.object(browser_launch, "spawn_browser") as mock_spawn:
            mock_proc = Mock()
            mock_proc.pid = 12345
            mock_spawn.return_value = mock_proc
            self.mock_tab["url"] = "https://example-form.com/contact"
            self.mock_tab["title"] = "Contact Form - Example Site"

    def test_01_load_form_page(self):
        """测试：加载表单页面"""
        with patch.object(browser_nav, "goto") as mock_goto:
            mock_goto.return_value = True
            result = browser_nav.goto("https://example-form.com/contact")
            self.assertTrue(result)
            self.assertTabUrlContains("test-tab-1", "contact")
            self.assertTabTitleContains("test-tab-1", "Contact Form")

    def test_02_fill_text_fields(self):
        """测试：填写文本输入框"""
        # 模拟填写多个文本字段
        with patch.object(browser_input, "type_selector") as mock_type:
            mock_type.return_value = None
            
            browser_input.type_selector("#name", "John Doe")
            browser_input.type_selector("#company", "Acme Corp")
            browser_input.type_selector("#phone", "+1-555-1234")
            
            # 验证填写内容
            with patch.object(browser_input, "get_value") as mock_get:
                mock_get.side_effect = ["John Doe", "Acme Corp", "+1-555-1234"]
                name = browser_input.get_value("#name")
                company = browser_input.get_value("#company")
                phone = browser_input.get_value("#phone")
                self.assertEqual(name, "John Doe")
                self.assertEqual(company, "Acme Corp")
                self.assertEqual(phone, "+1-555-1234")

    def test_03_fill_email_field(self):
        """测试：填写邮箱字段并验证格式"""
        # 模拟填写邮箱
        with patch.object(browser_input, "type_selector") as mock_type:
            mock_type.return_value = None
            browser_input.type_selector("#email", "john.doe@example.com")
            
            # 验证邮箱格式（通过浏览器控制台执行JS验证）
            with patch.object(browser_console, "eval") as mock_eval:
                mock_eval.return_value = True
                is_valid = browser_console.eval("/\S+@\S+\.\S+/.test(document.getElementById('email').value)")
                self.assertTrue(is_valid)

    def test_04_fill_textarea(self):
        """测试：填写多行文本区域"""
        # 模拟填写textarea
        with patch.object(browser_input, "type_selector") as mock_type:
            mock_type.return_value = None
            message = ("Hello, I would like to inquire about your services. " +
                       "Please contact me at your earliest convenience.")
            browser_input.type_selector("#message", message)
            
            # 验证textarea内容
            with patch.object(browser_input, "get_value") as mock_get:
                mock_get.return_value = message
                content = browser_input.get_value("#message")
                self.assertEqual(content, message)
                self.assertGreater(len(content), 10)

    def test_05_select_dropdown_options(self):
        """测试：选择下拉选项"""
        # 模拟select下拉框选择
        with patch.object(browser_input, "select") as mock_select:
            mock_select.return_value = None
            
            # 选择国家
            browser_input.select("#country", "US")
            # 选择服务类型
            browser_input.select("#service-type", "consulting")
            
            # 验证选择的值
            with patch.object(browser_input, "get_value") as mock_get:
                mock_get.side_effect = ["US", "consulting"]
                country = browser_input.get_value("#country")
                service = browser_input.get_value("#service-type")
                self.assertEqual(country, "US")
                self.assertEqual(service, "consulting")

    def test_06_choose_date(self):
        """测试：选择日期"""
        # 模拟日期选择器
        with patch.object(browser_input, "type_selector") as mock_type, \
             patch.object(browser_input, "click_selector") as mock_click:
            mock_type.return_value = None
            mock_click.return_value = None
            
            # 输入日期
            browser_input.type_selector("#date-picker", "2024-01-15")
            
            # 或者点击日历按钮选择日期
            browser_input.click_selector("#calendar-btn")
            browser_input.click_selector(".calendar-date[data-date='2024-01-15']")
            
            # 验证日期已选择
            with patch.object(browser_input, "get_value") as mock_get:
                mock_get.return_value = "2024-01-15"
                date_value = browser_input.get_value("#date-picker")
                self.assertEqual(date_value, "2024-01-15")

    def test_07_check_radio_buttons(self):
        """测试：单选按钮选择"""
        # 模拟单选按钮选择
        with patch.object(browser_input, "click_selector") as mock_click:
            mock_click.return_value = None
            
            # 选择性别（男性）
            browser_input.click_selector("input[name=gender][value='male']")
            
            # 验证选中状态
            with patch.object(browser_console, "eval") as mock_eval:
                mock_eval.return_value = True
                is_checked = browser_console.eval("document.querySelector('input[name=gender][value=male]').checked")
                self.assertTrue(is_checked)

    def test_08_check_checkboxes(self):
        """测试：复选框选择"""
        # 模拟复选框选择
        with patch.object(browser_input, "click_selector") as mock_click:
            mock_click.return_value = None
            
            # 选择多个选项
            browser_input.click_selector("#interest-python")
            browser_input.click_selector("#interest-web")
            browser_input.click_selector("#interest-data")
            
            # 验证所有复选框都被选中
            with patch.object(browser_console, "eval") as mock_eval:
                mock_eval.side_effect = [True, True, True]
                python_checked = browser_console.eval("document.getElementById('interest-python').checked")
                web_checked = browser_console.eval("document.getElementById('interest-web').checked")
                data_checked = browser_console.eval("document.getElementById('interest-data').checked")
                self.assertTrue(python_checked)
                self.assertTrue(web_checked)
                self.assertTrue(data_checked)

    def test_09_validate_form_errors(self):
        """测试：表单验证错误处理"""
        # 模拟提交空表单触发验证错误
        with patch.object(browser_input, "click_selector") as mock_click, \
             patch.object(browser_extract, "extract_text") as mock_extract:
            mock_click.return_value = None
            mock_extract.return_value = "Name is required"
            
            # 直接提交（不填写任何字段）
            browser_input.click_selector("#submit-button")
            
            # 验证错误信息显示
            error_msg = browser_extract.extract_text(mode="text", selector=".error-message")
            self.assertIn("required", error_msg.lower())

    def test_10_successful_submission(self):
        """测试：成功提交表单"""
        # 先填写完整表单
        with patch.object(browser_input, "type_selector") as mock_type, \
             patch.object(browser_input, "select") as mock_select:
            mock_type.return_value = None
            mock_select.return_value = None
            
            browser_input.type_selector("#name", "Jane Smith")
            browser_input.type_selector("#email", "jane@example.com")
            browser_input.type_selector("#message", "Thank you for your assistance!")
            browser_input.select("#country", "CA")
            
            # 提交表单
            with patch.object(browser_input, "click_selector") as mock_click, \
                 patch.object(browser_nav, "wait_url_contains") as mock_wait_url:
                mock_click.return_value = None
                mock_wait_url.return_value = True
                
                browser_input.click_selector("#submit-button")
                
                # 验证跳转到成功页
                self.assertTabUrlContains("test-tab-1", "success")
                self.assertTabTitleContains("test-tab-1", "Thank You")

    def test_11_upload_file(self):
        """测试：文件上传功能"""
        # 模拟文件上传
        with patch.object(browser_input, "click_selector") as mock_click, \
             patch.object(browser_input, "type") as mock_type:
            mock_click.return_value = None
            mock_type.return_value = None
            
            # 点击上传按钮
            browser_input.click_selector("#upload-btn")
            
            # 选择文件（模拟）
            browser_input.type("input[type=file]", "C:/temp/resume.pdf")
            
            # 等待上传完成
            browser_nav.wait_element("#upload-status.success")
            
            # 验证上传成功
            with patch.object(browser_extract, "extract_text") as mock_extract:
                mock_extract.return_value="Resume uploaded successfully"
                status = browser_extract.extract_text(mode="text", selector="#upload-status")
                self.assertIn("uploaded", status.lower())

    def test_12_multi_step_form(self):
        """测试：多步骤表单流程"""
        # 模拟多步骤表单
        with patch.object(browser_input, "type_selector") as mock_type, \
             patch.object(browser_input, "click_selector") as mock_click, \
             patch.object(browser_nav, "wait_url_contains") as mock_wait_url:
            mock_type.return_value = None
            mock_click.return_value = None
            mock_wait_url.return_value = True
            
            # Step 1: 填写基本信息
            browser_input.type_selector("#step1-name", "Bob Johnson")
            browser_input.click_selector("#step1-next")
            self.assertTabUrlContains("test-tab-1", "step=2")
            
            # Step 2: 填写详细信息
            browser_input.type_selector("#step2-email", "bob@example.com")
            browser_input.click_selector("#step2-next")
            self.assertTabUrlContains("test-tab-1", "step=3")
            
            # Step 3: 确认并提交
            browser_input.click_selector("#step3-confirm")
            self.assertTabUrlContains("test-tab-1", "confirmation")

    def test_13_capture_form_screenshot(self):
        """测试：截取表单截图用于文档记录"""
        # 模拟截图功能
        with patch.object(browser_screenshot, "capture") as mock_capture:
            mock_capture.return_value = "form_screenshot.png"
            screenshot_path = browser_screenshot.capture(
                annotate=True,
                out="test_form_submission.png"
            )
            self.assertEqual(screenshot_path, "test_form_submission.png")

    def test_14_extract_form_fields(self):
        """测试：提取表单字段信息"""
        # 模拟提取表单中的所有字段
        with patch.object(browser_extract, "extract_elements") as mock_extract:
            mock_extract.return_value = [
                {"id": "name", "type": "text", "label": "Name", "required": True},
                {"id": "email", "type": "email", "label": "Email", "required": True},
                {"id": "message", "type": "textarea", "label": "Message", "required": False},
                {"id": "country", "type": "select", "label": "Country", "required": False}
            ]
            fields = browser_extract.extract_elements(mode="elements", selector="form input, form select, form textarea")
            self.assertEqual(len(fields), 4)
            required_fields = [f for f in fields if f.get("required")]
            self.assertEqual(len(required_fields), 2)

    def test_15_clear_form(self):
        """测试：清空表单"""
        # 模拟清空所有表单字段
        with patch.object(browser_input, "clear") as mock_clear, \
             patch.object(browser_input, "click_selector") as mock_click:
            mock_clear.return_value = None
            mock_click.return_value = None
            
            # 逐个清空字段
            browser_input.clear("#name")
            browser_input.clear("#email")
            browser_input.clear("#message")
            
            # 或使用一键清空按钮
            browser_input.click_selector("#clear-all-btn")
            
            # 验证所有字段为空
            with patch.object(browser_input, "get_value") as mock_get:
                mock_get.side_effect = ["", "", ""]
                name = browser_input.get_value("#name")
                email = browser_input.get_value("#email")
                message = browser_input.get_value("#message")
                self.assertEqual(name, "")
                self.assertEqual(email, "")
                self.assertEqual(message, "")


if __name__ == "__main__":
    unittest.main()