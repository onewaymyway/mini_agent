"""
dynamic_form_handler.py - 动态表单处理模块

支持：
- AJAX 级联选择器
- 动态表单字段
- 表单验证绕过
- 多步骤表单状态保持
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class DynamicFormField:
    """动态表单字段"""
    selector: str
    field_type: str = "text"
    value: Any = None
    depends_on: Optional[str] = None  # 依赖的其他字段
    options_selector: Optional[str] = None  # 选项选择器
    load_url: Optional[str] = None  # 加载选项的 URL
    
    def to_dict(self) -> dict:
        return {
            "selector": self.selector,
            "type": self.field_type,
            "value": self.value,
            "depends_on": self.depends_on,
            "options_selector": self.options_selector,
            "load_url": self.load_url,
        }


@dataclass
class MultiStepForm:
    """多步骤表单"""
    steps: List[Dict[str, Any]] = field(default_factory=list)
    current_step: int = 0
    saved_state: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return {
            "steps": self.steps,
            "current_step": self.current_step,
            "saved_state": self.saved_state,
        }


class DynamicFormHandler:
    """
    动态表单处理器
    
    支持 AJAX 级联选择、动态表单字段加载、多步骤表单。
    """
    
    def __init__(self, session, delay_range: tuple = (0.5, 1.5)):
        self.session = session
        self.delay_range = delay_range
        self._form_fields: List[DynamicFormField] = []
        self._multi_step_forms: Dict[str, MultiStepForm] = {}
    
    def register_field(self, field: DynamicFormField):
        """注册动态表单字段"""
        self._form_fields.append(field)
        logger.info(f"注册动态字段: {field.selector}")
    
    def fill_dynamic_form(self, form_data: Dict[str, Any]) -> bool:
        """
        填写动态表单
        
        Args:
            form_data: 表单数据字典
        
        Returns:
            是否成功
        """
        success = True
        
        for field in self._form_fields:
            if field.selector not in form_data:
                continue
            
            value = form_data[field.selector]
            
            # 检查是否有依赖
            if field.depends_on and field.depends_on in form_data:
                # 等待依赖字段加载
                self._wait_for_dependency(field.depends_on)
            
            # 填写字段
            if field.field_type == "select" and field.options_selector:
                # 级联选择器
                success = self._fill_cascading_select(field, value)
            else:
                # 普通字段
                success = self._fill_field(field.selector, value)
            
            if not success:
                logger.error(f"填写字段失败: {field.selector}")
                break
        
        return success
    
    def _fill_cascading_select(self, field: DynamicFormField, value: Any) -> bool:
        """填写级联选择器"""
        try:
            # 加载选项
            if field.load_url:
                self._load_options(field.load_url)
            
            # 选择选项
            js = f'''
            (function() {{
                var select = document.querySelector({field.options_selector!r});
                if (!select) return false;
                
                var options = Array.from(select.options);
                var option = options.find(function(o) {{
                    return o.value === {value!r} || o.text === {value!r};
                }});
                
                if (option) {{
                    select.value = option.value;
                    select.dispatchEvent(new Event('change', {{bubbles: true}}));
                    return true;
                }}
                return false;
            }})()
            '''
            
            result = self.session.eval_js(js)
            return bool(result)
        except Exception as e:
            logger.error(f"级联选择器填写失败: {e}")
            return False
    
    def _fill_field(self, selector: str, value: Any) -> bool:
        """填写普通字段"""
        try:
            js = f'''
            (function() {{
                var el = document.querySelector({selector!r});
                if (!el) return false;
                
                var type = el.type || el.tagName.toLowerCase();
                
                if (type === 'checkbox' || type === 'radio') {{
                    el.checked = {str(value).lower()};
                    el.dispatchEvent(new Event('change', {{bubbles: true}}));
                    return true;
                }}
                
                el.focus();
                el.value = {value!r};
                el.dispatchEvent(new Event('input', {{bubbles: true}}));
                el.dispatchEvent(new Event('change', {{bubbles: true}}));
                return true;
            }})()
            '''
            
            result = self.session.eval_js(js)
            return bool(result)
        except Exception as e:
            logger.error(f"字段填写失败: {selector}: {e}")
            return False
    
    def _wait_for_dependency(self, selector: str, timeout: float = 10.0):
        """等待依赖字段加载"""
        import random
        delay = random.uniform(*self.delay_range)
        time.sleep(delay)
        
        # 等待元素出现
        js = f'''
        (function() {{
            var el = document.querySelector({selector!r});
            return el !== null;
        }})()
        '''
        
        start = time.time()
        while time.time() - start < timeout:
            try:
                if self.session.eval_js(js):
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        
        logger.warning(f"等待依赖字段超时: {selector}")
        return False
    
    def _load_options(self, url: str):
        """加载选项数据"""
        try:
            self.session.send("Page.navigate", {"url": url})
            time.sleep(1)
        except Exception as e:
            logger.error(f"加载选项失败: {e}")
    
    def register_multi_step_form(self, form_id: str, steps: List[Dict[str, Any]]):
        """注册多步骤表单"""
        form = MultiStepForm(steps=steps)
        self._multi_step_forms[form_id] = form
        logger.info(f"注册多步骤表单: {form_id}")
    
    def save_step_state(self, form_id: str, step_data: Dict[str, Any]) -> bool:
        """保存步骤状态"""
        if form_id not in self._multi_step_forms:
            logger.error(f"表单不存在: {form_id}")
            return False
        
        form = self._multi_step_forms[form_id]
        form.saved_state[f"step_{form.current_step}"] = step_data
        logger.info(f"保存步骤 {form.current_step} 状态")
        return True
    
    def restore_step_state(self, form_id: str, step_index: int = None) -> Dict[str, Any]:
        """恢复步骤状态"""
        if form_id not in self._multi_step_forms:
            logger.error(f"表单不存在: {form_id}")
            return {}
        
        form = self._multi_step_forms[form_id]
        
        if step_index is None:
            step_index = form.current_step
        
        key = f"step_{step_index}"
        return form.saved_state.get(key, {})
    
    def next_step(self, form_id: str) -> bool:
        """进入下一步"""
        if form_id not in self._multi_step_forms:
            logger.error(f"表单不存在: {form_id}")
            return False
        
        form = self._multi_step_forms[form_id]
        if form.current_step < len(form.steps) - 1:
            form.current_step += 1
            logger.info(f"进入步骤 {form.current_step}")
            return True
        
        return False
    
    def prev_step(self, form_id: str) -> bool:
        """返回上一步"""
        if form_id not in self._multi_step_forms:
            logger.error(f"表单不存在: {form_id}")
            return False
        
        form = self._multi_step_forms[form_id]
        if form.current_step > 0:
            form.current_step -= 1
            logger.info(f"返回步骤 {form.current_step}")
            return True
        
        return False


# 便捷函数
def create_dynamic_form_handler(session, delay_range: tuple = (0.5, 1.5)) -> DynamicFormHandler:
    """创建动态表单处理器"""
    return DynamicFormHandler(session, delay_range)


def fill_dynamic_form(session, form_data: Dict[str, Any]) -> bool:
    """填写动态表单"""
    handler = create_dynamic_form_handler(session)
    return handler.fill_dynamic_form(form_data)
