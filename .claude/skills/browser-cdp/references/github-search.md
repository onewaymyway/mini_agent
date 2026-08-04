# GitHub 搜索自动化脚本

## 概述

`github_search.py` 是 GitHub 代码仓库、Issue、PR、代码片段和用户搜索的自动化脚本。

## 功能特性

- **仓库搜索**：搜索 GitHub 代码仓库，获取名称、描述、星标数、语言、作者等信息
- **Issue 搜索**：搜索 Issue，获取标题、状态、作者、评论数、时间等信息
- **PR 搜索**：搜索 Pull Request，获取标题、状态、作者、变更文件数等信息
- **代码搜索**：搜索代码片段，获取文件路径、代码片段、仓库、语言等信息
- **用户搜索**：搜索用户，获取用户名、简介、关注者数、仓库数等信息
- **详情抓取**：获取仓库/Issue/PR 的完整详情

## 用法

### 命令行

```bash
# 搜索仓库
cd .claude/skills/browser-cdp
python src/searchers/github_search.py "machine learning" --type repo --max-results 10

# 搜索 Issue
cd .claude/skills/browser-cdp
python src/searchers/github_search.py "bug authentication" --type issue --max-results 20

# 搜索代码
cd .claude/skills/browser-cdp
python src/searchers/github_search.py "useState" --type code --max-results 15

# 按星标排序
cd .claude/skills/browser-cdp
python src/searchers/github_search.py "transformer" --type repo --sort stars --output-dir ./github_results

# 搜索用户
cd .claude/skills/browser-cdp
python src/searchers/github_search.py "google" --type user --max-results 5
```

### Python API

```python
from src.searchers.github_search import GitHubSearcher

# 创建搜索器
searcher = GitHubSearcher()

# 搜索仓库
results = searcher.search(
    query="machine learning",
    max_results=10,
    search_type="repo",
    sort="stars",
    stealth=True,
    output_dir="./results"
)

# 搜索 Issue
results = searcher.search(
    query="bug authentication",
    max_results=20,
    search_type="issue"
)

# 获取仓库详情
detail = searcher.get_detail("https://github.com/microsoft/TypeScript")

# 获取 Issue 详情
detail = searcher.get_detail("https://github.com/facebook/react/issues/12345")
```

## 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `query` | str | - | 搜索关键词 |
| `--type` | str | `repo` | 搜索类型：`repo`/`issue`/`pr`/`code`/`user` |
| `--sort` | str | `None` | 排序方式：`stars`/`recently_updated`/`created` |
| `--max-results` | int | 10 | 最大结果数 |
| `--output-dir` | str | `None` | 输出目录 |
| `--port` | int | 9333 | 浏览器调试端口 |
| `--tab` | str | `None` | Tab ID |
| `--stealth` | bool | `True` | 启用反检测模式 |
| `--wait-timeout` | int | 30 | 等待超时时间（秒） |

## 输出格式

### 仓库搜索结果

```json
{
  "title": "microsoft/TypeScript",
  "url": "https://github.com/microsoft/TypeScript",
  "description": "TypeScript is a language for application-scale JavaScript.",
  "stars": "100k stars",
  "forks": "20k forks",
  "language": "TypeScript",
  "author": "microsoft",
  "source": "github",
  "type": "repo"
}
```

### Issue 搜索结果

```json
{
  "title": "TypeError: undefined is not an object",
  "url": "https://github.com/facebook/react/issues/12345",
  "state": "open",
  "author": "react-bot",
  "comments": "15 comments",
  "time": "2024-01-15T10:30:00Z",
  "source": "github",
  "type": "issue"
}
```

### 代码搜索结果

```json
{
  "title": "src/utils/helpers.ts",
  "url": "https://github.com/microsoft/TypeScript/blob/main/src/utils/helpers.ts",
  "snippet": "export function debounce(fn, delay) { ... }",
  "repo": "microsoft/TypeScript",
  "language": "TypeScript",
  "source": "github",
  "type": "code"
}
```

## 技术要点

### 搜索 URL 构建

GitHub 搜索 URL 格式：
```
https://github.com/search?q=<query>&type=<type>&s=<sort>
```

- `q`：搜索关键词（URL 编码）
- `type`：搜索类型（`repositories`/`issues`/`prs`/`code`/`users`）
- `s`：排序方式（`stars`/`recently_updated`/`created`）

### 反爬策略

- GitHub 有 API 限流（未登录 60次/小时），浏览器模式可绕过部分限制
- 建议启用 `--stealth` 模式
- 控制请求频率，避免触发验证码
- 如需高频访问，建议使用已登录态

### 验证码处理

GitHub 在检测到异常访问时会弹出验证码：
- 滑块验证码：自动处理
- 点选验证码：提示用户手动完成
- 速率限制：建议等待后重试

## 注意事项

1. **登录态**：部分搜索结果需要登录态才能完整查看
2. **API 限流**：未登录状态下 API 限流 60次/小时，浏览器模式可绕过
3. **反检测**：建议始终启用 `--stealth` 模式
4. **请求频率**：控制请求频率，避免触发验证码
5. **结果去重**：自动基于 URL 去重

## 相关文件

- 搜索器：`src/searchers/github_search.py`
- 基础类：`src/searchers/base.py`
- 工具函数：`src/searchers/utils.py`
- 浏览器导航：`src/core/browser_nav.py`
- 内容提取：`src/core/browser_console.py`
