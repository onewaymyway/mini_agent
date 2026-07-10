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
python -m error_log_analyzer --days 7 --by-date

# 显示 Top 20 高频错误
python -m error_log_analyzer --top 20

# 生成完整 HTML 报告
python -m error_log_analyzer --report --output error_report.html
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
