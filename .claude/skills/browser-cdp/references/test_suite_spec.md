# Browser CDP 技能测试套件规范

**版本**: 1.0.0  
**最后更新**: 2026-07-28  
**状态**: 待执行验证  
**纳入版本控制**: ✅

---

## 1. 测试套件概述

本测试套件为 browser-cdp 技能提供全面的测试覆盖，包括：

- **核心功能测试**: 验证 browser-cdp 各模块的基本功能
- **场景化测试**: 针对电商、新闻、搜索、社交、表单等常见网站类型
- **专项测试**: 处理动态内容、SPA、AJAX、无限滚动等复杂交互场景
- **基类模板**: 统一的测试框架和断言方法，确保测试一致性

---

## 2. 测试文件清单

### 2.1 基础模板

| 文件 | 大小 | 描述 |
|------|------|------|
| `base_test_template.py` | 7.3 KB | 基础测试基类，包含 mock 浏览器实例、断言方法、测试数据工厂 |

### 2.2 场景化测试模板

| 文件 | 大小 | 测试用例数 | 优先级 | 覆盖场景 |
|------|------|-----------|--------|----------|
| `test_ecommerce_flow.py` | 7.0 KB | 8 | P1 | 电商购物流程（搜索、查看详情、加入购物车、购物车、结算） |
| `test_news_extraction.py` | 8.1 KB | 10 | P1 | 新闻资讯提取（文章正文、分页、元数据、图片） |
| `test_search_engine.py` | 9.7 KB | 12 | P1 | 搜索引擎（查询构建、结果解析、自动补全、分页） |
| `test_social_media.py` | 10.4 KB | 12 | P1 | 社交媒体（无限滚动、发帖、点赞评论、通知） |
| `test_form_submission.py` | 14.4 KB | 15 | P1 | 表单提交（字段填写、验证、文件上传、多步骤） |

### 2.3 专项测试模板

| 文件 | 大小 | 测试用例数 | 优先级 | 覆盖场景 |
|------|------|-----------|--------|----------|
| `test_dynamic_content.py` | 10.6 KB | 10 | P2 | 动态内容专项（无限滚动、AJAX、SPA、懒加载、弹窗、WebSocket） |

### 2.4 文档与配置

| 文件 | 大小 | 描述 |
|------|------|------|
| `test_cases_priority.md` | 15.5 KB | 测试用例优先级分组文档，按 P0-P2 分类 |
| `test_case_library.md` | 13.0 KB | 标准测试用例库文档，纳入版本控制 |
| `test_report.md` | 4.7 KB | 测试报告文档，记录执行结果和问题 |
| `conftest.py` | 2.0 KB | pytest 配置文件，提供全局 fixture |
| `README.md` | 2.4 KB | 使用说明文档 |
| `run_browser_cdp_tests.py` | - | 测试执行脚本 |

---

## 3. 测试用例总数统计

| 类别 | 测试用例数 |
|------|-----------|
| P0 (核心功能) | 15 (来自 base_test_template.py 的模块级测试) |
| P1 (重要功能) | 60 (5 个场景化测试模板) |
| P2 (辅助功能) | 10 (动态内容专项测试) |
| **总计** | **85** |

---

## 4. 测试执行规范

### 4.1 环境准备

```bash
# 进入 tests 目录
cd tests

# 安装依赖
pip install unittest mock requests pillow websocket-client pytest
```

### 4.2 执行命令

```bash
# 运行所有测试
python -m pytest browser-cdp/templates/ -v

# 运行单个模块
python -m pytest browser-cdp/templates/test_ecommerce_flow.py -v

# 使用自定义脚本执行
python run_browser_cdp_tests.py

# 生成 HTML 报告
python -m pytest browser-cdp/templates/ --html=report.html --self-contained-html
```

### 4.3 测试输出示例

```bash
============================= test session starts =============================
collected 67 items

test_ecommerce_flow.py .......                                         [ 11%]
test_news_extraction.py ............                                   [ 28%]
test_search_engine.py .........................                        [ 46%]
test_social_media.py ..................................................   [ 64%]
test_form_submission.py ................................................. [ 86%]
test_dynamic_content.py ............                                 [100%]

============================ 67 passed in 15.23 seconds =============================
```

---

## 5. 测试用例设计原则

### 5.1 命名规范

- **测试文件名**: `test_[场景]_[类型].py` (如 `test_ecommerce_flow.py`)
- **测试类名**: `Test[场景][类型]` (如 `TestEcommerceFlow`)
- **测试方法**: `test_[序号]_[描述]` (如 `test_01_navigate_to_store`)

### 5.2 测试结构

