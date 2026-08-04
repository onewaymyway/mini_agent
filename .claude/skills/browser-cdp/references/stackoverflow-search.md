# Stack Overflow 搜索自动化脚本

## 概述

`stackoverflow_search.py` 是 Stack Overflow 技术问题搜索的自动化脚本。

## 功能特性

- **问题搜索**：搜索技术问题，获取标题、摘要、投票数、答案数、标签等信息
- **详情抓取**：获取问题的完整详情，包括正文、答案列表、标签等
- **答案提取**：提取前 5 个答案的正文、投票数、作者、是否被采纳等信息

## 用法

### 命令行

```bash
# 搜索问题
cd .claude/skills/browser-cdp
python src/searchers/stackoverflow_search.py "python pandas merge dataframe" --max-results 10

# 搜索并保存结果
cd .claude/skills/browser-cdp
python src/searchers/stackoverflow_search.py "react useEffect cleanup" --max-results 5 --output-dir ./so_results

# 使用已登录浏览器
cd .claude/skills/browser-cdp
python src/searchers/stackoverflow_search.py "javascript async await" --port 9333 --stealth
```

### Python API

```python
from src.searchers.stackoverflow_search import StackOverflowSearcher

# 创建搜索器
searcher = StackOverflowSearcher()

# 搜索问题
results = searcher.search(
    query="python pandas merge dataframe",
    max_results=10,
    stealth=True,
    output_dir="./results"
)

# 获取问题详情
detail = searcher.get_detail("https://stackoverflow.com/questions/12345")
```

## 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `query` | str | - | 搜索关键词 |
| `--max-results` | int | 10 | 最大结果数 |
| `--output-dir` | str | `None` | 输出目录 |
| `--port` | int | 9333 | 浏览器调试端口 |
| `--tab` | str | `None` | Tab ID |
| `--stealth` | bool | `True` | 启用反检测模式 |
| `--wait-timeout` | int | 30 | 等待超时时间（秒） |

## 输出格式

### 搜索结果

```json
{
  "title": "How to merge two DataFrames in pandas?",
  "url": "https://stackoverflow.com/questions/12345",
  "excerpt": "I have two DataFrames and I want to merge them on a common column...",
  "votes": "42",
  "answers": "3",
  "views": "15000",
  "tags": ["python", "pandas", "merge"],
  "author": "stackoverflow_user",
  "time": "2024-01-15T10:30:00Z",
  "source": "stackoverflow",
  "type": "question"
}
```

### 问题详情

```json
{
  "title": "How to merge two DataFrames in pandas?",
  "body": "I have two DataFrames...",
  "votes": "42",
  "answers": "3",
  "views": "15000",
  "tags": ["python", "pandas", "merge"],
  "author": "stackoverflow_user",
  "created_at": "2024-01-15T10:30:00Z",
  "answers_list": [
    {
      "body": "You can use pd.merge()...",
      "votes": "35",
      "author": "answer_author",
      "time": "2024-01-15T11:00:00Z",
      "accepted": true
    }
  ],
  "url": "https://stackoverflow.com/questions/12345",
  "source": "stackoverflow"
}
```

## 技术要点

### 搜索 URL

Stack Overflow 搜索 URL 格式：
```
https://stackoverflow.com/search?q=<query>
```

### 反爬策略

- Stack Overflow 反爬较弱，但仍建议启用 `--stealth` 模式
- 控制请求频率，避免触发验证码
- 如需高频访问，建议使用已登录态

### 验证码处理

Stack Overflow 在检测到异常访问时会弹出验证码：
- 滑块验证码：自动处理
- 点选验证码：提示用户手动完成
- 访问限制：建议更换代理或使用已登录态

## 注意事项

1. **登录态**：部分搜索结果需要登录态才能完整查看
2. **反检测**：建议启用 `--stealth` 模式
3. **请求频率**：控制请求频率，避免触发验证码
4. **结果去重**：自动基于 URL 去重
5. **答案限制**：详情中最多提取 5 个答案

## 相关文件

- 搜索器：`src/searchers/stackoverflow_search.py`
- 基础类：`src/searchers/base.py`
- 工具函数：`src/searchers/utils.py`
- 浏览器导航：`src/core/browser_nav.py`
- 内容提取：`src/core/browser_console.py`
