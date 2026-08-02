# Browser CDP 测试框架

基于 pytest 的自动化测试框架，用于测试 browser-cdp skill 的各个模块。

## 目录结构

```
tests/browser-cdp/
├── conftest.py                 # pytest 配置和共享 fixtures
├── test_logging_demo.py        # 框架演示测试
├── README.md                   # 本文件
├── support/                    # 核心框架模块
│   ├── __init__.py
│   ├── test_logger.py          # 测试日志记录器
│   ├── exception_handler.py    # 异常处理与重试机制
│   └── test_reporter.py        # 测试报告生成器
├── templates/                  # 测试用例模板
│   ├── base_test_template.py   # 基础测试类模板
│   ├── test_form_submission.py # 表单提交测试模板
│   ├── test_search_engine.py   # 搜索引擎测试模板
│   ├── test_news_extraction.py # 新闻抓取测试模板
│   ├── test_dynamic_content.py # 动态内容测试模板
│   ├── test_ecommerce_flow.py  # 电商流程测试模板
│   └── test_social_media.py    # 社交媒体测试模板
├── unit/                       # 单元测试（Mock 测试，无需浏览器）
│   ├── test_browser_launch.py
│   ├── test_browser_nav.py
│   ├── test_browser_extract.py
│   ├── test_browser_screenshot.py
│   └── test_browser_input.py
├── integration/                # 集成测试（需要真实浏览器）
│   └── test_ecommerce_flow.py
├── e2e/                        # 端到端测试（完整业务流程）
│   └── test_full_workflow.py
├── fixtures/                   # 测试固定数据
│   ├── pages/                  # 页面 HTML 示例
│   └── mock_data/              # 模拟数据 JSON
│       ├── ecommerce_products.json
│       ├── social_media_posts.json
│       └── news_articles.json
└── .github/workflows/          # CI/CD 配置
    └── test.yml
```

## 快速开始

### 1. 安装依赖

```bash
pip install pytest pytest-asyncio pytest-mock websocket-client requests pillow
```

### 2. 运行单元测试（无需浏览器）

```bash
cd tests/browser-cdp
python -m pytest unit/ -v
```

### 3. 运行框架演示测试

```bash
cd tests/browser-cdp
python -m pytest test_logging_demo.py -v
```

### 4. 运行集成测试（需要 Chrome）

```bash
# 启动带调试端口的 Chrome
google-chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-test &

# 运行集成测试
cd tests/browser-cdp
python -m pytest integration/ -v -m integration
```

### 5. 运行端到端测试

```bash
cd tests/browser-cdp
python -m pytest e2e/ -v -m e2e
```

## 核心框架使用指南

### TestLogger - 测试日志记录

```python
from support import get_logger

logger = get_logger("my_test")
logger.start_test("TestClass", "test_method")

logger.log_step("step_name", "started", {"param": "value"})
# ... 执行测试步骤 ...
logger.log_step("step_name", "completed", {"result": "success"})

logger.end_test()
```

### ExceptionHandler - 异常处理与重试

```python
from support import RetryableOperation, RetryConfig, ErrorCategory, ErrorSeverity

# 使用装饰器
@RetryableOperation(RetryConfig(max_attempts=3, base_delay=1.0))
def flaky_operation():
    # 可能失败的操作
    pass

# 使用上下文管理器
from support import error_boundary

with error_boundary(ErrorCategory.NETWORK, ErrorSeverity.HIGH) as ctx:
    result = risky_operation()
```

### TestReporter - 测试报告生成

```python
from support import TestReporter, TestResult, TestSuiteResult

reporter = TestReporter()
suite = TestSuiteResult(name="MySuite", description="Test suite")

result = TestResult(
    name="test_case",
    status="passed",
    duration=1.5,
    steps=logger.get_steps()
)
suite.add_result(result)
suite.finalize()

# 生成不同格式报告
json_report = reporter.generate_json(suite)
junit_xml = reporter.generate_junit_xml(suite)
html_report = reporter.generate_html(suite)
markdown_report = reporter.generate_markdown(suite)
```

## 编写新测试用例

### 1. 使用模板

从 `templates/` 目录复制相应模板：

```bash
cp templates/base_test_template.py unit/test_my_module.py
```

### 2. 继承 BaseBrowserTest

```python
from templates.base_test_template import BaseBrowserTest

class TestMyFeature(BaseBrowserTest):
    def test_my_feature(self):
        logger = self.logger
        logger.start_test(self.__class__.__name__, "test_my_feature")
        
        # 测试代码
        logger.log_step("step1", "completed", {})
        
        logger.end_test()
```

### 3. 使用 Mock 进行单元测试

```python
from unittest.mock import patch, Mock

@patch('browser_nav.goto')
@patch('browser_extract.extract_text')
def test_with_mocks(self, mock_extract, mock_goto):
    mock_goto.return_value = True
    mock_extract.return_value = "Page content"
    
    # 测试逻辑
    assert True
```

## CI/CD 集成

GitHub Actions 工作流配置在 `.github/workflows/test.yml`：

- **unit-tests**: 每次推送运行，无需浏览器
- **integration-tests**: 需要 Chrome，可选运行
- **e2e-tests**: 完整业务流程测试
- **lint-and-type-check**: 代码质量检查
- **generate-report**: 汇总测试报告

### 本地运行完整 CI 流程

```bash
# 运行所有测试
cd tests/browser-cdp
python -m pytest unit/ integration/ e2e/ -v --tb=short

# 生成 HTML 报告
python -m pytest unit/ --html=report.html --self-contained-html
```

## 常见问题

### Q: pytest 收集警告 "cannot collect test class"

A: 这是正常现象。框架核心类（TestLogger、TestReporter 等）带有 `__init__` 构造函数，pytest 会误认为是测试类。这些警告不影响测试运行。

### Q: 导入 browser-cdp 模块失败

A: 确保 PYTHONPATH 包含 skill 目录：

```bash
export PYTHONPATH=$PYTHONPATH:/path/to/.claude/skills/browser-cdp
```

或在 conftest.py 中自动配置（已内置）。

### Q: Chrome 连接失败

A: 检查 Chrome 是否以调试模式启动：

```bash
google-chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-debug
```

然后访问 `http://localhost:9222/json/version` 验证 CDP 端点可用。

### Q: Windows 上 python3 命令不可用

A: 本环境使用 `python` 命令（Anaconda），不要使用 `python3`。

## 扩展指南

### 添加新的测试模块

1. 在 `unit/` 创建 `test_<module>.py`
2. 使用 Mock 完全隔离浏览器依赖
3. 继承 `BaseBrowserTest` 获取日志和报告功能

### 添加新的网站测试模板

1. 在 `templates/` 创建 `test_<site_type>.py`
2. 参考现有模板结构
3. 在 `mock_data/` 添加对应的模拟数据 JSON

### 自定义错误分类

在 `support/exception_handler.py` 的 `ErrorCategory` 枚举中添加新类别，并在 `BrowserErrorClassifier.classify()` 中添加匹配规则。

## 许可证

MIT License
