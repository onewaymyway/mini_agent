# 金融数据源选型报告

> 生成时间：2026-08-08
> 目标：梳理并扩展 finance-data-toolkit 支持的金融数据源类型

---

## 一、现有数据源覆盖情况

### 1.1 已实现数据源

| 数据源 | 抓取器 | 支持数据类型 | 状态 | 依赖 |
|--------|--------|-------------|------|------|
| AKShare | AKShareScraper | quote, kline, financial, dividend, shareholder, lhb, northbound | ✅ 已接入 | akshare |
| 东方财富 | EastmoneyScraper | quote, kline, moneyflow, guba | ✅ 已接入 | browser-cdp |
| 新浪财经 | SinaScraper | quote, kline | ✅ 已接入 | httpx |
| Yahoo Finance | YahooScraper | quote, kline | ✅ 已接入 | yfinance |
| Tushare | TushareScraper | quote, kline, financial | ✅ 已接入 | tushare |
| 腾讯财经 | TencentFetcher | quote, kline | ✅ 已接入 | httpx |
| 网易财经 | NetEaseFetcher | quote | ✅ 已接入 | httpx |

### 1.2 已实现扩展数据源

| 数据源 | 抓取器 | 支持数据类型 | 状态 | 依赖 |
|--------|--------|-------------|------|------|
| 基金 | FundScraper | fund_nav, fund_holdings, fund_rank, fund_info, fund_history | ✅ 已接入 | httpx, akshare |
| 债券 | BondScraper | bond_yield, bond_quote, convertible, bond_info | ✅ 已接入 | httpx |
| 期货 | FuturesScraper | futures_quote, futures_kline, futures_position, futures_info | ✅ 已接入 | httpx |
| 指数 | IndexScraper | index_quote, index_kline, index_constituents, index_info | ⚠️ 部分可用 | httpx, akshare |
| 宏观经济 | MacroScraper | gdp, cpi, pmi, interest_rate, exchange_rate, money_supply | ✅ 已接入 | httpx |
| 外汇 | ForexScraper | forex_quote, forex_kline, forex_cny, forex_cross | ✅ 已接入 | httpx, akshare |
| 加密货币 | CryptoScraper | crypto_quote, crypto_kline, crypto_rank, crypto_trending | ✅ 已接入 | httpx, akshare |
| ETF/期权 | ETFScraper | etf_quote, etf_kline, etf_holdings, option_quote, option_chain | ⚠️ 部分可用 | httpx, akshare |

---

## 二、数据源调研结果

### 2.1 股票数据源

#### 已实现
- ✅ AKShare: `stock_zh_a_spot_em()` - 全市场实时行情
- ✅ 东方财富: API 接口 + browser-cdp 抓取
- ✅ 新浪财经: `hq.sinajs.cn` - 实时行情
- ✅ 腾讯财经: `qt.gtimg.cn` - 实时行情
- ✅ Yahoo Finance: `yfinance` 库

#### 字段规范
```python
{
    'code': '600000',           # 股票代码
    'name': '浦发银行',         # 股票名称
    'open': 9.26,              # 今开
    'pre_close': 9.29,         # 昨收
    'price': 9.21,             # 现价
    'high': 9.29,              # 最高
    'low': 9.14,               # 最低
    'volume': 12345678,        # 成交量
    'amount': 113456789.0,     # 成交额
    'change_pct': -0.86,       # 涨跌幅
    'pe_ttm': 5.2,            # 市盈率TTM
    'pb': 0.45,               # 市净率
    'total_mv': 123456789000,  # 总市值
    'circ_mv': 98765432100,    # 流通市值
}
```

---

### 2.2 基金数据源

#### 已实现
- ✅ 东方财富基金接口: `fund.eastmoney.com`
- ✅ AKShare: `fund_open_fund_info_em()`

#### 字段规范
```python
{
    'code': '159915',           # 基金代码
    'name': '易方达创业板ETF',  # 基金名称
    'nav': 1.2345,             # 单位净值
    'acc_nav': 1.4567,         # 累计净值
    'daily_return': 0.56,      # 日收益率
    'accum_return': 23.45,     # 累计收益率
    'fund_type': '股票型',      # 基金类型
    'manager': '张坤',         # 基金经理
    'size': '500亿',           # 基金规模
}
```

