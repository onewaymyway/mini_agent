---
name: zhihu-search-simple
skill: browser-cdp
script: zhihu_search_simple.py
description: 简化版知乎搜索器，通过百度搜索 site:zhihu.com 获取知乎问题和专栏文章，无需登录态。
triggers: 知乎搜索, zhihu search, zhihu_search_simple.py, 知乎问题搜索
platforms: windows, macos, linux, pc
---

# 简化版知乎搜索器 (`zhihu_search_simple.py`)

## 用途

通过百度搜索 `site:zhihu.com` 获取知乎问题和专栏文章链接，无需登录态，适合快速搜索。

## 使用示例

```python
from src.searchers.zhihu_search_simple import ZhihuSearchSimple

searcher = ZhihuSearchSimple()
results = searcher.search("Python 教程", max_results=10)

for r in results:
    print(f"{r['title']}")
    print(f"URL: {r['url']}")
    print(f"Snippet: {r['snippet'][:100]}...")
```

```bash
# 命令行使用
python src/searchers/zhihu_search_simple.py "Python 教程" --max-results 10
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `query` | 搜索关键词（必填） | - |
| `--max-results` | 最大结果数 | 10 |
| `--output` | 输出文件路径 | - |

## 输出格式

```json
{
  "title": "问题标题",
  "url": "https://www.zhihu.com/question/xxxxx",
  "snippet": "问题描述或回答摘要...",
  "type": "question",
  "source": "zhihu",
  "scraped_at": "2026-08-02T10:30:00Z"
}
```

## 核心实现要点

- 使用 httpx 发送请求，无需浏览器
- 通过百度搜索 `site:zhihu.com` 限定域名
- 解析百度结果页提取知乎链接
- 自动过滤问题和专栏文章

## 已知限制

1. 依赖百度搜索，结果可能不完整
2. 无法获取回答内容，只能获取标题和摘要
3. 高频搜索可能触发百度验证码
4. 需要手动处理登录态才能获取完整内容

## 最佳实践

1. 控制搜索频率，每次间隔至少 3 秒
2. 使用 `--max-results` 限制结果数量
3. 结合 `zhihu_search.py` 使用，先搜索后详情抓取