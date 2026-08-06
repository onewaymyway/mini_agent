# 网站操作能力评估工具与方法指南

**版本**: 1.0.0  
**创建日期**: 2026-08-06  
**状态**: 正式版  
**关联文档**: [evaluation-standards-v2.md](./evaluation-standards-v2.md)、[assessment-metrics-v2.md](../references/assessment-metrics-v2.md)

---

## 1. 评估工具架构

### 1.1 工具组件总览

```
.claude/skills/browser-cdp/
├── src/evaluators/           # 评估器模块
│   ├── base_evaluator.py     # 评估器基类
│   ├── performance_evaluator.py    # 页面加载能力评估
│   ├── element_evaluator.py        # 元素定位能力评估
│   ├── success_rate_evaluator.py   # 数据提取能力评估
│   ├── anti_detection_evaluator.py # 反检测能力评估
│   ├── stability_evaluator.py      # 稳定性评估
│   ├── error_recovery_evaluator.py # 错误恢复评估
│   ├── data_quality_evaluator.py   # 数据质量评估
│   ├── report_generator.py         # 报告生成器
│   └── website_evaluator.py        # 主评估器入口
├── scripts/
│   ├── eval_runner.py          # 评估执行器
│   ├── eval_scenarios.py       # 评估场景定义
│   ├── eval_config.py          # 评估配置
│   └── run_evaluation.py       # 评估运行入口
├── tests/evaluation/           # 评估测试用例
└── docs/
    ├── evaluation-standards-v2.md    # 评估标准
    └── evaluation-tools-guide.md     # 本文件
```

### 1.2 评估流程

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│  准备阶段   │ →  │  执行阶段   │ →  │  采集阶段   │ →  │  报告阶段   │
└─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘
   • 配置环境      • 浏览器启动      • 指标采集      • 报告生成
   • 准备数据      • 场景执行        • 日志收集      • 结果分析
   • 设定基线      • 数据采集        • 截图保存      • 改进建议
```

---

## 2. 自动化评估工具

### 2.1 评估器模块

#### 2.1.1 页面加载能力评估器 (PerformanceEvaluator)

**文件**: `src/evaluators/performance_evaluator.py`

**功能**:
- 页面访问成功率计算
- 首屏加载时间测量
- 页面完全加载时间测量
- 超时处理成功率评估

**使用示例**:
```python
from src.evaluators.performance_evaluator import PerformanceEvaluator

evaluator = PerformanceEvaluator()
result = evaluator.evaluate({
    "total_attempts": 10,
    "successful_visits": 9,
    "first_contentful_paint": 2.5,  # 秒
    "page_load_time": 8.2,          # 秒
    "total_timeouts": 2,
    "handled_timeouts": 2,
})
print(f"页面加载能力得分: {result['score']}")
```

#### 2.1.2 元素定位能力评估器 (ElementEvaluator)

**文件**: `src/evaluators/element_evaluator.py`

**功能**:
- 元素定位成功率计算
- 交互成功率评估
- 动态元素识别率计算
- 定位策略覆盖率评估

**使用示例**:
```python
from src.evaluators.element_evaluator import ElementEvaluator

evaluator = ElementEvaluator()
result = evaluator.evaluate({
    "total_locate_attempts": 20,
    "successful_locates": 18,
    "total_interaction_attempts": 15,
    "successful_interactions": 13,
    "total_dynamic_elements": 10,
    "identified_dynamic_elements": 8,
    "verified_strategies": 5,
    "total_strategies": 7,
})
print(f"元素定位能力得分: {result['score']}")
```

#### 2.1.3 数据提取能力评估器 (SuccessRateEvaluator)

**文件**: `src/evaluators/success_rate_evaluator.py`

**功能**:
- 数据提取准确率计算
- 字段完整率评估
- 数据质量得分计算
- 结构化提取成功率评估

**使用示例**:
```python
from src.evaluators.success_rate_evaluator import SuccessRateEvaluator

evaluator = SuccessRateEvaluator()
result = evaluator.evaluate({
    "total_extractions": 50,
    "correct_extractions": 45,
    "expected_fields": 10,
    "extracted_fields": 8,
    "total_structured_attempts": 20,
    "successful_structured": 16,
})
print(f"数据提取能力得分: {result['score']}")
```

#### 2.1.4 反检测能力评估器 (AntiDetectionEvaluator)

**文件**: `src/evaluators/anti_detection_evaluator.py`

**功能**:
- 反爬绕过率计算
- 验证码通过率评估
- 指纹伪装有效性评估
- 行为模拟自然度评估

**使用示例**:
```python
from src.evaluators.anti_detection_evaluator import AntiDetectionEvaluator

