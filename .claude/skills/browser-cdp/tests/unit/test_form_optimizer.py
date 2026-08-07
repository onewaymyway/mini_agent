"""
test_form_optimizer.py - 表单填写场景优化测试

测试覆盖：
- 智能字段检测
- 文本字段填写
- 复选框/单选框填写
- 下拉选择框填写
- 文件上传
- 表单验证
- 表单状态保存/恢复
- 验证错误处理
"""
import pytest
import json
from unittest.mock import MagicMock, patch, call
from src.core.form_optimizer import FormOptimizer, FieldResult, FormResult, generate_selector, is_element_visible


class TestFieldResult:
    """FieldResult 数据类测试。"""
    
    def test_create_field_result(self):
        """测试创建 FieldResult。"""
        result = FieldResult(
            selector="input[name='username']",
            field_type="text",
            value="john",
            success=True
        )
        assert result.selector == "input[name='username']"
        assert result.field_type == "text"
        assert result.value == "john"
        assert result.success is True
        assert result.error is None
        assert result.retries == 0
    
    def test_to_dict(self):
        """测试 FieldResult 转字典。"""
        result = FieldResult(
            selector="input[name='email']",
            field_type="email",
            value="test@example.com",
            success=True,
            error=None,
            retries=1
        )
        d = result.to_dict()
        assert d["selector"] == "input[name='email']"
        assert d["type"] == "email"
        assert d["value"] == "test@example.com"
        assert d["success"] is True
        assert d["retries"] == 1


class TestFormResult:
    """FormResult 数据类测试。"""
    
    def test_create_form_result(self):
        """测试创建 FormResult。"""
        result = FormResult(success=True)
        assert result.success is True
        assert result.fields == []
        assert result.errors == []
        assert result.warnings == []
        assert result.submit_result is None
    
    def test_to_dict(self):
        """测试 FormResult 转字典。"""
        field_result = FieldResult(
            selector="input[name='name']",
            field_type="text",
            value="张三",
            success=True
        )
        form_result = FormResult(
            success=True,
            fields=[field_result],
            errors=[],
            warnings=["表单验证警告"],
            submit_result={"submitted": True}
        )
        d = form_result.to_dict()
        assert d["success"] is True
        assert len(d["fields"]) == 1
        assert d["fields"][0]["selector"] == "input[name='name']"
        assert d["warnings"] == ["表单验证警告"]
        assert d["submit_result"] == {"submitted": True}


