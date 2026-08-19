#!/usr/bin/env python3
"""诊断和修复 test_core_scenarios.py 语法错误"""
import ast
import sys

path = '.claude/skills/browser-cdp/tests/templates/test_core_scenarios.py'

with open(path, 'rb') as f:
    raw = f.read()

# Show bytes around line 260
lines_raw = raw.split(b'\r\n')
print(f'Total lines (CRLF split): {len(lines_raw)}')
for i in range(257, 263):
    print(f'L{i+1}: {repr(lines_raw[i])}')

# Try parsing with different line endings
content = raw.decode('utf-8')
try:
    ast.parse(content)
    print('\nParse OK (as-is)')
except SyntaxError as e:
    print(f'\nParse failed: {e}')
    # Try replacing \r\n with \n
    content_n = content.replace('\r\n', '\n')
    try:
        ast.parse(content_n)
        print('Parse OK (after CRLF->LF)')
    except SyntaxError as e2:
        print(f'Parse still failed: {e2}')

# Fix: replace LOGIN-0X with LOGIN-X (remove leading zero)
print('\nSearching for LOGIN patterns...')
for i, line in enumerate(lines_raw, 1):
    if b'LOGIN-0' in line and b'LOGIN-00' not in line:
        print(f'  L{i}: {repr(line)}')
