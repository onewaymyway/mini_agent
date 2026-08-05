---
name: sematic-scholar-search
skill: browser-cdp
script: sematic_scholar_search.py
description: Semantic Scholar 搜索自动化脚本，支持学术论文搜索，获取论文标题、作者、引用数、摘要等信息。
triggers: Semantic Scholar, sematicscholar, 学术论文搜索, sematic_scholar_search.py
platforms: windows, macos, linux, pc
---

# Semantic Scholar 学术论文搜索自动化脚本 (`sematic_scholar_search.py`)

## 用途

使用 browser-cdp skill 搜索 Semantic Scholar，获取学术论文元数据信息。

## 技术特征分析

### 网站结构

- **搜索接口**：`https://www.semanticscholar.org/search?q={query}&sort=relevance`
- **论文详情**：`https://www.semanticscholar.org/paper/{paper_id}`
- **数据格式**：JSON API + SSR 混合
- **主要 API**：`https://api.semanticscholar.org/graph/v1/paper/search?query={query}&limit={limit}`

### 反爬机制

| 机制 | 强度 | 说明 |
|------|------|------|
| IP 频率限制 | ⭐⭐ | 较弱频率限制 |
| UA 检测 | ⭐ | 检测较弱 |
| 验证码 | ⭐ | 极少触发 |
| 登录态 | ⭐ | 搜索无需登录 |
| 频率限制 | ⭐⭐ | API 限制 100 次/分钟 |

### 抓取策略

```python
# 推荐策略
- 优先使用 API 直接调用（无需浏览器）
- 如需浏览器，使用 browser-cdp + --stealth
- 请求间隔 1-2 秒
- 适合批量抓取论文数据
```

## 使用示例

```bash
cd .claude/skills/browser-cdp

# 搜索论文
python src/searchers/sematic_scholar_search.py "machine learning" --max-results 20

# 指定字段
python src/searchers/sematic_scholar_search.py "transformer" --fields title,authors,citationCount --max-results 10

# 指定输出目录
python src/searchers/sematic_scholar_search.py "deep learning" --output-dir ./sematic_results --port 9333
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `query` | 搜索关键词（必填） | - |
| `--max-results` | 最大结果数量 | 20 |
| `--fields` | 输出字段 (title,authors,year,citationCount,abstract) | title,authors,year |
| `--output-dir` | 输出目录 | `./search_results/sematic_scholar` |
| `--port` | CDP 调试端口 | 9333 |
| `--stealth` | 启用反检测模式 | True |
| `--wait-timeout` | 页面等待超时(秒) | 30 |

## 输出格式

```json
{
  "title": "Attention Is All You Need",
  "url": "https://www.semanticscholar.org/paper/Attention-Is-All-You-Need-Vaswani-Shazeer/ajheQiCcRt",
  "authors": ["Ashish Vaswani", "Noam Shazeer", "Niki Parmar"],
  "year": 2017,
  "citation_count": 95000,
  "abstract": "The dominant sequence transduction models...",
  "source": "sematic_scholar",
  "scraped_at": "2026-08-05 10:30:00"
}
```

## 核心实现要点

- 优先调用 Semantic Scholar API（更稳定）
- 使用 JS 提取论文列表中的元数据
- 支持按引用数、年份排序
- 反检测模式隐藏自动化特征

## 注意事项

- Semantic Scholar API 稳定且免费
- 适合批量抓取学术论文数据
- 注意 API 速率限制（100 次/分钟）
- 仅抓取公开可见的元数据
