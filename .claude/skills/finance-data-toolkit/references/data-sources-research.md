# 金融数据源调研与接入方案

> 生成时间：2026-08-07
> 目标：拓展 finance-data-toolkit 的金融数据源覆盖范围

---

## 一、现有数据源覆盖情况

### 已实现数据源

| 数据源 | 抓取器 | 支持数据类型 | 状态 |
|--------|--------|-------------|------|
| AKShare | AKShareScraper | quote, kline, financial, dividend, shareholder, lhb, northbound | ✅ 已接入 |
| 东方财富 | EastmoneyScraper | quote, kline, moneyflow, guba | ✅ 已接入 |
| 新浪财经 | SinaScraper | quote, kline | ✅ 已接入 |
| Yahoo Finance | YahooScraper | quote, kline | ✅ 已接入 |
| Tushare | TushareScraper | quote, kline, financial | ✅ 已接入 |

### 现有缺口

- ❌ 基金数据（净值、持仓、排行榜）
- ❌ 债券数据（国债收益率、企业债、可转债）
- ❌ 期货数据（商品期货、金融期货）
- ❌ 指数数据（沪深指数、行业指数）
- ❌ 宏观经济数据（GDP、CPI、PMI、利率、汇率）

---

## 二、新增数据源调研

### 2.1 基金数据源

#### 东方财富基金接口
- **接口地址**: `https://fund.eastmoney.com/f10/F10Data.aspx`
- **支持数据**:
  - 基金净值（历史净值、累计净值）
  - 基金持仓（股票持仓、债券持仓）
  - 基金排行榜
  - 基金基本信息
- **特点**: 免费、无需 token、数据完整
- **接入优先级**: ⭐⭐⭐⭐⭐ (最高)

#### AKShare 基金接口
- **接口**: `ak.fund_open_fund_info_em()`
- **支持数据**: 开放式基金净值、封闭式基金净值
- **特点**: 免费、无需 token
- **接入优先级**: ⭐⭐⭐⭐

### 2.2 债券数据源

#### 东方财富债券接口
- **接口地址**: `https://data.eastmoney.com/bond/`
- **支持数据**:
  - 国债收益率曲线
  - 企业债行情
  - 可转债行情
  - 债券基本信息
- **特点**: 免费、无需 token
- **接入优先级**: ⭐⭐⭐⭐⭐

#### 中国债券信息网
- **接口**: 网页抓取
- **支持数据**: 国债收益率、政策性金融债收益率
- **特点**: 官方数据源、权威性强
- **接入优先级**: ⭐⭐⭐

### 2.3 期货数据源

#### 东方财富期货接口
- **接口地址**: `https://quote.eastmoney.com/center/gridlist.html#futures_sse`
- **支持数据**:
  - 商品期货实时行情
  - 金融期货行情（IF、IC、IH、T）
  - 期货持仓数据
  - 期货K线数据
- **特点**: 免费、无需 token
- **接入优先级**: ⭐⭐⭐⭐⭐

#### 同花顺期货接口
- **接口**: 网页抓取
- **支持数据**: 期货行情、持仓、交割
- **特点**: 数据全面
- **接入优先级**: ⭐⭐⭐

### 2.4 指数数据源

#### 东方财富指数接口
- **接口地址**: `https://quote.eastmoney.com/center/gridlist.html#hs_indices`
- **支持数据**:
  - 沪深主要指数（上证综指、深证成指、创业板指）
  - 行业指数
  - 风格指数
  - 指数成分股
- **特点**: 免费、无需 token
- **接入优先级**: ⭐⭐⭐⭐⭐

#### 同花顺指数接口
- **接口**: 网页抓取
- **支持数据**: 指数行情、指数成分
- **特点**: 数据全面
- **接入优先级**: ⭐⭐⭐

### 2.5 宏观经济数据源

