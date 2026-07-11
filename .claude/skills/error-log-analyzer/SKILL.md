---
name: error-log-analyzer
description: 错误日志统计分析技能，用于分析 ~/.agent/logs/error.jsonl 文件，按日期统计不同错误类型的数量，生成错误趋势报告。当用户需要分析错误日志、统计错误频率、排查高频错误时使用。
triggers: error log, error analysis, error statistics, 错误日志, 错误统计, 错误分析, error.jsonl
---

# Error Log Analyzer Skill

## 概述

本技能用于分析 mini_agent 项目的全局错误日志文件 `~/.agent/logs/error.jsonl`，该文件由 `mini_agent.errors.log_exception()` 记录，格式为 JSON Lines（每行一个完整的 JSON 记录）。

## 日志格式

每行包含以下字段：
- `ts`: ISO 8601 时间戳 (UTC)
- `pid`: 进程 ID
- `thread`: 线程名
- `where`: 发生位置 (模块:行号 或 模块.函数)
- `exc_type`: 异常类型名 (如 `TypeError`, `AttributeError`, `ConnectionResetError`)
- `message`: 异常消息
- `traceback`: 完整堆栈跟踪字符串
- `extra` (可选): 附加上下文字段

## 核心功能

### 1. 按日期统计错误分布
- 解析 `ts` 字段提取日期 (YYYY-MM-DD)
- 按日期分组，统计每日总错误数
- 按日期 + 异常类型分组，统计每种错误在每天的出现次数

### 2. 错误类型频率排行
- 统计所有异常类型的总出现次数
- 按频率降序排列，识别高频错误

### 3. 错误位置热点分析
- 按 `where` 字段统计，找出高频报错位置
- 结合 `exc_type` 定位具体问题代码

### 4. 时间趋势分析
- 按小时/天粒度展示错误趋势
- 识别错误爆发时段

## 使用示例

```python
from error_log_analyzer import ErrorLogAnalyzer

analyzer = ErrorLogAnalyzer()

# 按日期统计每种错误的数量
stats = analyzer.stats_by_date()
for date, errors in stats.items():
    print(f"{date}: {errors}")

# 获取高频错误 Top 10
top_errors = analyzer.top_errors(10)
for exc_type, count in top_errors:
    print(f"{exc_type}: {count}")

# 生成完整报告
report = analyzer.generate_report()
print(report)
```

## CLI 使用

```bash
# 统计最近 7 天按日期分组的错误
python .claude/skills/error-log-analyzer/analyzer.py --days 7 --by-date

# 显示 Top 20 高频错误
python .claude/skills/error-log-analyzer/analyzer.py --top 20

# 生成完整文本报告并保存到文件
python .claude/skills/error-log-analyzer/analyzer.py --report --output ./temp/error_report.txt

# 只分析最新日期的错误（含详细堆栈）
python .claude/skills/error-log-analyzer/analyzer.py --latest-date --output ./temp/error_report_latest.txt

# 只显示最新日期的错误统计摘要（不含详细堆栈）
python .claude/skills/error-log-analyzer/analyzer.py --latest-date-summary
```

## 常见错误与避坑指南

### ❌ 错误做法：手动查找日志文件路径
```bash
# 错误：在项目目录下找不到 ~/.agent/logs/error.jsonl
find . -name "error.jsonl"
ls ~/.agent/logs/error.jsonl  # Windows 下不存在该路径
```

### ✅ 正确做法：直接使用分析器默认路径
分析器 `ErrorLogAnalyzer` 默认会自动解析 `~/.agent/logs/error.jsonl`（Windows 下为 `C:\Users\<用户名>\.agent\logs\error.jsonl`），**无需手动指定路径**。

```bash
# 直接运行，自动使用默认路径
python .claude/skills/error-log-analyzer/analyzer.py --report
```

### ❌ 错误做法：使用错误的模块路径
```bash
# 错误：python -m error_log_analyzer 找不到模块
python -m error_log_analyzer --report
```

### ✅ 正确做法：使用完整脚本路径
```bash
# 正确：直接运行 analyzer.py 脚本
python .claude/skills/error-log-analyzer/analyzer.py --report
```

