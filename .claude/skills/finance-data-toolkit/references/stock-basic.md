# 股票基础数据抓取

> 核心行情模块：实时行情、历史K线、财务报表、分红配股、股本结构、龙虎榜、北向资金

---

## 1. 统一数据契约

所有抓取器输出标准化为 `FinanceData`：

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any

@dataclass
class FinanceData:
    source: str                    # 数据源: akshare/tushare/eastmoney/sina
    data_type: str                 # quote/kline/financial/dividend/shareholder/lhb/northbound
    symbol: str                    # 标的代码: 000001.SZ / 600000.SH
    timestamp: datetime            # 数据时间戳 (UTC)
    payload: Dict[str, Any]        # 业务载荷
    raw: Optional[Dict] = None     # 原始响应 (调试用)
    meta: Optional[Dict] = None    # 元信息: 耗时、重试次数、代理IP、版本号
```

---

## 2. AKShare 抓取器 (推荐主力源)

### 2.1 实时行情
```python
import akshare as ak
import pandas as pd
from datetime import datetime

async def fetch_realtime_quote(symbols: list[str]) -> list[FinanceData]:
    """获取 A 股实时行情"""
    df = ak.stock_zh_a_spot_em()  # 东方财富接口，无需 token
    
    # 标准化代码格式
    df['symbol'] = df['代码'].apply(lambda x: f"{x}.SZ" if x.startswith(('0','3')) else f"{x}.SH")
    
    results = []
    for _, row in df[df['symbol'].isin(symbols)].iterrows():
        payload = {
            'open': row['今开'],
            'high': row['最高'],
            'low': row['最低'],
            'close': row['最新价'],
            'pre_close': row['昨收'],
            'volume': row['成交量'],
            'amount': row['成交额'],
            'change_pct': row['涨跌幅'],
            'change_amt': row['涨跌额'],
            'turnover': row['换手率'],
            'pe_ttm': row['市盈率-动态'],
            'pb': row['市净率'],
            'total_mv': row['总市值'],
            'circ_mv': row['流通市值']
        }
        results.append(FinanceData(
            source='akshare',
            data_type='quote',
            symbol=row['symbol'],
            timestamp=datetime.utcnow(),
            payload=payload
        ))
    return results
```

### 2.2 历史 K 线
```python
async def fetch_kline(
    symbol: str,
    period: str = 'daily',  # daily/weekly/monthly/1m/5m/15m/30m/60m
    start: str = '20240101',
    end: str = '20241231',
    adjust: str = 'qfq'     # qfq前复权/hfq后复权/不复权
) -> list[FinanceData]:
    """获取历史 K 线"""
    # 代码转换: 600000.SH -> 600000
    code = symbol.split('.')[0]
    
    df = ak.stock_zh_a_hist(
        symbol=code,
        period=period,
        start_date=start,
        end_date=end,
        adjust=adjust
    )
    
    # 统一列名
    df = df.rename(columns={
        '日期': 'date', '开盘': 'open', '收盘': 'close',
        '最高': 'high', '最低': 'low', '成交量': 'volume',
        '成交额': 'amount', '振幅': 'amplitude',
        '涨跌幅': 'change_pct', '涨跌额': 'change_amt',
        '换手率': 'turnover'
    })
    
    results = []
    for _, row in df.iterrows():
        results.append(FinanceData(
            source='akshare',
            data_type='kline',
            symbol=symbol,
            timestamp=pd.to_datetime(row['date']),
            payload=row.to_dict()
        ))
    return results
```

### 2.3 财务报表
```python
async def fetch_financials(symbol: str) -> list[FinanceData]:
    """获取财务报表 (资产负债表/利润表/现金流量表)"""
    code = symbol.split('.')[0]
    
    # 利润表
    income = ak.stock_financial_report_sina(stock=code, symbol='利润表')
    # 资产负债表
    balance = ak.stock_financial_report_sina(stock=code, symbol='资产负债表')
    # 现金流量表
    cashflow = ak.stock_financial_report_sina(stock=code, symbol='现金流量表')
    
    # 合并关键指标
    results = []
    for report_type, df in [('income', income), ('balance', balance), ('cashflow', cashflow)]:
        for _, row in df.iterrows():
            results.append(FinanceData(
                source='akshare',
                data_type='financial',
                symbol=symbol,
                timestamp=pd.to_datetime(row.get('报告期', row.get('日期'))),
                payload={
                    'report_type': report_type,
                    'metrics': row.to_dict()
                }
            ))
    return results
