---
name: thp-news
skill: browser-cdp
script: thp_news.py
description: 澎湃新闻新闻抓取脚本，支持关键词搜索、分类浏览（时政/财经/天下/观察）和详情获取。
triggers: 澎湃新闻, thepaper, thp news, 时政新闻, 财经新闻, thp_news.py
platforms: windows, macos, linux, pc
---

# 澎湃新闻新闻抓取脚本 (`thp_news.py`)

## 用途

使用 browser-cdp skill 抓取澎湃新闻新闻列表和详情。支持关键词搜索和分类浏览。

## 使用示例

```bash
cd .claude/skills/browser-cdp

# 浏览时政分类
python src/searchers/thp_news.py --category shizheng --max-results 20

# 搜索关键词
python src/searchers/thp_news.py --query "人工智能" --max-results 10

# 财经分类 + 获取详情
python src/searchers/thp_news.py --category caijing --max-results 10 --fetch-detail

# 保存到指定目录
python src/searchers/thp_news.py --query "经济" --output-dir ./thp_results

# 直接获取单篇详情
python src/searchers/thp_news.py --detail "https://www.thepaper.cn/newsDetail_forward_xxxxxx"
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--query` | 搜索关键词（可选，为空则按分类浏览） | - |
| `--category` | 新闻分类 (shizheng/caijing/tianxia/guancha) | - |
| `--max-results` | 最大结果数量 | 20 |
| `--output-dir` | 输出目录 | `./search_results/thp` |
| `--fetch-detail` | 获取新闻详情内容 | False |
| `--detail` | 直接获取指定 URL 的详情 | - |
| `--port` | CDP 调试端口 | 9333 |
| `--tab` | Tab ID（可选，自动创建） | - |
| `--stealth` | 启用反检测模式 | True |
| `--no-stealth` | 禁用反检测模式 | - |
| `--wait-timeout` | 页面等待超时(秒) | 30 |

## 支持分类

| 分类 | 说明 | URL |
|------|------|-----|
| shizheng | 时政 | https://www.thepaper.cn/channel_25955 |
| caijing | 财经 | https://www.thepaper.cn/channel_25956 |
| tianxia | 天下 | https://www.thepaper.cn/channel_25957 |
| guancha | 观察 | https://www.thepaper.cn/channel_25958 |

## 输出格式

### 列表结果

```json
[
  {
    "title": "新闻标题",
    "url": "https://www.thepaper.cn/newsDetail_forward_xxxxxx",
    "time": "2026-08-02 10:30:00",
    "snippet": "新闻摘要...",
    "source": "thepaper",
    "category": "shizheng",
    "scraped_at": "2026-08-02 10:30:00"
  }
]
```

### 详情结果（--fetch-detail 或 --detail）

```json
{
  "title": "新闻标题",
  "url": "https://www.thepaper.cn/newsDetail_forward_xxxxxx",
  "content": "新闻正文内容...",
  "author": "作者名",
  "time": "2026-08-02 10:30:00",
  "tags": ["标签1", "标签2"],
  "source": "thepaper",
  "scraped_at": "2026-08-02 10:30:00"
}
```

## 核心实现要点

- 继承 `BaseSearcher` 基类
- 使用 `browser_nav.py` 进行页面导航
- 使用 `browser_console.py` 执行 JS 提取内容
- 支持关键词搜索和分类浏览两种模式
- 支持批量获取详情（`--fetch-detail`）
- 自动反检测模式（`--stealth`）
- 输出 JSON 格式结果

## 注意事项

- 澎湃新闻反爬中等，建议启用反检测模式
- 搜索功能依赖页面搜索接口，可能需要手动登录
- 新闻发布时间格式可能不统一，建议做后处理
- 详情页提取依赖页面结构，如遇选择器失效需调整 JS 代码