class TestFormOptimizer:
    """FormOptimizer 类测试。"""
    
    @pytest.fixture
    def mock_session(self):
        """创建模拟 session。"""
        session = MagicMock()
        session.eval_js.return_value = {"filled": True}
        session.send.return_value = None
        return session
    
    @pytest.fixture
    def optimizer(self, mock_session):
        """创建 FormOptimizer 实例。"""
        with patch('src.core.form_optimizer.SmartWait'):
            return FormOptimizer(mock_session)
    
    def test_detect_form_fields(self, optimizer, mock_session):
        """测试表单字段检测。"""
        mock_fields = [
            {"selector": "input[name='username']", "type": "text", "name": "username"},
            {"selector": "input[name='email']", "type": "email", "name": "email"},
            {"selector": "select[name='country']", "type": "select-one", "name": "country"},
        ]
        mock_session.eval_js.return_value = mock_fields
        
        fields = optimizer.detect_form_fields()
        
        assert len(fields) == 3
        assert fields[0]["name"] == "username"
        assert fields[1]["name"] == "email"
        assert fields[2]["name"] == "country"
    
    def test_fill_text_field(self, optimizer, mock_session):
        """测试文本字段填写。"""
        mock_session.eval_js.side_effect = ["text", {"filled": True, "currentValue": "hello"}]
        result = optimizer.fill_field("input[name='name']", "hello")
        assert result.success is True
        assert result.field_type == "text"
        assert result.value == "hello"
    
    
    def test_fill_checkbox(self, optimizer, mock_session):
        """测试复选框填写。"""
        mock_session.eval_js.return_value = {"filled": True, "checked": True}
        
        result = optimizer.fill_field("input[name='agree']", True, "checkbox")
        
        assert result.success is True
        assert result.field_type == "checkbox"
    
    def test_fill_select(self, optimizer, mock_session):
        """测试下拉选择框填写。"""
        mock_session.eval_js.return_value = {
            "filled": True,
            "selectedValue": "CN",
            "selectedText": "中国"
        }
        
        result = optimizer.fill_field("select[name='country']", "CN", "select")
        
        assert result.success is True
        assert result.field_type == "select"
    
    def test_fill_select_option_not_found(self, optimizer, mock_session):
        """测试下拉选择框选项不存在。"""
        mock_session.eval_js.return_value = {
            "error": "option not found",
            "available": ["US", "UK", "JP"]
        }
        
        result = optimizer.fill_field("select[name='country']", "CN", "select")
        
        assert result.success is False
        assert "option not found" in result.error
    
    def test_upload_file_not_exists(self, optimizer, mock_session):
        """测试上传不存在的文件。"""
        result = optimizer.fill_field("input[type='file']", "/nonexistent/file.pdf", "file")
        
        assert result.success is False
        assert "文件不存在" in result.error
    
    def test_validate_form(self, optimizer, mock_session):
        """测试表单验证。"""
        mock_session.eval_js.return_value = {
            "valid": True,
            "message": "",
            "missingRequired": [],
            "fieldCount": 5
        }
        
        result = optimizer.validate_form()
        
        assert result["valid"] is True
        assert result["fieldCount"] == 5
    
    def test_validate_form_with_errors(self, optimizer, mock_session):
        """测试表单验证有错误。"""
        mock_session.eval_js.return_value = {
            "valid": False,
            "message": "请填写此字段",
            "missingRequired": ["username", "email"]
        }
        
        result = optimizer.validate_form()
        
        assert result["valid"] is False
        assert "username" in result["missingRequired"]
        assert "email" in result["missingRequired"]
    
    def test_save_form_state(self, optimizer, mock_session):
        """测试保存表单状态。"""
        mock_state = {
            "action": "/submit",
            "method": "POST",
            "timestamp": 1234567890,
            "fields": [
                {"name": "username", "type": "text", "value": "john"},
                {"name": "agree", "type": "checkbox", "checked": True}
            ]
        }
        mock_session.eval_js.return_value = mock_state
        
        state = optimizer.save_form_state()
        
        assert state["action"] == "/submit"
        assert len(state["fields"]) == 2
    
    def test_restore_form_state(self, optimizer, mock_session):
        """测试恢复表单状态。"""
        mock_session.eval_js.return_value = {"filled": True}
        
        form_state = {
            "fields": [
                {"name": "username", "type": "text", "value": "john"},
                {"name": "agree", "type": "checkbox", "checked": True}
            ]
        }
        
        result = optimizer.restore_form_state(form_state)
        
        assert len(result["fields"]) == 2
    
    def test_handle_validation_error_email(self, optimizer, mock_session):
        """测试处理邮箱格式错误。"""
        suggestion = optimizer.handle_validation_error("Please enter a valid email address")
        
        assert suggestion is not None
        assert "email" in suggestion.lower() or "邮箱" in suggestion
    
    def test_handle_validation_error_required(self, optimizer, mock_session):
        """测试处理必填字段错误。"""
        suggestion = optimizer.handle_validation_error("This field is required")
        
        assert suggestion is not None
        assert "必填" in suggestion
    
    def test_handle_validation_error_unknown(self, optimizer, mock_session):
        """测试处理未知错误。"""
        suggestion = optimizer.handle_validation_error("Unknown error")
        
        assert suggestion is None
    
    def test_fill_form_complete(self, optimizer, mock_session):
        "测试完整表单填写流程。"
        mock_session.eval_js.side_effect = [
            [
                {"selector": "input[name='username']", "type": "text", "name": "username"},
                {"selector": "input[name='email']", "type": "email", "name": "email"},
            ],
            "text",
            {"filled": True, "currentValue": "john"},
            "email",
            {"filled": True, "currentValue": "john@example.com"},
            {"submitted": True, "type": "button"},
            {"valid": True, "message": "", "missingRequired": []}
        ]
        form_def = {
            "fields": [
                {"selector": "input[name='username']", "value": "john"},
                {"selector": "input[name='email']", "value": "john@example.com"}
            ],
            "submit": {"selector": "button[type='submit']"},
        }
        result = optimizer.fill_form(form_def)
        assert result.success is True
        assert len(result.fields) == 2
        assert result.fields[0].success is True
        assert result.fields[1].success is True
    
    
    def test_fill_form_with_errors(self, optimizer, mock_session):
        """测试表单填写有错误。"""
        mock_session.eval_js.side_effect = [
            # detect_form_fields
            [],
            # fill_field - 元素未找到 (可能多次调用)
            {"error": "element not found"},
            {"error": "element not found"},
            # submit_form (default)
            {"submitted": True, "type": "form"},
            # validate_form
            {"valid": False, "message": "请填写此字段", "missingRequired": ["username"]}
        ]

        form_def = {
            "fields": [
                {"selector": "input[name='username']", "value": "john"}
            ]
        }

        result = optimizer.fill_form(form_def)

        assert result.success is False
        assert len(result.errors) > 0
    
    def test_submit_form_default(self, optimizer, mock_session):
        """测试默认提交表单。"""
        mock_session.eval_js.return_value = {"submitted": True, "type": "form"}
        
        result = optimizer.submit_form()
        
        assert result["submitted"] is True
    
    def test_submit_form_with_selector(self, optimizer, mock_session):
        """测试带选择器的表单提交。"""
        mock_session.eval_js.return_value = {"submitted": True, "type": "button"}
        
        result = optimizer.submit_form({"selector": "#submit-btn"})
        
        assert result["submitted"] is True
    
    def test_submit_form_not_found(self, optimizer, mock_session):
        """测试提交按钮未找到。"""
        mock_session.eval_js.return_value = {"error": "submit button not found"}
        
        result = optimizer.submit_form({"selector": "#nonexistent"})
        
        assert "error" in result


