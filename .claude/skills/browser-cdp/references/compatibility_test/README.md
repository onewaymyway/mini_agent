# 网站兼容性测试框架

> 生成时间：2026-08-07
> 版本：1.0.0

---

## 一、框架概述

网站兼容性测试框架是 browser-cdp skill 的核心组件，用于验证和评估对不同类别网站的抓取和浏览能力。

### 1.1 覆盖范围

| 类别 | 网站数量 | 测试用例数 | 优先级 |
|------|----------|-----------|--------|
| 电商 (ECOM) | 6 | 12 | P0-P1 |
| 新闻 (NEWS) | 6 | 8 | P0-P2 |
| 社交 (SOCIAL) | 8 | 12 | P0-P2 |
| 政务 (GOV) | 6 | 8 | P0-P1 |
| **合计** | **26** | **40** | - |

### 1.2 核心特性

- **统一评估体系**：5 项核心指标，综合评分 + 评级
- **可扩展架构**：支持快速添加新网站和新测试场景
- **自动化执行**：支持定时任务自动执行测试
- **详细报告**：生成 Markdown 和 JSON 格式测试报告

---

## 二、快速开始

### 2.1 安装依赖

```bash
pip install -r requirements.txt
```

### 2.2 运行测试

```bash
# 运行所有测试
python -m compatibility_test

# 运行电商类测试
python -m compatibility_test --category ECOM

# 运行 P0 优先级测试
python -m compatibility_test --priority P0

# 运行指定网站测试
python -m compatibility_test --websites 京东 淘宝

# 详细输出
python -m compatibility_test --verbose
```

### 2.3 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--category` | 按分类运行测试 | 全部 |
| `--priority` | 按优先级运行测试 | 全部 |
| `--websites` | 指定网站名称 | 全部 |
| `--output-dir` | 测试结果输出目录 | `test_reports` |
| `--concurrency` | 最大并发数 | 3 |
| `--verbose` | 详细输出 | False |

---

## 三、架构设计

### 3.1 模块结构

```
compatibility_test/
├── __init__.py          # 模块入口
├── models.py            # 数据模型
├── executor.py          # 测试执行器
├── scheduler.py         # 测试调度器
├── collector.py         # 结果收集器
├── evaluator.py         # 评估指标计算器
├── test_cases.py        # 测试用例定义
├── main.py              # 主入口
└── README.md            # 说明文档
```

### 3.2 核心类

| 类名 | 职责 |
|------|------|
| `WebsiteConfig` | 网站配置数据模型 |
| `TestCase` | 测试用例数据模型 |
| `TestResult` | 测试结果数据模型 |
| `TestRun` | 测试执行记录 |
| `TestCaseExecutor` | 测试用例执行器 |
| `TestScheduler` | 测试调度器 |
| `ResultCollector` | 结果收集器 |
| `EvaluationMetrics` | 评估指标计算器 |

---

## 四、评估指标

### 4.1 核心指标

| 指标 | 权重 | 说明 |
|------|------|------|
| 页面访问成功率 | 20% | 页面正常加载的比例 |
| 元素定位准确率 | 20% | 元素成功定位的比例 |
| 数据提取成功率 | 30% | 数据成功提取的比例 |
| 稳定性 | 20% | 连续测试通过率 |
| 反检测能力 | 10% | 绕过反爬机制的比例 |

### 4.2 综合评分

```
综合评分 = 页面访问成功率 × 0.20 +
           元素定位准确率 × 0.20 +
           数据提取成功率 × 0.30 +
           稳定性 × 0.20 +
           反检测能力 × 0.10
```

### 4.3 评级标准

| 评级 | 综合评分 | 说明 |
|------|----------|------|
| A+ | >= 95% | 优秀，完全支持 |
| A | 90-95% | 良好，基本支持 |
| B | 80-90% | 中等，部分支持 |
| C | 70-80% | 较差，需优化 |
| D | < 70% | 不支持，需重构 |

---

## 五、测试用例设计

### 5.1 用例结构

```python
TestCase(
    case_id="JD-01",
    name="首页访问",
    description="验证京东首页能否正常加载",
    steps=[
        Step(action="navigate", target="https://www.jd.com/"),
    ],
    expected_results=[
        ExpectedResult(
            condition="页面标题包含'京东'",
            expected_value="京东",
            check_type="contains",
        ),
    ],
    evaluation_dimensions=["页面访问成功率"],
    pass_criteria={"page_access_success_rate": 0.95},
)
```

### 5.2 步骤类型

| 动作 | 说明 | 参数 |
|------|------|------|
| `navigate` | 导航到 URL | `target`: URL |
| `click` | 点击元素 | `target`: 选择器 |
| `input` | 输入文本 | `target`: 选择器, `value`: 文本 |
| `wait` | 等待 | `timeout`: 超时时间 |
| `scroll` | 滚动页面 | `target`: 滚动方向 |

### 5.3 检查类型

| 类型 | 说明 | 示例 |
|------|------|------|
| `contains` | 包含 | 页面标题包含"京东" |
| `equals` | 等于 | 状态码等于 200 |
| `greater_than` | 大于 | 提取结果数 > 5 |
| `less_than` | 小于 | 响应时间 < 5s |

---

## 六、测试结果

### 6.1 报告格式

测试结果以 JSON 和 Markdown 两种格式保存：

```
test_reports/
├── summary.json              # 汇总数据
├── report_京东_20260807_020000.md  # 测试报告
├── 京东_20260807_020000.json  # 原始数据
└── ...
```

### 6.2 JSON 报告结构

```json
{
  "run_id": "京东_1234567890",
  "website_name": "京东",
  "started_at": "2026-08-07T02:00:00",
  "completed_at": "2026-08-07T02:01:30",
  "total_cases": 6,
  "passed_cases": 5,
  "failed_cases": 1,
  "success_rate": 0.833,
  "results": [...]
}
```

---

## 七、扩展指南

### 7.1 添加新网站

1. 在 `test_cases.py` 中添加网站配置
2. 添加测试用例
3. 实现对应的搜索器（可选）

```python
# 在 create_xxx_websites() 中添加
WebsiteConfig(
    name="新网站",
    url="https://www.example.com/",
    category=Category.ECOM,
    subcategory="ECOM_B2C",
    frontend_framework="React",
    anti_crawl_level=AntiCrawlLevel.MEDIUM,
    login_required=False,
    priority=Priority.P1,
    timeout=30,
    retry_count=3,
    target_success_rate=0.95,
    target_accuracy=0.90,
    searcher_file="example_search.py",
),
```

### 7.2 添加新测试用例

```python
TestCase(
    case_id="NEW-01",
    name="新功能测试",
    description="验证新功能",
    steps=[...],
    expected_results=[...],
    evaluation_dimensions=[...],
    pass_criteria={...},
)
```

---

## 八、注意事项

1. **浏览器实例**：当前框架为骨架代码，需要集成 browser-cdp 的浏览器管理能力
2. **登录态**：需要登录的网站，需要提前配置好登录态
3. **反爬策略**：强反爬网站需要启用反检测模式
4. **性能优化**：高并发测试需要注意资源限制

---

## 九、相关链接

- [网站技术栈调研报告](./website-tech-stack-research.md)
- [兼容性测试框架设计](./compatibility-test-framework-design.md)
- [网站分类标签体系](./website-classification.md)
- [测试用例规格文档](./website-compatibility-test-spec.md)
