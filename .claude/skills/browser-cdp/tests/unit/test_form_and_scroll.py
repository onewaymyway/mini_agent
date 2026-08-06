"""
表单和滚动优化单元测试

测试：
- dynamic_form_handler.py
- infinite_scroll.py
"""
import pytest
import time
from unittest.mock import MagicMock, patch

import sys
sys.path.insert(0, '.')

from src.core.login.dynamic_form_handler import (
    DynamicFormHandler,
    DynamicFormField,
    MultiStepForm,
    create_dynamic_form_handler,
    fill_dynamic_form,
)
from src.core.infinite_scroll import (
    InfiniteScrollHandler,
    ScrollState,
    create_infinite_scroll_handler,
    scroll_to_bottom,
    scroll_and_collect,
)


class TestDynamicFormField:
    """DynamicFormField 测试"""
    
    def test_to_dict(self):
        """测试序列化"""
        field = DynamicFormField(
            selector="#username",
            field_type="text",
            value="test",
            depends_on=None,
        )
        
        d = field.to_dict()
        assert d["selector"] == "#username"
        assert d["type"] == "text"
        assert d["value"] == "test"


class TestMultiStepForm:
    """MultiStepForm 测试"""
    
    def test_to_dict(self):
        """测试序列化"""
        form = MultiStepForm(
            steps=[{"step": 1}, {"step": 2}],
            current_step=0,
            saved_state={"step_0": {"field": "value"}},
        )
        
        d = form.to_dict()
        assert len(d["steps"]) == 2
        assert d["current_step"] == 0
        assert "step_0" in d["saved_state"]


class TestDynamicFormHandler:
    """DynamicFormHandler 测试"""
    
    def setup_method(self):
        self.session = MagicMock()
        self.handler = DynamicFormHandler(self.session)
    
    def test_register_field(self):
        """测试注册字段"""
        field = DynamicFormField(selector="#username", field_type="text")
        self.handler.register_field(field)
        
        assert len(self.handler._form_fields) == 1
        assert self.handler._form_fields[0].selector == "#username"
    
    def test_fill_field_success(self):
        """测试填写字段成功"""
        self.session.eval_js.return_value = True
        
        result = self.handler._fill_field("#username", "test")
        
        assert result is True
        self.session.eval_js.assert_called()
    
    def test_fill_field_failure(self):
        """测试填写字段失败"""
        self.session.eval_js.side_effect = Exception("Error")
        
        result = self.handler._fill_field("#username", "test")
        
        assert result is False
    
    def test_fill_dynamic_form(self):
        """测试填写动态表单"""
        self.session.eval_js.return_value = True
        
        field = DynamicFormField(selector="#username", field_type="text")
        self.handler.register_field(field)
        
        result = self.handler.fill_dynamic_form({"#username": "test"})
        
        assert result is True
    
    def test_register_multi_step_form(self):
        """测试注册多步骤表单"""
        self.handler.register_multi_step_form("test_form", [
            {"step": 1, "fields": ["name"]},
            {"step": 2, "fields": ["email"]},
        ])
        
        assert "test_form" in self.handler._multi_step_forms
    
    def test_save_step_state(self):
        """测试保存步骤状态"""
        self.handler.register_multi_step_form("test_form", [
            {"step": 1},
            {"step": 2},
        ])
        
        result = self.handler.save_step_state("test_form", {"field": "value"})
        
        assert result is True
    
    def test_restore_step_state(self):
        """测试恢复步骤状态"""
        self.handler.register_multi_step_form("test_form", [
            {"step": 1},
        ])
        self.handler.save_step_state("test_form", {"field": "value"})
        
        state = self.handler.restore_step_state("test_form", 0)
        
        assert state == {"field": "value"}
    
    def test_next_step(self):
        """测试进入下一步"""
        self.handler.register_multi_step_form("test_form", [
            {"step": 1},
            {"step": 2},
        ])
        
        result = self.handler.next_step("test_form")
        
        assert result is True
        assert self.handler._multi_step_forms["test_form"].current_step == 1
    
    def test_prev_step(self):
        """测试返回上一步"""
        self.handler.register_multi_step_form("test_form", [
            {"step": 1},
            {"step": 2},
        ])
        self.handler.next_step("test_form")
        
        result = self.handler.prev_step("test_form")
        
        assert result is True
        assert self.handler._multi_step_forms["test_form"].current_step == 0


class TestInfiniteScrollHandler:
    """InfiniteScrollHandler 测试"""
    
    def setup_method(self):
        self.session = MagicMock()
        self.handler = InfiniteScrollHandler(self.session)
    
    def test_scroll_to_bottom(self):
        """测试滚动到底部"""
        # 模拟滚动到底部
        self.session.eval_js.side_effect = [False, True]  # 第一次没到底，第二次到了
        
        count = self.handler.scroll_to_bottom(max_scrolls=3)
        
        assert count > 0
    
    def test_scroll_to_element(self):
        """测试滚动到元素"""
        self.session.eval_js.return_value = True
        
        result = self.handler.scroll_to_element("#target")
        
        assert result is True
    
    def test_is_at_bottom(self):
        """测试检测是否到底部"""
        self.session.eval_js.return_value = True
        
        result = self.handler._is_at_bottom()
        
        assert result is True
    
    def test_get_scroll_position(self):
        """测试获取滚动位置"""
        self.session.eval_js.return_value = 500
        
        position = self.handler._get_scroll_position()
        
        assert position == 500.0
    
    def test_get_content_height(self):
        """测试获取内容高度"""
        self.session.eval_js.return_value = 2000
        
        height = self.handler._get_content_height()
        
        assert height == 2000.0
    
    def test_wait_for_content_load(self):
        """测试等待内容加载"""
        # 模拟内容稳定
        self.session.eval_js.return_value = 1000
        
        result = self.handler.wait_for_content_load(timeout=1, check_interval=0.1)
        
        assert result is True
    
    def test_get_scroll_state(self):
        """测试获取滚动状态"""
        self.session.eval_js.return_value = 500
        
        state = self.handler.get_scroll_state()
        
        assert isinstance(state, ScrollState)
        assert state.current_position == 500.0
    
    def test_scroll_history(self):
        """测试滚动历史"""
        self.handler._scroll_history = [
            {"scroll_count": 1, "position": 500},
            {"scroll_count": 2, "position": 1000},
        ]
        
        history = self.handler.get_scroll_history()
        assert len(history) == 2
        
        self.handler.clear_history()
        assert len(self.handler.get_scroll_history()) == 0


class TestScrollIntegration:
    """滚动集成测试"""
    
    def test_scroll_and_collect(self):
        """测试滚动并收集"""
        session = MagicMock()
        handler = InfiniteScrollHandler(session)
        
        # 模拟收集函数
        def collector(session):
            return [{"title": "item"}]
        
        # 模拟滚动到底部
        session.eval_js.side_effect = [False, True]
        
        results = handler.scroll_and_collect(collector, max_scrolls=3)
        
        assert len(results) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