evaluator = AntiDetectionEvaluator()
result = evaluator.evaluate({
    "total_crawl_triggers": 10,
    "successful_bypasses": 7,
    "total_captchas": 3,
    "passed_captchas": 2,
    "total_checks": 20,
    "identified_as_bot": 3,
    "total_operations": 50,
    "human_like_operations": 40,
})
print(f"反检测能力得分: {result['score']}")
```

#### 2.1.5 稳定性评估器 (StabilityEvaluator)

**文件**: `src/evaluators/stability_evaluator.py`

**功能**:
- 重复执行一致性计算
- 异常恢复率评估
- 连接稳定性评估
- 内存稳定性评估

**使用示例**:
```python
from src.evaluators.stability_evaluator import StabilityEvaluator

evaluator = StabilityEvaluator()
result = evaluator.evaluate({
    "total_executions": 10,
    "consistent_executions": 9,
    "total_errors": 5,
    "successful_recoveries": 4,
    "total_runtime": 1800,  # 秒
    "connected_time": 1750,  # 秒
    "start_memory": 100,    # MB
    "end_memory": 108,      # MB
    "runtime_hours": 0.5,
})
print(f"稳定性得分: {result['score']}")
```

#### 2.1.6 错误恢复评估器 (ErrorRecoveryEvaluator)

**文件**: `src/evaluators/error_recovery_evaluator.py`

**功能**:
- 错误分类准确率评估
- 重试成功率计算
- 降级策略有效性评估

**使用示例**:
```python
from src.evaluators.error_recovery_evaluator import ErrorRecoveryEvaluator

evaluator = ErrorRecoveryEvaluator()
result = evaluator.evaluate({
    "total_errors": 10,
    "correctly_classified": 9,
    "total_retries": 15,
    "successful_retries": 11,
    "total_fallback_attempts": 5,
    "successful_fallbacks": 3,
})
print(f"错误恢复能力得分: {result['score']}")
```

### 2.2 主评估器 (WebsiteEvaluator)

**文件**: `src/evaluators/website_evaluator.py`

**功能**: 整合所有评估维度，提供统一的评估接口。

**使用示例**:
```python
from src.evaluators.website_evaluator import WebsiteEvaluator

evaluator = WebsiteEvaluator(
    website_url="https://www.baidu.com",
    website_name="百度"
)

# 执行完整评估
result = evaluator.evaluate({
    "performance": { ... },      # 页面加载能力数据
    "element": { ... },          # 元素定位能力数据
    "scraping": { ... },         # 数据提取能力数据
    "anti_detection": { ... },   # 反检测能力数据
    "stability": { ... },        # 稳定性数据
    "error_recovery": { ... },   # 错误恢复数据
})

# 获取报告
print(f"综合评分: {result['overall_score']}")
print(evaluator.get_markdown_report())

# 保存报告
evaluator.save_report("output/eval_baidu.json", format="json")
evaluator.save_report("output/eval_baidu.md", format="markdown")
```

### 2.3 报告生成器 (ReportGenerator)

**文件**: `src/evaluators/report_generator.py`

**功能**: 生成 JSON 和 Markdown 格式的评估报告。

**使用示例**:
```python
from src.evaluators.report_generator import ReportGenerator

generator = ReportGenerator()
generator.add_dimension("页面加载能力", {
    "score": 85,
    "weight": 0.25,
    "metrics": [...]
})
# 生成报告
report = generator.generate_report()
markdown = generator.generate_markdown_report()
generator.save_report("output/report.json", format="json")
```

---

## 3. 自动化测试脚本

### 3.1 评估执行器 (EvalRunner)

**文件**: `scripts/eval_runner.py`

**功能**: 实现网站评估的完整流程。

**核心方法**:
```python
class EvalRunner:
    def __init__(self, website_url: str, config: Dict[str, Any]):
        self.website_url = website_url
        self.config = config
        self.evaluator = WebsiteEvaluator(website_url)
    
    def run_evaluation(self) -> EvalResult:
        """执行完整评估流程"""
        # 1. 浏览器初始化
        # 2. 场景执行
        # 3. 数据采集
        # 4. 评估计算
        # 5. 报告生成
        pass
    
    def run_scenario(self, scenario: Dict[str, Any]) -> Dict[str, Any]:
        """执行单个评估场景"""
        pass