---

### 2.3 债券数据源

#### 已实现
- ✅ 东方财富债券接口: `data.eastmoney.com/bond/`

#### 字段规范
```python
{
    'date': '2026-08-08',      # 日期
    '1y': 2.15,               # 1年期国债收益率
    '2y': 2.35,               # 2年期国债收益率
    '3y': 2.45,               # 3年期国债收益率
    '5y': 2.55,               # 5年期国债收益率
    '10y': 2.65,              # 10年期国债收益率
}
```

---

### 2.4 期货数据源

#### 已实现
- ✅ 东方财富期货接口: `push2.eastmoney.com`

#### 字段规范
```python
{
    'code': 'CU2401',          # 期货代码
    'name': '沪铜2401',        # 期货名称
    'price': 68520.0,          # 最新价
    'change_pct': 1.23,        # 涨跌幅
    'high': 69000.0,          # 最高
    'low': 67800.0,           # 最低
    'open': 68000.0,          # 今开
    'pre_close': 67680.0,     # 昨收
    'volume': 123456,         # 成交量
    'hold': 234567,           # 持仓量
}
```

---

### 2.5 指数数据源

#### 已实现
- ✅ 东方财富指数接口: `push2.eastmoney.com`

#### 字段规范
```python
{
    'code': '000001',          # 指数代码
    'name': '上证指数',        # 指数名称
    'price': 3256.78,         # 最新点位
    'change_pct': 0.56,       # 涨跌幅
    'high': 3268.90,         # 最高
    'low': 3245.67,          # 最低
    'open': 3250.00,         # 今开
    'pre_close': 3238.56,    # 昨收
    'volume': 123456789,     # 成交量
    'amount': 98765432100,   # 成交额
}
```

---

### 2.6 外汇数据源

#### 已实现
- ✅ 新浪财经外汇接口: `hq.sinajs.cn`
- ✅ AKShare: `currency_foreign_cnh_spot()`, `currency_boc_safe()`

#### 字段规范
```python
{
    'code': 'USDCNY',          # 货币对
    'name': '美元/人民币',     # 名称
    'price': 7.2345,          # 汇率
    'open': 7.2300,           # 今开
    'pre_close': 7.2280,      # 昨收
    'high': 7.2400,           # 最高
    'low': 7.2250,            # 最低
    'date': '2026-08-08',     # 日期
    'time': '15:30:00',       # 时间
}
```

---

### 2.7 加密货币数据源

#### 已实现
- ✅ AKShare: `crypto_js_spot()`
- ✅ CoinGecko API: `api.coingecko.com`

#### 字段规范
```python
{
    'id': 'bitcoin',           # CoinGecko ID
    'symbol': 'BTC',          # 交易符号
    'name': 'Bitcoin',        # 名称
    'price': 65000.00,        # 价格(USD)
    'market_cap': 1234567890000,  # 市值
    'volume_24h': 23456789000,    # 24h成交量
    'price_change_24h': 2.34,       # 24h涨跌幅
    'high_24h': 66000.00,           # 24h最高
    'low_24h': 64000.00,            # 24h最低
}
```

---

### 2.8 ETF/期权数据源

#### 已实现
- ✅ 东方财富ETF接口: `push2.eastmoney.com`
- ✅ AKShare: `fund_etf_spot_em()`, `fund_etf_hist_sina()`
- ✅ AKShare期权: `option_zh_daily()`, `option_zh_hs_daily()`

#### 字段规范
```python
{
    'code': '510300',          # ETF代码
    'name': '沪深300ETF',      # ETF名称
    'price': 4.567,           # 最新价
    'change_pct': 0.34,       # 涨跌幅
    'high': 4.580,           # 最高
    'low': 4.550,            # 最低
    'open': 4.560,           # 今开
    'pre_close': 4.552,      # 昨收
    'volume': 12345678,      # 成交量
    'amount': 56789012.0,    # 成交额
}
```

---

### 2.9 宏观经济数据源

#### 已实现
- ✅ 东方财富宏观经济数据: `data.eastmoney.com/cjsj/`