```

### 2.4 分红配股
```python
async def fetch_dividend(symbol: str) -> list[FinanceData]:
    """分红配股数据"""
    code = symbol.split('.')[0]
    df = ak.stock_fhps_em(symbol=code)
    
    results = []
    for _, row in df.iterrows():
        results.append(FinanceData(
            source='akshare',
            data_type='dividend',
            symbol=symbol,
            timestamp=pd.to_datetime(row['方案公告日']),
            payload={
                'dividend_plan': row['分红方案'],
                'cash_dividend': row['每股分红'],
                'stock_dividend': row['每股送转'],
                'record_date': row['股权登记日'],
                'ex_date': row['除权除息日'],
                'pay_date': row['派息日']
            }
        ))
    return results
```

### 2.5 股本结构 / 限售解禁
```python
async def fetch_share_structure(symbol: str) -> list[FinanceData]:
    """股本结构变动"""
    code = symbol.split('.')[0]
    df = ak.stock_gbq_em(symbol=code)  # 股本结构
    
    results = []
    for _, row in df.iterrows():
        results.append(FinanceData(
            source='akshare',
            data_type='share_structure',
            symbol=symbol,
            timestamp=pd.to_datetime(row['日期']),
            payload=row.to_dict()
        ))
    return results
```

### 2.6 龙虎榜
```python
async def fetch_lhb(symbol: str, start: str, end: str) -> list[FinanceData]:
    """龙虎榜详情"""
    df = ak.stock_lhb_detail_em(symbol=symbol, start_date=start, end_date=end)
    
    results = []
    for _, row in df.iterrows():
        results.append(FinanceData(
            source='akshare',
            data_type='lhb',
            symbol=symbol,
            timestamp=pd.to_datetime(row['上榜日期']),
            payload={
                'reason': row['上榜原因'],
                'buy_amount': row['买入金额'],
                'sell_amount': row['卖出金额'],
                'net_amount': row['净额'],
                'dept_name': row['营业部'],
                'buy_rank': row['买入排名'],
                'sell_rank': row['卖出排名']
            }
        ))
    return results
```

### 2.7 北向资金
```python
async def fetch_northbound(symbol: str = None, start: str = '20240101', end: str = '20241231') -> list[FinanceData]:
    """北向资金持仓/流向"""
    if symbol:
        # 个股北向持仓
        df = ak.stock_hsgt_hold_em(symbol=symbol.split('.')[0])
    else:
        # 沪深港通总额度/余额/历史流向
        df = ak.stock_hsgt_hist_em(symbol='沪股通')
    
    results = []
    for _, row in df.iterrows():
        results.append(FinanceData(
            source='akshare',
            data_type='northbound',
            symbol=symbol or 'MARKET',
            timestamp=pd.to_datetime(row['日期']),
            payload=row.to_dict()
        ))
    return results
```

---

## 3. Tushare Pro 抓取器 (回测/合规首选)

```python
import tushare as ts

pro = ts.pro_api('YOUR_TOKEN')

class TushareScraper:
    """Tushare Pro 标准化抓取器"""
    
    async def fetch_basic_info(self) -> pd.DataFrame:
        """股票基础信息"""
        return pro.stock_basic(
            exchange='',
            list_status='L',
            fields='ts_code,symbol,name,area,industry,list_date,market,exchange'
        )
    
    async def fetch_daily(self, ts_code: str, start: str, end: str) -> list[FinanceData]:
        """日线行情 (前复权)"""
        df = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
        df = pro.adj_factor(ts_code=ts_code, start_date=start, end_date=end)
        # 合并复权因子计算前复权价格...
        
    async def fetch_financials(self, ts_code: str, period: str) -> list[FinanceData]:
        """财务报表 (需积分)"""
        income = pro.income(ts_code=ts_code, period=period)
        balancesheet = pro.balancesheet(ts_code=ts_code, period=period)
        cashflow = pro.cashflow(ts_code=ts_code, period=period)
        
    async def fetch_moneyflow(self, ts_code: str, start: str, end: str) -> list[FinanceData]:
        """资金流向"""
        df = pro.moneyflow(ts_code=ts_code, start_date=start, end_date=end)
        
    async def fetch_top_inst(self, trade_date: str) -> list[FinanceData]:
        """龙虎榜机构席位"""
        df = pro.top_inst(trade_date=trade_date)
