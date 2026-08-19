#!/usr/bin/env python3
"""深入诊断语法错误"""
import ast
import re

path = '.claude/skills/browser-cdp/tests/templates/test_core_scenarios.py'

with open(path, 'rb') as f:
    raw = f.read()

# Find the exact bytes around line 260
lines = raw.split(b'\r\n')
print(f'Total lines: {len(lines)}')
print(f'L260 bytes: {repr(lines[259])}')
print(f'L260 hex: {lines[259].hex()}')

# Check what's before line 260
print(f'\nL259 bytes: {repr(lines[258])}')
print(f'L261 bytes: {repr(lines[260])}')

# Try parsing line by line to find where the issue starts
print('\n=== Line-by-line parse test ===')
for i in range(240, 270):
    try:
        ast.parse(lines[i].decode('utf-8'))
        status = 'OK'
    except SyntaxError as e:
        status = f'FAIL: {e.msg}'
    print(f'L{i+1}: {status} - {repr(lines[i][:60])}')
