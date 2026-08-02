# SPA 路由等待与动态内容检测

## 概述

`browser_smart_wait.py` 提供针对 SPA（单页应用）和动态加载网站的智能等待策略，解决传统 `Page.loadEventFired` 等待机制在 SPA 场景下的失效问题。

## 核心组件

### SPARouteWatcher

监听 SPA 路由变化（pushState/replaceState/popState/hashchange）。

```python
from browser_smart_wait import SPARouteWatcher

watcher = SPARouteWatcher(session)

# 获取 SPA 路由事件
events = watcher.get_spa_events()

# 判断 URL 是否为 SPA 路由
is_spa = watcher.is_spa_route("https://example.com/article/123")
```

### DynamicContentWatcher

检测页面加载状态和动态内容。

```python
from browser_smart_wait import DynamicContentWatcher

watcher = DynamicContentWatcher(session)

# 检测是否处于加载状态
is_loading = watcher.is_loading()

# 等待特定内容出现
watcher.wait_for_content("加载完成", timeout=15)

# 获取内容长度
length = watcher.get_content_length()
```

### NetworkIdleWatcher

等待网络请求空闲。

```python
from browser_smart_wait import NetworkIdleWatcher

watcher = NetworkIdleWatcher(session)

# 等待网络空闲 2 秒
watcher.wait_for_network_idle(idle_time=2.0, timeout=30.0)

# 获取当前活跃请求数
active = watcher.get_active_requests()
```

### InfiniteScrollDetector

检测滚动状态和稳定性。

```python
from browser_smart_wait import InfiniteScrollDetector

detector = InfiniteScrollDetector(session)

# 获取滚动状态
info = detector.get_scroll_info()

# 等待滚动稳定
detector.wait_for_scroll_stable(check_interval=1.0, max_checks=10)
```

## 命令行用法

```bash
# SPA 路由等待
python browser_smart_wait.py --tab <id> --wait-url-contains "search"
python browser_smart_wait.py --tab <id> --wait-url-changed --from-url "https://example.com"

# 动态内容等待
python browser_smart_wait.py --tab <id> --wait-content "加载完成"
python browser_smart_wait.py --tab <id> --wait-selector-visible "#result-list"

# 网络空闲检测
python browser_smart_wait.py --tab <id> --wait-network-idle --idle-time 2

# 滚动稳定检测
python browser_smart_wait.py --tab <id> --wait-scroll-stable

# 查询命令
python browser_smart_wait.py --tab <id> --get-spa-events
python browser_smart_wait.py --tab <id> --get-scroll-info
python browser_smart_wait.py --tab <id> --check-loading
```

## 集成到 browser_nav.py

`browser_nav.py` 已集成智能等待功能：

```bash
# 导航后等待 URL 变化
python browser_nav.py --tab <id> --goto "https://example.com" --wait-url-contains "result"

# 等待内容加载
python browser_nav.py --tab <id> --goto "https://example.com" --wait-content "数据加载完成"

# 等待网络空闲
python browser_nav.py --tab <id> --goto "https://example.com" --wait-network-idle
```

## 最佳实践

1. **SPA 场景优先使用 URL 等待**：不要死等 `Page.loadEventFired`，改用 `--wait-url-contains`
2. **动态内容等待关键词**：选择页面加载完成后必然出现的独特关键词
3. **网络空闲检测**：适合 AJAX 加载较多的页面，等待所有请求完成
4. **滚动稳定检测**：适合无限滚动页面，检测内容是否加载完毕

## 常见场景

### 场景 1：SPA 搜索结果页

```bash
# 搜索后等待结果页加载
python browser_nav.py --tab <id> --goto "https://example.com/search?q=test" --wait-url-contains "/search?q=test"
python browser_smart_wait.py --tab <id> --wait-content "共找到" --timeout 10
```

### 场景 2：动态加载内容

```bash
# 等待加载指示器消失
python browser_smart_wait.py --tab <id> --wait-selector-visible "#content"
python browser_smart_wait.py --tab <id> --wait-network-idle --idle-time 1
```

### 场景 3：无限滚动页面

```bash
# 滚动到底部并等待稳定
python browser_infinite_scroll.py --tab <id> --scroll-to-bottom --max-pages 10
python browser_smart_wait.py --tab <id> --wait-scroll-stable
```