### ❌ 错误做法：在项目根目录下寻找 .agent 目录
```bash
# 错误：项目根目录下没有 .agent 目录
ls .agent/logs/error.jsonl
```

### ✅ 正确认知：日志在用户主目录下
- Windows: `C:\Users\<用户名>\.agent\logs\error.jsonl`
- Linux/Mac: `~/.agent/logs/error.jsonl`
- 这是 mini_agent 全局共享的错误日志，不在项目目录内

## 核心参数速查表

| 参数 | 说明 | 示例 |
|------|------|------|
| `--log` | 指定日志文件路径（可选，默认自动检测用户主目录下的 `~/.agent/logs/error.jsonl`） | `--log C:\path\to\error.jsonl` |
| `--days` | 只分析最近 N 天 | `--days 7` |
| `--by-date` | 按日期分组显示错误类型统计 | `--by-date` |
| `--top` | 显示高频错误 Top N | `--top 20` |
| `--where` | 显示高频报错位置 Top N | `--where 10` |
| `--report` | 生成完整报告（默认行为） | `--report` |
| `--output` | 报告输出文件路径 | `--output ./report.txt` |
| `--hourly` | 显示小时分布 | `--hourly` |
| `--no-details` | 不包含详细错误信息和堆栈示例 | `--no-details` |
| `--by-date-details` | 按日期显示详细错误信息 | `--by-date-details` |
| `--latest-date` | **只分析最新日期的错误（含详细堆栈）** | `--latest-date` |
| `--latest-date-summary` | **只显示最新日期的错误统计摘要** | `--latest-date-summary` |

## 关键经验总结：避免再次犯错

### 1. 日志文件位置认知偏差
- **错误认知**：以为 `~/.agent/logs/error.jsonl` 在项目目录下
- **正确认知**：这是 mini_agent 全局共享的错误日志，位于**用户主目录**
  - Windows: `C:\Users\<用户名>\.agent\logs\error.jsonl`
  - Linux/Mac: `~/.agent/logs/error.jsonl`
- **解决方案**：分析器已内置 `Path.home()` 自动检测，**无需手动指定路径**，直接运行即可

### 2. Python 模块运行方式错误
- **错误做法**：`python -m error_log_analyzer`（模块不在 sys.path 中）
- **正确做法**：`python .claude/skills/error-log-analyzer/analyzer.py`（使用完整脚本路径）

### 3. Windows 路径处理
- 使用 `Path.home()` 自动获取用户主目录，跨平台兼容
- 不要硬编码 `~` 或 `$HOME`，Python 的 `Path.home()` 会正确处理

### 4. 报告输出目录规范
- 临时生成的报告文件应放在 `./temp/` 目录下
- 使用 `--output ./temp/error_report.txt` 格式

### 5. 快速上手命令（推荐收藏）
```bash
# 一键生成完整报告（自动检测日志路径）
python .claude/skills/error-log-analyzer/analyzer.py --report --output ./temp/error_report.txt

# 一键查看最新日期错误（含堆栈）
python .claude/skills/error-log-analyzer/analyzer.py --latest-date --output ./temp/error_report_latest.txt

# 一键查看最新日期摘要（不含堆栈，快速概览）
python .claude/skills/error-log-analyzer/analyzer.py --latest-date-summary
```

## 实现要点

1. **流式处理**: 文件可能很大 (10MB+，轮转保留 5 个)，使用生成器逐行读取，避免一次性加载内存
2. **日期解析**: 使用 `datetime.fromisoformat()` 解析 ISO 格式时间戳，提取日期部分
3. **聚合统计**: 使用 `collections.Counter` 和 `defaultdict` 高效聚合
4. **时区处理**: 日志使用 UTC 时间，按需转换为本地时区显示
5. **错误容错**: 单行 JSON 解析失败不应中断整体分析，记录并跳过

## 输出格式

### 按日期统计示例
```
2026-07-10:
  TypeError: 45
  AttributeError: 12
  ConnectionResetError: 8
  URLError: 8
  Total: 73

2026-07-09:
  TypeError: 32
  ConnectionResetError: 15
  Total: 47
```

### 高频错误 Top N 示例
```
Top 10 Errors:
1. TypeError: 127
2. ConnectionResetError: 45
3. AttributeError: 38
4. URLError: 22
5. KeyError: 15
```
