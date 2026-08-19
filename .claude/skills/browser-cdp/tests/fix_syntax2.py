#!/usr/bin/env python3
"""修复 test_core_scenarios.py 所有语法错误"""
import re
import ast

path = '.claude/skills/browser-cdp/tests/templates/test_core_scenarios.py'

with open(path, 'rb') as f:
    content = f.read()

text = content.decode('utf-8')

# Replace all CRLF with LF for consistent parsing
text = text.replace('\r\n', '\n')

# Fix all LOGIN-0X patterns (remove leading zero in number)
# LOGIN-01 -> LOGIN-1, LOGIN-02 -> LOGIN-2, etc.
text = re.sub(r'LOGIN-0(\d)', r'LOGIN-\1', text)

# Also fix FORM-0X, NAV-0X, SEARCH-0X, EXTRA-0X, INTER-0X
for prefix in ['FORM', 'NAV', 'SEARCH', 'EXTRA', 'INTER']:
    text = re.sub(f'{prefix}-0(\d)', f'{prefix}-\1', text)

# Check for any remaining leading-zero numeric literals in docstrings
lines = text.split('\n')
print(f'Total lines: {len(lines)}')
for i, line in enumerate(lines, 1):
    if 'LOGIN' in line or 'FORM' in line or 'NAV' in line:
        if '0' in line and ':' in line:
            # Check if it's a docstring with leading zero
            if line.strip().startswith('"""'):
                print(f'L{i}: {repr(line[:80])}')

with open(path, 'w', encoding='utf-8', newline='\n') as f:
    f.write(text)

# Verify
try:
    ast.parse(text)
    print('\nParse OK!')
except SyntaxError as e:
    print(f'\nParse failed at L{e.lineno}: {e.msg}')
    print(f'  {repr(e.text)}')
