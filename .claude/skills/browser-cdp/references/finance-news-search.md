# 财经资讯搜索自动化脚本

本文档介绍财经资讯搜索器（finance_news_search.py）的使用方法。

## 快速开始

### 1. 启动浏览器

```bash
cd .claude/skills/browser-cdp
python src/core/browser_launch.py --dedicated --name finance_news_session --start-url "https://www.cls.cn"
```

### 2. 运行搜索

```bash
# 搜索财联社新闻
python src/searchers/finance_news_search.py "央行" --source cls --max-results 20

# 搜索证券时报新闻
python src/searchers/finance_news_search.py "股市" --source stcn --max-results 20

# 搜索第一财经新闻
python src/searchers/finance_news_search.py "经济" --source yicai --max-results 20

# 多源搜索
python src/searchers/finance_news_search.py "美联储" --sources cls,stcn,yicai --max-results 30

# 保存结果
python src/searchers/finance_news_search.py "A股" --output-dir ./results
```

## 搜索器参数

### 通用参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `query` | 搜索关键词（位置参数） | - |
| `--source` | 新闻源（cls/stcn/yicai） | cls |
| `--sources` | 多新闻源（逗号分隔） | cls |
| `--max-results` | 最大结果数 | 20 |
| `--port` | 浏览器调试端口 | 9333 |
| `--stealth` | 启用反检测模式 | True |
| `--no-stealth` | 禁用反检测模式 | - |
| `--output-dir` | 输出目录 | - |
| `--timeout` | 等待超时时间（秒） | 30 |
| `--session` | 浏览器会话名称 | finance_news_session |

### Python API 使用

```python
from src.searchers.finance_news_search import FinanceNewsSearcher

# 创建搜索器
searcher = FinanceNewsSearcher()

# 执行搜索
results = searcher.search(
    query="央行",
    source="cls",
    max_results=20,
    port=9333,
    stealth=True,
    output_dir="./results"
)

# 输出结果
for r in results:
    print(f"{r['title']}: {r['source']}")
```

## 输出格式

### JSON 格式

```json
[
  {
    "id": "finance_news_123456",
    "title": "央行：保持流动性合理充裕",
    "source": "cls",
    "summary": "中国人民银行表示...",
    "publish_time": "2026-08-03 15:30:00",
    "url": "https://www.cls.cn/detail/123456",
    "scraped_at": "2026-08-03 15:30:00"
  }
]
```

## 数据字段说明

| 字段 | 说明 |
|------|------|
| id | 结果ID |
| title | 标题 |
| source | 新闻源（cls/stcn/yicai） |
| summary | 摘要内容 |
| publish_time | 发布时间 |
| url | 详情页链接 |
| scraped_at | 抓取时间 |

## 新闻源说明

| 源代码 | 说明 |
|-------|------|
| cls | 财联社 |
| stcn | 证券时报 |
| yicai | 第一财经 |

## 已知限制

1. **无登录态要求**：财经新闻公开信息无需登录
2. **分页限制**：单次最多获取20条结果
3. **实时性**：新闻时效性强，需及时抓取

## 最佳实践

1. **控制请求频率**：使用 `--stealth` 模式，设置合理延迟（1-3秒）
2. **增量更新**：基于发布时间去重
3. **定时抓取**：建议交易时段每分钟抓取一次

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
python src/core/browser_nav.py --port 9333 --tab <id> --goto "https://www.cls.cn"
```
