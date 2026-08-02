---
name: douban-search
skill: browser-cdp
script: douban_search.py
description: 豆瓣搜索自动化脚本，支持书籍/电影/音乐搜索，获取评分、评价数等核心信息。
triggers: 豆瓣搜索, douban search, 豆瓣, 书籍搜索, 电影搜索, douban_search.py
platforms: windows, macos, linux, pc
---

# 豆瓣搜索自动化脚本 (`douban_search.py`)

## 用途

使用 browser-cdp skill 搜索豆瓣书籍/电影/音乐，获取评分、评价数等核心信息。
豆瓣需要登录态，建议首次使用时手动登录。

## 使用示例

```bash
cd .claude/skills/browser-cdp

# 搜索书籍
python src/searchers/douban_search.py "三体" --type book --max-results 10

# 搜索电影
python src/searchers/douban_search.py "肖申克的救赎" --type movie --max-results 5

# 搜索音乐
python src/searchers/douban_search.py "周杰伦" --type music --port 9333

# 指定输出目录
python src/searchers/douban_search.py "活着" --type book --output-dir ./douban_results
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `query` | 搜索关键词（必填） | - |
| `--type` | 搜索类型 (book/movie/music/play) | book |
| `--max-results` | 最大结果数量 | 10 |
| `--output-dir` | 输出目录 | `./search_results/douban` |
| `--port` | CDP 调试端口 | 9333 |
| `--stealth` | 启用反检测模式 | True |
| `--wait-timeout` | 页面等待超时(秒) | 30 |

## 输出格式

```json
{
  "title": "书名/电影名",
  "url": "https://book.douban.com/subject/xxxx/",
  "rating": "9.7",
  "rating_count": "50万人评价",
  "author": "刘慈欣",
  "publisher": "重庆出版社",
  "year": "2008",
  "source": "douban",
  "scraped_at": "2026-08-02 10:30:00"
}
```

## 核心实现要点

- 使用 JS 提取搜索结果列表中的条目
- 评分从 `.rating_num` 提取
- 评价数从 `.rating_people` 提取
- 支持按类型过滤（书籍/电影/音乐/戏剧）
- 需要登录态访问完整信息

## 注意事项

- 豆瓣需要登录态才能获取完整信息
- 首次使用建议手动登录豆瓣账号
- 搜索频率不宜过高，避免触发验证码
- 部分书籍/电影可能需要付费阅读/观看
