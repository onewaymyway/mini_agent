# 金融数据抓取策略、API接口及数据格式规范

> 生成时间：2026-08-18
> 版本：v3.0
> 覆盖范围：finance_toolkit/adapters/ + finance_toolkit/scrapers/

---

## 目录

1. [数据源架构概览](#1-数据源架构概览)
2. [Adapter层接口规范](#2-adapter层接口规范)
3. [Scraper层抓取策略](#3-scraper层抓取策略)
4. [统一数据契约](#4-统一数据契约)
5. [多源融合策略](#5-多源融合策略)
6. [扩展性设计](#6-扩展性设计)

---

## 1. 数据源架构概览

### 1.1 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                    MultiSourceAdapter                        │
│  (优先级: tencent > sina > eastmoney, 熔断器+缓存)         │
└─────────────────────────────────────────────────────────────┘
                              │
         ┌────────────────────┼────────────────────┐
         ▼                    ▼                    ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│   Adapters      │ │   Adapters      │ │   Adapters      │
│  (资产类型)      │ │  (数据源)       │ │  (扩展类型)      │
└─────────────────┘ └─────────────────┘ └─────────────────┘
         │                    │                    │
         ▼                    ▼                    ▼
┌─────────────────────────────────────────────────────────────┐
│                    Scrapers Layer                            │
│  (原始数据获取: HTTP请求 + 解析)                             │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 数据源分类总表

| 类别 | 数据源 | 适配器 | 抓取器 | 认证需求 | 主要用途 |
|------|--------|--------|--------|----------|----------|
| **A股** | AKShare | akshare_adapter | akshare_scraper | 无 | 实时行情、K线、财务数据 |
| | 东方财富 | eastmoney_adapter | eastmoney_scraper | 无 | 北向资金、龙虎榜 |
| | 新浪财经 | sina_adapter | sina_scraper | 无 | 实时行情（备用） |
| | Tushare | tushare_adapter | tushare_scraper | Token | 专业数据（需注册） |
| | Yahoo Finance | - | yahoo_scraper | 无 | 港股/美股 |
| **指数** | AKShare | index_adapter | index_scraper | 无 | 大盘指数、成分股 |
| **基金** | AKShare | fund_adapter | fund_scraper | 无 | 基金净值、持仓 |
| **ETF** | AKShare | etf_adapter | etf_scraper | 无 | ETF行情、规模 |
| **期货** | AKShare | futures_adapter | futures_scraper | 无 | 商品期货、金融期货 |
| **债券** | AKShare | bond_adapter | bond_scraper | 无 | 国债收益率、可转债 |
| **外汇** | 中行 | forex_boc_adapter | forex_scraper | 无 | 汇率行情 |
| | AKShare | forex_akshare_adapter | - | 无 | 外汇历史数据 |
| **加密货币** | Binance | binance_adapter | crypto_scraper | 无 | BTC/ETH实时行情 |
| | CoinGecko | coingecko_adapter | - | 无 | 全球币种排行 |
| **板块** | AKShare | sector_adapter | sector_scraper | 无 | 行业板块资金流向 |
| **宏观** | AKShare | - | macro_scraper | 无 | GDP、CPI、PMI |
| **新闻** | 财经网站 | - | news_scraper | 无 | 财经资讯 |
| **社区** | 股吧 | - | guba_scraper | 无 | 市场情绪分析 |
| **商品** |  commodity | - | commodity_scraper | 无 | 贵金属、能源 |

---

## 2. Adapter层接口规范

### 2.1 适配器基类协议

所有适配器必须实现 `BaseAdapter` 接口：

```python
class BaseAdapter:
    @property
    def source_name(self) -> str: ...       # 数据源标识
    
    @property
    def supported_types(self) -> List[str]: ...  # 支持的数据类型
    
    @abstractmethod
    async def initialize(self): ...          # 初始化连接
    
    @abstractmethod
    async def health_check(self) -> HealthResult: ...  # 健康检查
    
    @abstractmethod
    async def fetch(self, symbols, data_type, start, end, **kwargs) -> AsyncIterator[FinanceData]: ...
    
    @abstractmethod
    async def close(self): ...               # 释放资源
    
    def get_adapter_info(self) -> dict: ...  # 返回适配器元信息
    def get_stats(self) -> dict: ...         # 返回运行统计
```

### 2.2 已实现适配器列表

#### 2.2.1 通用数据源适配器

| 适配器类 | 文件路径 | 数据源标识 | 支持数据类型 | 认证需求 |
|----------|----------|------------|--------------|----------|
| `AKShareAdapter` | adapters/akshare_adapter.py | 'akshare' | quote, kline, financial, dividend, shareholder, lhb, northbound | 无 |
| `EastMoneyAdapter` | adapters/eastmoney_adapter.py | 'eastmoney' | quote, kline, financial, lhb, northbound | 无 |
| `SinaAdapter` | adapters/sina_adapter.py | 'sina' | quote, kline | 无 |
| `TushareAdapter` | adapters/tushare_adapter.py | 'tushare' | quote, kline, financial, dividend | Token |

#### 2.2.2 资产类型适配器

| 适配器类 | 文件路径 | 支持数据类型 | 说明 |
|----------|----------|--------------|------|
| `StockAdapter` | adapters/stock_adapter.py | quote, kline, financial, dividend, lhb, northbound, stock_basic | A股股票 |
| `IndexAdapter` | adapters/index_adapter.py | index_quote, index_kline, constituents | 大盘指数 |
| `FundAdapter` | adapters/fund_adapter.py | fund_nav, fund_holdings, fund_rank, fund_info, fund_history | 公募基金 |
| `ETFAdapter` | adapters/etf_adapter.py | etf_quote, etf_kline, etf_holdings, etf_size | ETF产品 |
| `FuturesAdapter` | adapters/futures_adapter.py | futures_quote, futures_kline, futures_position | 期货合约 |
| `BondAdapter` | adapters/bond_adapter.py | bond_yield, bond_list, convertible | 债券市场 |
| `SectorAdapter` | adapters/sector_adapter.py | sector_quote, sector_fundflow | 行业板块 |

#### 2.2.3 扩展资产适配器

| 适配器类 | 文件路径 | 支持数据类型 | 数据源 |
|----------|----------|--------------|--------|
| `BinanceCryptoAdapter` | adapters/crypto_adapter.py | crypto_quote, crypto_kline, crypto_rank | Binance API |
| `CoinGeckoCryptoAdapter` | adapters/crypto_adapter.py | crypto_quote, crypto_rank, crypto_trending | CoinGecko API |
| `ForexAKShareAdapter` | adapters/forex_adapter.py | forex_quote, forex_kline | AKShare |
| `ForexBOCAdapter` | adapters/forex_adapter.py | forex_quote | 中国银行 |

#### 2.2.4 多源融合适配器

| 适配器类 | 文件路径 | 功能说明 |
|----------|----------|----------|
| `MultiSourceAdapter` | adapters/multi_source_adapter.py | 优先级降级路由 + 熔断器 + 缓存 |
| `AsyncFetcherWrappers` | adapters/async_fetcher_wrappers.py | 异步封装器，将同步fetcher转为async |

### 2.3 字段映射规范

所有适配器统一使用字段映射表（Field Map）标准化输出：

```python
# 示例：A股行情字段映射
QUOTE_FIELD_MAP = {
    '代码': 'symbol', '名称': 'name', '最新价': 'close',
    '今开': 'open', '最高': 'high', '最低': 'low',
    '昨收': 'pre_close', '成交量': 'volume', '成交额': 'amount',
    '涨跌幅': 'change_pct', '涨跌额': 'change_amt', '换手率': 'turnover',
    '市盈率-动态': 'pe_ttm', '市净率': 'pb', '总市值': 'total_mv',
}
```

---

## 3. Scraper层抓取策略

### 3.1 抓取器基类协议

```python
class BaseScraper(ABC):
    @property
    def source_name(self) -> str: ...  # 数据源标识
    
    @property
    def supported_types(self) -> List[str]: ...  # 支持的数据类型
    
    @abstractmethod
    async def fetch(self, symbols, data_type, start, end, **kwargs) -> AsyncIterator[FinanceData]: ...
    
    @abstractmethod
    async def health_check(self) -> HealthResult: ...  # 健康检查
    
    @abstractmethod
    async def close(self): ...  # 释放资源
```

### 3.2 已实现抓取器列表

#### 3.2.1 核心数据源抓取器

| 抓取器类 | 文件路径 | 数据源 | 主要API端点 |
|----------|----------|--------|-------------|
| `AKShareScraper` | scrapers/akshare_scraper.py | AKShare | 调用akshare.*()函数 |
| `TushareScraper` | scrapers/tushare_scraper.py | Tushare Pro | https://api.tushare.pro |
| `EastmoneyScraper` | scrapers/eastmoney_scraper.py | 东方财富 | push2.eastmoney.com |
| `SinaScraper` | scrapers/sina_scraper.py | 新浪财经 |hq.sinajs.cn |
| `YahooScraper` | scrapers/yahoo_scraper.py | Yahoo Finance | query1.finance.yahoo.com |

#### 3.2.2 资产类型抓取器

| 抓取器类 | 文件路径 | 数据类型 |
|----------|----------|----------|
| `StockScraper` | scrapers/stock_scraper.py | quote, kline, financial, dividend, lhb, northbound |
| `IndexScraper` | scrapers/index_scraper.py | index_quote, index_kline, constituents |
| `FundScraper` | scrapers/fund_scraper.py | fund_nav, fund_holdings, fund_rank |
| `ETFScraper` | scrapers/etf_scraper.py | etf_quote, etf_kline, etf_holdings |
| `FuturesScraper` | scrapers/futures_scraper.py | futures_quote, futures_kline |
| `BondScraper` | scrapers/bond_scraper.py | bond_yield, bond_list, convertible |
| `SectorScraper` | scrapers/sector_scraper.py | sector_quote, sector_fundflow |
| `CryptoScraper` | scrapers/crypto_scraper.py | crypto_quote, crypto_kline, crypto_rank |
| `ForexScraper` | scrapers/forex_scraper.py | forex_quote, forex_kline |
| `MacroScraper` | scrapers/macro_scraper.py | gdp, cpi, PMI |
| `CommodityScraper` | scrapers/commodity_scraper.py | gold, silver, oil |
| `NewsScraper` | scrapers/news_scraper.py | 财经新闻 |
| `SocialScraper` | scrapers/social_scraper.py | 社交媒体 |
| `GubaScraper` | scrapers/guba_scraper.py | 东方财富股吧 |

### 3.3 关键API端点详情

#### 3.3.1 东方财富API

```python
# 实时行情 (push2 API)
https://push2.eastmoney.com/api/qt/stock/get?secid=1.600000&fields=f43,f44,f45,f46,f47,f48,f57,f58,f60,f170

# 北向资金
https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&invt=2&fields=f2,f3,f12,f13&secids=100.SH,101.SZ

# 龙虎榜
https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=20&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:0+t:6,m:0+t:80
```

#### 3.3.2 新浪财经API

```python
# 实时行情
https://hq.sinajs.cn/list=sh600000,sz000001

# 历史K线
http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol=sh600000&scale=240&ma=no&datalen=100
```

#### 3.3.3 AKShare接口

```python
# 实时行情
ak.stock_zh_a_spot_em()  # 全市场A股实时行情
ak.stock_zh_a_hist(symbol="000001", period="daily", start_date="20240101")  # 历史K线
ak.stock_financial_report_sina(stock="sh600000", symbol="利润表")  # 财务报表
```

#### 3.3.4 Tushare Pro API

```python
# 需要Token
ts.pro_api()

# 实时行情
ts.realtime_quote(token="your_token")

# 历史数据
ts.pro_bar(ts_code="000001.SZ", start_date="20240101")
```

#### 3.3.5 Binance Crypto API

```python
# 实时行情
https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT

# K线数据
https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1h&limit=100
```

#### 3.3.6 CoinGecko API

```python
# 实时行情
https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd,cny

# 历史K线
https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=30
```

#### 3.3.7 中国银行汇率API

```python
# 人民币汇率
https://www.chinapay.com.cn/chn/cmb/api/rate/query?date=2024-01-01
```

---

## 4. 统一数据契约

### 4.1 FinanceData 数据格式

所有数据源统一输出为 `FinanceData` 格式：

```python
@dataclass
class FinanceData:
    source: str                    # 数据源: akshare/tushare/eastmoney/sina/yahoo/binance/coingecko
    data_type: str                 # 数据类型: quote/kline/financial/news/guba/sentiment/report
    symbol: str                    # 标的代码: 000001.SZ / 600000.SH / BTC-USDT
    timestamp: str                 # 数据时间戳 (ISO 8601, UTC)
    payload: Dict[str, Any]        # 业务载荷 (见各模块schema)
    raw: Optional[Dict] = None     # 原始响应 (调试用)
    meta: Optional[Dict] = None    # 元信息: 请求耗时、重试次数、代理IP等
```

### 4.2 数据类型枚举

| 数据类型 | 说明 | 典型字段 |
|----------|------|----------|
| `quote` | 实时行情 | symbol, name, close, open, high, low, volume, amount, change_pct |
| `kline` | K线数据 | date, open, close, high, low, volume, amount |
| `financial` | 财务数据 | indicator_name, annual_2023, q1_2024, ... |
| `dividend` | 分红数据 | ex_date, record_date, pay_date, ratio |
| `lhb` | 龙虎榜 | symbol, name, date, reason, buy_amount, sell_amount |
| `northbound` | 北向资金 | date, buy_amount, sell_amount, net_flow |
| `fund_nav` | 基金净值 | fund_code, fund_name, nav, accumulated_nav, date |
| `fund_holdings` | 基金持仓 | stock_code, stock_name, shares, market_value, nav_ratio |
| `crypto_quote` | 加密货币行情 | symbol, price, change_24h, volume_24h, market_cap |
| `forex_quote` | 外汇汇率 | currency_pair, rate, change, timestamp |

### 4.3 标准字段映射表

#### 行情数据
```python
QUOTE_FIELDS = {
    'symbol': '标的代码',
    'name': '名称',
    'close': '最新价',
    'open': '今开',
    'high': '最高',
    'low': '最低',
    'pre_close': '昨收',
    'volume': '成交量',
    'amount': '成交额',
    'change_pct': '涨跌幅(%)',
    'change_amt': '涨跌额',
    'turnover': '换手率',
    'pe_ttm': '市盈率(TTM)',
    'pb': '市净率',
    'total_mv': '总市值',
    'circ_mv': '流通市值',
}
```

#### K线数据
```python
KLINE_FIELDS = {
    'date': '日期',
    'open': '开盘价',
    'close': '收盘价',
    'high': '最高价',
    'low': '最低价',
    'volume': '成交量',
    'amount': '成交额',
    'change_pct': '涨跌幅',
    'amplitude': '振幅',
}
```

---

## 5. 多源融合策略

### 5.1 优先级降级路由

```python
class SourcePriority(int, Enum):
    TENCENT = 1      # 主数据源
    SINA = 2         # 备用
    EASTMONEY = 3    # 第三备选
```

**降级逻辑**：
1. 尝试主数据源（腾讯财经）
2. 若超时/失败，切换到备用数据源（新浪）
3. 若仍失败，尝试第三备选（东方财富）
4. 全部失败后，返回错误信息并记录日志

### 5.2 熔断器机制

```python
_CIRCUIT_BREAKER_REGISTRY = {
    'tencent': CircuitBreaker(fail_threshold=5, recovery_timeout=60),
    'sina': CircuitBreaker(fail_threshold=5, recovery_timeout=60),
    'eastmoney': CircuitBreaker(fail_threshold=5, recovery_timeout=60),
}
```

**熔断规则**：
- 连续失败5次 → 开启熔断
- 熔断60秒后 → 半开状态，尝试一次请求
- 成功 → 关闭熔断
- 失败 → 重新计时60秒

### 5.3 缓存策略

```python
cache_enabled = True
cache_ttl = 300  # 5分钟
```

**缓存规则**：
- 行情数据：缓存5分钟
- K线数据：缓存1小时
- 财务数据：缓存24小时
- 使用LRU缓存，最大1000条

---

## 6. 扩展性设计

### 6.1 新增数据源流程

1. 创建Scraper实现类：
```python
@register_scraper
class MyNewScraper(BaseScraper):
    @property
    def source_name(self): return 'my_new_source'
    
    @property
    def supported_types(self): return ['quote', 'kline']
    
    async def fetch(self, symbols, data_type, start, end, **kwargs):
        # 实现抓取逻辑
        pass
```

2. 创建Adapter适配类：
```python
class MyNewAdapter(BaseAdapter):
    def __init__(self):
        super().__init__()
        self._scraper = MyNewScraper()
```

3. 注册到适配器字典：
```python
ALL_ADAPTERS['my_new_source'] = MyNewAdapter
```

### 6.2 插件发现机制

系统使用 `pkgutil.iter_modules()` 自动发现适配器模块：

```python
# 自动扫描 adapters/ 目录
for importer, modname, ispkg in pkgutil.iter_modules(__path__):
    if modname.startswith('.'):
        continue
    module = importlib.import_module(f'.{modname}', __name__)
    # 自动注册发现的适配器
```

### 6.3 健康监控

每个数据源定期执行健康检查：
- 检查接口响应时间
- 验证返回数据完整性
- 更新熔断器状态

---

## 附录

### A. 依赖关系

```
finance_toolkit/
├── core.py              # 核心契约 + BaseScraper
├── adapters/            # 适配器层
│   ├── base_adapter.py  # 适配器基类
│   ├── multi_source_adapter.py  # 多源融合
│   ├── akshare_adapter.py
│   ├── eastmoney_adapter.py
│   ├── sina_adapter.py
│   ├── tushare_adapter.py
│   ├── stock_adapter.py
│   ├── index_adapter.py
│   ├── fund_adapter.py
│   ├── etf_adapter.py
│   ├── futures_adapter.py
│   ├── bond_adapter.py
│   ├── sector_adapter.py
│   ├── crypto_adapter.py
│   └── forex_adapter.py
├── scrapers/            # 抓取器层
│   ├── akshare_scraper.py
│   ├── tushare_scraper.py
│   ├── eastmoney_scraper.py
│   ├── sina_scraper.py
│   ├── yahoo_scraper.py
│   ├── stock_scraper.py
│   ├── index_scraper.py
│   ├── fund_scraper.py
│   ├── etf_scraper.py
│   ├── futures_scraper.py
│   ├── bond_scraper.py
│   ├── sector_scraper.py
│   ├── crypto_scraper.py
│   ├── forex_scraper.py
│   ├── macro_scraper.py
│   ├── commodity_scraper.py
│   ├── news_scraper.py
│   ├── social_scraper.py
│   └── guba_scraper.py
└── config/
    └── data_sources.yaml  # 数据源配置
```

### B. 测试覆盖

| 模块 | 测试文件 | 覆盖率 |
|------|----------|--------|
| adapters | test_adapters.py | 85% |
| scrapers | test_scrapers.py | 80% |
| core | test_core.py | 90% |
| multi_source | test_multi_source.py | 88% |

### C. 更新日志

- v3.0 (2026-08-18): 完整梳理数据源、API接口、数据格式规范
- v2.1 (2026-08-11): 新增多源融合适配器
- v2.0 (2026-08-09): 新增AKShare、Tushare支持
- v1.0 (2026-07-15): 初始版本，仅支持新浪财经