```

### 3.2 评估场景定义 (EvalScenarios)

**文件**: `scripts/eval_scenarios.py`

**功能**: 定义标准化的评估场景。

**预定义场景**:
```python
SCENARIOS = {
    "page_access": {
        "name": "页面访问测试",
        "description": "测试页面加载成功率",
        "steps": [
            {"action": "goto", "url": "{{website_url}}"},
            {"action": "wait_for_load", "timeout": 10},
            {"action": "check_status", "expected": 200},
        ]
    },
    "search": {
        "name": "搜索功能测试",
        "description": "测试搜索查询功能",
        "steps": [
            {"action": "goto", "url": "{{website_url}}"},
            {"action": "type", "selector": "input[type='search']", "text": "测试关键词"},
            {"action": "click", "selector": "button[type='submit']"},
            {"action": "wait_for_load", "timeout": 10},
            {"action": "extract", "selector": ".search-results", "count": 10},
        ]
    },
    "element_locate": {
        "name": "元素定位测试",
        "description": "测试元素定位能力",
        "steps": [
            {"action": "goto", "url": "{{website_url}}"},
            {"action": "locate", "selector": ".main-content", "strategy": "css"},
            {"action": "locate", "selector": "//div[@class='article']", "strategy": "xpath"},
            {"action": "locate", "text": "点击这里", "strategy": "text"},
        ]
    },
    "data_extraction": {
        "name": "数据提取测试",
        "description": "测试数据提取能力",
        "steps": [
            {"action": "goto", "url": "{{website_url}}"},
            {"action": "extract", "selector": ".item-title", "field": "title"},
            {"action": "extract", "selector": ".item-content", "field": "content"},
            {"action": "extract", "selector": ".item-date", "field": "date"},
        ]
    },
    "stability": {
        "name": "稳定性测试",
        "description": "测试多次执行一致性",
        "steps": [
            {"action": "repeat", "count": 10, "scenario": "search"},
            {"action": "compare", "field": "results", "tolerance": 0.9},
        ]
    },
}
```

### 3.3 评估配置 (EvalConfig)

**文件**: `scripts/eval_config.py`

**功能**: 管理评估配置参数。

**配置示例**:
```python
EVAL_CONFIG = {
    "browser": {
        "type": "chrome",
        "headless": True,
        "timeout": 30,
        "retry_count": 3,
    },
    "network": {
        "use_proxy": True,
        "proxy_pool": "default",
        "rate_limit": 1.0,  # 每秒请求数
    },
    "evaluation": {
        "scenarios": ["page_access", "search", "element_locate", "data_extraction", "stability"],
        "keywords": ["测试", "新闻", "天气"],
        "data_scale": 100,
        "max_duration": 300,  # 秒
    },
    "output": {
        "dir": "output/eval_results",
        "screenshots": "output/screenshots",
        "logs": "logs/eval",
    },
}
```

### 3.4 评估运行入口 (run_evaluation.py)

**文件**: `scripts/run_evaluation.py`

**功能**: 命令行入口，执行评估任务。

**使用方式**:
```bash
# 评估单个网站
python scripts/run_evaluation.py --url https://www.baidu.com --name 百度

# 评估多个网站
python scripts/run_evaluation.py --config eval_config.json

# 指定评估场景
python scripts/run_evaluation.py --url https://www.baidu.com --scenarios page_access,search

# 生成报告
python scripts/run_evaluation.py --url https://www.baidu.com --output-format json,markdown
```

---

## 4. 人工评估流程

### 4.1 评估准备

#### 4.1.1 环境检查

```bash
# 检查 Python 环境
python --version  # 需要 3.8+

# 检查依赖包
pip list | grep -E "playwright|requests|beautifulsoup4|lxml"

