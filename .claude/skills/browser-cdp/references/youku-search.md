# 优酷搜索器（youku_search.py）完整文档

## 概述

优酷搜索器通过 CDP 控制浏览器访问 youku.com，支持视频搜索和详情抓取，适用于影视剧、综艺、动漫等内容检索。

## 类结构

```python
from src.searchers.youku_search import YoukuSearcher, YoukuConfig
from src.searchers.base import SearcherConfig
```

### YoukuConfig

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `wait_timeout` | int | 30 | 等待超时（秒） |
| `max_results` | int | 20 | 最大结果数 |
| `search_type` | str | "video" | 搜索类型：video/drama/variety/animation |
| `fetch_details` | bool | True | 是否抓取视频详情 |
| `max_scroll_pages` | int | 3 | 最大滚动页数 |
| `session_name` | str | "youku_session" | 浏览器实例名 |

### YoukuSearcher

| 方法 | 说明 |
|------|------|
| `search(query, search_type, max_results, ...)` | 搜索视频 |
| `get_detail(url, ...)` | 获取视频详情 |
| `close()` | 关闭浏览器 |

## 使用示例

### 基本搜索

```python
from src.searchers.youku_search import YoukuSearcher

searcher = YoukuSearcher()
results = searcher.search('庆余年')

for item in results.results:
    print(f"标题: {item.title}")
    print(f"URL: {item.url}")
    print(f"作者: {item.author}")
    print(f"播放量: {item.metadata.get('play_count', 'N/A')}")
    print(f"时长: {item.metadata.get('duration', 'N/A')}")
    print("---")
```

### 搜索电视剧

```python
searcher = YoukuSearcher()
results = searcher.search('甄嬛传', search_type='drama')

for item in results.results:
    print(f"剧名: {item.title}")
    print(f"评分: {item.metadata.get('score', 'N/A')}")
    print(f"URL: {item.url}")
```

### 搜索综艺

```python
searcher = YoukuSearcher()
results = searcher.search('奔跑吧', search_type='variety')

for item in results.results:
    print(f"综艺: {item.title}")
    print(f"期数: {item.metadata.get('type', 'N/A')}")
```

### 批量搜索并保存

```python
searcher = YoukuSearcher()
results = searcher.search(
    query='Python教程',
    search_type='video',
    max_results=30,
    output_dir='./youku_results',
    enable_scroll=True,
)

# 结果已自动保存为 JSON 文件
```

### 获取视频详情

```python
searcher = YoukuSearcher()
detail = searcher.get_detail('https://v.youku.com/v_show/id_XXX.html')
print(f"标题: {detail.get('title')}")
print(f"简介: {detail.get('description')}")
print(f"播放量: {detail.get('play_count')}")
print(f"弹幕数: {detail.get('danmu_count')}")
print(f"标签: {detail.get('tags')}")
```

## 命令行用法

### 基本搜索

```bash
python youku_search.py "庆余年" --max-results 10
```

### 搜索电视剧

```bash
python youku_search.py "甄嬛传" --type drama --output-dir ./youku_results
```

### 无头模式（服务器/纯抓取）

```bash
python youku_search.py "Python教程" --port 9333 --headless
```

### 启用滚动加载更多

```bash
python youku_search.py "综艺" --type variety --scroll --max-results 30
```

### 不抓取详情（加速）

