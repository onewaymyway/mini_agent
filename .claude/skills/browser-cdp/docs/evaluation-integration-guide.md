# 网站操作能力评估与迭代机制集成指南

**版本**: 1.0.0  
**创建日期**: 2026-08-07  
**状态**: 草案  
**适用范围**: browser-cdp skill 评估与迭代机制

---

## 1. 概述

本文档描述如何将评估机制集成到 browser-cdp skill 中，形成持续改进闭环。

### 1.1 核心组件

| 组件 | 文件 | 职责 |
|------|------|------|
| 评估框架 | `temp/evaluation_framework.py` | 定义评估维度和评分逻辑 |
| 评估执行器 | `temp/eval_runner.py` | 执行评估流程，生成报告 |
| 迭代机制 | `temp/iteration_mechanism.py` | 管理评估周期和改进行动 |

### 1.2 评估维度权重

| 维度 | 权重 | 核心指标 |
|------|------|----------|
| 页面加载能力 | 25% | 页面访问成功率、首屏加载时间、页面完全加载时间、超时处理成功率 |
| 元素定位能力 | 25% | 元素定位成功率、交互成功率、动态元素识别率、定位策略覆盖率 |
| 数据提取能力 | 20% | 数据提取准确率、字段完整率、数据质量得分、结构化提取成功率 |
| 反检测能力 | 15% | 反爬绕过率、验证码通过率、指纹伪装有效性、行为模拟自然度 |
| 稳定性与恢复 | 15% | 重复执行一致性、异常恢复率、连接稳定性、内存稳定性 |

---

## 2. 集成步骤

### 2.1 复制评估工具到 Skill 目录

```bash
# 复制评估框架
cp temp/evaluation_framework.py .claude/skills/browser-cdp/src/evaluators/

# 复制评估执行器
cp temp/eval_runner.py .claude/skills/browser-cdp/scripts/

# 复制迭代机制
cp temp/iteration_mechanism.py .claude/skills/browser-cdp/scripts/
```

### 2.2 更新 SKILL.md

在 SKILL.md 中添加评估机制相关内容：

```markdown
## 评估与迭代机制

### 评估维度
- 页面加载能力 (25%)
- 元素定位能力 (25%)
- 数据提取能力 (20%)
- 反检测能力 (15%)
- 稳定性与恢复 (15%)

### 评估周期
- 全量评估：每周
- 增量评估：每日
- 专项评估：按需
- 版本评估：每次发布

### 迭代触发条件
- 评分下降 > 5 分
- 指标低于目标值
- 网站结构变更
- 新增反爬机制
- 版本发布
```

### 2.3 创建评估配置

创建 `.claude/skills/browser-cdp/config/evaluation_config.json`：

```json
{
  "dimensions": {
    "页面加载能力": {"weight": 0.25, "target_score": 85},
    "元素定位能力": {"weight": 0.25, "target_score": 85},
    "数据提取能力": {"weight": 0.20, "target_score": 80},
    "反检测能力": {"weight": 0.15, "target_score": 75},
    "稳定性与恢复": {"weight": 0.15, "target_score": 85}
  },
  "cycles": {
    "full_evaluation": {"frequency": "weekly", "scope": "P0/P1"},
    "incremental_evaluation": {"frequency": "daily", "scope": "new/changed"},
    "special_evaluation": {"frequency": "on_demand", "scope": "specific"},
    "version_evaluation": {"frequency": "per_release", "scope": "P0/P1"}
  },
  "triggers": {
    "score_drop": {"threshold": 5.0, "action": "special_evaluation"},
    "metric_below_target": {"threshold": 70, "action": "optimization_plan"},
    "website_change": {"action": "re_evaluation"},
    "new_anti_crawl": {"action": "strategy_update"},
    "version_release": {"action": "full_regression"}
  }
}
```

---

## 3. 使用示例

### 3.1 执行单次评估

```python
from src.evaluators.evaluation_framework import evaluate_website

# 执行评估
report = evaluate_website(
    website_name="知乎",
    website_url="https://www.zhihu.com",
    context={
        "页面加载能力": {
            "page_access_rate": 95.0,
            "first_contentful_paint": 2.5,
            "page_load_time": 8.0,
            "timeout_handling_rate": 92.0,
        },
        "元素定位能力": {
            "element_locate_rate": 88.0,
            "interaction_success_rate": 85.0,
            "dynamic_element_rate": 82.0,
            "locator_strategy_coverage": 75.0,
        },
        "数据提取能力": {
            "extraction_accuracy": 85.0,
            "field_completeness": 80.0,
            "data_quality_score": 82.0,
            "structured_extraction_rate": 78.0,
        },
        "反检测能力": {
            "anti_crawl_bypass_rate": 72.0,
            "captcha_pass_rate": 65.0,
            "fingerprint_evasion_rate": 85.0,
            "behavior_naturalness": 78.0,
        },
        "稳定性与恢复": {
            "execution_consistency": 90.0,
            "error_recovery_rate": 85.0,
            "connection_stability": 96.0,
            "memory_stability": 3.5,
        },
    },
    output_dir=Path("./output/eval_reports")
)

print(f"综合评分: {report['overall_score']}/100 ({report['grade']})")
```

### 3.2 检查迭代触发条件

