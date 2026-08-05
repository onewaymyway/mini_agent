---
name: wangyi-news
skill: browser-cdp
script: wangyi_news.py
description: 网易新闻抓取脚本，支持新闻列表页抓取、详情页抓取、分类导航和时间范围筛选。
triggers: 网易新闻, wangyi news, 163新闻, 新闻抓取, wangyi_news.py
platforms: windows, macos, linux, pc
---

# 网易新闻抓取脚本 (`wangyi_news.py`)

## 用途

使用 browser-cdp skill 抓取网易新闻，支持新闻列表页和详情页抓取，覆盖新闻/财经/科技/体育等分类。

## 使用示例

```bash
cd .claude/skills/browser-cdp

# 抓取新闻列表
python src/searchers/wangyi_news.py --category news --max-results 20

# 抓取财经新闻
python src/searchers/wangyi_news.py --category finance --max-results 15

# 抓取科技新闻
python src/searchers/wangyi_news.py --category tech --query "AI" --max-results 10

# 抓取详情页
python src/searchers/wangyi_news.py --url "https://news.163.com/xxx" --detail
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--category` | 新闻分类 (news/finance/tech/sports/entertainment/war) | news |
| `--query` | 搜索关键词 | - |
| `--max-results` | 最大结果数 | 20 |
| `--output-dir` | 输出目录 | `./search_results/wangyi` |
| `--port` | CDP 调试端口 | 9333 |
| `--stealth` | 启用反检测模式 | True |
| `--url` | 直接抓取指定 URL 的详情页 | - |
| `--detail` | 抓取详情页内容 | False |

## 输出格式

```json
{
  "title": "新闻标题",
  "url": "https://news.163.com/xxx.html",
  "summary": "新闻摘要...",
  "published": "2026-08-02 10:30:00",
  "category": "news",
  "source": "wangyi_news",
  "scraped_at": "2026-08-02 10:30:00"
}
```

## 核心实现要点

- 使用百度搜索 `site:news.163.com` 获取新闻链接
- 直接访问网易新闻分类页获取列表
- 详情页抓取使用 browser_extract.py
- 反爬强度低，无需特殊处理

## 注意事项

- 网易新闻反爬较弱，可直接抓取
- 部分新闻需要登录才能查看完整内容
- 建议使用 `--stealth` 模式降低检测风险
