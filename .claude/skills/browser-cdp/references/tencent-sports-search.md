# 腾讯体育搜索自动化脚本

本文档介绍腾讯体育搜索器（tencent_sports_search.py）的使用方法。

## 快速开始

### 1. 启动浏览器

```bash
cd .claude/skills/browser-cdp
python src/core/browser_launch.py --dedicated --name tencent_sports_session --start-url "https://sports.qq.com"
```

### 2. 运行搜索

```bash
# 搜索体育新闻
python src/searchers/tencent_sports_search.py "NBA" --max-results 20

# 搜索足球资讯
python src/searchers/tencent_sports_search.py "世界杯" --max-results 20

# 搜索篮球资讯
python src/searchers/tencent_sports_search.py "CBA" --max-results 15

# 保存结果
python src/searchers/tencent_sports_search.py "奥运会" --output-dir ./results
```

## 搜索器参数

### 通用参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `query` | 搜索关键词（位置参数） | - |
| `--max-results` | 最大结果数 | 20 |
| `--port` | 浏览器调试端口 | 9333 |
| `--stealth` | 启用反检测模式 | True |
| `--no-stealth` | 禁用反检测模式 | - |
| `--output-dir` | 输出目录 | - |
| `--timeout` | 等待超时时间（秒） | 30 |
| `--session` | 浏览器会话名称 | tencent_sports_session |

### Python API 使用

```python
from src.searchers.tencent_sports_search import TencentSportsSearcher

# 创建搜索器
searcher = TencentSportsSearcher()

# 执行搜索
results = searcher.search(
    query="NBA",
    max_results=20,
    port=9333,
    stealth=True,
    output_dir="./results"
)

# 输出结果
for r in results:
    print(f"{r['title']}: {r['url']}")
```

## 输出格式

### JSON 格式

```json
[
  {
    "id": "tencent_sports_123456",
    "title": "NBA季后赛：湖人晋级总决赛",
    "type": "体育新闻",
    "summary": "在昨晚的比赛中...",
    "url": "https://sports.qq.com/a/123456",
    "source": "tencent_sports",
    "scraped_at": "2026-08-03 15:30:00"
  }
]
```

## 数据字段说明

| 字段 | 说明 |
|------|------|
| id | 结果ID |
| title | 标题 |
| type | 结果类型（体育新闻/赛事资讯/球员动态） |
| summary | 摘要内容 |
| url | 详情页链接 |
| source | 数据源标识 |
| scraped_at | 抓取时间 |

## 已知限制

1. **无登录态要求**：腾讯体育公开信息无需登录
2. **分页限制**：单次最多获取20条结果
3. **视频内容**：部分内容为视频，需额外处理

## 最佳实践

1. **控制请求频率**：使用 `--stealth` 模式，设置合理延迟（2-4秒）
2. **增量更新**：基于 URL 去重
3. **定时抓取**：建议在赛事期间增加抓取频率

## 错误处理

| 错误 | 原因 | 解决方案 |
|------|------|----------|
| 浏览器连接失败 | 端口不通 | 检查浏览器是否启动 |
| 验证码检测 | 触发反爬 | 启用 `--stealth` 模式 |
| 搜索结果为空 | 关键词无结果 | 尝试更宽泛的关键词 |

## 调试技巧

```bash
# 查看浏览器状态
python src/core/browser_launch.py --list-running

# 手动导航测试
python src/core/browser_nav.py --port 9333 --tab <id> --goto "https://sports.qq.com"
```