```

**积分管理**：基础 120 分/分钟，高频接口需更高积分。建议本地缓存 + 定时刷新。

---

## 4. 东方财富 Web API 逆向 (高频实时)

### 4.1 实时行情推送
```python
import httpx
import json

class EastmoneyRealtime:
    BASE = 'https://push2.eastmoney.com/api/qt/stock/get'
    
    async def fetch(self, symbols: list[str]) -> list[FinanceData]:
        # secid 格式: 1.600000 (沪市) / 0.000001 (深市)
        secids = ','.join(self._to_secid(s) for s in symbols)
        
        params = {
            'secid': secids,
            'fields': 'f43,f44,f45,f46,f47,f48,f49,f50,f51,f52,f57,f58,f60,f61,f170',
            'ut': 'fa5fd1943c7b386f172d6893dbfba10b',
            'fltt': '2'
        }
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(self.BASE, params=params, timeout=10)
            data = resp.json()
        
        results = []
        for secid, item in data['data']['diff'].items():
            symbol = self._from_secid(secid)
            results.append(FinanceData(
                source='eastmoney',
                data_type='quote',
                symbol=symbol,
                timestamp=datetime.utcnow(),
                payload={
                    'open': item.get('f46'),
                    'high': item.get('f44'),
                    'low': item.get('f45'),
                    'close': item.get('f43'),
                    'volume': item.get('f47'),
                    'amount': item.get('f48'),
                    'change_pct': item.get('f170'),
                    'turnover': item.get('f49'),
                    'pe_ttm': item.get('f51'),
                    'pb': item.get('f52'),
                    'total_mv': item.get('f60'),
                    'circ_mv': item.get('f61')
                }
            ))
        return results
    
    def _to_secid(self, symbol: str) -> str:
        code, market = symbol.split('.')
        prefix = '1' if market == 'SH' else '0'
        return f"{prefix}.{code}"
    
    def _from_secid(self, secid: str) -> str:
        prefix, code = secid.split('.')
        market = 'SH' if prefix == '1' else 'SZ'
        return f"{code}.{market}"
```

### 4.2 历史 K 线
```python
class EastmoneyKline:
    BASE = 'https://push2his.eastmoney.com/api/qt/stock/kline/get'
    
    async def fetch(self, symbol: str, klt: str = '101', fqt: str = '1',
                    beg: str = '20240101', end: str = '20241231') -> list[FinanceData]:
        """
        klt: 101日线 102周线 103月线 1分钟 5 5分钟 15 15分钟 30 30分钟 60 60分钟
        fqt: 0不复权 1前复权 2后复权
        """
        secid = self._to_secid(symbol)
        params = {
            'secid': secid,
            'klt': klt,
            'fqt': fqt,
            'beg': beg,
            'end': end,
            'fields1': 'f1,f2,f3,f4,f5,f6',
            'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58',
            'ut': 'fa5fd1943c7b386f172d6893dbfba10b'
        }
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(self.BASE, params=params)
            data = resp.json()
        
        results = []
        for kl in data['data']['klines']:
            parts = kl.split(',')
            results.append(FinanceData(
                source='eastmoney',
                data_type='kline',
                symbol=symbol,
                timestamp=pd.to_datetime(parts[0]),
                payload={
                    'open': float(parts[1]),
                    'close': float(parts[2]),
                    'high': float(parts[3]),
                    'low': float(parts[4]),
                    'volume': int(parts[5]),
                    'amount': float(parts[6]),
                    'amplitude': float(parts[7])
                }
            ))
        return results
