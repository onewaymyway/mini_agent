# 微博搜索自动化脚本

## 概述

`weibo_search.py` 是微博搜索自动化脚本，支持两种模式：
- **热搜榜模式**：获取微博实时热搜榜单
- **关键词搜索模式**：搜索微博内容

## 安装与依赖

```bash
pip install websocket-client requests pillow
```

## 命令行使用

### 获取热搜榜

```bash
cd .claude/skills/browser-cdp
python src/searchers/weibo_search.py --type hot --max-results 50
```

### 关键词搜索

```bash
python src/searchers/weibo_search.py --type keyword --query "AI Agent" --max-results 10
```

### 保存到指定目录

```bash
python src/searchers/weibo_search.py --type hot --output-dir ./weibo_results
```

## 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--type` | str | `hot` | 搜索类型：`hot`（热搜榜）/ `keyword`（关键词搜索） |
| `--query` | str | `""` | 搜索关键词（type=keyword 时使用） |
| `--max-results` | int | 20 | 最大结果数 |
| `--output-dir` | str | `None` | 输出目录 |
| `--port` | int | 9333 | 浏览器调试端口 |
| `--tab` | str | `None` | Tab ID |
| `--stealth` | bool | `True` | 启用反检测模式 |
| `--no-stealth` | flag | - | 禁用反检测模式 |
| `--wait-timeout` | int | 30 | 等待超时时间（秒） |

## Python API 使用示例

```python
from src.searchers.weibo_search import WeiboSearcher

searcher = WeiboSearcher()

# 获取热搜榜
hot_results = searcher.search(
    query="",
    search_type="hot",
    max_results=50,
    port=9333
)

# 关键词搜索
keyword_results = searcher.search(
    query="Python",
    search_type="keyword",
    max_results=20,
    port=9333
)

# 获取详情
detail = searcher.get_detail(
    url="https://weibo.com/...",
    port=9333
)
```

## 输出格式

### 热搜榜结果

```json
[
  {
    "rank": 1,
    "title": "热搜标题",
    "hot_value": "1234万",
    "url": "https://s.weibo.com/weibo?q=...",
    "source": "weibo",
    "scraped_at": "2026-08-04 08:00:00"
  }
]
```

### 关键词搜索结果

```json
[
  {
    "title": "微博正文内容",
    "author": "用户名",
    "time": "2026-08-04",
    "reposts": 100,
    "comments": 50,
    "likes": 200,
    "url": "https://weibo.com/...",
    "source": "weibo",
    "scraped_at": "2026-08-04 08:00:00"
  }
]
```

## 注意事项

1. **登录态**：微博部分功能需要登录态，建议首次使用时手动登录
2. **反爬机制**：微博有较严格的反爬机制，建议使用 stealth 模式
3. **频率控制**：避免高频请求，建议每次搜索间隔 2-5 秒
4. **热搜榜更新**：热搜榜每 5 分钟更新一次，建议不要过于频繁获取

## 核心实现要点

- 使用 `browser_nav.py` 导航到微博页面
- 使用 `browser_console.py` 执行 JS 提取内容
- 支持多种 CSS 选择器适配页面结构变化
- 内置登录态检测和去重机制
