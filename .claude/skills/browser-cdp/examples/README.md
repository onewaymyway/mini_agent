# Browser CDP Examples

本目录包含 browser-cdp skill 的使用示例。

## 示例列表

| 文件名 | 描述 |
|--------|------|
| `playwright_demo.py` | Playwright 集成示例：导航、搜索、滚动、JS 执行 |
| `browser_cdp_demo.py` | CDP 基础操作示例 |
| `search_examples.py` | 搜索器使用示例 |

## 运行示例

```bash
# 进入 skill 目录
cd .claude/skills/browser-cdp

# 运行 Playwright 示例
python examples/playwright_demo.py

# 运行 CDP 示例
python examples/browser_cdp_demo.py

# 运行搜索示例
python examples/search_examples.py
```

## 前置条件

- Python 3.10+
- Playwright 已安装: `pip install playwright`
- 浏览器已安装: `playwright install chromium`

## 测试

```bash
# 运行单元测试
cd .claude/skills/browser-cdp
python -m pytest tests/unit/ -v

# 运行集成测试
cd .claude/skills/browser-cdp
python -m pytest tests/integration/ -v
```
