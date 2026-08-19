#!/usr/bin/env python3
"""合并wait_strategy文件的脚本"""
import os

# 文件路径
base_path = 'E:/codes/mini_claude_code/.claude/skills/browser-cdp/src/core'
part1_file = os.path.join(base_path, 'wait_strategy.py')
part2_file = os.path.join(base_path, 'wait_strategy_part2.py')
output_file = os.path.join(base_path, 'wait_strategy_combined.py')

# 读取两部分内容
with open(part1_file, 'r', encoding='utf-8') as f:
    part1 = f.read()

with open(part2_file, 'r', encoding='utf-8') as f:
    part2 = f.read()

# 写入合并后的文件
with open(output_file, 'w', encoding='utf-8') as f:
    f.write(part1)
    f.write('\n\n')
    f.write(part2)

print(f'合并完成: {output_file}')
print(f'第一部分: {len(part1)} 字符')
print(f'第二部分: {len(part2)} 字符')
print(f'总长度: {len(part1) + len(part2)} 字符')

# 删除临时文件
os.remove(part1_file)
os.remove(part2_file)
print('临时文件已清理')

# 重命名为最终文件名
final_file = os.path.join(base_path, 'wait_strategy.py')
os.rename(output_file, final_file)
print(f'重命名为: {final_file}')