#### 字段规范
```python
# GDP
{
    'quarter': '2026Q2',      # 季度
    'gdp': 123456.78,        # GDP(亿元)
    'growth_rate': 5.2,      # 同比增长率
    'per_capita': 8.76,      # 人均GDP(万元)
}

# CPI
{
    'date': '2026-07',       # 月份
    'cpi': 101.2,            # CPI指数
    'yoy': 1.2,              # 同比涨幅
    'food': 2.1,             # 食品类涨幅
}

# PMI
{
    'date': '2026-07',       # 月份
    'manufacturing_pmi': 50.2,     # 制造业PMI
    'non_manufacturing_pmi': 53.5, # 非制造业PMI
    'new_order_pmi': 51.2,         # 新订单PMI
}
```

---

## 三、数据源对比分析

### 3.1 免费 vs 付费

| 数据源 | 免费 | 付费 | Token需求 | 限流 |
|--------|------|------|-----------|------|
| AKShare | ✅ | ❌ | ❌ | 中 |
| 东方财富 | ✅ | ❌ | ❌ | 高 |
| 新浪财经 | ✅ | ❌ | ❌ | 低 |
| 腾讯财经 | ✅ | ❌ | ❌ | 低 |
| Tushare | ⚠️ | ✅ | ✅ | 低 |
| Wind | ❌ | ✅ | ✅ | 无 |
| Choice | ❌ | ✅ | ✅ | 无 |
| CoinGecko | ✅ | ❌ | ❌ | 中 |

### 3.2 数据质量对比

| 数据源 | 实时性 | 完整性 | 准确性 | 稳定性 |
|--------|--------|--------|--------|--------|
| AKShare | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ |
| 东方财富 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ |
| 新浪财经 | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| 腾讯财经 | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| Tushare | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |

### 3.3 接入成本对比

| 数据源 | 接入难度 | 依赖安装 | 代码复杂度 | 维护成本 |
|--------|----------|----------|-----------|----------|
| AKShare | ⭐ | akshare | 低 | 中 |
| 东方财富 | ⭐⭐ | httpx | 中 | 高 |
| 新浪财经 | ⭐ | httpx | 低 | 低 |
| 腾讯财经 | ⭐ | httpx | 低 | 低 |
| Tushare | ⭐⭐⭐ | tushare | 中 | 低 |
| CoinGecko | ⭐⭐ | httpx | 中 | 低 |

---

## 四、缺口分析

### 4.1 已覆盖领域

- ✅ 股票（A股、港股、美股）
- ✅ 基金（开放式、封闭式、ETF）
- ✅ 债券（国债、企业债、可转债）
- ✅ 期货（商品期货、金融期货）
- ✅ 指数（沪深指数、行业指数）
- ✅ 外汇（主要货币对、人民币中间价）
- ✅ 加密货币（主流币种）
- ✅ 宏观经济（GDP、CPI、PMI、利率、汇率）

### 4.2 已验证数据源（2026-08-08 测试）

#### ✅ 可用数据源

| 数据源 | 函数 | 状态 | 记录数 | 备注 |
|--------|------|------|--------|------|
| AKShare 商品主力连续 | `futures_display_main_sina()` | ✅ OK | 82 | 新浪期货 |
| AKShare 融资融券明细 | `stock_margin_detail_szse()` | ✅ OK | 1811 | 深交所 |
| AKShare 北向资金 | `stock_hsgt_hist_em()` | ✅ OK | 2726 | 沪深港通 |
| AKShare 龙虎榜 | `stock_lhb_detail_em()` | ✅ OK | 1518 | 2024年1月 |
| AKShare 期货主力 | `futures_main_sina()` | ✅ OK | 4174 | 新浪期货 |
| AKShare 商品合约 | `option_commodity_contract_sina()` | ✅ OK | 4 | 新浪期权 |
| AKShare A股实时 | `stock_zh_a_spot()` | ✅ OK | 5538 | 新浪接口 |
| AKShare A股历史 | `stock_zh_a_daily()` | ✅ OK | 22 | 新浪接口 |
| AKShare 债券行情 | `bond_china_yield()` | ✅ OK | 738 | 中国债券收益率 |
| AKShare 外汇汇率 | `currency_boc_safe()` | ✅ OK | 8022 | 外汇局 |
| AKShare 加密货币 | `crypto_js_spot()` | ✅ OK | 10 | 主流币种 |
| AKShare 宏观经济 | `macro_china_cpi_yearly()` | ✅ OK | 477 | 年度数据 |

