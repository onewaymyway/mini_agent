---
name: finance-data-toolkit
description: 金融数据抓取与分析全链路工具箱。覆盖股票基础数据、东方财富股吧帖子、财经新闻、研报等多源抓取，提供数据清洗、技术/基本面信号计算、舆情分析、研报自动生成功能。适用于量化选股、舆情监控、投研自动化、个人投研决策支持等场景。
triggers: 股票数据, 东方财富, 股吧, 财经新闻, 舆情分析, 量化选股, 研报生成, 投研, 数据清洗, 技术指标, 基本面分析, stock, eastmoney, guba, finance, sentiment, quant, report
tags:
  - finance
  - data-scraping
  - analysis
  - automation
resources:
  - id: stock-basic
    path: references/stock-basic.md
    description: 股票基础数据抓取（实时行情、历史K线、财务报表、分红配股、股本结构、龙虎榜、北向资金）
    triggers: 股票基础, 实时行情, 历史K线, 财务报表, 龙虎榜, 北向资金, stock basic, quote, kline, financial
  - id: eastmoney-guba
    path: references/eastmoney-guba.md
    description: 东方财富股吧帖子抓取（指定股票/板块、热度排序、时间范围、评论树、用户画像、关键词过滤）
    triggers: 东方财富股吧, 股吧抓取, 帖子抓取, 评论树, 热度排序, guba, eastmoney
  - id: news-scraper
    path: references/news-scraper.md
    description: 多源财经新闻抓取（新浪财经、同花顺、雪球、华尔街见闻、财联社、英文媒体、微信公众号、arXiv）
    triggers: 财经新闻, 新闻抓取, 新浪财经, 同花顺, 雪球, 华尔街见闻, 财联社, 微信公众号, arXiv
  - id: data-cleaning
    path: references/data-cleaning-part1.md
    description: 数据清洗标准化规范（上）：L1-L4分级流水线、结构标准化、类型转换、时间标准化、字段映射、业务校验
    triggers: 数据清洗, 去重, 缺失值, 异常值, 字段映射, 时间对齐, 增量更新, data cleaning
  - id: data-cleaning-part2
    path: references/data-cleaning-part2.md
    description: 数据清洗标准化规范（下）：去重策略(精确/业务键/SimHash)、缺失值处理、时间对齐、增量更新、质量监控、配置文件示例
    triggers: 数据清洗进阶, 去重策略, SimHash, 增量更新, 质量监控
  - id: analysis-signals
    path: references/analysis-signals-part1.md
    description: 技术指标与基本面信号计算（上）：趋势/动量/波动率/成交量指标、资金流向分析、基本面因子、估值模型(DCF/DDM/RIM)
    triggers: 技术指标, 基本面信号, 因子选股, 多因子, MA, MACD, RSI, BOLL, KDJ, 资金流向, 估值模型
  - id: analysis-signals-part2
    path: references/analysis-signals-part2.md
    description: 技术指标与基本面信号计算（下）：因子预处理(缩尾/标准化/中性化/正交化)、多因子打分(IC加权/等权/ML)、回测框架、信号合成、风控
    triggers: 因子预处理, 多因子打分, 回测框架, 信号合成, 风控, IC加权, 中性化, 正交化
  - id: sentiment-analysis
    path: references/sentiment-analysis.md
    description: 舆情分析全流程（文本预处理、词典法/ML/BERT情感模型、方面级情感、实体识别链接、热度追踪异动预警、多源交叉验证）
    triggers: 舆情分析, 情感分析, 实体识别, 热度追踪, 异动预警, 交叉验证, sentiment, NLP
  - id: report-generation
    path: references/report-generation-part1.md
    description: 研报/周报/月报自动生成（上）：核心架构、Jinja2模板引擎(基础/周报模板)、图表生成(matplotlib/plotly)
    triggers: 研报生成, 周报生成, 月报生成, 模板引擎, 图表嵌入, 定时调度, report generation
  - id: report-generation-part2
    path: references/report-generation-part2.md
    description: 研报/周报/月报自动生成（下）：多格式导出(HTML/PDF/DOCX/Excel/MD)、定时调度、版本管理、周报/个股深度研报生成器示例
    triggers: 多格式导出, PDF导出, DOCX导出, 定时调度, 版本管理, 研报生成器
  - id: api-reference
    path: references/api-reference.md
    description: 常用数据源 API 端点速查（东方财富、同花顺、新浪、雪球、AKShare、Tushare、Wind、Choice）
    triggers: API端点, 接口速查, 东方财富API, 同花顺API, 新浪API, 雪球API, AKShare, Tushare
  - id: troubleshooting
    path: references/troubleshooting.md
    description: 常见错误排查表（反爬应对、IP封禁、签名失效、数据格式变更、浏览器崩溃、内存泄漏、并发控制）
    triggers: 报错, 失败, 反爬, IP封禁, 签名失效, 数据格式变更, 浏览器崩溃, 内存泄漏, 并发控制
