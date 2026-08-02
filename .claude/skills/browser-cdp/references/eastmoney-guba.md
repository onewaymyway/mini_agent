---
name: eastmoney-guba
skill: browser-cdp
script: eastmoney_guba.py
description: 东方财富股吧帖子抓取脚本，支持按股票代码搜索、热度排序、评论树获取。
triggers: 东方财富股吧, 股吧抓取, 帖子抓取, 评论树, 热度排序, eastmoney_guba.py
platforms: windows, macos, linux, pc
---

# 东方财富股吧帖子抓取脚本 (`eastmoney_guba.py`)

## 用途

使用 browser-cdp skill 抓取东方财富股吧帖子列表和详情。支持按股票代码搜索帖子，获取阅读量、评论数、发布时间等核心信息。

## 使用示例

```bash
cd .claude/skills/browser-cdp

# 搜索某股票帖子
python src/searchers/eastmoney_guba.py 600519 --max-posts 20

# 按热度排序
python src/searchers/eastmoney_guba.py 000001 --sort hot --max-posts 10 --output-dir ./guba_results

# 按时间排序，获取第2页
python src/searchers/eastmoney_guba.py 300750 --sort time --page 2

# 获取帖子详情和评论
python src/searchers/eastmoney_guba.py 600519 --max-posts 5 --with-comments
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `stock_code` | 股票代码（必填） | - |
| `--max-posts` | 最大帖子数 | 20 |
| `--sort` | 排序方式 (time/hot) | time |
| `--page` | 页码 | 1 |
| `--output-dir` | 输出目录 | `./search_results/eastmoney_guba` |
| `--port` | CDP 调试端口 | 9333 |
| `--stealth` | 启用反检测模式 | True |
| `--with-comments` | 获取评论树 | False |
| `--wait-timeout` | 页面等待超时(秒) | 30 |

## 输出格式

```json
{
  "post_id": "12345678",
  "title": "帖子标题",
  "url": "https://guba.eastmoney.com/news,12345678.html",
  "author": "用户名",
  "publish_time": "2026-08-02 10:30:00",
  "read_count": 10000,
  "comment_count": 500,
  "content": "帖子内容...",
  "comments": [
    {
      "comment_id": "87654321",
      "author": "评论者",
      "content": "评论内容",
      "publish_time": "2026-08-02 11:00:00",
      "like_count": 10
    }
  ],
  "source": "eastmoney_guba",
  "scraped_at": "2026-08-02 10:30:00"
}
```

## 核心实现要点

- 使用 JS 提取帖子列表中的信息
- 支持按时间/热度排序
- 可选获取评论树（递归提取）
- 支持分页浏览
- 反检测模式隐藏自动化特征

## 注意事项

- 股吧内容更新频繁，建议及时抓取
- 大量抓取可能触发验证码
- 评论树深度建议限制在 3 层以内
- 部分帖子可能被删除，需处理 404 情况
