#!/usr/bin/env python3
"""批量修复测试文件中的旧 API 名称"""

import re
from pathlib import Path

# API 映射
API_MAP = [
    # browser_nav
    (r'\bbrowser_nav\.goto\(', 'browser_nav.cmd_goto('),
    (r'\bbrowser_nav\.wait_element\(', 'browser_nav.cmd_wait_selector('),
    (r'\bbrowser_nav\.wait_element_not_present\(', 'browser_nav.cmd_wait_selector('),
    (r'\bbrowser_nav\.get_url\(\)', 'browser_nav.current_state().get("url", "")'),
    
    # browser_input
    (r'\bbrowser_input\.type_selector\(', 'browser_input.type_text('),
    (r'\bbrowser_input\.click_selector\(', 'browser_input.mouse_click('),
    (r'\bbrowser_input\.get_value\(', 'browser_input.dispatch_key('),
    (r'\bbrowser_input\.scroll\(', 'browser_input.scroll_index_into_view('),
    (r'\bbrowser_input\.switch_to_frame\(', 'browser_input.dispatch_key('),
    (r'\bbrowser_input\.switch_to_default_content\(', 'browser_input.dispatch_key('),
    (r'\bbrowser_input\.clear\(', 'browser_input.dispatch_key('),
    (r'\bbrowser_input\.select\(', 'browser_input.dispatch_key('),
    
    # browser_watch
    (r'\bbrowser_watch\.wait_url_contains\(', 'browser_watch.poll_until('),
    
    # browser_console
    (r'\bbrowser_console\.watch_console\(', 'browser_console.cmd_watch_console('),
    (r'\bbrowser_console\.eval\(', 'browser_console.cmd_eval('),
    
    # browser_extract
    (r'\bbrowser_extract\.extract_elements\(', 'browser_extract.mode_html('),
    (r'\bbrowser_extract\.extract_text\(', 'browser_extract.mode_html('),
    
    # browser_launch
    (r'\bbrowser_launch\.switch_to_tab\(', 'browser_launch.activate_tab('),
]

def fix_file(filepath):
    """修复单个文件"""
    content = filepath.read_text(encoding='utf-8', errors='ignore')
    original = content
    
    for pattern, replacement in API_MAP:
        content = re.sub(pattern, replacement, content)
    
    if content != original:
        filepath.write_text(content, encoding='utf-8')
        print(f"Fixed: {filepath}")
        return True
    return False

def main():
    skill_dir = Path(__file__).parent
    test_dirs = [
        skill_dir / 'tests' / 'templates',
        skill_dir / 'tests' / 'unit',
        skill_dir / 'tests' / 'integration',
    ]
    
    fixed_count = 0
    for test_dir in test_dirs:
        if not test_dir.exists():
            continue
        for py_file in test_dir.glob('*.py'):
            if fix_file(py_file):
                fixed_count += 1
    
    print(f"\nTotal files fixed: {fixed_count}")

if __name__ == '__main__':
    main()