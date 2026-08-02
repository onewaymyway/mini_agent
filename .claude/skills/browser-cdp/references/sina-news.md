---
name: sina-news
skill: browser-cdp
script: sina_news.py
description: 新浪财经新闻抓取脚本，支持多分类新闻获取、RSS解析、新闻详情抓取。
triggers: 新浪财经, sina news, 财经新闻, 新闻抓取, sina_news.py
platforms: windows, macos, linux, pc
---

# 新浪财经新闻抓取脚本 (`sina_news.py`)

## 用途

使用 browser-cdp skill 抓取新浪财经新闻列表和详情。新浪财经反爬较弱，可直接使用 requests 抓取 RSS 或 HTML。

## 使用示例

```bash
cd .claude/skills/browser-cdp

# 获取股票新闻
python src/searchers/sina_news.py --category stock --max-results 20

# 获取宏观经济新闻
python src/searchers/sina_news.py --category macro --output-dir ./sina_results

# 获取行业新闻
python src/searchers/sina_news.py --category industry --port 9333

# 指定关键词过滤
python src/searchers/sina_news.py --category stock --keyword "茅台" --max-results 10
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--category` | 新闻分类 (stock/macro/industry/forex/futures) | stock |
| `--max-results` | 最大结果数量 | 20 |
| `--keyword` | 关键词过滤（可选） | - |
| `--output-dir` | 输出目录 | `./search_results/sina` |
| `--port` | CDP 调试端口 | 9333 |
| `--stealth` | 启用反检测模式 | True |
| `--wait-timeout` | 页面等待超时(秒) | 30 |

## 支持分类

| 分类 | 说明 | RSS 地址 |
|------|------|----------|
| stock | 股票新闻 | https://feed.finance.sina.com.cn/rss/stock.xml |
| macro | 宏观经济 | https://feed.finance.sina.com.cn/rss/macro.xml |
| industry | 行业动向 | https://feed.finance.sina.com.cn/rss/industry.xml |
| forex | 外汇 | https://feed.finance.sina.com.cn/rss/forex.xml |
| futures | 期货 | https://feed.finance.sina.com.cn/rss/futures.xml |

## 输出格式

```json
{
  "title": "新闻标题",
  "url": "https://finance.sina.com.cn/stock/...",
  "summary": "新闻摘要...",
  "published": "2026-08-02 10:30:00",
  "source": "sina_finance",
  "category": "stock",
  "scraped_at": "2026-08-02 10:30:00"
}
```

## 核心实现要点

- 优先使用 RSS 直接抓取（无需浏览器）
- RSS 失败时回退到浏览器抓取
- 支持关键词过滤
- 自动解析发布时间

## 注意事项

- 新浪财经反爬较弱，RSS 方式成功率较高
- 如需抓取详情页内容，建议启用浏览器模式
- 新闻发布时间可能有时区差异
