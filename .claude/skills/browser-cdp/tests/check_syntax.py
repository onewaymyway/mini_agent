#!/usr/bin/env python3
"""检查并修复 test_core_scenarios.py 的语法错误"""
import ast
import sys

path = '.claude/skills/browser-cdp/tests/templates/test_core_scenarios.py'

try:
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    ast.parse(content)
    print('Syntax OK')
except SyntaxError as e:
    print(f'SyntaxError: {e}')
    print(f'Line {e.lineno}: {e.text!r}')
    lines = content.split('\n')
    if e.lineno:
        print(f'\nContext (lines {max(1,e.lineno-2)}-{e.lineno+2}):')
        for i in range(max(0,e.lineno-3), min(len(lines), e.lineno+1)):
            marker = '>>>' if i == e.lineno - 1 else '   '
            print(f'{marker} {i+1}: {lines[i]!r}')
