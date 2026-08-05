# 今日头条搜索器 (toutiao_search.py)

## 概述

今日头条搜索器通过浏览器自动化搜索头条新闻和文章，支持关键词搜索和热榜抓取。

## 功能特性

- 关键词搜索：通过 Google 搜索 `site:toutiao.com` 获取头条文章
- 热榜抓取：获取今日头条热榜内容
- 文章详情：抓取文章标题、内容、作者、发布时间
- 反检测模式：支持 stealth 模式降低检测风险

## 使用方法

### 命令行

```bash
# 搜索头条文章
python toutiao_search.py "AI 新闻" --max-results 10

# 获取热榜
python toutiao_search.py "科技" --hot

# 保存结果
python toutiao_search.py "经济" --output-dir ./toutiao_results
```

### Python API

```python
from src.searchers.toutiao_search import ToutiaoSearcher
import asyncio

async def main():
    searcher = ToutiaoSearcher(port=9333, stealth=True)
    
    # 搜索
    results = await searcher.search("AI 新闻")
    
    # 获取热榜
    hot_results = await searcher.get_hot()
    
    # 获取详情
    detail = await searcher.get_detail("https://www.toutiao.com/article/xxx")

asyncio.run(main())
```

## 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| query | str | - | 搜索关键词 |
| search_type | str | "news" | 搜索类型：news/article/hot |
| max_results | int | 10 | 最大结果数 |
| output_dir | str | None | 输出目录 |
| port | int | 9333 | 浏览器调试端口 |
| stealth | bool | True | 是否启用反检测模式 |

## 返回格式

```json
{
  "source": "toutiao",
  "title": "文章标题",
  "url": "https://www.toutiao.com/article/xxx",
  "snippet": "文章摘要",
  "metadata": {
    "query": "关键词",
    "type": "news"
  },
  "scraped_at": "2024-01-01T00:00:00Z"
}
```

## 注意事项

1. 头条文章通过 Google 搜索获取，需要 Google 可访问
2. 建议使用已登录的浏览器会话以提高成功率
3. 热榜页面可能需要登录才能获取完整内容
4. 反爬机制较强，建议添加随机延迟

## 技术实现

- 使用 `browser_cdp` 模块控制浏览器
- 通过 Google 搜索 `site:toutiao.com` 获取头条文章
- 使用 JavaScript 提取搜索结果
- 支持异步操作

## 相关文件

- 搜索器源码：`src/searchers/toutiao_search.py`
- 基础类：`src/searchers/base.py`
- 工具函数：`src/searchers/utils.py`