#### 东方财富宏观经济数据
- **接口地址**: `https://data.eastmoney.com/cjsj/`
- **支持数据**:
  - GDP（季度/年度）
  - CPI、PPI
  - PMI（制造业/非制造业）
  - 利率（存贷款基准利率、MLF利率）
  - 汇率（美元/欧元/日元兑人民币）
  - 货币供应量（M0、M1、M2）
- **特点**: 免费、无需 token、数据权威
- **接入优先级**: ⭐⭐⭐⭐⭐

#### 国家统计局
- **接口**: 网页抓取
- **支持数据**: 官方宏观经济指标
- **特点**: 最权威、但更新较慢
- **接入优先级**: ⭐⭐⭐

---

## 三、技术方案

### 3.1 统一接口设计

所有新抓取器均继承 `BaseScraper`，实现统一接口：

```python
@register_scraper
class FundScraper(BaseScraper):
    @property
    def source_name(self) -> str:
        return 'fund'
    
    @property
    def supported_types(self) -> List[str]:
        return ['fund_nav', 'fund_holdings', 'fund_rank', 'fund_info', 'fund_history']
    
    async def fetch(self, symbols, data_type, start=None, end=None, **kwargs):
        # 实现具体数据获取逻辑
        pass
```

### 3.2 数据标准化

所有数据源返回统一格式 `FinanceData`：

```python
@dataclass
class FinanceData:
    source: str           # 数据源标识
    data_type: str        # 数据类型
    symbol: str           # 标的代码
    timestamp: str        # 时间戳
    payload: Dict         # 数据内容
    raw: Optional[Dict]   # 原始数据
    meta: Optional[Dict]  # 元数据
```

### 3.3 错误处理

- 网络异常：自动重试（指数退避）
- 数据缺失：返回空结果而非抛出异常
- 接口变更：健康检查机制

---

## 四、接入优先级

| 优先级 | 数据类型 | 数据源 | 预计工作量 |
|--------|---------|--------|-----------|
| P0 | 基金净值 | 东方财富 | 1天 |
| P0 | 债券收益率 | 东方财富 | 1天 |
| P0 | 期货行情 | 东方财富 | 1天 |
| P1 | 指数行情 | 东方财富 | 1天 |
| P1 | 宏观经济 | 东方财富 | 1天 |
| P2 | 基金持仓 | 东方财富 | 0.5天 |
| P2 | 可转债 | 东方财富 | 0.5天 |
| P3 | 行业指数 | 东方财富 | 0.5天 |

---

## 五、已实现功能

### 5.1 新增抓取器

1. **FundScraper** (`fund_scraper.py`)
   - 基金净值查询
   - 基金持仓查询
   - 基金排行榜
   - 基金基本信息
   - 基金历史净值

2. **BondScraper** (`bond_scraper.py`)
   - 国债收益率曲线
   - 债券行情查询
   - 可转债数据
   - 债券基本信息

3. **FuturesScraper** (`futures_scraper.py`)
   - 期货实时行情
   - 期货K线数据
   - 期货持仓数据
   - 期货合约信息

4. **IndexScraper** (`index_scraper.py`)
   - 指数实时行情
   - 指数K线数据
   - 指数成分股
   - 指数基本信息

5. **MacroScraper** (`macro_scraper.py`)
   - GDP数据
   - CPI/PPI数据
   - PMI数据
   - 利率数据
   - 汇率数据
   - 货币供应量

### 5.2 更新文件

- `scrapers/__init__.py`: 注册新抓取器
- `data_fetching/fetchers.py`: 添加新数据类型支持函数

---

## 六、后续优化建议

1. **异步并发**: 使用 `asyncio.gather` 并发获取多只标的数据
2. **数据缓存**: 添加 Redis 缓存层，减少重复请求
3. **增量更新**: 实现数据增量更新机制
4. **数据质量**: 添加数据校验和异常检测
5. **监控告警**: 添加数据源健康监控

---

## 七、依赖说明

新增抓取器依赖：
- `httpx`: HTTP 客户端（已存在）
- 无需额外安装第三方库

---

*文档生成于 finance-data-toolkit 数据源拓展任务*
