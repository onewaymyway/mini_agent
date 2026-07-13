---
name: arxiv-search
skill: browser-cdp
script: arxiv_search.py
description: arXiv 论文搜索自动化脚本，支持按关键词搜索最新论文列表、获取论文详细信息（标题、作者、摘要、日期、主题分类、PDF链接），结果保存为 JSON 和 Markdown。
triggers: arxiv, 论文搜索, paper search, arxiv search, 抓取论文, 学术搜索
platforms: windows, macos, linux, pc
---

# arXiv 论文搜索自动化脚本 (`arxiv_search.py`)

## 用途

通过 CDP 控制浏览器访问 arxiv.org，搜索特定关键词的最新论文列表，
获取论文详细信息。支持：

- 按关键词搜索 arXiv 论文（按最新发布日期排序）
- 提取搜索结果列表（标题、arXiv ID、作者、摘要、分类标签）
- 获取论文详情页完整信息（完整摘要、提交日期、主题分类、PDF链接、评论）
- 结果保存为 JSON 和 Markdown 格式
- 支持无头模式、自定义端口、实例复用

## 使用示例

```bash
cd .claude/skills/browser-cdp

# 基础搜索（只获取结果列表，不获取详情）
python arxiv_search.py "agent harness" --max-results 10 --no-detail

# 完整搜索（获取结果列表 + 论文详情）
python arxiv_search.py "agent harness" --max-results 10

# 限制详情数量 + 自定义输出目录
python arxiv_search.py "LLM agent" --max-results 5 --max-detail 3 --output-dir ./papers

# 无头模式
python arxiv_search.py "reinforcement learning" --headless --max-results 5

# 复用已有浏览器实例
python arxiv_search.py "AI Agent" --port 9333 --name my_search
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `query` | 搜索关键词（必填） | - |
| `--max-results` | 最大搜索结果数量 | 10 |
| `--max-detail` | 最多获取详情的论文数 | 5 |
| `--output-dir` | 输出目录 | `./search_results` |
| `--port` | CDP 调试端口 | 9333 |
| `--name` | 浏览器实例名称 | `arxiv_search` |
| `--headless` | 无头模式运行 | False |
| `--wait-timeout` | 页面等待超时(秒) | 30 |
| `--no-detail` | 不获取论文详情，仅结果列表 | False |

## 输出文件

运行后会在 `--output-dir` 生成：
- `arxiv_search_<query>.json` — 完整结构化数据（含搜索索引和论文详情）
- `arxiv_search_<query>.md` — 人类可读的 Markdown 报告（含论文详情和索引表格）

## 核心实现要点（供参考/二次开发）

### 1. arXiv 搜索 URL 构建

```python
search_url = f"https://arxiv.org/search/?query={quote(query)}&searchtype=all&order=-announced_date_first"
```

- `searchtype=all` 搜索所有字段（标题、摘要、作者等）
- `order=-announced_date_first` 按最新发布日期降序排列
- 使用 `urllib.parse.quote` 对关键词进行 URL 编码

### 2. 搜索结果页 DOM 结构

```
li.arxiv-result              — 每个论文结果容器
  p.title.is-5.mathjax       — 论文标题
  a[href*="/abs/"]           — 论文链接 (arXiv ID 在 URL 中)
  p.authors                   — 作者列表 (内含多个 <a> 链接)
  span.abstract-short         — 短摘要 (需去掉末尾 "▽ More")
  div.tags                    — 分类标签 (如 cs.AI, cs.CL)
```

### 3. 论文详情页 DOM 结构

```
h1.title.mathjax             — 论文标题
.authors a                   — 作者列表 (每个作者一个 <a>)
.abstract.mathjax            — 完整摘要 (需去掉 "Abstract:" 前缀)
.dateline                    — 提交日期 (如 "[Submitted on 9 Jul 2026]")
.subjects                     — 主题分类
a[href*="/pdf/"]             — PDF 下载链接
link[rel="canonical"]       — 规范链接 (用于提取 arXiv ID)
```

### 4. JS 返回 JSON 字符串避免转义问题

```javascript
// JS 端返回 JSON.stringify(result)
// Python 端再 json.loads(inner_str) 解析
return JSON.stringify(results);
```

这是从知乎抓取经验中总结的关键技巧：JS 返回 `JSON.stringify()` 字符串，Python 端再 `json.loads()` 解析，避免中文引号和特殊字符的转义问题。

### 5. arXiv 特点

- **无需登录**：arXiv 是开放学术平台，所有论文页面均可直接访问
- **无重定向**：arXiv 搜索结果中的链接直接指向 `/abs/` 页面，无需解析重定向
- **无反爬限制**：arXiv 对学术爬取较为宽容，但仍建议添加合理延迟
- **MathJax 渲染**：标题和摘要中可能包含 LaTeX 公式，`innerText` 会获取渲染后的文本

## 常见问题

- **搜索结果为空**：检查关键词拼写，尝试更宽泛的搜索词
- **标题/摘要包含 LaTeX**：arXiv 使用 MathJax 渲染公式，`innerText` 获取的是渲染后文本，可能包含特殊字符
- **页面加载慢**：arXiv 服务器响应较慢时，增加 `--wait-timeout` 参数值
- **获取详情失败**：论文可能已被撤回或链接失效，检查 URL 是否有效

## 依赖脚本

- `browser_launch.py` — 启动/复用浏览器实例（通过 `ensure_browser`）
- `browser_nav.py` — 导航到搜索结果页和论文详情页
- `browser_console.py` — 执行 JS 提取搜索结果和论文详情
- `baidu_search.py` — 复用 `ensure_browser`、`random_delay`、`get_random_ua`、`run_cmd` 等函数

## 文件结构

```
.claude/skills/browser-cdp/
├── arxiv_search.py            # 主脚本
├── arxiv_search_skill.md      # 本文档
├── baidu_search.py            # 依赖（复用浏览器管理、延迟等函数）
├── search_results/            # 默认输出目录
│   ├── arxiv_search_<query>.json
│   └── arxiv_search_<query>.md
└── temp_data/                 # 临时文件
```