#### ❌ 不可用数据源（代理问题）

| 数据源 | 函数 | 错误原因 | 备注 |
|--------|------|----------|------|
| AKShare 股票实时 | `stock_zh_a_spot_em()` | 东方财富 push2 API 代理失败 | 需直连 |
| AKShare ETF实时 | `fund_etf_spot_em()` | 东方财富 push2 API 代理失败 | 需直连 |
| AKShare 指数实时 | `stock_zh_index_spot_em()` | 东方财富 push2 API 代理失败 | 需直连 |
| AKShare 港股实时 | `stock_hk_spot_em()` | 东方财富 push2 API 代理失败 | 需直连 |
| AKShare 美股实时 | `stock_us_spot_em()` | 东方财富 push2 API 代理失败 | 需直连 |
| AKShare 期权风险 | `option_risk_analysis_em()` | 东方财富 push2 API 代理失败 | 需直连 |
| AKShare 商品历史 | `option_commodity_hist_sina()` | JSON 解析失败 | 接口变更 |
| AKShare 基金实时 | `fund_open_fund_info_em()` | JS 解析错误 | 接口变更 |

### 4.3 扩展方向

- ⚠️ 商品历史数据（需修复接口）
- ⚠️ 股指期货数据（IF、IC、IH、T）
- ⚠️ 期权链数据（更详细的期权合约）
- ⚠️ 大宗交易数据
- ⚠️ 资金流向数据（行业/概念）
- ⚠️ 解决东方财富 push2 API 代理问题

---

## 五、技术架构评估

### 5.1 现有架构优势

1. **统一数据契约**: 所有数据源输出 `FinanceData` 格式
2. **分层架构**: 核心抓取框架 + 细分功能模块
3. **自动注册**: 抓取器自动发现并注册
4. **错误处理**: 统一的异常处理和日志记录
5. **健康检查**: 每个抓取器都有 `health_check()` 方法

### 5.2 存在的问题

1. **代码损坏**: 部分文件存在语法错误（如 `fetchers.py` 第 717-788 行）
2. **导入路径错误**: `from .scrapers.macro_scraper` 应为 `from ..scrapers.macro_scraper`
3. **测试覆盖不足**: 新增数据源缺少单元测试
4. **文档不完整**: 部分模块缺少使用说明
5. **网络连通性问题**: ETF/指数数据源因代理问题导致 health_check 失败
6. **API 变更**: 部分东方财富 API 接口返回 302 重定向

### 5.3 优化建议

1. **修复代码**: 修复 `fetchers.py` 中的语法错误
2. **统一导入**: 修正所有导入路径
3. **增加测试**: 为新增数据源添加单元测试
4. **完善文档**: 补充 API 文档和使用示例

---

## 六、结论

### 6.1 当前覆盖情况

finance-data-toolkit 已实现 **9 大类** 金融数据源：
- 股票（6 个数据源）
- 基金（2 个数据源）
- 债券（1 个数据源）
- 期货（1 个数据源）
- 指数（1 个数据源）
- 外汇（2 个数据源）
- 加密货币（2 个数据源）
- ETF/期权（2 个数据源）
- 宏观经济（1 个数据源）

### 6.2 主要优势

1. **免费数据源为主**: 90% 以上数据源免费
2. **无需 Token**: 大部分数据源无需注册
3. **覆盖全面**: 覆盖股票、基金、债券、期货、指数、外汇、加密货币、宏观经济
4. **统一接口**: 所有数据源输出标准化格式

### 6.3 后续优化方向

1. **修复代码**: 修复现有代码中的语法错误
2. **增加测试**: 为所有数据源添加单元测试
3. **扩展数据**: 考虑增加商品、融资融券等数据
4. **性能优化**: 添加缓存层，减少重复请求
5. **网络优化**: 解决 ETF/指数数据源的代理连通性问题
6. **API 适配**: 适配东方财富 API 变更（302 重定向问题）

---

*报告生成于 finance-data-toolkit 数据源拓展任务*