```python
from scripts.iteration_mechanism import check_iteration_triggers

# 检查触发条件
triggers = check_iteration_triggers(current_result, previous_result)

if triggers:
    for trigger in triggers:
        print(f"触发: {trigger['message']} ({trigger['priority']})")
        # 执行相应动作
        if trigger['priority'] == 'high':
            # 立即触发专项评估
            pass
```

### 3.3 记录改进

```python
from scripts.iteration_mechanism import record_improvement

# 记录改进
record_improvement(
    website_name="知乎",
    dimension="数据提取能力",
    improvement="优化选择器策略",
    metric_before=65.0,
    metric_after=82.0,
)
```

---

## 4. 评估报告格式

### 4.1 JSON 报告

```json
{
  "website_name": "知乎",
  "website_url": "https://www.zhihu.com",
  "eval_time": "2026-08-07 06:00:00",
  "overall_score": 82.5,
  "grade": "良好 (B)",
  "duration_seconds": 125.5,
  "dimensions": {
    "页面加载能力": {
      "score": 85.0,
      "weight": 0.25,
      "weighted_score": 21.25,
      "metrics": [...],
      "observations": [...]
    },
    ...
  },
  "findings": [...],
  "recommendations": [...],
  "errors": [...]
}
```

### 4.2 Markdown 报告

```
# 网站操作能力评估报告

**评估网站**: 知乎 (https://www.zhihu.com)
**评估日期**: 2026-08-07 06:00:00
**综合评分**: 82.5/100 (良好 (B))
**评估耗时**: 125.5秒

## 各维度得分

| 维度 | 得分 | 权重 | 加权得分 |
|------|------|------|----------|
| 页面加载能力 | 85.0 | 25% | 21.25 |
| 元素定位能力 | 88.0 | 25% | 22.00 |
| 数据提取能力 | 82.0 | 20% | 16.40 |
| 反检测能力 | 75.0 | 15% | 11.25 |
| 稳定性与恢复 | 88.0 | 15% | 13.20 |

## 关键发现

- ✅ 页面加载能力: 表现优秀 (85.0分)
- ✅ 元素定位能力: 表现优秀 (88.0分)
- ⚠️ 数据提取能力: 基本可用 (82.0分)
- ⚠️ 反检测能力: 基本可用 (75.0分)
- ✅ 稳定性与恢复: 表现优秀 (88.0分)

## 改进建议

- [ ] 持续改进 数据提取能力（当前得分 82.0分）
- [ ] 持续改进 反检测能力（当前得分 75.0分）
```

---

## 5. 迭代机制流程

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   评估执行   │ ──▶ │   结果分析   │ ──▶ │   触发检查   │
└─────────────┘     └─────────────┘     └─────────────┘
       │                                         │
       │                    ┌────────────────────┘
       │                    ▼
       │              ┌─────────────┐
       │              │   触发条件   │
       │              │   满足？     │
       │              └─────────────┘
       │                    │
       │           ┌────────┴────────┐
       │           ▼                 ▼
       │      ┌─────────┐       ┌─────────┐
       │      │  记录改进 │       │  执行优化 │
       │      └─────────┘       └─────────┘
       │           │                 │
       │           └────────┬────────┘
       │                    ▼
       │              ┌─────────────┐
       │              │   复评验证   │
       │              └─────────────┘
       │                    │
       └────────────────────┘
```

---

## 6. 监控与告警

### 6.1 监控指标

| 指标 | 警告阈值 | 严重阈值 | 告警方式 |
|------|----------|----------|----------|
| 页面访问成功率 | < 90% | < 80% | 邮件+钉钉 |
| 元素定位成功率 | < 85% | < 75% | 邮件+钉钉 |
| 数据提取准确率 | < 80% | < 70% | 邮件+钉钉 |
| 反爬绕过率 | < 60% | < 50% | 邮件+钉钉 |
| 异常恢复率 | < 70% | < 60% | 邮件+钉钉 |

### 6.2 趋势分析

- **周趋势**: 对比上周同期数据，识别退化趋势
- **月趋势**: 对比上月数据，评估整体改进效果
- **网站趋势**: 跟踪单个网站的指标变化

---

## 7. 附录

### 7.1 文件结构

```
.claude/skills/browser-cdp/
├── src/
│   └── evaluators/
│       ├── evaluation_framework.py  # 评估框架
│       ├── base_evaluator.py        # 基类
│       ├── performance_evaluator.py # 性能评估
│       ├── element_evaluator.py     # 元素定位评估
│       ├── success_rate_evaluator.py # 成功率评估
│       ├── anti_detection_evaluator.py # 反检测评估
│       └── stability_evaluator.py   # 稳定性评估
├── scripts/
│   ├── eval_runner.py               # 评估执行器
│   └── iteration_mechanism.py       # 迭代机制
├── config/
│   └── evaluation_config.json       # 评估配置
└── references/
    ├── evaluation-standards-v2.md   # 评估标准
    └── target-websites-expansion-list.md # 目标网站清单
```

### 7.2 参考文档

- [evaluation-standards-v2.md](../../.claude/skills/browser-cdp/references/evaluation-standards-v2.md) - 评估标准 v2.0
- [target-websites-expansion-list.md](../../.claude/skills/browser-cdp/references/target-websites-expansion-list.md) - 目标网站拓展清单
- [assessment-metrics-v2.md](../../.claude/skills/browser-cdp/references/assessment-metrics-v2.md) - 评估指标体系

---

*本文件为评估机制集成指南，请随 skill 演进持续更新。*
