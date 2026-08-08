# 金融数据源选型报告

> 生成时间：2026-08-08
> 调研范围：股票、债券、基金、外汇、加密货币、期货、指数、宏观经济、ETF

---

## 一、数据源总览

### 1.1 已实现的 13 个抓取器

| 抓取器 | 文件 | 支持数据类型 | 装饰器状态 |
|--------|------|-------------|----------|
| AKShareScraper | akshare_scraper.py | quote, kline, financial, dividend, shareholder, lhb, northbound | ✅ 已注册 |
| TushareScraper | tushare_scraper.py | quote, kline, financial | ✅ 已注册 |
| EastmoneyScraper | eastmoney_scraper.py | quote, kline, moneyflow, guba | ✅ 已注册 |
| SinaScraper | sina_scraper.py | quote, kline | ✅ 已注册 |
| YahooScraper | yahoo_scraper.py | quote, kline | ✅ 已注册 |
| FundScraper | fund_scraper.py | fund_nav, fund_holdings, fund_rank, fund_info, fund_history | ✅ 已注册 |
| BondScraper | bond_scraper.py | bond_yield, bond_quote, convertible, bond_info | ✅ 已注册 |
| FuturesScraper | futures_scraper.py | futures_quote, futures_kline, futures_position, futures_info | ✅ 已注册 |
| IndexScraper | index_scraper.py | index_quote, index_kline, index_constituents, index_info | ✅ 已注册 |
| MacroScraper | macro_scraper.py | gdp, cpi, pmi, interest_rate, exchange_rate, money_supply | ✅ 已注册 |
| ForexScraper | forex_scraper.py | forex_quote, forex_kline, forex_cny, forex_cross | ❌ 缺少装饰器 |
| CryptoScraper | crypto_scraper.py | crypto_quote, crypto_kline, crypto_rank, crypto_trending | ❌ 缺少装饰器 |
| ETFScraper | etf_scraper.py | etf_quote, etf_kline, etf_holdings, option_quote, option_chain | ❌ 缺少装饰器 |

---

## 二、数据源对比分析

### 2.1 免费数据源对比

| 数据源 | 股票 | 基金 | 债券 | 期货 | 指数 | 宏观 | 外汇 | 加密货币 | ETF | 认证 | 成本 |
|--------|------|------|------|------|------|------|------|----------|-----|------|------|
| **AKShare** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 无 | 免费 |
| **东方财富** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ | 无 | 免费 |
| **新浪财经** | ✅ | ❌ | ❌ | ✅ | ✅ | ❌ | ✅ | ❌ | ❌ | 无 | 免费 |
| **CoinGecko** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ | 无 | 免费 |
| **腾讯财经** | ✅ | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | 无 | 免费 |
| **网易财经** | ✅ | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | 无 | 免费 |

### 2.2 付费数据源对比

| 数据源 | 股票 | 基金 | 债券 | 期货 | 指数 | 宏观 | 外汇 | 加密货币 | ETF | 认证 | 成本 |
|--------|------|------|------|------|------|------|------|----------|-----|------|------|
| **Tushare** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | Token | 免费/付费 |
| **Wind** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 账号 | 付费 |
| **Choice** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 账号 | 付费 |

---

## 三、推荐数据源策略

### 3.1 优先使用免费数据源

**核心推荐**：AKShare（覆盖最全、免登录、免费）

- 股票：实时行情、历史K线、财务数据、分红送股、股东持股、龙虎榜、北向资金
- 基金：开放式基金净值、持仓、排名、历史数据
- 债券：国债收益率、企业债、可转债、债券行情
- 期货：日频行情、持仓数据
- 指数：主要指数行情、成分股
- 宏观：GDP、CPI、PMI、利率、汇率、货币供应量
- 外汇：人民币汇率、主要货币对
- 加密货币：主流币种行情、历史数据
- ETF：ETF行情、持仓、期权数据

**辅助推荐**：东方财富（基金/债券/期货数据补充）

- 基金：开放式基金净值、持仓、排名
- 债券：国债收益率、企业债、可转债
- 期货：日频行情、持仓数据
- 指数：主要指数行情、成分股
- 宏观：经济数据
- ETF：ETF行情、持仓

### 3.2 加密货币专项数据源

**推荐**：CoinGecko API

- 免费、免登录
- 支持主流币种实时行情、历史数据
- 支持市值排名、趋势数据
- 限制：免费版有速率限制（约 10-30 次/分钟）

**备选**：CoinLore API

- 免费、免登录
- 支持主流币种实时行情
- 限制：速率限制更严格

### 3.3 外汇专项数据源

**推荐**：AKShare + 新浪财经

- AKShare：人民币汇率中间价、主要货币对
- 新浪财经：实时外汇行情

---

## 四、数据源覆盖情况

### 4.1 已覆盖的数据类型