# 检查浏览器
which chrome  # 或 which chromium
```

#### 4.1.2 测试数据准备

1. **关键词列表**: 准备 3-5 个测试关键词
2. **预期结果**: 定义每个网站的目标数据字段
3. **基线数据**: 收集历史评估数据作为对比基准

### 4.2 评估执行

#### 4.2.1 手动评估步骤

1. **页面访问测试**
   - 打开目标网站
   - 记录加载时间
   - 检查页面是否正常显示

2. **搜索功能测试**
   - 输入测试关键词
   - 执行搜索
   - 检查搜索结果数量和质量

3. **数据提取测试**
   - 提取列表页数据
   - 提取详情页数据
   - 验证数据完整性和准确性

4. **交互功能测试**
   - 测试点击、输入、滚动等操作
   - 检查动态内容加载
   - 验证分页功能

5. **稳定性测试**
   - 重复执行相同操作
   - 检查结果一致性
   - 监控内存使用

#### 4.2.2 评估记录

使用评估记录表记录每次评估结果：

| 评估项 | 操作 | 预期结果 | 实际结果 | 评分 | 备注 |
|--------|------|----------|----------|------|------|
| 页面访问 | 导航到首页 | HTTP 200 | HTTP 200 | 10 | |
| 搜索功能 | 搜索"测试" | 返回结果页 | 返回结果页 | 9 | 加载时间 3.2s |
| 数据提取 | 提取标题 | 标题文本 | 标题文本 | 8 | 字段完整率 80% |

### 4.3 评估分析

#### 4.3.1 数据分析

1. **计算各维度得分**
   - 使用评估器模块计算各维度得分
   - 汇总所有指标数据

2. **生成综合评分**
   - 按照权重计算综合评分
   - 确定等级（优秀/良好/合格/待改进/不可用）

3. **识别薄弱环节**
   - 对比各维度得分
   - 找出得分最低的维度
   - 分析失败原因

#### 4.3.2 报告生成

使用报告生成器生成评估报告：

```python
from src.evaluators.report_generator import ReportGenerator

generator = ReportGenerator()

# 添加各维度结果
generator.add_dimension("页面加载能力", {
    "score": 85,
    "weight": 0.25,
    "metrics": [...]
})
# ... 其他维度

# 生成报告
report = generator.generate_report()
markdown = generator.generate_markdown_report()

# 保存报告
generator.save_report("output/eval_report.json", format="json")
generator.save_report("output/eval_report.md", format="markdown")
```

---

## 5. 评估工具使用示例

### 5.1 完整评估流程示例

```python
#!/usr/bin/env python3
"""
网站操作能力评估示例
"""

import sys
from pathlib import Path

# 添加 skill 目录到路径
SKILL_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SKILL_DIR))

from src.evaluators.website_evaluator import WebsiteEvaluator
from src.evaluators.report_generator import ReportGenerator


def evaluate_website(website_url: str, website_name: str = None):
    """评估单个网站"""
    # 初始化评估器
    evaluator = WebsiteEvaluator(
        website_url=website_url,
        website_name=website_name or website_url
    )
    
    # 准备评估数据（实际使用时从浏览器采集）
    context = {
        "performance": {
            "total_attempts": 10,
            "successful_visits": 9,
            "first_contentful_paint": 2.5,
            "page_load_time": 8.2,
            "total_timeouts": 2,
            "handled_timeouts": 2,
        },
        "element": {
            "total_locate_attempts": 20,
            "successful_locates": 18,
            "total_interaction_attempts": 15,
            "successful_interactions": 13,
            "total_dynamic_elements": 10,
            "identified_dynamic_elements": 8,
            "verified_strategies": 5,
            "total_strategies": 7,
        },
        "scraping": {
            "total_extractions": 50,
            "correct_extractions": 45,
            "expected_fields": 10,
            "extracted_fields": 8,
            "total_structured_attempts": 20,
            "successful_structured": 16,
        },
        "anti_detection": {
            "total_crawl_triggers": 10,
            "successful_bypasses": 7,
            "total_captchas": 3,
            "passed_captchas": 2,
            "total_checks": 20,
            "identified_as_bot": 3,
            "total_operations": 50,
            "human_like_operations": 40,
        },
        "stability": {
            "total_executions": 10,
            "consistent_executions": 9,
            "total_errors": 5,
            "successful_recoveries": 4,
            "total_runtime": 1800,
            "connected_time": 1750,
            "start_memory": 100,
            "end_memory": 108,
            "runtime_hours": 0.5,
        },
        "error_recovery": {
            "total_errors": 10,
            "correctly_classified": 9,
            "total_retries": 15,
            "successful_retries": 11,
            "total_fallback_attempts": 5,
            "successful_fallbacks": 3,
        },
    }
    
    # 执行评估
    result = evaluator.evaluate(context)
    
    # 输出结果
    print(f"\n=== 评估报告 ===")
    print(f"网站: {website_name or website_url}")
    print(f"综合评分: {result['overall_score']}")
    print(f"等级: {result['grade']}")
    print(f"\n各维度得分:")
    for dim_name, dim_result in result['dimensions'].items():
        print(f"  {dim_name}: {dim_result['score']:.1f} (权重 {dim_result['weight']:.0%})")
    
    # 生成 Markdown 报告
    markdown_report = evaluator.get_markdown_report()
    print(f"\n{markdown_report}")
    
    # 保存报告
    output_dir = SKILL_DIR / "output" / "eval_results"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    evaluator.save_report(str(output_dir / f"eval_{website_name or website_url}.json"), format="json")
    evaluator.save_report(str(output_dir / f"eval_{website_name or website_url}.md"), format="markdown")
    
    return result


