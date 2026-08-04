# B站搜索器（bilibili_search.py）完整文档

## 概述

B站搜索器通过 CDP 控制浏览器访问 bilibili.com，支持视频搜索和 UP 主搜索，自动处理无限滚动加载。

## 类结构

```python
from src.searchers.bilibili_search import BilibiliSearcher, BilibiliConfig
from src.searchers.base import SearcherConfig
```

### BilibiliConfig

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `wait_timeout` | int | 30 | 等待超时（秒） |
| `max_results` | int | 20 | 最大结果数 |
| `retry_count` | int | 3 | 重试次数 |
| `search_type` | str | "video" | 搜索类型：video/user |
| `scroll_limit` | int | 10 | 最大滚动次数 |
| `scroll_delay` | float | 0.8 | 滚动间隔（秒） |

### BilibiliSearcher

| 方法 | 说明 |
|------|------|
| `search(keyword, search_type=None)` | 搜索视频或 UP 主 |
| `get_video_detail(video_id)` | 获取视频详情 |
| `get_user_detail(user_id)` | 获取 UP 主详情 |
| `search_batch(keywords)` | 批量搜索 |
| `close()` | 关闭浏览器 |

## 使用示例

### 基本搜索

```python
from src.searchers.bilibili_search import BilibiliSearcher

searcher = BilibiliSearcher()
results = searcher.search('Python 教程')

for item in results.results:
    print(f"标题: {item.title}")
    print(f"URL: {item.url}")
    print(f"作者: {item.metadata.get('author', 'N/A')}")
    print(f"播放量: {item.metadata.get('play_count', 'N/A')}")
    print("---")

searcher.close()
```

### 搜索 UP 主

```python
searcher = BilibiliSearcher()
results = searcher.search('老番茄', search_type='user')

for item in results.results:
    print(f"UP 主: {item.title}")
    print(f"粉丝数: {item.metadata.get('fans', 'N/A')}")
    print(f"URL: {item.url}")
```

### 批量搜索

```python
searcher = BilibiliSearcher()
results = searcher.search_batch(['Python', 'JavaScript', 'Go'])

# 保存结果
results.save_json('output/bilibili_results.json')
results.save_csv('output/bilibili_results.csv')
```

### 获取视频详情

```python
searcher = BilibiliSearcher()
detail = searcher.get_video_detail('BV1xx411c7mD')
print(f"标题: {detail.title}")
print(f"简介: {detail.snippet}")
print(f"标签: {detail.metadata.get('tags', [])}")
```

## 输出格式

### 搜索结果

```json
{
  "source": "bilibili",
  "title": "Python 入门教程",
  "url": "https://www.bilibili.com/video/BV1xx411c7mD",
  "snippet": "从零开始学习 Python 编程...",
  "published_time": "2024-01-15",
  "author": "技术教程",
  "metadata": {
    "play_count": 1234567,
    "danmaku_count": 8888,
    "like_count": 56789,
    "coin_count": 12345,
    "favorite_count": 6789,
    "share_count": 2345,
    "duration": "10:30",
    "tags": ["Python", "教程", "入门"]
  },
  "scraped_at": "2024-01-20T10:30:00Z"
}
```

### UP 主结果

```json
{
  "source": "bilibili",
  "title": "老番茄",
  "url": "https://space.bilibili.com/12345678",
  "snippet": "知名游戏 UP 主...",
  "author": "老番茄",
  "metadata": {
    "fans": 10000000,
    "videos": 500,
    "likes": 50000000,
    "following": 100,
    "level": 6
  },
  "scraped_at": "2024-01-20T10:30:00Z"
}
```

## 核心实现要点

### 1. 搜索 URL 构建

```python
BASE_URL = "https://search.bilibili.com/all"

def _build_search_url(keyword: str, search_type: str = "video") -> str:
    if search_type == "user":
        return f"https://search.bilibili.com/upuser?keyword={keyword}"
    return f"{BASE_URL}?keyword={keyword}"
```

### 2. 视频列表解析

```python
def _extract_video_results(html: str) -> list:
    # 使用 CSS 选择器提取视频卡片
    # 解析 JSON 数据获取详细信息
    pass
```

### 3. 无限滚动加载

```python
def _scroll_to_load(self, session, max_scrolls: int = 10) -> int:
    """滚动加载更多内容"""
    from src.core.dynamic_loader import DynamicLoader
    loader = DynamicLoader(session)
    return loader.scroll_to_load(
        max_height_change=500,
        height_threshold=100,
        max_scrolls=max_scrolls,
        scroll_delay=0.8
    )
```

### 4. 去重策略

```python
# 基于 URL 去重
results.deduplicate(by="url")

# 基于标题去重（相似度阈值 0.9）
results.deduplicate(by="title", threshold=0.9)
```

## 已知限制

1. **登录态依赖**：部分视频需要登录才能查看完整信息
2. **反爬机制**：高频搜索可能触发验证码
3. **数据延迟**：播放量等数据可能有延迟更新
4. **分页限制**：最多加载 200 个结果

## 最佳实践

1. **控制搜索频率**：每次搜索间隔至少 2 秒
2. **使用去重**：批量搜索时启用去重避免重复
3. **限制滚动次数**：设置合理的 `scroll_limit` 避免过度加载
4. **异常处理**：捕获 `CDPError` 和超时异常

## 测试覆盖

- 配置测试：默认值、自定义值、序列化
- 搜索类型测试：视频搜索、UP 主搜索
- 结果解析测试：完整数据、空数据、异常数据
- 滚动加载测试：停止条件、继续条件
- 集成测试：搜索流程、详情获取、批量搜索
- 边界测试：空结果、JS 错误、JSON 解析错误
- 保存测试：JSON、CSV 格式
- 去重测试：URL 去重、标题去重

共 23 个测试用例，全部通过。