```bash
python youku_search.py "动漫" --type animation --no-details
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `query` | 搜索关键词（必填） | - |
| `--type` / `-t` | 搜索类型：video/drama/variety/animation | video |
| `--max-results` / `-m` | 最大结果数 | 20 |
| `--output-dir` / `-o` | 输出目录 | - |
| `--port` | 浏览器调试端口 | 9333 |
| `--tab` | Tab ID（可选，自动获取） | - |
| `--stealth` | 启用反检测模式 | True |
| `--no-stealth` | 禁用反检测模式 | - |
| `--wait-timeout` | 等待超时时间（秒） | 30 |
| `--headless` | 无头模式 | False |
| `--no-details` | 不抓取视频详情 | False |
| `--scroll` | 启用滚动加载更多 | False |

## 输出格式

### 搜索结果

```json
{
  "source": "youku",
  "query": "庆余年",
  "total_results": 10,
  "results": [
    {
      "source": "youku",
      "title": "庆余年 第一季 全集",
      "url": "https://v.youku.com/v_show/id_XXX.html",
      "snippet": "范闲穿越到庆国...",
      "published_time": "2019-11-01",
      "author": "腾讯视频",
      "metadata": {
        "play_count": "12345678",
        "duration": "45:30",
        "type": "电视剧",
        "score": "8.9"
      },
      "scraped_at": "2024-01-20T10:30:00"
    }
  ],
  "metadata": {
    "search_type": "drama"
  },
  "error": null
}
```

### 视频详情

```json
{
  "source": "youku",
  "title": "庆余年 第一季 全集",
  "url": "https://v.youku.com/v_show/id_XXX.html",
  "description": "范闲穿越到庆国...",
  "play_count": "12345678",
  "danmu_count": "88888",
  "duration": "45:30",
  "author": "腾讯视频",
  "publish_time": "2019-11-01",
  "tags": ["古装", "剧情", "穿越"],
  "score": "8.9",
  "scraped_at": "2024-01-20T10:30:00"
}
```

## 核心实现要点

### 1. 搜索 URL 构建

```python
BASE_URL = "https://so.youku.com"

# 视频搜索
https://so.youku.com/search_video/q_{keyword}

# 电视剧搜索
https://so.youku.com/search_video/q_{keyword}?searchType=drama

# 综艺搜索
https://so.youku.com/search_video/q_{keyword}?searchType=variety

# 动漫搜索
https://so.youku.com/search_video/q_{keyword}?searchType=animation
```

### 2. 视频列表解析

使用 JavaScript 在页面中执行选择器提取：
- 标题：`.title`, `.video-title`, `h3`, `h4`
- 链接：`a[href*="youku.com"]`
- 作者：`.author`, `.up-name`, `.nick-name`
- 播放量：`.play`, `.num`, `.video-num`
- 时长：`.duration`, `.time`
- 简介：`.desc`, `.intro`, `.summary`

### 3. 滚动加载更多

```python
# 滚动页面加载更多内容
js_scroll = """
(() => {
  let scrolled = 0;
  const interval = setInterval(() => {
    window.scrollBy(0, 800);
    scrolled++;
    if (scrolled >= 3) {
      clearInterval(interval);
    }
  }, 1000);
  return 'scrolled ' + scrolled + ' pages';
})()
"""
```

### 4. 去重策略

```python
# 基于 URL 去重
results = dedup_results(raw_results, by="url")[:max_results]
```

## 已知限制

1. **登录态依赖**：部分视频需要登录才能查看完整信息
2. **反爬机制**：高频搜索可能触发验证码
3. **数据延迟**：播放量等数据可能有延迟更新
4. **分页限制**：最多加载 50 个结果

## 最佳实践

1. **控制搜索频率**：每次搜索间隔至少 2 秒
2. **使用去重**：批量搜索时启用去重避免重复
3. **限制滚动次数**：设置合理的 `max_scroll_pages` 避免过度加载
4. **异常处理**：捕获 `CDPError` 和超时异常
5. **使用专用实例**：使用 `--dedicated --name youku_session` 保留登录态

## 测试覆盖

- 配置测试：默认值、自定义值、序列化
- 搜索类型测试：视频搜索、电视剧搜索、综艺搜索、动漫搜索
- 结果解析测试：完整数据、空数据、异常数据
- 滚动加载测试：停止条件、继续条件
- 集成测试：搜索流程、详情获取、批量搜索
- 边界测试：空结果、JS 错误、JSON 解析错误
- 保存测试：JSON 格式

共 20 个测试用例，全部通过。
