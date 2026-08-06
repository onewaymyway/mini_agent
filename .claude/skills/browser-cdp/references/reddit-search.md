# Reddit搜索器文档

**版本**: 1.0.0  
**创建日期**: 2026-08-06  
**关联脚本**: `src/searchers/reddit_search.py`

---

## 1. 功能概述

Reddit是全球最大的论坛社区之一，本搜索器支持：
- 关键词搜索帖子
- 子版块筛选
- 帖子列表和评论抓取
- 排序方式（最新/热门/Top）
- 反检测模式

---

## 2. 使用方式

### 2.1 Python API

```python
from src.searchers import RedditSearcher, RedditConfig

# 创建配置
config = RedditConfig(
    query="Python",
    subreddit="programming",
    sort="hot",
    max_results=20,
    fetch_comments=True,
)

# 创建搜索器
searcher = RedditSearcher(config=config)

# 执行搜索
results = await searcher.search("Python")

# 输出结果
for post in results[:10]:
    print(f"{post.title} - r/{post.subreddit} - {post.score} points")

# 关闭资源
await searcher.close()
```

### 2.2 命令行

```bash
cd .claude/skills/browser-cdp
python src/searchers/reddit_search.py \
    --port 9333 \
    --tab <tab_id> \
    --keyword "Python" \
    --subreddit "programming" \
    --sort "hot" \
    --max-results 20 \
    --comments \
    --output output/reddit_results.json
```

---

## 3. 配置参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| query | str | "" | 搜索关键词 |
| subreddit | str | "" | 子版块名称 |
| sort | str | "relevance" | 排序方式：relevance/new/top/hot |
| max_results | int | 10 | 最大结果数 |
| stealth | bool | True | 是否启用反检测模式 |
| fetch_comments | bool | False | 是否抓取帖子评论 |
| max_comment_depth | int | 2 | 评论嵌套深度 |

---

## 4. 输出格式

### 4.1 帖子信息结构

```json
{
  "source": "reddit",
  "title": "Python 3.12 性能优化指南",
  "url": "https://www.reddit.com/r/programming/...",
  "subreddit": "programming",
  "author": "u/python_dev",
  "score": "1.2k",
  "comment_count": "234",
  "created_time": "2026-08-01",
  "upvote_ratio": "95%",
  "scraped_at": "2026-08-06T10:00:00"
}
```

### 4.2 评论信息结构

```json
{
  "source": "reddit",
  "title": "u/comment_user",
  "content": "非常好的文章，学到了...",
  "author": "u/comment_user",
  "score": "45",
  "created_time": "2026-08-01",
  "depth": 0,
  "scraped_at": "2026-08-06T10:00:00"
}
```

---

## 5. 技术要点

### 5.1 搜索流程

1. 导航到 Reddit 搜索页面
2. 输入关键词和子版块
3. 选择排序方式
4. 等待搜索结果加载
5. 提取帖子列表
6. 无限滚动加载更多

### 5.2 反检测策略

- 启用 stealth 模式移除 webdriver 标识
- 随机延迟 1-3 秒
- 模拟人类滚动行为
- 使用真实浏览器指纹

### 5.3 注意事项

- Reddit 对自动化访问较敏感，建议降低请求频率
- 部分子版块需要登录才能访问
- 评论树结构较深，建议限制 max_comment_depth

---

## 6. 错误处理

| 错误类型 | 原因 | 解决方案 |
|----------|------|----------|
| 搜索无结果 | 关键词过于具体 | 尝试更通用的关键词 |
| 反爬拦截 | 请求频率过高 | 增加随机延迟 |
| 登录提示 | 需要登录查看 | 使用已登录的浏览器实例 |
| 子版块不存在 | subreddit 名称错误 | 检查 subreddit 名称拼写 |

---

## 7. 相关资源

- [Reddit 官网](https://www.reddit.com)
- [browser-cdp 使用指南](./searchers-guide.md)
