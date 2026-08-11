# Finance Data Toolkit

金融数据抓取与分析全链路工具箱，采用**分层架构**：核心抓取框架常驻内存，细分功能模块按需加载。

## 目录

- [快速开始](#快速开始)
- [核心架构](#核心架构)
- [模块索引](#模块索引)
- [数据格式规范](#数据格式规范)
- [使用示例](#使用示例)
- [配置说明](#配置说明)
- [依赖安装](#依赖安装)
- [测试与验证](#测试与验证)
- [贡献指南](#贡献指南)

---

## 快速开始

### 1. 安装依赖

```bash
cd .claude/skills/finance-data-toolkit
pip install -e .
```

### 2. 基础使用

```python
from finance_toolkit import create_scraper, analyze_stock, run_backtest

# 创建抓取器
async with create_scraper('akshare') as scraper:
    async for data in scraper.fetch(['600000.SH'], 'quote'):
        print(f"实时行情: {data.payload}")
    async for data in scraper.fetch(['600000.SH'], 'kline', period='daily'):
        print(f"日K线: {len(data.payload)} 条")

# 一键分析股票
result = analyze_stock('600000.SH')
print(f"当前价: {result['price_stats']['current_price']}")
print(f"技术评分: {result['latest_indicators']}")
print(f"交易信号: {result['signals']}")

# 运行回测
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

### 3. 舆情分析

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

### 4. 生成综合研报

```python
from finance_toolkit import generate_comprehensive_report

files = generate_comprehensive_report(
    codes=['600000.SH', '000001.SZ', '600519.SH'],
    formats=['html', 'md', 'json']
)
print(f"报告已生成: {files}")
```

### 5. 批量获取数据

```python
from finance_toolkit import batch_fetch_stocks, batch_fetch_klines

# 批量获取实时行情
summary = batch_fetch_stocks(['600000.SH', '000001.SZ', '600519.SH'])
print(f"成功: {summary['success']}, 失败: {summary['failed']}")

# 批量获取K线（需先启动 browser-cdp）
klines = batch_fetch_klines(['600000.SH', '000001.SZ'], port=9333, tab_id='xxx')
```

### 6. 东方财富股吧舆情抓取

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

---

## 核心架构

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

---

## 模块索引

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
| 免登录数据源快速抓取 | `skill_resource_load finance-data-toolkit free-data-sources` | 腾讯/网易/百度/中金/和讯/凤凰/新浪多市场 |

---

## 数据格式规范

### 顶层字段定义

| 字段 | 类型 | 必填 | 说明 | 示例 |
|------|------|------|------|------|
| `source` | string | ✅ | 数据源标识 | `akshare`, `eastmoney`, `sina` |
| `data_type` | string | ✅ | 数据类型 | `quote`, `kline`, `financial` |
| `symbol` | string | ✅ | 标的代码 | `600000.SH`, `BTC-USDT` |
| `timestamp` | string | ✅ | 数据时间戳 (ISO 8601) | `2024-01-15T10:30:00Z` |
| `payload` | object | ✅ | 业务载荷（见各类型定义） | 见下文 |
| `raw` | object/null | ❌ | 原始响应（调试用） | `{...}` |
| `meta` | object/null | ❌ | 元信息（请求耗时、质量评分等） | `{...}` |

### 数据类型枚举

#### 行情类

| data_type | 说明 | 核心字段 |
|-----------|------|----------|
| `quote` | 实时行情 | open, high, low, close, volume, amount |
| `kline` | K线数据 | date, open, high, low, close, volume, amount |
| `index_quote` | 指数行情 | open, high, low, close, volume, amount |
| `index_kline` | 指数K线 | date, open, high, low, close, volume, amount |
| `etf_quote` | ETF行情 | open, high, low, close, volume, amount, nav |
| `etf_kline` | ETF K线 | date, open, high, low, close, volume |
| `forex_quote` | 外汇行情 | currency_pair, rate, change_pct |
| `crypto_quote` | 加密货币 | symbol, price, volume_24h, market_cap |

#### 财务类

| data_type | 说明 | 核心字段 |
|-----------|------|----------|
| `financial` | 财务报表 | type, report_date, revenue, net_profit |
| `dividend` | 分红数据 | announcement_date, record_date, dividend_per_share |
| `lhb` | 龙虎榜 | trade_date, reason, net_buy_amount |
| `northbound` | 北向资金 | date, type, net_inflow |

#### 基金类

| data_type | 说明 | 核心字段 |
|-----------|------|----------|
| `fund_nav` | 基金净值 | nav_date, nav, accumulated_nav |
| `fund_holdings` | 基金持仓 | report_date, stock_code, weight |

#### 债券/期货类

| data_type | 说明 | 核心字段 |
|-----------|------|----------|
| `bond_yield` | 债券收益率 | date, bond_type, yield_rate |
| `bond_quote` | 债券行情 | bond_code, price, yield_rate |
| `futures_quote` | 期货行情 | contract_code, open, high, low, close, volume |
| `futures_kline` | 期货K线 | date, open, high, low, close, volume |

#### 宏观类

| data_type | 说明 | 核心字段 |
|-----------|------|----------|
| `macro_gdp` | GDP数据 | quarter, gdp, yoy |
| `macro_cpi` | CPI数据 | date, cpi, yoy |
| `macro_pmi` | PMI数据 | date, pmi |

#### 资讯/情绪类

| data_type | 说明 | 核心字段 |
|-----------|------|----------|
| `news` | 新闻资讯 | title, publish_time, source, sentiment_score |
| `sentiment` | 情绪数据 | date, sentiment_score, sentiment_label |
| `social` | 社交数据 | post_id, content, publish_time, author |

#### 基础信息类

| data_type | 说明 | 核心字段 |
|-----------|------|----------|
| `stock_basic` | 股票基础信息 | name, industry, list_date |

### meta 字段定义

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `fetch_time_ms` | number | 请求耗时（毫秒） | `150.5` |
| `retry_count` | integer | 重试次数 | `0` |
| `proxy_ip` | string | 代理IP | `192.168.1.1` |
| `version` | string | 数据版本号 | `v1.0.0` |
| `quality_score` | number | 数据质量评分 (0-1) | `0.95` |
| `warnings` | array[string] | 数据质量警告 | `["数据延迟", "部分字段缺失"]` |

---

## Python 包结构

```
finance_toolkit/
├── __init__.py              # 统一入口，导出所有公开 API
├── core.py                  # 核心抽象：FinanceData, BaseScraper, create_scraper
├── exceptions.py            # 异常定义：FinanceError, SourceError, DataError 等
├── resilience.py            # 容错机制：CircuitBreaker, FallbackManager, retry_with_backoff
├── validation.py            # 数据验证：QuoteValidator, FinancialValidator, NewsValidator 等
├── scrapers/                # 具体数据源抓取器
│   ├── __init__.py
│   ├── akshare_scraper.py   # AKShare 抓取器
│   ├── tushare_scraper.py   # Tushare 抓取器
│   ├── eastmoney_scraper.py # 东方财富抓取器
│   ├── sina_scraper.py      # 新浪财经抓取器
│   └── yahoo_scraper.py     # Yahoo Finance 抓取器
├── data_fetching/           # 数据获取高级封装
│   ├── __init__.py
│   ├── fetchers.py          # 同步/异步获取函数
│   ├── async_fetchers.py    # 异步获取函数
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
├── cleaning/                # 数据清洗标准化
│   ├── __init__.py
│   ├── alignment.py         # 时间对齐
│   ├── dedup.py             # 去重
│   ├── features.py          # 特征工程
│   ├── mappers.py           # 字段映射
│   ├── missing.py           # 缺失值处理
│   ├── normalizers.py       # 标准化
│   ├── pipeline.py          # 清洗流水线
│   ├── quality.py           # 质量监控
│   └── validators.py        # 数据验证
├── news/                    # 财经新闻抓取
│   ├── __init__.py
│   ├── models.py            # 新闻数据模型
│   ├── scrapers.py          # 多源新闻抓取器
│   ├── aggregator.py        # 新闻聚合
│   └── stream.py            # 实时新闻流
├── social/                  # 社交媒体数据
│   ├── __init__.py
│   ├── models.py            # 社交数据模型
│   └── scrapers.py          # 社交媒体抓取器
├── compliance_checker.py    # 合规检查器
├── schema_validator.py      # Schema 验证器
├── output_formatter.py      # 输出格式化器
├── health_monitor.py        # 健康监控模块
├── rate_limiter.py          # 限流器模块
└── unified_fetcher.py       # 统一数据抓取接口
```

---

## 配置说明

### 环境变量配置

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

### 配置文件

项目使用 `pyproject.toml` 管理依赖和构建配置：

```toml
[project]
name = "finance-toolkit"
version = "1.0.0"
description = "金融数据抓取与分析全链路工具箱"
requires-python = ">=3.10"
dependencies = [
    "akshare>=1.12.0",
    "tushare>=1.2.80",
    "yfinance>=0.2.40",
    "pandas>=2.0",
    "numpy>=1.24",
    "pydantic>=2.5",
    "httpx>=0.25",
    "lxml>=4.9",
    "beautifulsoup4>=4.12",
    "curl-cffi>=0.7",
    "jieba>=0.42",
    "snownlp>=0.12.3",
    "matplotlib>=3.7",
    "plotly>=5.17",
    "jinja2>=3.1",
    "arxiv>=2.1",
    "websockets>=12.0",
]

[project.optional-dependencies]
talib = ["talib-binary>=0.4.24"]
nlp = ["transformers>=4.36"]
```

---

## 依赖安装

### 核心依赖

```txt
akshare>=1.12.0
tushare>=1.2.80
yfinance>=0.2.40
pandas>=2.0
numpy>=1.24
pydantic>=2.5
httpx>=0.25
lxml>=4.9
beautifulsoup4>=4.12
curl-cffi>=0.7
jieba>=0.42
snownlp>=0.12.3
matplotlib>=3.7
plotly>=5.17
jinja2>=3.1
arxiv>=2.1
websockets>=12.0
```

### 可选依赖

```txt
# 技术指标
 talib-binary>=0.4.24

# NLP/深度学习
transformers>=4.36

# 浏览器自动化
browser-cdp>=0.3.0  # 本项目内置 skill
playwright>=1.40    # 备选
```

### 安装命令

```bash
# 基础安装
pip install -e .

# 完整安装（含可选依赖）
pip install -e ".[talib,nlp]"

# 开发安装（含测试依赖）
pip install -e ".[dev]"
```

---

## 测试与验证

### 运行测试

```bash
# 运行所有测试
cd .claude/skills/finance-data-toolkit
python -m pytest tests/ -v

# 运行特定测试
python -m pytest tests/test_data_fetching.py -v
python -m pytest tests/test_compliance_checker.py -v
python -m pytest tests/test_unified_interface.py -v
```

### 示例脚本

```bash
# 数据质量校验示例
python examples/data_validation_example.py

# 合规检查示例
python examples/compliance_check_example.py

# 数据验证脚本（支持多种输出格式）
python examples/data_validation_script.py --format json
python examples/data_validation_script.py --format text
python examples/data_validation_script.py --format table
python examples/data_validation_script.py --format summary
python examples/data_validation_script.py --output-dir ./output
```

### 测试覆盖

- `test_data_fetching.py` - 数据抓取测试
- `test_compliance_checker.py` - 合规检查测试
- `test_unified_interface.py` - 统一接口测试
- `test_validation.py` - 数据验证测试
- `test_technical_analysis.py` - 技术分析测试
- `test_news_scrapers.py` - 新闻抓取测试
- `test_social_scrapers.py` - 社交数据测试
- `test_backtest_framework.py` - 回测框架测试

---

## 贡献指南

### 新增数据源

1. 在 `finance_toolkit/scrapers/` 下创建新的抓取器类
2. 实现 `BaseScraper` 接口
3. 在 `finance_toolkit/__init__.py` 中导出新接口
4. 添加对应的测试用例
5. 更新 `references/` 下的 API 文档

### 新增数据类型

1. 在 `docs/templates/` 下添加新的 JSON 模板
2. 更新 `docs/data-schema.json` Schema 定义
3. 更新 `docs/data-schema-guide.md` 文档
4. 在 `finance_toolkit/validation.py` 中添加验证规则
5. 添加对应的测试用例

### 代码规范

- 遵循 PEP 8 编码规范
- 使用 type hints 标注函数签名
- 添加完整的 docstring
- 保持向后兼容性

---

## 维护记录

### 2026-08-09
- 整合 finance-data-toolkit 目录文件
- 更新 README 包含使用说明和快速开始指南
- 完善数据格式规范文档

### 2026-08-08
- 新增统一数据抓取接口与异常处理机制
- 新增 `finance_toolkit/exceptions.py` → 增强异常体系
- 新增 `finance_toolkit/resilience.py` → 增强容错机制
- 新增 `finance_toolkit/unified_fetcher.py` → 统一数据抓取接口
- 新增 `finance_toolkit/health_monitor.py` → 健康监控模块
- 新增 `finance_toolkit/rate_limiter.py` → 限流器模块
- 新增 `tests/test_unified_interface.py` → 33 个测试用例全部通过

### 2026-07-15
- 将 browser-cdp 中的金融相关脚本迁移至 finance_toolkit 包
- 新增统一入口 `finance_toolkit/__init__.py`

### 2026-07-14
- 创建 finance-data-toolkit skill
- 实现核心抓取框架和数据清洗模块
- 添加数据验证和合规检查功能

---

## 相关文档

- [数据格式使用指南](docs/data-format-guide.md)
- [数据 Schema 指南](docs/data-schema-guide.md)
- [统一接口规范](docs/unified-interface-spec.md)
- [合规检查器指南](docs/compliance-checker-guide.md)
- [数据源调研](docs/data-source-research.md)
- [依赖分析](docs/dependency-analysis.md)

---

## 许可证

本项目采用 MIT 许可证。

---

> **注意**：本项目仅供学习和研究使用，不构成投资建议。使用者应自行承担使用风险。