browse_paths:
  - path: references/full-api-docs/
    description: 完整 API 手册（各数据源详细参数、返回字段字典、错误码表、限流策略），体量大，请用 grep/view 检索具体片段
  - path: references/historical-data-dictionary/
    description: 历史数据字典（字段演变记录、废弃字段映射、版本对照表），按需查阅
  - path: references/example-notebooks/
    description: 完整示例 Notebook 库（选股策略回测、舆情监控仪表板、研报生成演示、多因子模型训练），请自行打开查看
---

# Finance Data Toolkit

金融数据抓取与分析全链路工具箱，采用**分层架构**：核心抓取框架常驻内存，细分功能模块按需加载。

## 核心架构（必读，覆盖 80% 场景）

### 1. 数据源分层

| 层级 | 数据源 | 适用场景 | 推荐工具 | 频率限制 |
|------|--------|----------|----------|----------|
| **一级（官方/准官方）** | 交易所官网、AKShare、Tushare Pro、Wind、Choice | 回测、合规研报、基础数据 | requests/httpx + 官方 SDK | 高（需 token） |
| **二级（主流财经门户）** | 东方财富、同花顺、新浪财经、财联社、华尔街见闻 | 实时行情、新闻、股吧、研报 | browser-cdp (CDP) + requests | 中（有反爬） |
| **三级（社区/另类）** | 雪球、微信公众号、arXiv、Reddit、Twitter/X | 舆情、另类数据、学术前沿 | browser-cdp + 专用解析器 | 低（严格反爬） |

### 2. 抓取技术栈选型决策树

```
需要登录/复杂交互/动态渲染？
  ├─ 是 → browser-cdp (CDP控制真实 Chrome/Edge)
  │       ├─ 观察模式：用户操作，agent 建议/记录
  │       ├─ 代劳模式：agent 全自动执行
  │       └─ 协作模式：人机共同操作
  └─ 否 → requests/httpx + 解析器 (lxml/regex/json)
         ├─ 结构化 API → 直接调用
         ├─ 网页解析 → CSS/XPath + 正则
         └─ WebSocket/长连接 → aiohttp/websockets
```

### 3. 统一数据契约（所有模块输出标准化为此格式）

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any

@dataclass
class FinanceData:
    source: str                    # 数据源标识: eastmoney/sina/ths/xueqiu/akshare...
    data_type: str                 # 数据类型: quote/kline/financial/news/guba/report
    symbol: str                    # 标的代码: 000001.SZ / 600000.SH / BTC-USDT
    timestamp: datetime            # 数据时间戳（统一 UTC）
    payload: Dict[str, Any]        # 业务载荷（见各模块 schema）
    raw: Optional[Dict] = None     # 原始响应（调试用，生产可关闭）
    meta: Optional[Dict] = None    # 元信息：请求耗时、重试次数、代理IP、版本号等
```

### 4. 核心抽象接口（所有抓取器实现此协议）

```python
from abc import ABC, abstractmethod
from typing import AsyncIterator, List, Optional

