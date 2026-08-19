#!/usr/bin/env python3
"""修复 test_core_scenarios.py 语法错误"""
import re

path = '.claude/skills/browser-cdp/tests/templates/test_core_scenarios.py'

with open(path, 'rb') as f:
    content = f.read()

# Convert to string
text = content.decode('utf-8')

# Find and show the area around line 260
lines = text.split('\r\n')
print(f'Line 260: {repr(lines[259])}')

# Replace LOGIN-0X pattern (where X is single digit) with LOGIN-X in docstrings
# But NOT inside regular strings like "#date", "2026-08-08"
# The issue is: "LOGIN-02" has leading zero which Python's parser may misinterpret
# Solution: remove the leading zero -> "LOGIN-2" or just use "LOGIN-002" 

# Actually, let's just check if removing the numeric part helps
# Try replacing LOGIN-002 with just LOGIN-A (no digits)
# Or better: replace with LOGIN002 (no dash)

# Let's try: change all LOGIN-0X to LOGIN-NOX (remove leading zero issue)
# Actually the real fix: the string content "LOGIN-002" should be fine
# Let me check if there's an invisible character

for i, line in enumerate(lines, 1):
    if 'LOGIN-0' in line and i >= 250 and i <= 270:
        print(f'L{i}: {repr(line)}')
        # Check each byte
        for j, ch in enumerate(line):
            if ord(ch) < 32 and ch not in '\r\n\t':
                print(f'  Hidden char at pos {j}: U+{ord(ch):04X}')

# Fix: replace all docstring patterns with LOGIN-0X (single digit after 0)
# to LOGIN-X (just the digit, no leading zero)
# Use regex to find """LOGIN-0(\d): in docstrings
defix = re.sub(
    r'("""LOGIN-0)(\d)(:)',
    r'\1\2\3',  # Keep as-is but let's try LOGIN-NO instead
    text
)

# Actually let's just replace with no leading zero in the number
# Login-02 -> Login-2 (but this changes meaning)
# Better: Login-002 -> Login-002 (already fixed by patch)

# The REAL fix: check if there's a stray character before the docstring
# that makes Python think we're in a numeric context

# Let me try: just strip all spaces and check
fixed = text
# Replace "LOGIN-002" with "LOGIN-XX" to avoid any numeric interpretation
fixed = re.sub(r'LOGIN-0*(\d+)', r'LOGIN-\1', fixed)

with open(path, 'w', encoding='utf-8', newline='\n') as f:
    f.write(fixed)

print('\nAfter fix:')
lines = fixed.split('\n')
for i in range(257, 263):
    print(f'L{i+1}: {repr(lines[i])}')

# Verify
import ast
try:
    ast.parse(fixed)
    print('\nParse OK!')
except SyntaxError as e:
    print(f'\nParse still failed: {e}')
