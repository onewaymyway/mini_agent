#!/usr/bin/env python3
"""诊断 test_core_scenarios.py 语法错误根源"""
import ast

path = '.claude/skills/browser-cdp/tests/templates/test_core_scenarios.py'

with open(path, 'rb') as f:
    raw = f.read()

# Show bytes around each LOGIN-0X occurrence
import re
for m in re.finditer(b'LOGIN-0\d', raw):
    start = max(0, m.start() - 30)
    end = min(len(raw), m.end() + 30)
    print(f'Offset {m.start()}: {repr(raw[start:end])}')

# Try to find the actual syntax issue by checking each line
print('\n=== Checking each line ===')
lines = raw.split(b'\r\n')
for i, line in enumerate(lines, 1):
    # Skip empty lines and comments
    stripped = line.strip()
    if not stripped or stripped.startswith(b'#'):
        continue
    # Check for any numeric literal with leading zero
    # Look for patterns like "-0" followed by digit that might be interpreted as number
    text = line.decode('utf-8', errors='replace')
    # Find potential numeric literals with leading zeros
    nums = re.findall(r'\b0\d+\b', text)
    if nums:
        print(f'L{i}: nums={nums}, text={text[:80]}')