class BaseScraper(ABC):
    """统一抓取器基类"""
    
    @property
    @abstractmethod
    def source_name(self) -> str: ...
    
    @property
    @abstractmethod
    def supported_types(self) -> List[str]: ...
    
    @abstractmethod
    async def fetch(self, 
        symbols: List[str],
        data_type: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        **kwargs
    ) -> AsyncIterator[FinanceData]: ...
    
    @abstractmethod
    async def health_check(self) -> bool: ...
    
    async def __aenter__(self): return self
    async def __aexit__(self, *args): await self.close()
    @abstractmethod
    async def close(self): ...
```

### 5. 并发与限流标准配置

```python
# 推荐默认配置（可通过环境变量覆盖）
CONCURRENCY_LIMIT = 5           # 同一数据源并发请求数
REQUEST_TIMEOUT = 30            # 单请求超时(秒)
RETRY_TIMES = 3                 # 失败重试次数
RETRY_BACKOFF = [1, 2, 5]       # 指数退避(秒)
RATE_LIMIT_PER_MIN = 60         # 每分钟最大请求数
PROXY_ROTATION = True           # 是否轮换代理
BROWSER_POOL_SIZE = 2           # CDP 浏览器实例池大小
```

### 6. 增量更新策略

| 数据类型 | 更新频率 | 去重键 | 变更检测 |
|----------|----------|--------|----------|
| 实时行情 | 秒级/分钟级 | (symbol, timestamp) | 全量覆盖 |
| 历史K线 | 日级 | (symbol, date, period) | 校验最后一根收盘价 |
| 财务报表 | 季度 | (symbol, report_date, report_type) | 版本号/发布时间 |
| 新闻/股吧 | 实时流式 | (source, article_id) | URL 去重 + 内容指纹 |
| 研报 | 日级 | (source, report_id) | 标题+机构+日期组合键 |

## 模块索引（按需加载）

| 场景 | 加载资源 | 说明 |
|------|----------|------|
| 获取股票基础行情/财务/资金流 | `skill_resource_load finance-data-toolkit stock-basic` | 核心行情模块 |
| 抓取股吧舆情/热度/评论 | `skill_resource_load finance-data-toolkit eastmoney-guba` | 股吧专用 |
| 多源新闻聚合/关键词监控 | `skill_resource_load finance-data-toolkit news-scraper` | 新闻聚合 |
| 数据入库前清洗/标准化 | `skill_resource_load finance-data-toolkit data-cleaning` | ETL 规范 |
| 计算技术指标/因子/选股 | `skill_resource_load finance-data-toolkit analysis-signals` | 量化分析 |
| 文本情感/实体/热度/预警 | `skill_resource_load finance-data-toolkit sentiment-analysis` | NLP 舆情 |
| 生成研报/周报/可视化 | `skill_resource_load finance-data-toolkit report-generation` | 报告输出 |
| 查找 API 端点/参数/错误码 | `skill_resource_load finance-data-toolkit api-reference` | 接口速查 |
| 遇到反爬/报错/性能问题 | `skill_resource_load finance-data-toolkit troubleshooting` | 故障排查 |

## Python 包结构

```
finance_toolkit/
├── __init__.py              # 统一入口，导出所有公开 API
├── core.py                  # 核心抽象：FinanceData, BaseScraper, create_scraper
├── scrapers/                # 具体数据源抓取器
│   ├── __init__.py
│   ├── akshare_scraper.py   # AKShare 抓取器
│   ├── tushare_scraper.py   # Tushare 抓取器
│   ├── eastmoney_scraper.py # 东方财富抓取器
│   └── sina_scraper.py      # 新浪财经抓取器
├── data_fetching/           # 数据获取高级封装
│   ├── __init__.py
│   ├── fetchers.py          # 同步/异步获取函数
│   ├── eastmoney_fetcher.py # 东方财富实时行情/财务/资金流向
│   ├── sina_kline_fetcher.py # 新浪 K 线 + 技术指标
│   └── guba_scraper.py      # 东方财富股吧帖子/评论/用户画像抓取
├── technical_analysis/      # 技术指标计算与信号生成
│   ├── __init__.py
│   └── indicators.py        # MA/MACD/RSI/BOLL/KDJ 等
├── backtesting/             # 因子回测框架
│   ├── __init__.py
│   └── backtest_framework.py
├── sentiment/               # 舆情分析
│   ├── __init__.py
│   └── sentiment_analyzer.py
├── batch_processing/        # 批量数据处理
│   ├── __init__.py
│   └── batch_fetcher.py
├── report_generation/       # 研报生成
│   ├── __init__.py
│   └── report_generator.py
```

## 快速开始示例

### 场景 1：获取某股票完整基础数据
```python
from finance_toolkit import create_scraper