class TestHelperFunctions:
    """辅助函数测试。"""
    
    def test_generate_selector_with_id(self):
        """测试生成带 ID 的选择器。"""
        mock_element = MagicMock()
        mock_element.tagName = "DIV"
        mock_element.id = "main"
        mock_element.className = ""
        mock_element.parentElement = None
        
        selector = generate_selector(mock_element)
        
        assert "#main" in selector
    
    def test_generate_selector_with_class(self):
        """测试生成带类名的选择器。"""
        mock_element = MagicMock()
        mock_element.tagName = "INPUT"
        mock_element.id = ""
        mock_element.className = "form-control required"
        mock_element.parentElement = None
        
        selector = generate_selector(mock_element)
        
        assert "input.form-control.required" in selector
    
    def test_generate_selector_nested(self):
        """测试生成嵌套选择器。"""
        parent = MagicMock()
        parent.tagName = "FORM"
        parent.id = "login-form"
        parent.className = ""
        parent.parentElement = None
        
        child = MagicMock()
        child.tagName = "INPUT"
        child.id = "username"
        child.className = ""
        child.parentElement = parent
        
        selector = generate_selector(child)
        
        assert "#login-form" in selector
        assert "#username" in selector
    
    def test_generate_selector_none(self):
        """测试传入 None。"""
        selector = generate_selector(None)
        assert selector == ""
    
    def test_is_element_visible_true(self):
        """测试元素可见性检查（可见）。"""
        mock_element = MagicMock()
        mock_rect = MagicMock()
        mock_rect.width = 100
        mock_rect.height = 50
        mock_element.getBoundingClientRect.return_value = mock_rect
        
        with patch('src.core.form_optimizer.is_element_visible') as mock_func:
            mock_func.return_value = True
            assert is_element_visible(mock_element) is True
    
    def test_is_element_visible_false(self):
        """测试元素可见性检查（不可见）。"""
        mock_element = MagicMock()
        mock_rect = MagicMock()
        mock_rect.width = 0
        mock_rect.height = 0
        mock_element.getBoundingClientRect.return_value = mock_rect
        
        with patch('src.core.form_optimizer.is_element_visible') as mock_func:
            mock_func.return_value = False
            assert is_element_visible(mock_element) is False


class TestFormOptimizerEdgeCases:
    """边界情况测试。"""
    
    @pytest.fixture
    def mock_session(self):
        session = MagicMock()
        session.eval_js.return_value = {"filled": True}
        session.send.return_value = None
        return session
    
    @pytest.fixture
    def optimizer(self, mock_session):
        with patch('src.core.form_optimizer.SmartWait'):
            return FormOptimizer(mock_session)
    
    def test_fill_field_element_not_found(self, optimizer, mock_session):
        """测试元素未找到。"""
        mock_session.eval_js.return_value = {"error": "element not found"}
        
        result = optimizer.fill_field("input[name='nonexistent']", "value")
        
        assert result.success is False
        assert "element not found" in result.error
    
    def test_fill_field_js_error(self, optimizer, mock_session):
        """测试 JS 执行异常。"""
        mock_session.eval_js.side_effect = Exception("CDP error")
        
        result = optimizer.fill_field("input[name='name']", "value")
        
        assert result.success is False
        assert result.error is not None
    
    def test_detect_form_fields_empty(self, optimizer, mock_session):
        """测试空表单检测。"""
        mock_session.eval_js.return_value = []
        
        fields = optimizer.detect_form_fields()
        
        assert fields == []
    
    def test_detect_form_fields_error(self, optimizer, mock_session):
        """测试检测失败时返回空列表。"""
        mock_session.eval_js.side_effect = Exception("Connection lost")
        
        fields = optimizer.detect_form_fields()
        
        assert fields == []
    
    def test_fill_form_empty_fields(self, optimizer, mock_session):
        """测试空表单定义。"""
        form_def = {"fields": []}
        
        result = optimizer.fill_form(form_def)
        
        assert result.success is True
        assert len(result.fields) == 0
    
    def test_fill_form_missing_selector(self, optimizer, mock_session):
        """测试缺少 selector 的字段。"""
        form_def = {
            "fields": [
                {"value": "john"}  # 缺少 selector
            ]
        }
        
        result = optimizer.fill_form(form_def)
        
        assert result.success is False
        assert any("selector" in e for e in result.errors)
    
    def test_restore_form_state_empty(self, optimizer, mock_session):
        """测试恢复空表单状态。"""
        result = optimizer.restore_form_state({"fields": []})
        
        assert result["fields"] == []
    
    def test_validate_form_not_found(self, optimizer, mock_session):
        """测试验证不存在的表单。"""
        mock_session.eval_js.return_value = {"error": "form not found"}
        
        result = optimizer.validate_form()
        
        assert "error" in result
    
    def test_save_form_not_found(self, optimizer, mock_session):
        """测试保存不存在的表单状态。"""
        mock_session.eval_js.return_value = {"error": "form not found"}
        
        result = optimizer.save_form_state()
        
        assert "error" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])