# CI/CD 流水线集成指南

## 概述

本文档说明如何将 browser-cdp skill 的评估机制集成到 CI/CD 流水线中，实现自动化测试和持续监控。

## 流水线架构

```
┌─────────────────────────────────────────────────────────────┐
│                    CI/CD Pipeline                           │
├─────────────────────────────────────────────────────────────┤
│  触发条件: 代码提交 / 定时任务 / 手动触发                    │
├─────────────────────────────────────────────────────────────┤
│  Step 1: 代码检查                                           │
│    - 语法检查                                               │
│    - 导入检查                                               │
│    - 代码风格检查                                           │
├─────────────────────────────────────────────────────────────┤
│  Step 2: 单元测试                                           │
│    - 运行现有测试用例                                       │
│    - 覆盖率检查                                             │
├─────────────────────────────────────────────────────────────┤
│  Step 3: 评估测试                                           │
│    - 运行评估测试用例                                       │
│    - 生成评估报告                                           │
├─────────────────────────────────────────────────────────────┤
│  Step 4: 结果分析                                           │
│    - 检查评分阈值                                           │
│    - 对比历史数据                                           │
│    - 生成告警                                               │
├─────────────────────────────────────────────────────────────┤
│  Step 5: 报告归档                                           │
│    - 保存评估报告                                           │
│    - 更新评估数据库                                         │
└─────────────────────────────────────────────────────────────┘
```

## GitHub Actions 配置

### 工作流文件

创建 `.github/workflows/browser-cdp-eval.yml`：

```yaml
name: Browser-CDP Evaluation

on:
  push:
    branches: [main, master]
    paths:
      - '.claude/skills/browser-cdp/**'
  pull_request:
    branches: [main, master]
  schedule:
    - cron: '0 2 * * *'  # 每天凌晨2点执行
  workflow_dispatch:

jobs:
  evaluate:
    runs-on: ubuntu-latest
    
    steps:
    - name: Checkout code
      uses: actions/checkout@v3
    
    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.11'
    
    - name: Install dependencies
      run: |
        pip install -r requirements.txt
    
    - name: Run syntax check
      run: |
        python -m py_compile .claude/skills/browser-cdp/scripts/*.py
    
    - name: Run evaluation tests
      run: |
        python .claude/skills/browser-cdp/scripts/run_test_cases.py \
          --website https://www.baidu.com \
          --output-dir output/eval_results
    
    - name: Check evaluation score
      run: |
        python .claude/skills/browser-cdp/scripts/check_eval_score.py \
          --min-score 80 \
          --min-pass-rate 80
    
    - name: Upload evaluation report
      uses: actions/upload-artifact@v3
      with:
        name: eval-report
        path: output/eval_results/
    
    - name: Notify on failure
      if: failure()
      run: |
        echo "Evaluation failed! Check the report for details."
```

## 定时任务配置

### Cron 表达式

| 任务 | Cron 表达式 | 说明 |
|------|-------------|------|
| 每日评估 | `0 2 * * *` | 每天凌晨2点执行 |
| 每周报告 | `0 3 * * 0` | 每周日凌晨3点生成周报 |
| 每月分析 | `0 4 1 * *` | 每月1号凌晨4点生成月报 |

### 定时任务脚本

创建 `scripts/cron_eval.sh`：

```bash
#!/bin/bash

# 每日评估任务
WEBSITE="https://www.baidu.com"
OUTPUT_DIR="output/eval_results"

# 执行评估
python scripts/run_test_cases.py \
  --website "$WEBSITE" \
  --output-dir "$OUTPUT_DIR"

# 检查评分
python scripts/check_eval_score.py \
  --min-score 80 \
  --min-pass-rate 80

# 发送通知
if [ $? -ne 0 ]; then
  python scripts/send_notification.py \
    --type "alert" \
    --message "Browser-CDP evaluation failed!"
fi
```

## 告警机制

### 告警阈值