async def get_stock_full_data(symbol: str):
    async with create_scraper('akshare') as scraper:
        async for data in scraper.fetch([symbol], 'quote'):
            print(f"实时行情: {data.payload}")
        async for data in scraper.fetch([symbol], 'kline', period='daily'):
            print(f"日K线: {len(data.payload)} 条")
        async for data in scraper.fetch([symbol], 'financial'):
            print(f"财务报表: {data.payload.keys()}")
```

### 场景 2：一键分析股票（获取K线+计算指标+生成信号）
```python
from finance_toolkit import analyze_stock

result = analyze_stock('600000.SH')
print(f"当前价: {result['price_stats']['current_price']}")
print(f"技术评分: {result['latest_indicators']}")
print(f"交易信号: {result['signals']}")
```

### 场景 3：运行因子回测
```python
from finance_toolkit import run_backtest

backtest_result = run_backtest(
    symbols=['600000.SH', '000001.SZ', '600519.SH'],
    periods=5,
    n_long=3,
    n_short=3,
    rebalance='W',
    method='equal_weight',
    long_only=False
)
print(f"年化收益: {backtest_result['annualized_return']:.2%}")
print(f"夏普比率: {backtest_result['sharpe_ratio']:.4f}")
print(f"最大回撤: {backtest_result['max_drawdown']:.2%}")
```

### 场景 4：舆情分析
```python
from finance_toolkit import analyze_sentiment, analyze_stock_sentiment

# 单条文本
result = analyze_sentiment("茅台今天大涨5%，业绩超预期，机构纷纷买入，北向资金大幅流入！")
print(f"情感: {result.payload['label']} (score={result.payload['score']:.3f})")

# 股票舆情聚合
posts = [
    {'content': '茅台业绩超预期，看好后市', 'read_count': 10000, 'comment_count': 500},
    {'content': '茅台估值太高了，泡沫风险大', 'read_count': 5000, 'comment_count': 200},
]
agg = analyze_stock_sentiment(posts, symbol='600519.SH')
print(f"舆情信号: {agg.payload['signal']['signal']}")
```

### 场景 5：生成综合研报
```python
from finance_toolkit import generate_comprehensive_report

files = generate_comprehensive_report(
    codes=['600000.SH', '000001.SZ', '600519.SH'],
    formats=['html', 'md', 'json']
)
print(f"报告已生成: {files}")
```

### 场景 6：批量获取数据
```python
from finance_toolkit import batch_fetch_stocks, batch_fetch_klines

# 批量获取实时行情
summary = batch_fetch_stocks(['600000.SH', '000001.SZ', '600519.SH'])
print(f"成功: {summary['success']}, 失败: {summary['failed']}")

# 批量获取K线（需先启动 browser-cdp）
klines = batch_fetch_klines(['600000.SH', '000001.SZ'], port=9333, tab_id='xxx')
```

### 场景 7：东方财富股吧舆情抓取
```python
from finance_toolkit import (
    sync_fetch_guba_posts,
    sync_fetch_guba_hot_posts,
    GubaPost,
    GubaComment,
    GubaUserProfile,
    EastmoneyGubaAPI,
    GubaCDPScraper,
)

