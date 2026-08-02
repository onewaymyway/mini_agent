# 无限滚动自动加载

## 概述

`browser_infinite_scroll.py` 提供无限滚动（infinite scroll）页面的自动滚动、内容收集和停止检测功能。

## 核心组件

### InfiniteScrollCollector

内容收集器，支持滚动到底部、分步滚动、稳定检测和元素收集。

```python
from browser_infinite_scroll import InfiniteScrollCollector

collector = InfiniteScrollCollector(session)

# 滚动到页面底部
scroll_count = collector.scroll_to_bottom(step=600, pause=1.0)

# 分步滚动
results = collector.scroll_incremental(steps=5, pause=1.0)

# 检测内容是否稳定
is_stable = collector.is_content_stable(tolerance=3, check_interval=2.0)

# 滚动直到稳定
result = collector.scroll_until_stable(tolerance=3, max_pages=20)

# 收集指定选择器的元素
items = collector.collect_items(".post", max_items=100)

# 滚动收集（去重）
items = collector.collect_with_scroll(".post", max_pages=10, pause=1.5)
```

### ScrollPatternDetector

滚动模式检测器，识别页面是否使用无限滚动。

```python
from browser_infinite_scroll import ScrollPatternDetector

detector = ScrollPatternDetector(session)

# 检测滚动容器
container = detector.detect_scroll_container()

# 检测是否有滚动加载行为
load_info = detector.detect_load_on_scroll()
```

## 命令行用法

```bash
# 滚动到底部
python browser_infinite_scroll.py --tab <id> --scroll-to-bottom --max-pages 10 --output results.json

# 滚动指定次数
python browser_infinite_scroll.py --tab <id> --scroll-count 5 --delay 2

# 滚动直到内容稳定
python browser_infinite_scroll.py --tab <id> --scroll-until-stable --tolerance 3

# 收集指定元素
python browser_infinite_scroll.py --tab <id> --item-selector ".post" --max-items 100 --output items.json

# 滚动收集（去重）
python browser_infinite_scroll.py --tab <id> --item-selector ".post" --max-pages 10 --output items.json

# 检测滚动模式
python browser_infinite_scroll.py --tab <id> --detect-scroll-pattern
```

## 输出格式

### 滚动结果

```json
{
  "success": true,
  "pages_scrolled": 8,
  "start_height": 800,
  "end_height": 12000,
  "height_increase": 11200,
  "at_bottom": true,
  "scroll_history": [
    {"step": 1, "scrollTop": 600, "scrollHeight": 2000},
    {"step": 2, "scrollTop": 1200, "scrollHeight": 4000}
  ]
}
```

### 元素收集结果

```json
[
  {
    "index": 0,
    "tag": "article",
    "text": "文章内容...",
    "href": "https://example.com/post/1",
    "visible": true,
    "rect": {"x": 0, "y": 100, "width": 800, "height": 200}
  }
]
```

## 最佳实践

1. **设置合理的 pause 时间**：根据页面加载速度调整，一般 1-2 秒
2. **使用 max-pages 限制**：防止无限滚动导致内存溢出
3. **检测滚动稳定**：在滚动到底部后等待稳定，确保内容完全加载
4. **去重收集**：使用 `collect_with_scroll` 自动去重，避免重复收集

## 常见场景

### 场景 1：抓取社交媒体时间线

```bash
# 滚动收集所有帖子
python browser_infinite_scroll.py --tab <id> \
  --item-selector ".timeline-item" \
  --max-pages 20 \
  --pause 1.5 \
  --output posts.json
```

### 场景 2：抓取电商商品列表

```bash
# 滚动到底部并收集商品信息
python browser_infinite_scroll.py --tab <id> \
  --scroll-to-bottom \
  --max-pages 10 \
  --output scroll_result.json

python browser_infinite_scroll.py --tab <id> \
  --item-selector ".product-item" \
  --max-items 200 \
  --output products.json
```

### 场景 3：抓取新闻列表

```bash
# 滚动直到内容稳定
python browser_infinite_scroll.py --tab <id> \
  --scroll-until-stable \
  --tolerance 3 \
  --check-interval 2.0
```
