# SPA 页面抓取框架使用文档

## 概述

`spa_scraper.py` 是 browser-cdp skill 的高层抓取框架，整合了智能等待、SPA 框架检测、无限滚动、内容提取等能力。

## 核心模块

### 1. SPAScraper - 主抓取器

```python
from src.core.spa_scraper import SPAScraper

# 创建抓取器
scraper = SPAScraper(session)

# 抓取单个页面
result = scraper.scrape(
    url="https://example.com",
    selectors=[".item", ".title"],
    scroll_to_load=True,
)

# 抓取搜索结果（带分页）
results = scraper.scrape_search(
    search_url="https://example.com/search?q={query}",
    item_selector=".result-item",
    max_pages=5,
)
```

### 2. EnhancedDynamicLoader - 无限滚动

```python
from src.core.enhanced_dynamic_loader import EnhancedDynamicLoader, ScrollConfig

loader = EnhancedDynamicLoader(session, ScrollConfig(
    max_pages=10,
    scroll_delay=0.8,
    item_selector=".video-item",
    height_threshold=100,
))

result = loader.smart_scroll(max_pages=5)
print(f"加载页数: {result.pages_loaded}")
print(f"找到项目: {result.items_found}")
```

### 3. PopupHandler - 弹窗处理

```python
from src.core.popup_handler import PopupHandler

handler = PopupHandler(session)

# 检测弹窗
popups = handler.detect_popups()
print(f"检测到 {len(popups)} 个弹窗")

# 处理弹窗
result = handler.handle_popups(auto_close=True, timeout=5.0)
print(f"关闭 {result['popups_closed']} 个弹窗")
```

### 4. browser_load - 统一加载接口

```python
from src.core.browser_load import load_page

result = load_page(
    session,
    url="https://example.com",
    mode="text",  # html/text/elements/forms/links/meta
    wait_for="networkidle",
    timeout=30,
)

print(f"标题: {result.title}")
print(f"内容长度: {len(result.data.get('content', ''))}")
```

## 使用示例

### 示例 1: 抓取知乎搜索

```python
from src.core.cdp_client import list_tabs, connect_tab
from src.core.browser_load import load_page

tabs = list_tabs()
session = connect_tab(tabs[0])

result = load_page(session, "https://www.zhihu.com/search?type=content&q=Python")
print(f"标题: {result.title}")
print(f"内容: {result.data.get('content', '')[:500]}")

session.close()
```

### 示例 2: 抓取 B站视频列表

```python
from src.core.cdp_client import list_tabs, connect_tab
from src.core.enhanced_dynamic_loader import EnhancedDynamicLoader, ScrollConfig

tabs = list_tabs()
session = connect_tab(tabs[0])

# 导航到 B站
session.send('Page.navigate', {'url': 'https://search.bilibili.com/all?keyword=Python'})

# 滚动加载
loader = EnhancedDynamicLoader(session, ScrollConfig(
    max_pages=5,
    scroll_delay=0.5,
    item_selector='.bili-video-card',
))

result = loader.smart_scroll(max_pages=3)
print(f"找到 {result.items_found} 个视频")

session.close()
```

### 示例 3: 处理弹窗

```python
from src.core.cdp_client import list_tabs, connect_tab
from src.core.popup_handler import PopupHandler

tabs = list_tabs()
session = connect_tab(tabs[0])

# 导航到可能有弹窗的页面
session.send('Page.navigate', {'url': 'https://www.bilibili.com/'})

# 处理弹窗
handler = PopupHandler(session)
result = handler.handle_popups(auto_close=True)
print(f"关闭了 {result['popups_closed']} 个弹窗")

session.close()
```

## API 参考

### SPAScraper

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `scrape()` | url, selectors, scroll_to_load, wait_for, extract_js, save_path | ScrapeResult | 抓取单个页面 |
| `scrape_search()` | search_url, query, item_selector, max_pages | List[ScrapeResult] | 抓取搜索结果 |

### EnhancedDynamicLoader

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `smart_scroll()` | max_pages, stop_condition, callback | ScrollResult | 智能无限滚动 |
| `_detect_scroll_container()` | - | str | 检测滚动容器 |
| `_get_scroll_height()` | selector | int | 获取滚动高度 |
| `_count_items()` | - | int | 统计项目数 |
| `_scroll_page()` | container | bool | 执行一次滚动 |

### PopupHandler

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `detect_popups()` | - | List[PopupInfo] | 检测弹窗 |
| `handle_popups()` | auto_close, timeout | Dict | 处理弹窗 |
| `get_popup_history()` | - | List[Dict] | 获取历史记录 |

## 配置选项

### ScrollConfig

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `max_pages` | 10 | 最大滚动页数 |
| `scroll_distance` | 800 | 每次滚动距离（像素） |
| `scroll_delay` | 0.8 | 滚动间隔（秒） |
| `height_threshold` | 100 | 高度变化阈值（像素） |
| `item_selector` | "" | 列表项选择器 |
| `loader_selector` | ".loading, .load-more" | 加载指示器选择器 |
| `bottom_selector` | ".end-of-list" | 底部指示器选择器 |
| `smart_detect` | True | 是否启用智能检测 |
| `scroll_style` | "natural" | 滚动风格 |

### SPAScraper Config

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `wait_timeout` | 30 | 等待超时（秒） |
| `scroll_max_pages` | 10 | 最大滚动页数 |
| `scroll_delay` | 0.8 | 滚动间隔（秒） |
| `extract_timeout` | 10 | 提取超时（秒） |
| `stealth` | True | 是否启用反检测 |

## 已知限制

1. **知乎反爬**: 知乎检测到自动化访问会返回 403 错误，需要启用 stealth 模式或添加真实用户代理
2. **GitHub 限流**: GitHub 搜索页面有请求频率限制，频繁访问可能被临时封禁
3. **B站首页**: B站首页没有无限滚动，只有搜索页有

## 下一步

- 步骤 6/6: 编写使用文档（当前）
- 部署到生产环境并监控运行状态
