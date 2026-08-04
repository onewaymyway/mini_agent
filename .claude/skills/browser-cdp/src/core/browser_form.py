"""
browser_form.py - 复杂表单自动化处理

支持：
- 多步骤表单填写
- 动态表单（AJAX 加载的选项）
- 文件上传
- 复选框/单选框/下拉框
- 表单验证
- 表单状态保存/恢复

用法：
  python browser_form.py --tab <id> --fill-form forms.json
  python browser_form.py --tab <id> --fill-selector "input[name='q']" --text "hello"
  python browser_form.py --tab <id> --upload-file --selector "input[type='file']" --file /path/to/file
  python browser_form.py --tab <id> --submit-form --selector "form"
  python browser_form.py --tab <id> --save-form --selector "form" --out saved_form.json
  python browser_form.py --tab <id> --restore-form --in saved_form.json
"""
from __future__ import annotations

import argparse
import json
import os
import time
from typing import Any

from src.core.utils import add_connection_args, get_session, die
from src.core.smart_wait import SmartWait


# 表单定义格式示例：
# {
#   "fields": [
#     {"selector": "input[name='username']", "value": "john"},
#     {"selector": "input[name='password']", "value": "secret", "type": "password"},
#     {"selector": "input[name='agree']", "value": true, "type": "checkbox"},
#     {"selector": "select[name='country']", "value": "CN"},
#     {"selector": "input[type='file']", "value": "/path/to/file.pdf", "type": "file"},
#     {"selector": "textarea[name='bio']", "value": "Hello world"}
#   ],
#   "submit": {"selector": "button[type='submit']"},
#   "wait_for": "url",  # 提交后等待的页面变化
#   "wait_url_contains": "success"  # 或等待 URL 包含某字符串
# }


def fill_field(session, selector: str, value: Any, field_type: str = None):
    """填写单个表单字段。"""
    js = f"""(() => {{
        const el = document.querySelector({selector!r});
        if (!el) return {{error: 'element not found'}};
        
        const type = el.type || el.tagName.toLowerCase();
        
        if (type === 'checkbox' || type === 'radio') {{
            el.checked = {str(value).lower()};
            el.dispatchEvent(new Event('change', {{bubbles: true}}));
            return {{filled: true, type: 'checkbox'}};
        }}
        
        if (type === 'file') {{
            // 文件上传需要特殊处理
            return {{needs_file_upload: true, selector: {selector!r}}};
        }}
        
        if (type === 'select-one' || type === 'select-multiple') {{
            const options = Array.from(el.options);
            const option = options.find(o => o.value === {value!r} || o.text === {value!r});
            if (option) {{
                el.value = option.value;
                el.dispatchEvent(new Event('change', {{bubbles: true}}));
                return {{filled: true, type: 'select'}};
            }}
            return {{error: 'option not found'}};
        }}
        
        // 文本输入
        el.focus();
        el.value = {value!r};
        el.dispatchEvent(new Event('input', {{bubbles: true}}));
        el.dispatchEvent(new Event('change', {{bubbles: true}}));
        return {{filled: true, type: type}};
    }})()"""
    
    result = session.eval_js(js)
    return result


def upload_file(session, selector: str, file_path: str):
    """上传文件到文件输入框。"""
    if not os.path.exists(file_path):
        die(f"文件不存在: {file_path}")
    
    # 使用 CDP 的 Input.uploadFile 方法
    abs_path = os.path.abspath(file_path)
    
    # 先找到元素
    js = f"""(() => {{
        const el = document.querySelector({selector!r});
        if (!el) return {{error: 'element not found'}};
        el.style.display = 'block';
        return {{found: true}};
    }})()"""
    
    result = session.eval_js(js)
    if result.get('error'):
        die(result['error'])
    
    # 使用 CDP 上传文件
    try:
        session.send(
            "Input.uploadFile",
            {"files": [abs_path], "element": selector}
        )
        print(f"[ok] 已上传文件: {file_path}")
        return {"uploaded": True, "path": abs_path}
    except Exception as e:
        # 如果 CDP 不支持 uploadFile，尝试通过 JS 设置 files
        print(f"[warn] CDP uploadFile 不可用，尝试 JS 方式: {e}")
        js = f"""(() => {{
            const el = document.querySelector({selector!r});
            const dt = new DataTransfer();
            const file = new File([''], '{os.path.basename(file_path)}', {{type: 'application/octet-stream'}});
            dt.items.add(file);
            el.files = dt.files;
            el.dispatchEvent(new Event('change', {{bubbles: true}}));
            return {{uploaded: true, filename: '{os.path.basename(file_path)}'}};
        }})()"""
        return session.eval_js(js)


