---
name: project-stats
description: 项目统计工具，包括代码行数统计、文档统计、文件数量统计等。当用户说"项目统计"、"代码行数"、"文档数量"、"统计项目"时使用。
triggers: 项目统计, 代码行数, 文档数量, 统计项目, stats, count, 统计, 项目规模
---

# 项目统计 Skill

## 概述

提供项目维度的统计功能，包括代码行数、文档数量、文件分布等。

## 功能模块

### 1. 代码行数统计

使用本 skill 目录下的 `count_lines.py` 脚本统计 Python 代码行数，自动区分项目实际代码与测试代码。

```bash
python3 .claude/skills/project-stats/count_lines.py
```

输出包含：
- 📦 项目实际代码（src/、.claude/skills/ 等）
- 🧪 测试代码（tests/ 目录）
- 📝 Markdown 文档（文件数量、总行数、总字数）
- 📊 汇总统计（文件数、总行数、代码/注释/空行比例）

### 2. 文档统计

统计项目中的 Markdown 文档数量、行数和字数，已集成到 `count_lines.py` 脚本中：

```bash
python3 .claude/skills/project-stats/count_lines.py
```

输出包含：
- 📝 Markdown 文档（文件数量、总行数、总字数、Top N 文件按字数排序）
- 自动排除 `.git`、`.agent`、`venv` 等目录

### 3. 文件数量统计

统计各类文件的数量分布：

```bash
find . -type f ! -path "./.git/*" ! -path "./.agent/*" ! -path "./venv/*" | sed 's/.*\.//' | sort | uniq -c | sort -rn
```

## 使用场景

- 用户询问"项目有多大"、"有多少代码" → 使用代码行数统计
- 用户询问"有多少文档"、"文档数量" → 使用文档统计
- 用户询问"项目结构"、"文件分布" → 使用文件数量统计
- 用户询问"项目统计" → 根据上下文选择合适的统计方式

## 注意事项

- 统计脚本已排除 `__pycache__`、`.git`、`venv`、`node_modules` 等目录
- 如需统计其他目录，可传入参数：`python3 .claude/skills/project-stats/count_lines.py /path/to/dir`
- 后续可扩展更多统计功能（如：依赖数量、测试覆盖率等）