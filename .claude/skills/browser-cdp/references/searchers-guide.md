# 搜索器使用指南

本文档介绍 browser-cdp skill 中所有搜索器的使用方法。

## 快速开始

### 1. 启动浏览器

```bash
cd .claude/skills/browser-cdp
python src/core/browser_launch.py --dedicated --name search_session --start-url "https://www.jd.com"
```

### 2. 运行搜索

```bash
# 京东商品搜索
python src/searchers/jd_search.py "iPhone 15" --max-results 10

# 拼多多商品搜索
python src/searchers/pdd_search.py "机械键盘" --max-results 5

# 豆瓣搜索
python src/searchers/douban_search.py "三体" --type book

# 新浪财经新闻
python src/searchers/sina_news.py --category stock --max-results 20

# 东方财富股吧
python src/searchers/eastmoney_guba.py --stock 600519 --sort hot

# Google Scholar
python src/searchers/scholar_search.py "transformer architecture" --max-results 10
```

## 搜索器列表

| 搜索器 | 文件 | 用途 | 输出字段 |
|--------|------|------|----------|
| 京东 | `jd_search.py` | 商品搜索、详情 | title, url, price, commit, shop, sku_id |
| 拼多多 | `pdd_search.py` | 商品搜索、详情 | title, url, price, sales, shop |
| 豆瓣 | `douban_search.py` | 书籍/电影/音乐 | title, url, rating, votes, type |
| 新浪财经 | `sina_news.py` | 财经新闻 | title, url, summary, published, category |
| 东方财富股吧 | `eastmoney_guba.py` | 股票帖子、评论 | title, url, content, author, read_count, comment_count |
| Google Scholar | `scholar_search.py` | 学术论文 | title, url, author, snippet, cited, year |
| 链家房产 | `lianjia_search.py` | 二手房/租房/小区 | title, url, price, area, district, type, direction, ghost_filtered |
| 雪球金融 | `xueqiu_search.py` | 行情/讨论/组合 | symbol, name, price, change_pct, volume, discussion, portfolio |
| 财联社新闻 | `cls_news.py` | 电报/分类新闻 | title, content, publish_time, category, importance, tags, url |

## 命令行参数

### 通用参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--port` | 浏览器调试端口 | 9333 |
| `--tab` | Tab ID | 自动获取 |
| `--stealth` | 启用反检测模式 | True |
| `--no-stealth` | 禁用反检测模式 | - |
| `--wait-timeout` | 等待超时时间（秒） | 30 |
| `--output-dir` | 输出目录 | 当前目录 |
| `--max-results` | 最大结果数 | 10 |

### 京东搜索器参数

```bash
python src/searchers/jd_search.py "关键词" \
    --max-results 10 \
    --output-dir ./jd_results \
    --port 9333
```

### 拼多多搜索器参数

```bash
python src/searchers/pdd_search.py "关键词" \
    --max-results 10 \
    --output-dir ./pdd_results
```

### 豆瓣搜索器参数

```bash
python src/searchers/douban_search.py "关键词" \
    --type book \
    --max-results 10
```

支持类型：`book`（书籍）、`movie`（电影）、`music`（音乐）

### 新浪财经搜索器参数

```bash
python src/searchers/sina_news.py \
    --category stock \
    --max-results 20 \
    --query "茅台"
```

支持分类：`stock`（股票）、`macro`（宏观）、`industry`（产业）、`forex`（外汇）、`futures`（期货）

### 东方财富股吧搜索器参数

```bash
python src/searchers/eastmoney_guba.py \
    --stock 600519 \
    --sort hot \
    --max-results 20
```

支持排序：`hot`（热度）、`time`（时间）

### Google Scholar 搜索器参数

```bash
python src/searchers/scholar_search.py "machine learning" \
    --max-results 10 \
    --output-dir ./scholar_results
```

### 链家房产搜索器参数

```bash
# 二手房搜索（默认北京）
python src/searchers/lianjia_search.py --city bj --type ershoufang --max-results 20

# 租房搜索（指定城区）
python src/searchers/lianjia_search.py --city sh --type zufang --district 浦东 --max-results 10

# 小区信息搜索
python src/searchers/lianjia_search.py --city gz --type xiaoqu --xiaoqu "天河北" --output-dir ./results
```

支持城市：`bj`（北京）、`sh`（上海）、`gz`（广州）、`sz`（深圳）、`cd`（成都）、`wh`（武汉）、`nj`（南京）、`hz`（杭州）、`xa`（西安）、`tl`（太原）
支持类型：`ershoufang`（二手房）、`zufang`（租房）、`xiaoqu`（小区）

