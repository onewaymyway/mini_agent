# 评估结果追踪机制

## 概述

本文档定义 browser-cdp skill 评估结果的追踪、存储和分析机制，确保每次评估结果可追溯、可对比、可分析。

## 评估结果存储规范

### 存储位置

所有评估结果统一存储至：
```
.claude/skills/browser-cdp/output/eval_results/
```

### 文件命名规范

```
eval_{网站安全名称}_{YYYYMMDD}_{HHMMSS}.{json|md}
```

示例：
- `eval_baidu_20260806_205140.json`
- `eval_baidu_20260806_205140.md`

### 安全名称转换规则

将 URL 中的特殊字符替换为下划线：
- `://` → `_`
- `/` → `_`
- `?` → `_`
- `&` → `_`

## 评估结果数据结构

### JSON 格式

```json
{
  "website_url": "https://www.baidu.com",
  "website_name": "baidu",
  "eval_time": "2026-08-06 20:51:40",
  "total_duration": 0.0,
  "overall_score": 80.0,
  "grade": "A",
  "pass_rate": 80.0,
  "passed_cases": 36,
  "total_cases": 45,
  "dimension_scores": {
    "页面访问": {"rate": 75.0, "passed": 3, "total": 4},
    "元素定位": {"rate": 100.0, "passed": 7, "total": 7},
    "数据提取": {"rate": 87.5, "passed": 7, "total": 8},
    "交互功能": {"rate": 37.5, "passed": 3, "total": 8},
    "反检测": {"rate": 66.7, "passed": 2, "total": 3},
    "稳定性": {"rate": 100.0, "passed": 6, "total": 6}
  },
  "test_results": [
    {
      "case_id": "PAGE-01",
      "name": "首页正常访问",
      "success": true,
      "duration": 0.0,
      "dimension": "页面访问"
    }
  ]
}
```

### Markdown 格式

包含：
- 评估概览（网站、日期、评分、等级）
- 各维度得分表格
- 测试用例执行结果表格
- 失败用例分析
- 改进建议

## 历史对比机制

### 对比维度

1. **评分趋势**：同一网站多次评估的评分变化
2. **维度对比**：各维度得分的横向对比
3. **用例通过率**：通过/失败用例的变化
4. **耗时分析**：执行时间的变化趋势

### 对比工具

```bash
# 对比两次评估结果
python scripts/compare_eval.py \
  --eval1 output/eval_results/eval_baidu_20260806_205140.json \
  --eval2 output/eval_results/eval_baidu_20260807_100000.json
```

## 评估报告生成

### 自动生成报告

```bash
# 生成最新评估报告
python scripts/generate_eval_report.py --latest
```

### 报告内容

1. **执行摘要**：总体评分、通过率、主要发现
2. **维度分析**：各维度得分及趋势
3. **用例详情**：通过/失败用例列表
4. **改进建议**：针对失败用例的优化建议
5. **历史对比**：与上次评估的对比结果

## 追踪指标

### 关键指标

| 指标 | 说明 | 目标值 |
|------|------|--------|
| 综合评分 | 整体能力评分 | ≥80 |
| 通过率 | 通过用例占比 | ≥80% |
| 页面访问成功率 | 页面加载能力 | ≥90% |
| 元素定位成功率 | 定位能力 | ≥95% |
| 数据提取准确率 | 提取质量 | ≥85% |
| 交互成功率 | 交互能力 | ≥70% |
| 反检测绕过率 | 反爬能力 | ≥60% |
| 稳定性评分 | 长时间运行能力 | ≥90% |

### 告警阈值

- **综合评分 < 60**：严重告警，需立即优化
- **通过率 < 70%**：警告，需分析失败原因
- **单一维度通过率 < 50%**：专项优化

## 使用流程

1. **执行评估**：运行测试用例脚本
2. **保存报告**：自动生成 JSON 和 Markdown 报告
3. **查看结果**：阅读 Markdown 报告
4. **分析失败**：针对失败用例进行优化
5. **重新评估**：优化后重新执行评估
6. **对比趋势**：使用对比工具分析变化

## 相关文件

- `docs/evaluation-standards-v2.md` - 评估标准
- `docs/evaluation-tools-guide.md` - 工具使用指南
- `references/evaluation-test-cases.md` - 测试用例库
- `scripts/run_test_cases.py` - 测试执行脚本
- `scripts/compare_eval.py` - 评估对比工具
- `scripts/generate_eval_report.py` - 报告生成工具