```python
class TestEcommerceFlow(BaseBrowserTest):
    """电商购物流程测试用例"""

    def setUp(self):
        super().setUp()
        self._setup_ecommerce_mocks()

    def test_01_navigate_to_store(self):
        """测试：导航到电商网站首页"""
        with patch.object(browser_nav, "goto") as mock_goto:
            mock_goto.return_value = True
            result = browser_nav.goto("https://example-store.com/home")
            self.assertTrue(result)
            self.assertTabUrlContains("test-tab-1", "example-store.com")
```

### 5.3 Mock 策略

- 使用 `unittest.mock` 模拟浏览器交互
- 避免实际启动浏览器进行单元测试
- 验证函数调用参数和返回值
- 模拟各种边界情况（超时、错误响应等）

---

## 6. 集成测试计划

### 6.1 真实浏览器测试

当单元测试通过后，应进行真实浏览器集成测试：

```bash
# 启动专用浏览器实例
cd .claude/skills/browser-cdp
python browser_launch.py --dedicated --name test-session --start-url "https://example.com"

# 然后运行真实浏览器测试（需修改测试脚本以使用真实浏览器）
```

### 6.2 测试覆盖矩阵

| 模块 | 单元测试 | 集成测试 | E2E 测试 |
|------|---------|---------|---------|
| browser_launch | ✓ | ○ | ○ |
| browser_nav | ✓ | ○ | ○ |
| browser_extract | ✓ | ○ | ○ |
| browser_input | ✓ | ○ | ○ |
| browser_screenshot | ✓ | ○ | ○ |
| browser_console | ○ | ○ | ○ |
| 电商场景 | ✓ | ○ | ○ |
| 新闻场景 | ✓ | ○ | ○ |
| 搜索场景 | ✓ | ○ | ○ |
| 社交场景 | ✓ | ○ | ○ |
| 表单场景 | ✓ | ○ | ○ |
| 动态内容 | ✓ | ○ | ○ |

> ✓ = 已实现, ○ = 待实现, ✗ = 未计划

---

## 7. 持续集成建议

### 7.1 CI 流水线配置示例 (GitHub Actions)

```yaml
name: Browser CDP Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Set up Python
        uses: actions/setup-python@v2
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          pip install unittest mock requests pillow websocket-client pytest
      - name: Run tests
        run: |
          cd tests
          python -m pytest browser-cdp/templates/ -v --html=report.html
      - name: Upload test results
        if: always()
        uses: actions/upload-artifact@v2
        with:
          name: test-results
          path: tests/report.html
```

### 7.2 测试覆盖率要求

- 核心模块 (browser_launch, browser_nav, browser_extract, browser_input, browser_screenshot): ≥ 90%
- 场景化测试模块: ≥ 80%
- 专项测试模块: ≥ 70%

---

## 8. 变更管理

### 8.1 修改现有测试用例

1. 备份原始测试用例（如有必要）
2. 修改测试方法并添加注释说明变更原因
3. 运行测试确保修改后仍通过
4. 更新 test_case_library.md 中的描述

### 8.2 添加新测试用例

1. 在相应的 test_*.py 文件中添加新的测试方法
2. 遵循现有的命名规范和测试结构
3. 更新 test_cases_priority.md 中的优先级列表
4. 提交时注明测试用例覆盖的场景和功能

### 8.3 版本发布

每次重大变更后，更新 test_case_library.md 的版本号：

| 版本 | 日期 | 作者 | 变更说明 |
|------|------|------|----------|
| 1.0.0 | 2026-07-28 | Orzooo | 初始版本，创建完整测试用例库 |
| 1.0.1 | YYYY-MM-DD | [Author] | 添加/修改测试用例 |

---

## 9. 问题跟踪

### 9.1 已知问题

| ID | 描述 | 严重程度 | 状态 |
|----|------|----------|------|
| TC-001 | browser_console.py 缺少测试模板 | 中 | 待创建 |
| TC-002 | browser_watch.py 缺少测试模板 | 中 | 待创建 |
| TC-003 | 缺少真实浏览器集成测试 | 高 | 待实现 |
| TC-004 | 缺少端到端测试用例 | 高 | 待实现 |

### 9.2 问题报告格式

```markdown
## 测试用例问题报告

**测试用例**: test_ecommerce_flow.py::TestEcommerceFlow::test_04_add_to_cart
**优先级**: P1
**失败类型**: AssertionError
**错误信息**: ...
**预期行为**: ...
**实际行为**: ...
**可能原因**: ...
**修复建议**: ...
```

---

*此测试套件规范纳入版本控制，请随测试用例库更新同步修订*