# 同步获取某股票最新帖子
posts = sync_fetch_guba_posts('600519', page=1, page_size=10, sort='time')
for p in posts:
    print(f"{p.post_id}: {p.title[:30]}... (阅读:{p.read_count}, 评论:{p.comment_count})")

# 获取概念板块热帖
hot_posts = sync_fetch_guba_hot_posts('concept', top_n=5)
for p in hot_posts:
    print(f"{p.post_id}: {p.title[:30]}... (阅读:{p.read_count})")

# 异步用法（需配合 CDP 浏览器）
# async with EastmoneyGubaAPI() as api:
#     async for post in api.get_post_list(stock_code='600519', page=1, sort='hot'):
#         print(post.title)
# 
# async with GubaCDPScraper() as scraper:
#     detail = await scraper.get_post_detail('https://guba.eastmoney.com/news,123456.html')
#     comments = await scraper.get_comment_tree('123456')
#     profile = await scraper.get_user_profile('user123')
```

## 索引说明

- **主文件**只保留架构决策、统一契约、快速入口，**不包含具体实现细节**
- 具体抓取逻辑、参数详解、代码示例请加载对应 `resources` 子资源
- 超大型参考资料（完整 API 手册、历史数据字典、示例 Notebook）走 `browse_paths`，请自行 `grep`/`view` 检索
- 所有子资源文件均在 `references/` 目录下，已在 frontmatter `resources`/`browse_paths` 登记

## 环境变量配置

```bash
# 必需：数据源 Token
AKSHARE_TOKEN=your_token
TUSHARE_TOKEN=your_token
WIND_USERNAME=xxx
WIND_PASSWORD=xxx

# 可选：代理/浏览器/并发
HTTP_PROXY=http://127.0.0.1:7890
HTTPS_PROXY=http://127.0.0.1:7890
CDP_ENDPOINT=http://127.0.0.1:9222  # 已打开的 Chrome CDP 端口
CONCURRENCY_LIMIT=5
RATE_LIMIT_PER_MIN=60
LOG_LEVEL=INFO
```

## 依赖建议

```txt
# 核心
akshare>=1.12.0
tushare>=1.2.80
pandas>=2.0
numpy>=1.24
pydantic>=2.5
httpx>=0.25
lxml>=4.9

# 浏览器自动化
browser-cdp>=0.3.0  # 本项目内置 skill
playwright>=1.40  # 备选

# 分析/可视化
talib-binary>=0.4.24  # 技术指标
matplotlib>=3.7
plotly>=5.17
jinja2>=3.1  # 模板引擎

# NLP
jieba>=0.42
snownlp>=0.12.3
transformers>=4.36  # 可选：深度学习模型
```

---

> **维护提示**：新增数据源/模块时，请同步更新：
> 1. 本文件的「模块索引」表
> 2. 对应的 `references/*.md` 子资源
> 3. `references/full-api-docs/` 下的 API 手册
> 4. `references/example-notebooks/` 下的演示 Notebook

> **迁移记录**：2026-07-15 将 browser-cdp 中的金融相关脚本迁移至 finance_toolkit 包：
> - fetch_eastmoney_stock.py → finance_toolkit.data_fetching.fetchers
> - fetch_kline_sina.py → finance_toolkit.data_fetching.fetchers
> - calc_technical_indicators.py → finance_toolkit.technical_analysis.indicators
> - backtest_framework.py → finance_toolkit.backtesting.backtest_framework
> - sentiment_analysis.py → finance_toolkit.sentiment.sentiment_analyzer
> - batch_fetch_stocks.py / batch_fetch_kline.py → finance_toolkit.batch_processing.batch_fetcher
> - generate_report.py / generate_comprehensive_report.py → finance_toolkit.report_generation.report_generator
> - 新增统一入口 finance_toolkit/__init__.py 提供 create_scraper, analyze_stock, run_backtest 等便捷函数