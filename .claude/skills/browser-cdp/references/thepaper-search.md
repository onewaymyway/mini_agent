# 澎湃新闻搜索器使用指南

> 生成时间：2026-08-10
> 状态：已支持 (supported)

---

## 概述

澎湃新闻（The Paper）是上海报业集团旗下新媒体平台，以时政新闻见长，是国内最具影响力的新闻平台之一。

- **网站地址**: https://www.thepaper.cn
- **搜索地址**: https://search.thepaper.cn/search?q={query}
- **搜索器文件**: `src/searchers/thepaper_search.py`
- **优先级**: P0
- **反爬难度**: ⭐（低）

---

## 功能特性

| 功能 | 说明 |
|------|------|
| 关键词搜索 | 支持中文关键词搜索新闻 |
| 频道浏览 | 支持按频道（时政/财经/国际/科技等）浏览 |
| 新闻详情 | 获取标题、作者、发布时间、正文摘要 |
| 分页支持 | 支持分页浏览更多结果 |

---

## 使用方法

### 命令行方式

```bash
# 搜索新闻
python thepaper_search.py "人工智能" --max-results 10

# 搜索并保存结果
python thepaper_search.py "经济政策" --max-results 20 --output-dir ./results

# 指定端口和反检测模式
python thepaper_search.py "国际关系" --port 9333 --stealth
```

### Python API 方式

```python
from src.searchers.thepaper_search import ThePaperSearcher

searcher = ThePaperSearcher()

# 关键词搜索
results = searcher.search(
    query="人工智能",
    search_type="query",
    max_results=10,
    port=9333,
    stealth=True,
    output_dir="./results"
)

# 频道浏览
results = searcher.search(
    query="finance",
    search_type="channel",
    max_results=20
)

# 获取新闻详情
detail = searcher.get_detail(
    url="https://www.thepaper.cn/newsDetail_forward_12345",
    port=9333,
    tab_id="tab_id_here"
)
```

---

## 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| query | str | - | 搜索关键词或频道名 |
| search_type | str | "query" | 搜索类型：query/channel |
| max_results | int | 10 | 最大结果数 |
| port | int | 9333 | 浏览器调试端口 |
| tab_id | str | None | Tab ID |
| stealth | bool | True | 是否启用反检测模式 |
| output_dir | str | None | 输出目录 |
| wait_timeout | int | 30 | 等待超时时间（秒） |
| session_name | str | "thepaper_session" | 浏览器会话名称 |

---

## 输出格式

### 搜索结果

```json
[
  {
    "title": "新闻标题",
    "url": "https://www.thepaper.cn/newsDetail_forward_12345",
    "summary": "新闻摘要内容...",
    "date": "2026-08-10",
    "source": "thepaper",
    "scraped_at": "2026-08-10T07:00:00Z"
  }
]
```

### 新闻详情

```json
{
  "title": "新闻标题",
  "author": "作者名",
  "publish_date": "2026-08-10 10:00:00",
  "content": "新闻正文内容...",
  "source_name": "澎湃新闻",
  "url": "https://www.thepaper.cn/newsDetail_forward_12345",
  "scraped_at": "2026-08-10T07:00:00Z"
}
```

---

## 频道 ID 映射

| 频道名 | ID | 说明 |
|--------|-----|------|
| news | 1 | 时政新闻 |
| politics | 2 | 政治 |
| finance | 3 | 财经 |
| world | 4 | 国际 |
| tech | 5 | 科技 |
| sports | 6 | 体育 |
| culture | 7 | 文化 |

---

## 注意事项

1. **验证码处理**: 澎湃新闻反爬较宽松，一般不需要验证码处理
2. **请求频率**: 建议每次搜索间隔 2-3 秒，避免触发限流
3. **编码问题**: 搜索结果默认 UTF-8 编码，无需额外处理
4. **SPA 支持**: 澎湃新闻使用传统服务端渲染，无需特殊处理

---

## 相关文件

- 搜索器实现: `src/searchers/thepaper_search.py`
- 基类定义: `src/searchers/base.py`
- 浏览器工具: `src/searchers/browser_utils.py`
- 环境验证: `scripts/env_init.py`

---

*文档生成时间: 2026-08-10*