def fill_form(session, form_def: dict) -> dict:
    """填写完整表单。"""
    results = {"fields": [], "errors": []}
    
    for field in form_def.get("fields", []):
        selector = field.get("selector")
        value = field.get("value")
        field_type = field.get("type")
        
        if not selector:
            results["errors"].append("字段缺少 selector")
            continue
        
        if field_type == "file" and value:
            result = upload_file(session, selector, value)
        else:
            result = fill_field(session, selector, value, field_type)
        
        results["fields"].append({"selector": selector, "result": result})
        
        if result.get("error"):
            results["errors"].append(f"{selector}: {result['error']}")
        else:
            print(f"[ok] 已填写: {selector}")
    
    return results


def submit_form(session, selector: str = None, wait_for: str = None, timeout: float = 30.0) -> dict:
    """提交表单并等待结果。"""
    if selector:
        js = f"""(() => {{
            const form = document.querySelector({selector!r});
            if (form) {{
                form.dispatchEvent(new Event('submit', {{bubbles: true, cancelable: true}}));
                return {{submitted: true, type: 'js'}};
            }}
            const btn = document.querySelector({selector!r});
            if (btn) {{
                btn.click();
                return {{submitted: true, type: 'button'}};
            }}
            return {{error: 'form or button not found'}};
        }})()"""
        result = session.eval_js(js)
    else:
        # 提交第一个表单
        js = """(() => {
            const form = document.querySelector('form');
            if (form) {
                form.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}));
                return {submitted: true};
            }
            const btn = document.querySelector('button[type="submit"]');
            if (btn) {
                btn.click();
                return {submitted: true, type: 'button'};
            }
            return {error: 'no form or submit button found'};
        })()"""
        result = session.eval_js(js)
    
    if result.get("error"):
        return result
    
    # 等待页面变化
    if wait_for:
        smart_wait = SmartWait(session)
        smart_wait.wait_for(wait_for, timeout=timeout)
    
    return result