```

---

## 5. 新浪财经 K 线 API (推荐历史数据源)

### 5.1 接口概览

| 项目 | 详情 |
|------|------|
| **API 地址** | `https://quotes.sina.cn/cn/api/jsonp_v2.php/var=/CN_MarketDataService.getKLineData` |
| **协议** | HTTP GET + JSONP |
| **认证** | 无需 Token，仅需 `User-Agent` + `Referer` |
| **频率限制** | ~60 req/min (建议间隔 1s) |
| **数据范围** | 日线最大约 1970 条 (~8 年)，分钟线按时间范围返回 |
| **复权** | 不复权 (需本地计算) |

### 5.2 请求参数

```python
params = {
    'symbol': 'sh603000',      # 沪市加 sh 前缀，深市加 sz 前缀
    'scale': '240',            # 240=日线, 60=60分钟, 30=30分钟, 15=15分钟, 5=5分钟, 1=1分钟
    'ma': 'no',                # no=不返回均线, yes=返回均线
    'datalen': 1023            # 数据条数，日线最大约 1970
}
```

### 5.3 返回格式 (JSONP)

```
var=([
  {"day":"2026-07-14","open":"15.450","high":"15.630","low":"15.020","close":"15.290","volume":"15243416"},
  {"day":"2026-07-11","open":"15.200","high":"15.450","low":"15.100","close":"15.350","volume":"12345678"},
  ...
]);
```

字段说明：`day`(日期/时间), `open`(开盘), `high`(最高), `low`(最低), `close`(收盘), `volume`(成交量)

### 5.4 Python 抓取示例

```python
import urllib.request
import json

SINA_KLINE_URL = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var=/CN_MarketDataService.getKLineData"

def to_sina_symbol(code: str) -> str:
    """603000 -> sh603000, 000001 -> sz000001"""
    if code.startswith(('60', '68', '90')):
        return f'sh{code}'
    return f'sz{code}'

def fetch_kline(code: str, scale: str = '240', datalen: int = 1023) -> list:
    symbol = to_sina_symbol(code)
    url = f"{SINA_KLINE_URL}?symbol={symbol}&scale={scale}&ma=no&datalen={datalen}"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://finance.sina.com.cn/',
    }
    
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        text = resp.read().decode('utf-8', errors='ignore')
    
    # 解析 JSONP: var=(...);
    idx = text.find('var=(')
    end = text.rfind(');')
    json_str = text[idx + 5:end]
    return json.loads(json_str)

# 使用示例
# 日线 1023 条 (~4 年)
daily = fetch_kline('603000', scale='240', datalen=1023)
# 日线 1970 条 (~8 年，最大)
daily_max = fetch_kline('603000', scale='240', datalen=1970)
# 60分钟线 100 条
min60 = fetch_kline('603000', scale='60', datalen=100)
```

### 5.5 优势与局限

| 优势 | 局限 |
|------|------|
| 无需 Token/注册 | 仅提供不复权价格 |
| 支持多周期 (1m~日线) | 最大 1970 条/请求 |
| JSONP 格式易解析 | 无财务/资金流数据 |
| 稳定性高，反爬较弱 | 需自行计算复权因子 |
| 适合技术指标计算 | 无基本面数据 |

> **推荐用途**：技术分析、指标计算、回测数据源、分钟级高频数据

---

## 6. 统一抓取器基类与工厂

```python
from abc import ABC, abstractmethod
from typing import AsyncIterator, List, Optional
from datetime import datetime

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


class ScraperFactory:
    """抓取器工厂"""
    _registry = {}
    
    @classmethod
    def register(cls, name: str, scraper_class):
        cls._registry[name] = scraper_class
    
    @classmethod
    def create(cls, name: str, **kwargs) -> BaseScraper:
        if name not in cls._registry:
            raise ValueError(f"Unknown scraper: {name}")
        return cls._registry[name](**kwargs)

# 注册默认抓取器
ScraperFactory.register('akshare', AKShareScraper)
ScraperFactory.register('tushare', TushareScraper)
ScraperFactory.register('eastmoney', EastmoneyScraper)

# 使用示例
async def get_stock_full_data(symbol: str):
    async with ScraperFactory.create('akshare') as scraper:
        async for data in scraper.fetch([symbol], 'quote'):
            print(f"实时行情: {data.payload}")
        async for data in scraper.fetch([symbol], 'kline', period='daily'):
            print(f"日K线: {len(data.payload)} 条")
        async for data in scraper.fetch([symbol], 'financial'):
            print(f"财务报表: {data.payload.keys()}")
```