| 数据类型 | 支持的数据源 | 实现状态 |
|----------|-------------|----------|
| 股票行情 | AKShare, 东方财富，新浪，腾讯，网易，Yahoo | ✅ 已实现 |
| 股票K线 | AKShare, 东方财富，新浪，腾讯，网易，Yahoo | ✅ 已实现 |
| 股票财务 | AKShare, Tushare, 东方财富 | ✅ 已实现 |
| 基金净值 | AKShare, 东方财富 | ✅ 已实现 |
| 基金持仓 | AKShare, 东方财富 | ✅ 已实现 |
| 债券收益率 | AKShare, 东方财富 | ✅ 已实现 |
| 债券行情 | AKShare, 东方财富 | ✅ 已实现 |
| 可转债 | AKShare, 东方财富 | ✅ 已实现 |
| 期货行情 | AKShare, 东方财富，新浪 | ✅ 已实现 |
| 期货K线 | AKShare, 东方财富，新浪 | ✅ 已实现 |
| 指数行情 | AKShare, 东方财富，新浪，腾讯，网易 | ✅ 已实现 |
| 指数成分 | AKShare, 东方财富 | ✅ 已实现 |
| 宏观经济 | AKShare, 东方财富 | ✅ 已实现 |
| 外汇行情 | AKShare, 新浪 | ✅ 已实现 |
| 加密货币 | AKShare, CoinGecko | ✅ 已实现 |
| ETF行情 | AKShare, 东方财富 | ✅ 已实现 |
| ETF持仓 | AKShare, 东方财富 | ✅ 已实现 |
| 期权行情 | AKShare | ✅ 已实现 |

### 4.2 未覆盖或薄弱的数据类型

| 数据类型 | 现状 | 建议 |
|----------|------|------|
| 港股实时行情 | 仅 Yahoo 支持 | 补充东方财富港股接口 |
| 美股实时行情 | 仅 Yahoo 支持 | 补充 Finnhub API |
| 期货持仓明细 | 仅 AKShare 支持 | 补充东方财富期货持仓接口 |
| 期权链数据 | 仅 AKShare 支持 | 补充东方财富期权接口 |
| 加密货币历史K线 | 仅 CoinGecko 支持 | 补充 Binance API |
| 外汇历史K线 | 仅 AKShare 支持 | 补充 OANDA API |

---

## 五、问题与修复建议

### 5.1 已知问题

1. **ForexScraper、CryptoScraper、ETFScraper 缺少 `@register_scraper` 装饰器**
   - 影响：无法通过 `create_scraper()` 工厂函数自动发现
   - 修复：在类定义前添加 `@register_scraper` 装饰器

2. **`fetchers.py` 中 `DataFetcher` 类有语法错误**
   - 位置：第 639-652 行
   - 影响：模块导入失败
   - 修复：修正类定义和方法缩进

3. **`__init__.py` 未导出新增的 fetch 函数**
   - 影响：外部无法调用 `fetch_fund`, `fetch_bond` 等函数
   - 修复：在 `finance_toolkit/__init__.py` 中添加导出

4. **`data_fetching/__init__.py` 未导出新增函数**
   - 影响：外部无法调用新增的 fetch 函数
   - 修复：在 `finance_toolkit/data_fetching/__init__.py` 中添加导出

### 5.2 修复优先级

| 优先级 | 问题 | 影响范围 | 修复难度 |
|--------|------|----------|----------|
| P0 | `fetchers.py` 语法错误 | 模块导入 | 低 |
| P1 | 缺少 `@register_scraper` 装饰器 | 工厂函数发现 | 低 |
| P1 | 未导出新增函数 | 外部调用 | 低 |
| P2 | 港股/美股实时行情 | 数据覆盖 | 中 |
| P2 | 期货持仓明细 | 数据覆盖 | 中 |
| P3 | 加密货币历史K线 | 数据覆盖 | 中 |

---

## 六、后续优化建议

### 6.1 短期优化（1-2周）

1. 修复已知问题（P0-P1 优先级）
2. 补充港股/美股实时行情数据源
3. 补充期货持仓明细数据源
4. 编写单元测试覆盖新增抓取器

### 6.2 中期优化（1个月）

1. 补充加密货币历史K线数据源（Binance API）
2. 补充外汇历史K线数据源（OANDA API）
3. 补充期权链数据源
4. 优化数据缓存策略

### 6.3 长期优化（3个月）

1. 建立数据质量监控机制
2. 实现数据源自动切换（主备切换）
3. 支持增量数据更新
4. 建立数据血缘追踪

---

## 七、附录

### 7.1 数据源 API 端点速查

详见 `api-reference.md`

### 7.2 免费数据源详解

详见 `free-data-sources.md`

### 7.3 调研记录

详见 `data-sources-research.md`
