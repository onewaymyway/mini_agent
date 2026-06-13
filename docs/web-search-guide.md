# Web Search 使用指南

mini-agent 内置 Web Search 功能，支持多种搜索后端，让你能够获取最新的网络信息。

## 快速开始

```python
# 直接使用 web_search 工具
web_search("Python 3.12 新特性")
```

默认使用 **DuckDuckGo** 后端（免费，无需 API key）。

---

## 搜索后端

mini-agent 支持多种搜索后端，可根据需求选择：

| Provider     | 说明                              | API Key        | 免费额度                    |
|--------------|-----------------------------------|----------------|----------------------------|
| duckduckgo   | DuckDuckGo HTML 搜索（默认）      | 无需           | 无限制（可能被限流）        |
| brave        | Brave Search API                  | BRAVE_API_KEY  | 2,000 次/月                 |
| serper       | Serper.dev（Google 结果代理）     | SERPER_API_KEY | 2,500 次（注册赠送）        |
| tavily       | Tavily AI 搜索（专为 LLM 优化）   | TAVILY_API_KEY | 1,000 次/月                 |

---

## 配置方式

### 1. 配置文件（agent_config.json）

```json
{
  "web_search": {
    "provider": "duckduckgo",
    "api_key": "",
    "max_results": 5,
    "timeout": 10.0
  }
}
```

**配置项说明：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `provider` | string | 搜索后端：`duckduckgo` \| `brave` \| `serper` \| `tavily` |
| `api_key` | string | API key（也可用环境变量） |
| `max_results` | int | 默认返回结果数量（默认 5） |
| `timeout` | float | 请求超时时间（秒，默认 10.0） |

### 2. 环境变量

```bash
# 切换搜索后端
export WEB_SEARCH_PROVIDER=brave

# 配置 API Key（二选一：配置文件或环境变量）
export BRAVE_API_KEY=your_key_here
export SERPER_API_KEY=your_key_here
export TAVILY_API_KEY=your_key_here
```

### 3. 运行时切换

```python
# 代码中临时切换 provider
from mini_agent.web_search.factory import create_web_search_provider

provider = create_web_search_provider(cfg, provider="tavily")
results = provider.search("Python 性能优化", max_results=10)
```

---

## 工具使用

### web_search 工具

```python
@tool
web_search(query: str, max_results: Optional[int] = None, provider: Optional[str] = None) -> str
```

**参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `query` | string | 搜索查询（必填） |
| `max_results` | int | 返回结果数量（默认使用配置文件） |
| `provider` | string | 临时覆盖 provider（`duckduckgo` \| `brave` \| `serper` \| `tavily`） |

**示例：**

```python
# 基本搜索
web_search("如何修复 Git merge conflict")

# 指定结果数量
web_search("机器学习框架对比", max_results=10)

# 临时使用特定后端
web_search("最新 AI 研究进展", provider="tavily")
```

**输出示例：**

```
[web_search via DuckDuckGo] Results for: "Python 3.12 新特性"

1. What's New in Python 3.12
   https://docs.python.org/3.12/whatsnew/3.12.html
   Python 3.12 introduces f-string improvements, better error messages...

2. Python 3.12 Release Notes - Real Python
   https://realpython.com/python-3-12-new-features/
   Explore the new features in Python 3.12 including exception groups...

3. ...（后续结果）
```

---

## 架构设计

```
┌─────────────────────────────────────────────────────────────┐
│                      Agent (builtin.py)                      │
│                        web_search()                          │
└────────────────────────────┬────────────────────────────────┘
                             │
              ┌──────────────▼──────────────┐
              │   Factory (factory.py)      │
              │  create_web_search_provider │
              └──────────────┬──────────────┘
                             │
      ┌──────────────────────┼──────────────────────┐
      │                      │                      │
┌─────▼─────┐        ┌──────▼──────┐       ┌──────▼──────┐
│  DuckDuck │        │   Brave     │       │   Serper    │
│    Go     │        │  Provider   │       │   Provider  │
└───────────┘        └─────────────┘       └─────────────┘
      │                      │                      │
      └──────────────────────┼──────────────────────┘
                             │
              ┌──────────────▼──────────────┐
              │  Base (base.py)             │
              │  WebSearchProvider (ABC)    │
              │  SearchResult (dataclass)   │
              └─────────────────────────────┘
```

**核心模块：**

| 文件 | 说明 |
|------|------|
| `web_search/base.py` | 抽象接口：`WebSearchProvider`、`SearchResult`、`WebSearchError` |
| `web_search/factory.py` | 工厂模式：`create_web_search_provider()`、`register_web_search_provider()` |
| `web_search/providers/` | 具体实现：`duckduckgo.py`、`brave.py`、`serper.py`、`tavily.py` |
| `tools/builtin.py` | `web_search` 工具函数 |

---

## 扩展自定义 Provider

接入新的搜索后端：

```python
# 1. 创建自定义 provider（web_search/providers/my_provider.py）
from mini_agent.web_search.base import WebSearchProvider, SearchResult, WebSearchError

class MyProvider(WebSearchProvider):
    requires_api_key = True
    api_key_env = "MY_API_KEY"

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        # 实现搜索逻辑
        pass

# 2. 注册（factory.py 中）
from mini_agent.web_search.factory import register_web_search_provider
register_web_search_provider("my_engine", MyProvider)

# 3. 使用
web_search("query", provider="my_engine")
```

---

## 注意事项

1. **错误处理**：搜索失败时返回 `WebSearchError`，工具会格式化为可读文本
2. **超时控制**：通过 `timeout` 配置控制请求超时（默认 10 秒）
3. **限流**：DuckDuckGo 无官方 SLA，高频使用建议切换至付费 API
4. **缓存**：可配合 `--tool-cache` 参数缓存搜索结果
5. **优先级**：provider 选择优先级 = 函数参数 > config > 环境变量 > 默认 duckduckgo

---

## 相关文档

- [命令与工具参考](commands-and-tools-reference.md) — web_search 工具说明
- [配置指南](config-guide.md) — 配置文件详解

---

*最后更新：2026-06*