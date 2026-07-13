---
name: arxiv-multi-search
skill: browser-cdp
script: arxiv_multi_search.py
description: 多关键词批量搜索 arXiv 论文脚本，支持自动合并去重、按相关性排序、批量获取详情。
triggers: 多关键词搜索, 批量搜索, multi-keyword search, arxiv batch, 论文批量抓取
platforms: windows, macos, linux, pc
---

# 多关键词批量搜索 arXiv 论文 (`arxiv_multi_search.py`)

## 用途

通过 CDP 控制浏览器访问 arxiv.org，使用多个相关关键词批量搜索论文，自动合并去重，获取论文详细信息。

**适用场景：**
- 需要全面收集某个领域的所有最新论文
- 单个关键词搜索结果不够全面
- 需要跨多个相关主题进行文献调研

## 使用示例

```bash
cd .claude/skills/browser-cdp

# 基础用法：使用默认关键词列表搜索
python arxiv_multi_search.py "自主进化Agent" --max-results-per-keyword 15

# 自定义关键词列表
python arxiv_multi_search.py "自主进化Agent" \n    --keywords "self-evolving agent,autonomous agent evolution,agent self-improvement,LLM agent adaptation,evolutionary agent"

# 限制每个关键词的结果数 + 获取详情数量
python arxiv_multi_search.py "AI Agent" \n    --max-results-per-keyword 10 --max-detail 20

# 无头模式
python arxiv_multi_search.py "reinforcement learning" --headless --max-results-per-keyword 10
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `query` | 搜索主题（用于生成默认关键词） | - |
| `--keywords` | 自定义关键词列表（逗号分隔） | 自动生成 |
| `--max-results-per-keyword` | 每个关键词的最大结果数 | 15 |
| `--max-detail` | 最多获取详情的论文数 | 10 |
| `--output-dir` | 输出目录 | `./search_results` |
| `--port` | CDP 调试端口 | 9333 |
| `--name` | 浏览器实例名称 | `arxiv_multi` |
| `--headless` | 无头模式运行 | False |
| `--wait-timeout` | 页面等待超时(秒) | 30 |
| `--no-detail` | 不获取论文详情，仅搜索结果列表 | False |

## 输出文件

运行后会在 `--output-dir` 生成：
- `arxiv_multi_search_<query>.json` — 完整结构化数据（含所有关键词的搜索结果和论文详情）
- `arxiv_multi_search_<query>.md` — 人类可读的 Markdown 报告

## 核心实现要点

### 1. 关键词生成策略

```python
DEFAULT_KEYWORDS = [
    "self-evolving agent",
    "autonomous agent evolution",
    "agent self-improvement",
    "LLM agent adaptation",
    "evolutionary agent",
    "self-adaptive agent",
    "agent learning from experience",
    "meta-learning agent",
    "continual learning agent",
    "agent self-modification"
]
```

当用户只提供一个主题词时，脚本会自动生成一组相关的英文关键词进行搜索。

### 2. 合并去重逻辑

```python
def merge_and_deduplicate(all_results: List[Dict]) -> List[Dict]:
    seen_ids = set()
    unique_results = []
    for r in all_results:
        arxiv_id = r.get('arxivId', '')
        if arxiv_id and arxiv_id not in seen_ids:
            seen_ids.add(arxiv_id)
            unique_results.append(r)
    return unique_results
```

- 使用 arXiv ID 作为唯一标识进行去重
- 保留首次出现的记录（通常是按时间排序的最新结果）
- 统计每个关键词的新增论文数

### 3. 搜索流程优化

```python
for keyword in keywords:
    results = search_arxiv_papers(port, tab_id, keyword, max_results)
    new_count = len([r for r in results if r['arxivId'] not in seen_ids])
    print(f"  [新增] {new_count} 篇新论文")
    
    # 达到目标数量时提前停止
    if len(seen_ids) >= TARGET_PAPERS:
        print("  已达到目标数量，停止搜索")
        break