---

## 7. 性能基准与选型建议

| 场景 | 推荐源 | 并发限制 | 更新频率 | 优势 |
|------|--------|----------|----------|------|
| 回测/合规研报 | Tushare Pro | 积分制 | 日级 | 数据权威、字段全、历史长 |
| 实时监控/选股 | AKShare (东财) | ~60 req/min | 秒/分钟级 | 免费、无需 token、字段丰富 |
| **历史K线/技术分析** | **新浪财经** | **~60 req/min** | **日/分钟级** | **免费、多周期、最长8年、反爬弱** |
| 高频实时推送 | 东财 WebSocket | 需逆向 | 毫秒级 | 延迟最低、支持推送 |
| 财务深度分析 | AKShare + Tushare 互补 | - | 季度 | 多源交叉验证 |
| 龙虎榜/北向/资金流 | AKShare (东财) | ~30 req/min | 日级 | 独家数据、解析方便 |

**并发配置建议**：
```python
CONCURRENCY_LIMIT = 5          # 同源并发
REQUEST_TIMEOUT = 30           # 单请求超时
RETRY_TIMES = 3                # 重试次数
RETRY_BACKOFF = [1, 2, 5]      # 指数退避
RATE_LIMIT_PER_MIN = 60        # 每分钟上限
PROXY_ROTATION = True          # 代理轮换
```

---

## 8. 增量更新策略

| 数据类型 | 去重键 | 变更检测 | 更新频率 |
|----------|--------|----------|----------|
| 实时行情 | (symbol, timestamp) | 全量覆盖 | 秒级/分钟级 |
| 历史K线 | (symbol, date, period) | 校验最后一根收盘价 | 日级 |
| 财务报表 | (symbol, report_date, report_type) | 版本号/发布时间 | 季度 |
| 分红配股 | (symbol, record_date, plan) | 方案变更检测 | 事件驱动 |
| 龙虎榜 | (symbol, trade_date, dept) | 净买入额变化 | 日级 |
| 北向资金 | (symbol, date) | 持仓量/市值变化 | 日级 |

```python
async def incremental_update(scraper: BaseScraper, symbol: str, data_type: str,
                             last_timestamp: datetime) -> list[FinanceData]:
    """增量更新：仅获取 last_timestamp 之后的数据"""
    new_data = []
    async for data in scraper.fetch([symbol], data_type, start=last_timestamp):
        if data.timestamp > last_timestamp:
            new_data.append(data)
        else:
            break  # 假设按时间倒序返回
    return new_data
```

---

## 9. 常见问题速查

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| AKShare 报 `KeyError` | 接口字段变更 | 升级 AKShare 版本、打印原始字段对比 |
| 东财返回 403/空数据 | IP 限流/签名失效 | 换代理、更新签名算法、降低频率 |
| Tushare 积分不足 | 高频调用消耗积分 | 本地缓存、批量请求、升级积分 |
| 财务报表日期对不齐 | 不同源报告期定义不同 | 统一按「报告期末日」对齐 |
| 复权价格不一致 | 复权基准日不同 | 统一使用「前复权」、固定基准日 |
| 停牌数据缺失 | 交易所不返回停牌数据 | 补全前收盘价、成交量置 0 |

---

> **完整 API 手册**请查阅 `references/full-api-docs/` 目录下的各数据源详细文档
>
> **新浪财经 K 线实现参考**：`.claude/skills/finance-data-toolkit/finance_toolkit/data_fetching/sina_kline_fetcher.py` (含技术指标计算 MA/EMA/MACD/RSI/BOLL/KDJ)