def save_form_state(session, selector: str = None) -> dict:
    """保存当前表单状态。"""
    if selector:
        js = f"""(() => {{
            const form = document.querySelector({selector!r});
            if (!form) return {{error: 'form not found'}};
            return {{
                action: form.action,
                method: form.method,
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
    else:
        js = """(() => {
            const form = document.querySelector('form');
            if (!form) return {error: 'no form found'};
            return {
                action: form.action,
                method: form.method,
                fields: Array.from(form.elements).map(el => ({
                    name: el.name,
                    id: el.id,
                    type: el.type,
                    value: el.type === 'password' ? null : el.value,
                    checked: el.type === 'checkbox' || el.type === 'radio' ? el.checked : null,
                    options: el.options ? Array.from(el.options).map(o => ({value: o.value, text: o.text})) : null
                }))
            };
        })()"""
    
    return session.eval_js(js)


def restore_form_state(session, form_state: dict) -> dict:
    """恢复表单状态。"""
    results = {"fields": [], "errors": []}
    
    for field in form_state.get("fields", []):
        selector = f"[name='{field.get('name')}']"
        if field.get('id'):
            selector = f"# {field['id']}"
        
        value = field.get("value")
        field_type = field.get("type")
        
        if field_type in ("checkbox", "radio"):
            result = fill_field(session, selector, field.get("checked"), field_type)
        elif value is not None:
            result = fill_field(session, selector, value, field_type)
        else:
            continue
        
        results["fields"].append({"selector": selector, "result": result})
    
    return results


def validate_form(session, selector: str = None) -> dict:
    """验证表单。"""
    if selector:
        js = f"""(() => {{
            const form = document.querySelector({selector!r});
            if (!form) return {{error: 'form not found'}};
            const validity = form.checkValidity();
            const validationMessage = form.validationMessage;
            const requiredFields = Array.from(form.elements).filter(el => el.required && !el.value);
            return {{
                valid: validity,
                message: validationMessage,
                missingRequired: requiredFields.map(el => el.name)
            }};
        }})()"""
    else:
        js = """(() => {
            const form = document.querySelector('form');
            if (!form) return {error: 'no form found'};
            const validity = form.checkValidity();
            const validationMessage = form.validationMessage;
            const requiredFields = Array.from(form.elements).filter(el => el.required && !el.value);
            return {
                valid: validity,
                message: validationMessage,
                missingRequired: requiredFields.map(el => el.name)
            };
        })()"""
    
    return session.eval_js(js)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_connection_args(parser)
    
    # 表单填写
    parser.add_argument("--fill-form", metavar="JSON_FILE", help="从 JSON 文件填写表单")
    parser.add_argument("--fill-selector", help="填写单个选择器")
    parser.add_argument("--text", help="填写的文本值")
    
    # 文件上传
    parser.add_argument("--upload-file", action="store_true", help="上传文件模式")
    parser.add_argument("--file", help="要上传的文件路径")
    
    # 表单提交
    parser.add_argument("--submit-form", action="store_true", help="提交表单")
    parser.add_argument("--submit-selector", help="提交按钮选择器")
    parser.add_argument("--wait-for", default=None, choices=["load", "networkidle", "route", "stable", "ajax", "selector"], help="提交后等待策略")
    parser.add_argument("--wait-url-contains", default=None, help="等待 URL 包含此字符串")
    
    # 表单状态
    parser.add_argument("--save-form", action="store_true", help="保存表单状态")
    parser.add_argument("--save-selector", help="要保存的表单选择器")
    parser.add_argument("--out", help="保存到的文件")
    parser.add_argument("--restore-form", help="从 JSON 文件恢复表单")
    
    # 表单验证
    parser.add_argument("--validate-form", action="store_true", help="验证表单")
    parser.add_argument("--validate-selector", help="要验证的表单选择器")
    
    parser.add_argument("--timeout", type=float, default=30.0)
    
    args = parser.parse_args()
    session = get_session(args)
    
    try:
        if args.fill_form:
            with open(args.fill_form, 'r', encoding='utf-8') as f:
                form_def = json.load(f)
            results = fill_form(session, form_def)
            print_json(results)
        
        elif args.fill_selector:
            if not args.text:
                die("--fill-selector 需要配合 --text 使用")
            result = fill_field(session, args.fill_selector, args.text)
            print_json(result)
        
        elif args.upload_file:
            if not args.file:
                die("--upload-file 需要配合 --file 使用")
            if not args.fill_selector:
                die("--upload-file 需要配合 --fill-selector 使用")
            result = upload_file(session, args.fill_selector, args.file)
            print_json(result)
        
        elif args.submit_form:
            result = submit_form(
                session, 
                selector=args.submit_selector,
                wait_for=args.wait_for,
                timeout=args.timeout
            )
            if args.wait_url_contains:
                # 额外等待 URL 变化
                smart_wait = SmartWait(session)
                smart_wait.wait_for_url_contains(args.wait_url_contains, timeout=args.timeout)
            print_json(result)
        
        elif args.save_form:
            result = save_form_state(session, selector=args.save_selector)
            if args.out:
                with open(args.out, 'w', encoding='utf-8') as f:
                    json.dump(result, f, ensure_ascii=False, indent=2)
                print(f"[ok] 表单状态已保存到: {args.out}")
            else:
                print_json(result)
        
        elif args.restore_form:
            with open(args.restore_form, 'r', encoding='utf-8') as f:
                form_state = json.load(f)
            result = restore_form_state(session, form_state)
            print_json(result)
        
        elif args.validate_form:
            result = validate_form(session, selector=args.validate_selector)
            print_json(result)
        
        else:
            parser.print_help()
    
    finally:
        session.close()


if __name__ == "__main__":
    main()
