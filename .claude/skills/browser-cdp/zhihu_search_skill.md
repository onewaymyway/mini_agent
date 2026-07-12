---
name: zhihu-search
skill: browser-cdp
script: zhihu_search.py
description: 知乎内容搜索自动化脚本，通过百度搜索 site:zhihu.com 获取知乎问答和专栏文章，自动解析百度重定向链接，分类抓取结构化内容。
triggers: 知乎搜索, 知乎抓取, zhihu search, 知乎问答, 知乎专栏, 抓取知乎
platforms: windows, macos, linux, pc
---

# 知乎内容搜索自动化脚本 (`zhihu_search.py`)

## 用途

通过百度搜索 `site:zhihu.com` 获取知乎相关结果，自动解析百度重定向链接，
区分知乎问答（question页面）和知乎专栏文章（zhuanlan.zhihu.com），
分别使用专用 JS 选择器提取结构化内容。支持：

- 百度搜索 `site:zhihu.com` 并提取搜索结果
- 自动解析百度重定向链接为真实知乎 URL
- 按 URL 分类：知乎问答 / 知乎专栏 / 其他
- 知乎问答：提取问题标题、问题描述、多个回答内容
- 知乎专栏：提取文章标题、作者、正文内容
- 结果保存为 JSON 和 Markdown 格式
- 支持无头模式、自定义端口、实例复用

## 使用示例

```bash
cd .claude/skills/browser-cdp

# 基础搜索（只获取结果列表，不抓取详情页）
python zhihu_search.py "AI Agent" --max-results 10 --no-detail

# 完整搜索（获取结果列表 + 抓取知乎问答和专栏内容）
python zhihu_search.py "自主Agent" --max-results 10

# 限制专栏抓取数量 + 自定义输出目录
python zhihu_search.py "大模型" --max-results 5 --max-detail 3 --output-dir ./zhihu_results

# 无头模式
python zhihu_search.py "AI Agent" --headless --max-results 5

# 复用已有浏览器实例
python zhihu_search.py "AI Agent" --port 9333 --name my_search
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `query` | 搜索关键词（必填） | - |
| `--max-results` | 最大搜索结果数量 | 10 |
| `--max-detail` | 最多抓取详情的专栏文章数 | 5 |
| `--output-dir` | 输出目录 | `./search_results` |
| `--port` | CDP 调试端口 | 9333 |
| `--name` | 浏览器实例名称 | `zhihu_search` |
| `--headless` | 无头模式运行 | False |
| `--wait-timeout` | 页面等待超时(秒) | 20 |
| `--max-chars` | 内容最大字符数 | 5000 |
| `--no-detail` | 不抓取详情内容，仅结果列表 | False |

## 输出文件

运行后会在 `--output-dir` 生成：
- `zhihu_search_<query>.json` — 完整结构化数据（含搜索索引、问答内容、专栏内容）
- `zhihu_search_<query>.md` — 人类可读的 Markdown 报告（含问答全文、专栏摘要、索引表格）

## 核心实现要点（供参考/二次开发）

### 1. 百度搜索 site:zhihu.com 限定知乎域名

```python
search_url = f"https://www.baidu.com/s?wd=site:zhihu.com+{quote(query)}"
```

使用 `site:` 语法限定搜索范围到知乎域名，避免获取非知乎结果。

### 2. 百度重定向链接解析

百度搜索结果中的链接均为 `baidu.com/link?url=xxx` 重定向链接，需要解析为真实 URL。
复用 `baidu_search.py` 的 `resolve_baidu_redirect` 函数：
- 策略1：JS `fetch(redirect: 'follow')` — 不离开当前页面（受 CORS 限制可能失败）
- 策略2：导航到重定向链接 → 读取最终 URL → 自动返回搜索结果页

**经验**：百度 CORS 限制导致策略1几乎总是失败，策略2（导航）是主要解析方式。

### 3. 知乎 URL 分类

```python
def classify_zhihu_url(url: str) -> str:
    if 'zhihu.com/question' in url or 'zhihu.com/answer' in url:
        return 'question'      # 知乎问答
    elif 'zhuanlan.zhihu.com' in url:
        return 'column'        # 知乎专栏
    elif 'zhihu.com' in url:
        return 'other_zhihu'   # 其他知乎页面
    else:
        return 'non_zhihu'     # 非知乎
```

### 4. 知乎问答 JS 提取

知乎问答页面结构：
- 问题标题：`.QuestionHeader-title` 或 `h1`
- 回答内容：`.RichContent-inner`（可能有多个回答）
- 回答者：`.AuthorInfo-name`

**关键经验**：
- JS 返回 `JSON.stringify()` 字符串，Python 端再 `json.loads()` 解析，避免中文引号转义问题
- `.RichContent-inner` 选择器可以提取到展开后的完整回答
- 首个 `.RichContent-inner` 可能是问题补充说明而非回答，需按内容长度过滤
- 限制每个回答最大 5000 字符

### 5. 知乎专栏 JS 提取

知乎专栏页面结构：
- 标题：`.Post-Title` 或 `h1`
- 作者：`.AuthorInfo-name`
- 正文：`.Post-RichTextContainer` 或 `.RichText`

**关键经验**：
- 专栏文章通常不需要登录即可查看完整内容
- 需要清理推荐文章、底部标签等无关元素
- 专栏之间间隔 2 秒避免触发反爬

### 6. 搜索策略经验总结

- `site:zhihu.com` 搜索主要返回知乎专栏文章（zhuanlan.zhihu.com）
- 要获取知乎问答（question页面），需搜索 `关键词 知乎问答` 而非 `site:zhihu.com/question`
- 百度对 `site:zhihu.com/question` 语法支持不佳，可能返回空结果
- 每次解析重定向后需要导航回搜索结果页继续解析下一个

## 常见问题

- **搜索结果全为专栏**：`site:zhihu.com` 偏向返回专栏文章，如需问答请用 `关键词 知乎问答` 搜索
- **知乎问答内容不完整**：知乎可能需要登录才能看到完整回答，建议使用非 headless 模式
- **重定向解析失败**：百度可能对高频导航触发反爬，增加延迟或减少 `--max-results`
- **专栏内容为空**：知乎页面结构可能变更，需更新 JS 选择器

## 依赖脚本

- `browser_launch.py` — 启动/复用浏览器实例（通过 `ensure_browser`）
- `browser_nav.py` — 导航到搜索结果页和详情页
- `browser_console.py` — 执行 JS 提取搜索结果和页面内容
- `baidu_search.py` — 复用 `ensure_browser`、`resolve_baidu_redirect`、`random_delay` 等函数
- `detail_cleaner.py` — 站点专用清理规则（可选）

## 文件结构

```
.claude/skills/browser-cdp/
├── zhihu_search.py            # 主脚本
├── zhihu_search_skill.md      # 本文档
├── baidu_search.py            # 依赖（复用浏览器管理、重定向解析等）
├── search_results/            # 默认输出目录
│   ├── zhihu_search_<query>.json
│   └── zhihu_search_<query>.md
└── temp_data/                 # 临时文件
```