```

- 每个关键词搜索后检查是否已收录
- 实时统计新增论文数
- 达到目标数量（默认 40 篇）时提前停止

### 4. 论文详情页 DOM 结构

```
h1.title.mathjax             — 论文标题
.authors a                   — 作者列表 (每个作者一个 <a>)
.abstract.mathjax            — 完整摘要 (需去掉 "Abstract:" 前缀)
.dateline                    — 提交日期
.subjects                     — 主题分类
a[href*="/pdf/"]             — PDF 下载链接
link[rel="canonical"]       — 规范链接 (用于提取 arXiv ID)
```

### 5. JS 返回 JSON 字符串避免转义问题

```javascript
// JS 端返回 JSON.stringify(result)
// Python 端再 json.loads(inner_str) 解析
return JSON.stringify(results);
```

这是从知乎抓取经验中总结的关键技巧：JS 返回 `JSON.stringify()` 字符串，Python 端再 `json.loads()` 解析，避免中文引号和特殊字符的转义问题。

## 常见问题

- **搜索结果为空**：检查网络连接，arXiv 服务器可能响应较慢
- **标题/摘要包含 LaTeX**：arXiv 使用 MathJax 渲染公式，`innerText` 获取的是渲染后文本
- **页面加载慢**：增加 `--wait-timeout` 参数值（默认 30 秒）
- **获取详情失败**：论文可能已被撤回或链接失效，检查 URL 是否有效
- **关键词太多导致重复**：使用 arXiv ID 去重，确保每篇论文只出现一次

## 依赖脚本

- `browser_launch.py` — 启动/复用浏览器实例（通过 `ensure_browser`）
- `browser_nav.py` — 导航到搜索结果页和论文详情页
- `browser_console.py` — 执行 JS 提取搜索结果和论文详情
- `baidu_search.py` — 复用 `ensure_browser`、`random_delay`、`get_random_ua`、`run_cmd` 等函数

## 文件结构

```
.claude/skills/browser-cdp/
├── arxiv_multi_search.py            # 多关键词批量搜索主脚本
├── arxiv-multi-search.md      # 本文档
├── arxiv_search.py            # arXiv 单关键词搜索脚本
├── baidu_search.py            # 依赖（复用浏览器管理、延迟等函数）
├── search_results/            # 默认输出目录
│   ├── arxiv_multi_search_<query>.json
│   └── arxiv_multi_search_<query>.md
└── temp_data/                 # 临时文件
```

## 性能提示

1. **合理设置 `--max-results-per-keyword`**：每个关键词搜索 15-20 篇通常足够
2. **使用 `--no-detail` 先获取索引**：确认论文列表后再获取详情，节省时间
3. **批量获取详情**：脚本默认获取前 10 篇论文的详情，可根据需要调整 `--max-detail`
4. **增量搜索**：如果之前已经搜索过，可以复用之前的结果，只获取新增论文

## 实战经验总结（2026-07-13 自主进化 Agent 搜索）

### 成功数据

- **搜索主题**：自主进化 Agent（autonomous evolution agent）
- **关键词数量**：5 个（"self-evolving agent", "autonomous agent evolution", "agent self-improvement", "LLM agent adaptation", "evolutionary agent"）
- **最终结果**：49 篇去重论文（远超 30 篇目标）
- **获取详情**：30 篇完整论文信息
- **运行时间**：约 15 分钟

### 数据质量优化

本次搜索中发现并修复了以下数据质量问题：

#### 1. 作者字段格式
- **问题**：作者字段最初是字符串格式（"Author1, Author2, ..."）
- **修复**：改为数组格式 `["Author1", "Author2", ...]`
- **好处**：便于后续处理，可以精确控制显示数量

#### 2. 日期字段清理
- **问题**：日期字段包含无用文本（"▽ More"、"doi" 等）
- **修复**：使用正则表达式清理
  ```javascript
  date = date.replace(/▽ More$/, '').replace(/^doi$/, '').trim();
  ```

#### 3. 摘要字段处理
- **问题**：摘要可能包含开头的省略号（"…"）
- **修复**：同时处理开头和结尾的省略号
  ```javascript
  abstract = abstract.replace(/▽ More$/, '').replace(/…$/, '').replace(/^…/, '').trim();
  ```

### 典型论文主题

本次搜索找到的相关论文包括：

1. **SAGEAgent**: Self-Evolving Agent for Cost-Aware Modality Acquisition
2. **Tool-Making and Self-Evolving LLM Agents** in Low-Latency Systems
3. **SpaCellAgent**: Self-Evolving LLM-Based Multi-Agent Framework
4. **The Blind Curator**: How a Biased Judge Silently Disables Skill Retirement
5. **MetaSkill-Evolve**: Recursive Self-Improvement of LLM Agents

### 关键 DOM 选择器

```javascript
// 搜索结果页
li.arxiv-result              — 每个论文结果容器
p.title.is-5.mathjax         — 论文标题
a[href*="/abs/"]             — 论文链接 (提取 arXiv ID)
p.authors                    — 作者列表
span.abstract-short          — 短摘要
div.tags                     — 分类标签
p.is-size-7                  — 提交日期

// 论文详情页
h1.title.mathjax             — 论文标题
.authors a                   — 作者列表（每个作者一个 <a>）
.abstract.mathjax            — 完整摘要
.dateline                    — 提交日期
.subjects                    — 主题分类
a[href*="/pdf/"]             — PDF 下载链接
link[rel="canonical"]        — 规范链接（提取 arXiv ID）
```

### 避坑指南

1. **不要依赖 `python3` 命令**：本环境使用 `python`（Anaconda），`python3` 会弹出应用商店提示
2. **必须先 `cd` 到 skill 目录**：所有脚本都依赖相对导入，必须在 `.claude/skills/browser-cdp/` 下运行
3. **页面加载时间**：arXiv 服务器响应较慢，建议 `--wait-timeout 30` 或更高
4. **JS 返回格式**：务必使用 `JSON.stringify()` 返回数据，Python 端再用 `json.loads()` 解析
5. **去重策略**：使用 arXiv ID 作为唯一标识，保留首次出现的记录（通常是最新的）

### 输出文件示例

运行后生成的文件：
- `arxiv_multi_search_autonomous_evolution_20260713_121047.json` (29,591 字节)
- `arxiv_multi_search_autonomous_evolution_20260713_121047.md` (14,114 字节)

JSON 数据结构：
```json
{
  "title": "SAGEAgent: A Self-Evolving Agent...",
  "url": "https://arxiv.org/abs/2607.09521",
  "arxivId": "2607.09521",
  "authors": ["Chongyu Qu", "Can Cui", ...],
  "abstract": "...for a given patient along this ordered workflow...",
  "tags": "cs.AI",
  "date": "2026-07-13"
}
```