---
name: scholar-search
skill: browser-cdp
script: scholar_search.py
description: Google Scholar 学术论文搜索脚本，支持关键词搜索、标题/作者/摘要/引用数提取。
triggers: Google Scholar, scholar search, 学术论文, 论文搜索, scholar_search.py
platforms: windows, macos, linux, pc
---

# Google Scholar 学术论文搜索脚本 (`scholar_search.py`)

## 用途

使用 browser-cdp skill 搜索 Google Scholar 学术论文，获取标题、作者、摘要、引用数等信息。
Google Scholar 有反爬机制，建议使用 stealth 模式并控制请求频率。

## 使用示例

```bash
cd .claude/skills/browser-cdp

# 基础搜索
python src/searchers/scholar_search.py "machine learning" --max-results 10

# 指定输出目录
python src/searchers/scholar_search.py "transformer architecture" --max-results 5 --output-dir ./scholar_results

# 启用反检测模式
python src/searchers/scholar_search.py "reinforcement learning" --stealth --port 9333

# 按引用数排序
python src/searchers/scholar_search.py "deep learning" --sort bydate --max-results 10
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `query` | 搜索关键词（必填） | - |
| `--max-results` | 最大结果数量 | 10 |
| `--sort` | 排序方式 (relevance/bydate/bycited) | relevance |
| `--output-dir` | 输出目录 | `./search_results/scholar` |
| `--port` | CDP 调试端口 | 9333 |
| `--stealth` | 启用反检测模式 | True |
| `--wait-timeout` | 页面等待超时(秒) | 30 |

## 输出格式

```json
{
  "title": "论文标题",
  "url": "https://scholar.google.com/...",
  "authors": ["Author A", "Author B"],
  "year": "2024",
  "venue": "NeurIPS",
  "citation_count": 150,
  "abstract": "论文摘要...",
  "source": "google_scholar",
  "scraped_at": "2026-08-02 10:30:00"
}
```

## 核心实现要点

- 使用 JS 提取搜索结果列表中的论文信息
- 标题从 `.gs_rt` 提取
- 作者从 `.gs_a` 提取
- 引用数从 `.gs_c` 提取
- 支持按相关性/日期/引用数排序
- 反检测模式隐藏自动化特征

## 注意事项

- Google Scholar 反爬较严格，建议控制请求频率
- 大量搜索可能触发验证码或 IP 封禁
- 建议启用 `--stealth` 模式
- 部分论文可能需要付费访问
- 引用数可能滞后于实际引用
