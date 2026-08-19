#!/usr/bin/env python3
"""修复 test_core_scenarios.py 所有问题（包括非打印字符）"""
import re
import ast

path = '.claude/skills/browser-cdp/tests/templates/test_core_scenarios.py'

with open(path, 'rb') as f:
    raw = f.read()

# Remove all non-printable characters except common whitespace
text = raw.decode('utf-8', errors='replace')
# Remove control characters (except \n, \r, \t)
text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)

# Replace CRLF with LF
text = text.replace('\r\n', '\n')

# Fix all LOGIN-0X patterns (remove leading zero in number)
for prefix in ['LOGIN', 'FORM', 'NAV', 'SEARCH', 'EXTRA', 'INTER']:
    text = re.sub(f'{prefix}-0(\d)', f'{prefix}-\1', text)

with open(path, 'w', encoding='utf-8', newline='\n') as f:
    f.write(text)

# Verify
try:
    ast.parse(text)
    print('Parse OK!')
except SyntaxError as e:
    print(f'Parse failed at L{e.lineno}: {e.msg}')
    print(f'  {repr(e.text)}')
