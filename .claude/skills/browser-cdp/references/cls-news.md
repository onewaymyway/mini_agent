# 财联社新闻搜索自动化脚本

本文档介绍财联社新闻搜索器（cls_news.py）的使用方法。

## 快速开始

### 1. 启动浏览器

```bash
cd .claude/skills/browser-cdp
python src/core/browser_launch.py --dedicated --name cls_session --start-url "https://www.cls.cn"
```

### 2. 运行搜索

```bash
# 电报流（实时新闻）
python src/searchers/cls_news.py --category telegraph --max-results 50

# 财经新闻
python src/searchers/cls_news.py --category finance --max-results 30

# 科技新闻
python src/searchers/cls_news.py --category tech --max-results 20

# 搜索特定关键词
python src/searchers/cls_news.py --query "茅台" --max-results 20

# 保存结果
python src/searchers/cls_news.py --category telegraph --max-results 50 --output-dir ./results
```

## 搜索器参数

### 通用参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--category` | 新闻分类（telegraph/finance/tech/stock/crypto/macro/world） | telegraph |
| `--query` | 搜索关键词 | - |
| `--max-results` | 最大结果数 | 50 |
| `--port` | 浏览器调试端口 | 9333 |
| `--stealth` | 启用反检测模式 | True |
| `--no-stealth` | 禁用反检测模式 | - |
| `--output-dir` | 输出目录 | - |
| `--timeout` | 等待超时时间（秒） | 30 |

### Python API 使用

```python
from src.searchers.cls_news import ClsNewsSearcher
from src.searchers.base import SearcherConfig

# 创建搜索器
searcher = ClsNewsSearcher()

# 执行搜索（电报流）
results = searcher.search(
    category="telegraph",
    max_results=50,
    port=9333,
    stealth=True,
    output_dir="./results"
)

# 输出结果
for r in results:
    print(f"[{r['importance']}] {r['title']}")
```

## 输出格式

### JSON 格式

```json
[
  {
    "id": "1234567",
    "title": "央行：保持流动性合理充裕",
    "content": "中国人民银行表示，将继续实施稳健的货币政策...",
    "publish_time": "2026-08-03 15:30:00",
    "category": "财经",
    "importance": "高",
    "tags": "央行,货币政策",
    "url": "https://www.cls.cn/detail/1234567",
    "source": "cls",
    "scraped_at": "2026-08-03 15:30:00"
  }
]
```

### CSV 格式

```csv
id,title,content,publish_time,category,importance,tags,url,source,scraped_at
1234567,央行：保持流动性合理充裕,中国人民银行表示...,2026-08-03 15:30:00,财经,高,央行,货币政策,https://www.cls.cn/detail/1234567,cls,2026-08-03T15:30:00
```

## 数据字段说明

| 字段 | 说明 |
|------|------|
| id | 新闻ID |
| title | 新闻标题 |
| content | 新闻正文 |
| publish_time | 发布时间 |
| category | 新闻分类 |
| importance | 重要性评级（低/中/高/极高） |
| tags | 标签（逗号分隔） |
| url | 详情页链接 |
| source | 数据源标识 |
| scraped_at | 抓取时间 |

## 新闻分类

| 分类代码 | 说明 | 典型内容 |
|---------|------|---------|
| telegraph | 电报（实时） | 7x24小时实时财经资讯 |
| finance | 财经 | 宏观经济、政策解读 |
| tech | 科技 | 科技行业动态 |
| stock | 股票 | A股、港股、美股行情 |
| crypto | 加密货币 | 比特币、以太坊等数字货币 |
| macro | 宏观 | GDP、CPI、利率等宏观数据 |
| world | 国际 | 国际财经新闻 |

## 已知限制

1. **实时性要求高**：电报流数据时效性强，需及时抓取
2. **API 结构变化**：财联社偶尔调整 API 参数，需维护选择器
3. **内容长度限制**：部分新闻正文可能被截断

## 最佳实践

1. **控制请求频率**：使用 `--stealth` 模式，设置合理延迟（1-3秒）
2. **增量更新**：基于时间戳去重，避免重复抓取
3. **定时抓取**：电报流适合定时任务，建议每分钟抓取一次
4. **分类筛选**：根据需求选择合适分类，减少无效数据

## 错误处理

| 错误 | 原因 | 解决方案 |
|------|------|----------|
| 浏览器连接失败 | 端口不通 | 检查浏览器是否启动，使用 `--list-running` 查看 |
| 验证码检测 | 触发反爬 | 启用 `--stealth` 模式，降低请求频率 |
| 搜索结果为空 | 页面结构变化 | 检查选择器，更新 JS 代码 |
| JSON 解析失败 | 提取内容格式异常 | 检查浏览器控制台输出 |

## 调试技巧

```bash
# 查看浏览器状态
python src/core/browser_launch.py --list-running

# 手动导航测试
python src/core/browser_nav.py --port 9333 --tab <id> --goto "https://www.cls.cn/"

# 执行 JS 调试
python src/core/browser_console.py --port 9333 --tab <id> --eval "document.querySelectorAll('.telegraph-item').length"
```