### 雪球金融搜索器参数

```bash
# 股票行情（需登录态）
python src/searchers/xueqiu_search.py --symbol 600519 --type quote --max-results 10

# 讨论区搜索
python src/searchers/xueqiu_search.py --symbol AAPL --type discussion --max-results 20

# 组合持仓查询
python src/searchers/xueqiu_search.py --portfolio P123456 --type portfolio --output-dir ./results
```

> ⚠️ 雪球需要登录态，首次使用需手动登录：
> ```bash
> python src/core/browser_launch.py --dedicated --name xueqiu_session --start-url "https://xueqiu.com"
> ```

支持类型：`quote`（行情）、`discussion`（讨论）、`portfolio`（组合持仓）

### 财联社新闻搜索器参数

```bash
# 电报流（实时新闻）
python src/searchers/cls_news.py --category telegraph --max-results 50

# 分类新闻
python src/searchers/cls_news.py --category finance --max-results 20

# 关键词搜索
python src/searchers/cls_news.py --query "茅台" --max-results 20 --output-dir ./results
```

支持分类：`telegraph`（电报）、`finance`（财经）、`tech`（科技）、`stock`（股票）、`crypto`（加密货币）、`macro`（宏观）、`world`（国际）

## Python API 使用

```python
from src.searchers.jd_search import JDSearcher
from src.searchers.base import SearcherConfig

# 创建搜索器
searcher = JDSearcher()

# 执行搜索
results = searcher.search(
    query="iPhone 15",
    max_results=10,
    port=9333,
    stealth=True
)

# 输出结果
for r in results:
    print(f"{r['title']}: {r['price']}")
```

## 输出格式

### JSON 格式

```json
[
  {
    "title": "商品标题",
    "url": "https://item.jd.com/123456.html",
    "price": "¥5999.00",
    "commit": "100万+",
    "shop": "京东自营",
    "sku_id": "123456",
    "source": "jd",
    "query": "iPhone 15",
    "scraped_at": "2026-08-02 10:30:00"
  }
]
```

### CSV 格式

```csv
title,url,price,commit,shop,sku_id,source,query,scraped_at
商品标题,https://item.jd.com/123456.html,¥5999.00,100万+,京东自营,123456,jd,iPhone 15,2026-08-02 10:30:00
```

### Markdown 格式

```
# 搜索结果

## 1. 商品标题
- 来源: jd
- 链接: https://item.jd.com/123456.html
- 价格: ¥5999.00
- 评价: 100万+
```

## 错误处理

### 常见错误及解决方案

| 错误 | 原因 | 解决方案 |
|------|------|----------|
| 浏览器连接失败 | 端口不通 | 检查浏览器是否启动，使用 `--list-running` 查看 |
| 验证码检测 | 触发反爬 | 启用 `--stealth` 模式，降低请求频率 |
| 搜索结果为空 | 页面结构变化 | 检查选择器，更新 JS 代码 |
| JSON 解析失败 | 提取内容格式异常 | 检查浏览器控制台输出 |

### 调试技巧

```bash
# 查看浏览器状态
python src/core/browser_launch.py --list-running

# 手动导航测试
python src/core/browser_nav.py --port 9333 --tab <id> --goto "https://www.jd.com"

# 执行 JS 调试
python src/core/browser_console.py --port 9333 --tab <id> --eval "document.title"
```

## 最佳实践

1. **控制请求频率**：使用 `--stealth` 模式，设置合理延迟
2. **复用浏览器实例**：使用 `--dedicated --name` 保持登录态
3. **保存结果**：指定 `--output-dir` 保存搜索结果
4. **错误重试**：遇到临时错误时，等待后重试
5. **验证结果**：检查输出字段完整性

## 扩展开发

### 添加新搜索器

1. 在 `src/searchers/` 下创建新文件
2. 继承 `BaseSearcher` 类
3. 实现 `source_name`、`supported_types`、`search`、`get_detail` 方法
4. 在 `src/searchers/__init__.py` 中导出
5. 编写测试用例

### 示例模板

```python
from src.searchers.base import BaseSearcher, SearcherConfig, SearchResult

class MySearcher(BaseSearcher):
    @property
    def source_name(self) -> str:
        return "my_site"
    
    @property
    def supported_types(self) -> List[str]:
        return ["search", "detail"]
    
    async def search(self, query: str, config: Optional[SearcherConfig] = None) -> List[SearchResult]:
        # 实现搜索逻辑
        pass
    
    async def get_detail(self, url: str, config: Optional[SearcherConfig] = None) -> Dict:
        # 实现详情获取逻辑
        pass
```
