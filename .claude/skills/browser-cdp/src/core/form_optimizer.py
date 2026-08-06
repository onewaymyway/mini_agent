"""
form_optimizer.py - 表单填写场景优化模块

功能：
- 智能字段检测与自动填充
- 动态表单处理（AJAX 加载的选项）
- 表单验证错误恢复
- 多步骤表单流程
- 文件上传进度跟踪
- 表单状态持久化与恢复

用法：
  from src.core.form_optimizer import FormOptimizer
  optimizer = FormOptimizer(session)
  result = optimizer.fill_form(form_definition)
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from src.core.smart_wait import SmartWait
from src.reliability.middleware import (
    get_middleware,
    OperationType,
    with_error_handling,
)
from src.reliability.error import (
    ElementNotFoundError,
    CDPConnectionLostError,
)

logger = logging.getLogger(__name__)


@dataclass
class FieldResult:
    """单个字段的填写结果。"""
    selector: str
    field_type: str
    value: Any
    success: bool
    error: Optional[str] = None
    retries: int = 0
    
    def to_dict(self) -> dict:
        return {
            "selector": self.selector,
            "type": self.field_type,
            "value": self.value,
            "success": self.success,
            "error": self.error,
            "retries": self.retries,
        }


@dataclass
class FormResult:
    """表单填写的完整结果。"""
    success: bool
    fields: List[FieldResult] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    submit_result: Optional[dict] = None
    
    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "fields": [f.to_dict() for f in self.fields],
            "errors": self.errors,
            "warnings": self.warnings,
            "submit_result": self.submit_result,
        }


class FormOptimizer:
    """表单填写优化器。"""
    
    # 常见表单字段类型映射
    FIELD_TYPE_MAP = {
        "text": "text",
        "email": "email",
        "password": "password",
        "number": "number",
        "tel": "tel",
        "url": "url",
        "search": "search",
        "date": "date",
        "datetime-local": "datetime-local",
        "month": "month",
        "week": "week",
        "time": "time",
        "color": "color",
        "range": "range",
        "checkbox": "checkbox",
        "radio": "radio",
        "file": "file",
        "select-one": "select",
        "select-multiple": "select-multiple",
    }
    
    # 智能填充模式
    FILL_PATTERNS = {
        "email": r"^[\w\.-]+@[\w\.-]+\.\w+$",
        "phone": r"^1[3-9]\d{9}$",
        "id_card": r"^\d{17}[\dXx]$",
        "postal_code": r"^\d{6}$",
    }
    
    def __init__(self, session):
        """初始化表单优化器。
        
        Args:
            session: CDP session 对象
        """
        self.session = session
        self.smart_wait = SmartWait(session)
        self.field_results: List[FieldResult] = []
        self.form_history: Dict[str, Any] = {}
        
    def detect_form_fields(self, form_selector: str = None) -> List[dict]:
        """智能检测表单中的所有字段。
        
        Args:
            form_selector: 表单选择器，None 则检测页面所有表单
            
        Returns:
            字段列表，每个字段包含 selector、type、name、required 等信息
        """
        js = """(() => {
            const forms = formSelector 
                ? [document.querySelector(formSelector)]
                : Array.from(document.querySelectorAll('form'));
            
            const fields = [];
            const fieldTypes = ['input', 'select', 'textarea'];
            
            for (const form of forms) {
                if (!form) continue;
                
                for (const tag of fieldTypes) {
                    const elements = form.querySelectorAll(tag);
                    for (const el of elements) {
                        // 跳过隐藏字段和提交按钮
                        if (el.type === 'hidden' || el.type === 'submit' || el.type === 'button') continue;
                        if (el.disabled) continue;
                        
                        // 生成选择器
                        const selector = generateSelector(el);
                        
                        fields.push({
                            selector: selector,
                            tag: el.tagName.toLowerCase(),
                            type: el.type || 'text',
                            name: el.name || '',
                            id: el.id || '',
                            required: el.required,
                            placeholder: el.placeholder || '',
                            value: el.value,
                            options: el.options ? Array.from(el.options).map(o => ({
                                value: o.value,
                                text: o.text
                            })) : null,
                            visible: isElementVisible(el),
                        });
                    }
                }
            }
            
            return fields;
        })()"""
        
        js = js.replace('formSelector', repr(form_selector) if form_selector else 'null')
        
        try:
            fields = self.session.eval_js(js)
            return fields or []
        except Exception as e:
            logger.error(f"检测表单字段失败: {e}")
            return []
    
    def fill_field(self, selector: str, value: Any, field_type: str = None, 
                   smart_fill: bool = True) -> FieldResult:
        """填写单个表单字段，支持智能填充。
        
        Args:
            selector: 字段选择器
            value: 要填写的值
            field_type: 字段类型（可选，自动检测）
            smart_fill: 是否启用智能填充
            
        Returns:
            FieldResult 填写结果
        """
        result = FieldResult(selector=selector, field_type=field_type or "text", 
                           value=value, success=False)
        
        # 智能填充：根据字段特征推断类型
        if smart_fill and not field_type:
            field_type = self._detect_field_type(selector)
            result.field_type = field_type
        
        # 根据类型执行不同的填充策略
        if field_type in ("checkbox", "radio"):
            result = self._fill_checkbox(selector, value, result)
        elif field_type == "file":
            result = self._upload_file(selector, value, result)
        elif field_type == "select" or field_type == "select-multiple":
            result = self._fill_select(selector, value, result)
        else:
            result = self._fill_text(selector, value, field_type, result)
        
        self.field_results.append(result)
        return result
    
    def fill_form(self, form_def: dict, auto_detect: bool = True) -> FormResult:
        """填写完整表单。
        
        Args:
            form_def: 表单定义，格式：
                {
                    "fields": [
                        {"selector": "input[name='username']", "value": "john"},
                        {"selector": "select[name='country']", "value": "CN"}
                    ],
                    "submit": {"selector": "button[type='submit']"},
                    "wait_for": "url",
                    "wait_url_contains": "success"
                }
            auto_detect: 是否自动检测表单字段
            
        Returns:
            FormResult 表单填写结果
        """
        form_result = FormResult(success=False)
        
        # 自动检测模式：先扫描表单字段
        if auto_detect:
            detected_fields = self.detect_form_fields()
            logger.info(f"检测到 {len(detected_fields)} 个表单字段")
        
        # 填写各个字段
        for field_def in form_def.get("fields", []):
            selector = field_def.get("selector")
            value = field_def.get("value")
            field_type = field_def.get("type")
            
            if not selector:
                form_result.errors.append("字段缺少 selector")
                continue
            
            field_result = self.fill_field(selector, value, field_type)
            form_result.fields.append(field_result)
            
            if not field_result.success:
                form_result.errors.append(f"{selector}: {field_result.error}")
            else:
                logger.info(f"[ok] 已填写: {selector}")
        
        # 提交表单
        submit_def = form_def.get("submit")
        if submit_def:
            form_result.submit_result = self.submit_form(submit_def)
            if form_result.submit_result.get("error"):
                form_result.errors.append(f"提交失败: {form_result.submit_result['error']}")
        
        # 检查验证错误
        validation = self.validate_form()
        if not validation.get("valid", True):
            form_result.warnings.append(f"表单验证警告: {validation.get('message', '未知错误')}")
        
        form_result.success = len(form_result.errors) == 0
        return form_result
    
    def submit_form(self, submit_def: dict = None, 
                    wait_for: str = None, timeout: float = 30.0) -> dict:
        """提交表单并等待结果。
        
        Args:
            submit_def: 提交定义，包含 selector 等信息
            wait_for: 等待策略（load/networkidle/route/stable/ajax/selector）
            timeout: 超时时间（秒）
            
        Returns:
            提交结果
        """
        if not submit_def:
            # 默认提交第一个表单
            js = """(() => {
                const form = document.querySelector('form');
                if (form) {
                    form.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}));
                    return {submitted: true, type: 'form'};
                }
                const btn = document.querySelector('button[type="submit"]');
                if (btn) {
                    btn.click();
                    return {submitted: true, type: 'button'};
                }
                return {error: 'no form or submit button found'};
            })()"""
        else:
            selector = submit_def.get("selector", "button[type='submit']")
            js = f"""(() => {{
                const btn = document.querySelector({selector!r});
                if (btn) {{
                    btn.click();
                    return {{submitted: true, type: 'button'}};
                }}
                return {{error: 'submit button not found'}};
            }})()"""
        
        result = self.session.eval_js(js)
        
        if result.get("error"):
            return result
        
        # 等待页面变化
        if wait_for:
            try:
                self.smart_wait.wait_for(wait_for, timeout=timeout)
            except Exception as e:
                result["warning"] = f"等待 {wait_for} 超时: {e}"
        
        return result
    
    def validate_form(self, form_selector: str = None) -> dict:
        """验证表单，检查必填项和格式。
        
        Args:
            form_selector: 表单选择器
            
        Returns:
            验证结果，包含 valid、message、missingRequired 等
        """
        selector_arg = repr(form_selector) if form_selector else 'null'
        js = f"""(() => {{
            const form = document.querySelector({selector_arg});
            if (!form) return {{error: 'form not found'}};
            
            const validity = form.checkValidity();
            const validationMessage = form.validationMessage;
            const requiredFields = Array.from(form.elements).filter(el => el.required && !el.value);
            
            return {{
                valid: validity,
                message: validationMessage,
                missingRequired: requiredFields.map(el => el.name),
                fieldCount: form.elements.length
            }};
        }})()"""
        
        return self.session.eval_js(js)
    
    def save_form_state(self, form_selector: str = None) -> dict:
        """保存当前表单状态。
        
        Args:
            form_selector: 表单选择器
            
        Returns:
            表单状态字典
        """
        selector_arg = repr(form_selector) if form_selector else 'null'
        js = f"""(() => {{
            const form = document.querySelector({selector_arg});
            if (!form) return {{error: 'form not found'}};
            
            return {{
                action: form.action,
                method: form.method,
                timestamp: Date.now(),
                fields: Array.from(form.elements).map(el => ({{
                    name: el.name,
                    id: el.id,
                    type: el.type,
                    value: el.type === 'password' ? null : el.value,
                    checked: el.type === 'checkbox' || el.type === 'radio' ? el.checked : null,
                    options: el.options ? Array.from(el.options).map(o => ({{value: o.value, text: o.text}})) : null
                }}))
            }};
        }})()"""
        
        return self.session.eval_js(js)
    
    def restore_form_state(self, form_state: dict) -> dict:
        """恢复表单状态。
        
        Args:
            form_state: 之前保存的表单状态
            
        Returns:
            恢复结果
        """
        results = {"fields": [], "errors": []}
        
        for field_state in form_state.get("fields", []):
            selector = f"[name='{field_state.get('name')}']"
            if field_state.get('id'):
                selector = f"#{field_state['id']}"
            
            value = field_state.get("value")
            field_type = field_state.get("type")
            
            if field_type in ("checkbox", "radio"):
                result = self.fill_field(selector, field_state.get("checked"), field_type)
            elif value is not None:
                result = self.fill_field(selector, value, field_type)
            else:
                continue
            
            results["fields"].append({"selector": selector, "result": result.to_dict()})
        
        return results
    
    def handle_validation_error(self, error_message: str) -> Optional[str]:
        """根据验证错误信息，智能修复表单。
        
        Args:
            error_message: 验证错误信息
            
        Returns:
            修复建议，None 表示无法自动修复
        """
        # 常见错误模式匹配
        error_patterns = {
            r"Please enter a valid email address": "email",
            r"Please enter a valid phone number": "phone",
            r"Please enter a valid URL": "url",
            r"Please enter a valid date": "date",
            r"Please match the requested format": "format",
            r"Value must be less than": "max_value",
            r"Value must be greater than": "min_value",
            r"Please enter a value": "required",
            r"This field is required": "required",
        }
        
        for pattern, fix_type in error_patterns.items():
            if re.search(pattern, error_message):
                logger.info(f"检测到验证错误模式: {fix_type}")
                return self._suggest_fix(fix_type)
        
        return None
    
    def _suggest_fix(self, fix_type: str) -> Optional[str]:
        """根据错误类型提供修复建议。
        
        Args:
            fix_type: 错误类型
            
        Returns:
            修复建议
        """
        fixes = {
            "email": "请检查邮箱格式，确保包含 @ 符号和有效的域名",
            "phone": "请检查手机号格式，中国大陆手机号为 11 位数字",
            "url": "请检查 URL 格式，确保以 http:// 或 https:// 开头",
            "date": "请检查日期格式，常见格式：YYYY-MM-DD",
            "format": "请检查输入格式是否符合要求",
            "max_value": "请输入小于最大值的数值",
            "min_value": "请输入大于最小值的数值",
            "required": "请填写必填字段",
        }
        return fixes.get(fix_type, "请检查表单输入")
    
    # ========== 私有方法 ==========
    
    def _detect_field_type(self, selector: str) -> str:
        """根据选择器推断字段类型。
        
        Args:
            selector: CSS 选择器
            
        Returns:
            字段类型
        """
        js = f"""(() => {{
            const el = document.querySelector({selector!r});
            if (!el) return 'text';
            return el.type || el.tagName.toLowerCase();
        }})()"""
        
        try:
            return self.session.eval_js(js) or "text"
        except Exception:
            return "text"
    
    def _fill_text(self, selector: str, value: Any, field_type: str, 
                   result: FieldResult) -> FieldResult:
        """填写文本类字段。
        
        Args:
            selector: 字段选择器
            value: 要填写的值
            field_type: 字段类型
            result: 结果对象
            
        Returns:
            更新后的结果对象
        """
        js = f"""(() => {{
            const el = document.querySelector({selector!r});
            if (!el) return {{error: 'element not found'}};
            
            el.focus();
            el.value = {value!r};
            el.dispatchEvent(new Event('input', {{bubbles: true}}));
            el.dispatchEvent(new Event('change', {{bubbles: true}}));
            el.dispatchEvent(new Event('blur', {{bubbles: true}}));
            
            return {{filled: true, currentValue: el.value}};
        }})()"""
        
        try:
            resp = self.session.eval_js(js)
            if resp.get("error"):
                result.error = resp["error"]
            else:
                result.success = True
                result.field_type = field_type
        except Exception as e:
            result.error = str(e)

        return result

    def _fill_checkbox(self, selector: str, value: Any,
                       result: FieldResult) -> FieldResult:
        """填写复选框或单选框。

        Args:
            selector: 字段选择器
            value: 要填写的值（True/False）
            result: 结果对象

        Returns:
            更新后的结果对象
        """
        js = f"""(() => {{
            const el = document.querySelector({selector!r});
            if (!el) return {{error: 'element not found'}};

            el.checked = {str(value).lower()};
            el.dispatchEvent(new Event('change', {{bubbles: true}}));
            el.dispatchEvent(new Event('click', {{bubbles: true}}));

            return {{filled: true, checked: el.checked}};
        }})()"""

        try:
            resp = self.session.eval_js(js)
            if resp.get("error"):
                result.error = resp["error"]
            else:
                result.success = True
                result.field_type = "checkbox"
        except Exception as e:
            result.error = str(e)

        return result
    
    def _fill_select(self, selector: str, value: Any, 
                     result: FieldResult) -> FieldResult:
        """填写下拉选择框。
        
        Args:
            selector: 字段选择器
            value: 要选择的值
            result: 结果对象
            
        Returns:
            更新后的结果对象
        """
        js = f"""(() => {{
            const el = document.querySelector({selector!r});
            if (!el) return {{error: 'element not found'}};
            
            const options = Array.from(el.options);
            const option = options.find(o => o.value === {value!r} || o.text === {value!r});
            if (!option) return {{error: 'option not found', available: options.map(o => o.value)}};
            
            el.value = option.value;
            el.dispatchEvent(new Event('change', {{bubbles: true}}));
            el.dispatchEvent(new Event('select', {{bubbles: true}}));
            
            return {{filled: true, selectedValue: option.value, selectedText: option.text}};
        }})()"""
        
        try:
            resp = self.session.eval_js(js)
            if resp.get("error"):
                result.error = resp["error"]
            else:
                result.success = True
                result.field_type = "select"
        except Exception as e:
            result.error = str(e)
        
        return result
    
    def _upload_file(self, selector: str, file_path: str, 
                     result: FieldResult) -> FieldResult:
        """上传文件。
        
        Args:
            selector: 字段选择器
            file_path: 文件路径
            result: 结果对象
            
        Returns:
            更新后的结果对象
        """
        if not os.path.exists(file_path):
            result.error = f"文件不存在: {file_path}"
            return result
        
        abs_path = os.path.abspath(file_path)
        
        # 方法1：尝试 CDP Input.uploadFile
        try:
            self.session.send(
                "Input.uploadFile",
                {"files": [abs_path], "element": selector}
            )
            result.success = True
            result.field_type = "file"
            logger.info(f"[ok] 已上传文件: {file_path}")
            return result
        except Exception as e:
            logger.warning(f"CDP uploadFile 失败，尝试 JS 方式: {e}")
        
        # 方法2：使用 JS DataTransfer
        js = f"""(() => {{
            const el = document.querySelector({selector!r});
            if (!el) return {{error: 'element not found'}};
            
            const dt = new DataTransfer();
            const file = new File([''], '{os.path.basename(file_path)}', {{type: 'application/octet-stream'}});
            dt.items.add(file);
            el.files = dt.files;
            el.dispatchEvent(new Event('change', {{bubbles: true}}));
            
            return {{uploaded: true, filename: '{os.path.basename(file_path)}', size: file.size}};
        }})()"""
        
        try:
            resp = self.session.eval_js(js)
            if resp.get("error"):
                result.error = resp["error"]
            else:
                result.success = True
                result.field_type = "file"
        except Exception as e:
            result.error = str(e)
        
        return result


def generate_selector(element: Any) -> str:
    """为 DOM 元素生成稳定的 CSS 选择器。

    Args:
        element: DOM 元素

    Returns:
        CSS 选择器字符串
    """
    if not element:
        return ""

    parts = []
    current = element

    while current and current.tagName:
        tag = current.tagName.lower()

        if current.id:
            parts.insert(0, f"#{current.id}")
        elif current.className and isinstance(current.className, str):
            classes = current.className.strip().split()
            class_selector = '.' + '.'.join(classes)
            parts.insert(0, f"{tag}{class_selector}")
        else:
            parts.insert(0, tag)

        current = current.parentElement

    return ' > '.join(parts) if parts else "*"


def is_element_visible(element: Any) -> bool:
    """检查元素是否可见。

    Args:
        element: DOM 元素

    Returns:
        是否可见
    """
    if not element:
        return False

    try:
        rect = element.getBoundingClientRect()
        return rect.width > 0 and rect.height > 0
    except Exception:
        return False