if __name__ == "__main__":
    # 示例：评估百度
    evaluate_website("https://www.baidu.com", "百度")
```

### 5.2 批量评估示例

```python
#!/usr/bin/env python3
"""
批量评估多个网站
"""

import sys
from pathlib import Path

SKILL_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SKILL_DIR))

from src.evaluators.website_evaluator import batch_evaluate


if __name__ == "__main__":
    # 定义要评估的网站列表
    sites = [
        {"url": "https://www.baidu.com", "name": "百度", "context": {...}},
        {"url": "https://www.bing.com", "name": "Bing", "context": {...}},
        {"url": "https://www.zhihu.com", "name": "知乎", "context": {...}},
    ]
    
    # 批量评估
    results = batch_evaluate(sites)
    
    # 输出汇总报告
    print("\n=== 批量评估汇总 ===")
    for result in results:
        print(f"{result['website_name']}: {result['overall_score']:.1f} ({result['grade']})")
```

---

## 6. 评估结果解读

### 6.1 评分等级说明

| 等级 | 分数范围 | 说明 | 建议操作 |
|------|----------|------|----------|
| 优秀 (A) | 90-100 | 抓取能力成熟，可稳定生产使用 | 直接部署 |
| 良好 (B) | 75-89 | 基本可用，个别场景需优化 | 针对性优化 |
| 合格 (C) | 60-74 | 核心功能可用，需持续改进 | 制定改进计划 |
| 待改进 (D) | 40-59 | 存在明显短板，需重点优化 | 优先修复关键问题 |
| 不可用 (F) | < 40 | 当前能力无法支持该网站 | 重新评估可行性 |

### 6.2 通过标准

| 优先级 | 综合评分 | 抓取成功率 | 说明 |
|--------|----------|-----------|------|
| P0 | ≥ 75 | ≥ 80% | 核心网站，必须达标 |
| P1 | ≥ 65 | ≥ 70% | 重要网站，基本可用 |
| P2 | ≥ 55 | ≥ 60% | 扩展网站，核心功能可用 |
| P3 | ≥ 50 | ≥ 50% | 探索网站，最低可用标准 |

### 6.3 改进建议生成

评估报告会自动生成改进建议，包括：

1. **优势分析**: 识别表现良好的维度
2. **不足分析**: 指出得分较低的维度
3. **改进建议**: 提供具体的优化方向
4. **优先级排序**: 按影响程度排序改进项

---

## 7. 附录

### 7.1 依赖安装

```bash
# 核心依赖
pip install playwright
pip install requests
pip install beautifulsoup4
pip install lxml

# 报告生成
pip install jinja2
pip install tabulate

# 性能分析（可选）
pip install py-spy
pip install memory-profiler
```

### 7.2 参考文档

- [evaluation-standards-v2.md](./evaluation-standards-v2.md) - 评估标准 v2.0
- [assessment-metrics-v2.md](../references/assessment-metrics-v2.md) - 评估指标体系
- [test-case-specification.md](../references/test-case-specification.md) - 测试用例规格
- [anti-detection.md](../references/anti-detection.md) - 反检测指南

---

*本文件为评估工具使用指南，请随 skill 演进持续更新*