| 告警级别 | 条件 | 通知方式 |
|----------|------|----------|
| 严重 | 综合评分 < 60 | 邮件 + 钉钉 |
| 警告 | 通过率 < 70% | 钉钉 |
| 提示 | 单一维度通过率 < 50% | 日志 |

### 通知脚本

创建 `scripts/send_notification.py`：

```python
import requests
import json
import sys

def send_dingtalk(webhook_url, message):
    """发送钉钉通知"""
    data = {
        "msgtype": "text",
        "text": {
            "content": message
        }
    }
    response = requests.post(webhook_url, json=data)
    return response.status_code == 200

def send_email(to_email, subject, body):
    """发送邮件通知"""
    # 实现邮件发送逻辑
    pass

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", required=True, choices=["alert", "warning", "info"])
    parser.add_argument("--message", required=True)
    args = parser.parse_args()
    
    if args.type == "alert":
        send_dingtalk("https://oapi.dingtalk.com/robot/send?access_token=xxx", args.message)
        send_email("admin@example.com", "Browser-CDP Alert", args.message)
    elif args.type == "warning":
        send_dingtalk("https://oapi.dingtalk.com/robot/send?access_token=xxx", args.message)
    else:
        print(args.message)
```

## 评估数据库

### 数据库表结构

```sql
CREATE TABLE eval_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    website_url TEXT NOT NULL,
    website_name TEXT NOT NULL,
    eval_time DATETIME NOT NULL,
    overall_score REAL NOT NULL,
    grade TEXT NOT NULL,
    pass_rate REAL NOT NULL,
    passed_cases INTEGER NOT NULL,
    total_cases INTEGER NOT NULL,
    dimension_scores TEXT NOT NULL,
    report_path TEXT NOT NULL
);

CREATE TABLE eval_comparison (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    website_url TEXT NOT NULL,
    eval_time_1 DATETIME NOT NULL,
    eval_time_2 DATETIME NOT NULL,
    score_change REAL NOT NULL,
    pass_rate_change REAL NOT NULL,
    trend TEXT NOT NULL
);
```

### 数据查询示例

```sql
-- 查询最近10次评估结果
SELECT * FROM eval_history 
ORDER BY eval_time DESC 
LIMIT 10;

-- 查询同一网站的评分趋势
SELECT website_name, eval_time, overall_score, pass_rate 
FROM eval_history 
WHERE website_name = 'baidu' 
ORDER BY eval_time DESC;

-- 查询评分下降的网站
SELECT website_name, overall_score, pass_rate 
FROM eval_history 
WHERE overall_score < 60 
ORDER BY eval_time DESC;
```

## 报告生成

### 自动生成报告

```bash
# 生成最新评估报告
python scripts/generate_eval_report.py --latest

# 生成指定日期的报告
python scripts/generate_eval_report.py --date 2026-08-06

# 生成周报
python scripts/generate_eval_report.py --type weekly

# 生成月报
python scripts/generate_eval_report.py --type monthly
```

### 报告模板

报告包含以下部分：
1. **执行摘要**：总体评分、通过率、主要发现
2. **维度分析**：各维度得分及趋势
3. **用例详情**：通过/失败用例列表
4. **改进建议**：针对失败用例的优化建议
5. **历史对比**：与上次评估的对比结果

## 集成检查清单

- [ ] 创建 GitHub Actions 工作流文件
- [ ] 配置定时任务
- [ ] 设置告警阈值
- [ ] 创建通知脚本
- [ ] 初始化评估数据库
- [ ] 配置报告生成工具
- [ ] 测试完整流水线
- [ ] 文档更新

## 相关文件

- `.github/workflows/browser-cdp-eval.yml` - CI/CD 工作流
- `scripts/cron_eval.sh` - 定时任务脚本
- `scripts/send_notification.py` - 通知脚本
- `scripts/generate_eval_report.py` - 报告生成工具
- `docs/evaluation-standards-v2.md` - 评估标准
- `docs/evaluation-tracking-guide.md` - 追踪机制
