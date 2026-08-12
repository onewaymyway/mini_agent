# FinanceData Toolkit 鏁版嵁瀛楀吀 v2.0

> **鐗堟湰**: v2.0
> **鍒涘缓鏃ユ湡**: 2026-08-12
> **閫傜敤鑼冨洿**: 鑲＄エ銆佸€哄埜銆佸熀閲戙€佹湡璐с€佸畯瑙傘€佽祫璁叚澶ч噾铻嶉鍩?
> **瀵瑰簲 Schema**: `docs/data-schema.json`

---

## 涓€銆侀《灞傛暟鎹粨鏋?

鎵€鏈夋暟鎹緭鍑洪伒寰粺涓€鐨?`FinanceData` 濂戠害锛?

```python
@dataclass
class FinanceData:
    """缁熶竴閲戣瀺鏁版嵁濂戠害 - 鎵€鏈夋ā鍧楄緭鍑烘爣鍑嗗寲涓烘鏍煎紡"""
    
    # ========== 蹇呭～瀛楁 ==========
    source: str                    # 鏁版嵁婧愭爣璇? akshare/tushare/eastmoney/sina/tencent/netease/binance/coingecko/yahoo/okx
    data_type: str                 # 鏁版嵁绫诲瀷鏋氫妇 (瑙佺涓夎妭)
    symbol: str                    # 鏍囩殑浠ｇ爜 (鏍囧噯鍖栨牸寮? 瑙佺鍥涜妭)
    timestamp: str                 # 鏁版嵁鏃堕棿鎴?(ISO 8601, UTC)
    payload: Dict[str, Any]        # 涓氬姟杞借嵎 (瑙佸悇绫诲瀷瀹氫箟)
    
    # ========== 鍙€夊瓧娈?==========
    raw: Optional[Dict] = None     # 鍘熷鍝嶅簲 (璋冭瘯鐢? 鐢熶骇鍙叧闂?
    meta: Optional[Dict] = None    # 鍏冧俊鎭? 璇锋眰鑰楁椂銆侀噸璇曟鏁般€佷唬鐞咺P銆佽川閲忚瘎鍒嗙瓑
```

### 1.1 meta 瀛楁瑙勮寖

| 瀛楁 | 绫诲瀷 | 蹇呭～ | 璇存槑 | 绀轰緥 |
|------|------|------|------|------|
| `fetch_time_ms` | float | 鍚?| 璇锋眰鑰楁椂(姣) | `150.5` |
| `retry_count` | int | 鍚?| 閲嶈瘯娆℃暟 | `0` |
| `proxy_ip` | str | 鍚?| 浠ｇ悊IP鍦板潃 | `192.168.1.1` |
| `version` | str | 鍚?| 鏁版嵁鐗堟湰鍙?| `v1.0.0` |
| `quality_score` | float | 鍚?| 鏁版嵁璐ㄩ噺璇勫垎 (0-1) | `0.98` |
| `warnings` | List[str] | 鍚?| 鏁版嵁璐ㄩ噺璀﹀憡鍒楄〃 | `["鏁版嵁寤惰繜", "閮ㄥ垎瀛楁缂哄け"]` |
| `data_completeness` | float | 鍚?| 瀛楁瀹屾暣鐜?(0-1) | `0.95` |
| `duplicate_check` | bool | 鍚?| 鏄惁宸插幓閲?| `true` |

---

## 浜屻€佸瓧娈靛懡鍚嶈鍒?

| 瑙勫垯 | 璇存槑 | 姝ｇ‘绀轰緥 | 閿欒绀轰緥 |
|------|------|---------|----------|
| **snake_case** | 鎵€鏈夊瓧娈典娇鐢ㄥ皬鍐欏姞涓嬪垝绾?| `net_profit`, `total_assets` | `NetProfit`, `totalAssets` |
| **鏃犵缉鍐?* | 閬垮厤浣跨敤缂╁啓锛屼娇鐢ㄥ畬鏁村崟璇?| `change_pct` | `chng_pct`, `chg_pct` |
| **鍗曚綅鏄庣‘** | 浠锋牸鐢?`price`锛屾瘮鐜囩敤 `_ratio`锛岀櫨鍒嗘瘮鐢?`_pct` | `pe_ratio`, `change_pct` | `pe`, `change_rate` |
| **璐у竵鍗曚綅** | 閲戦榛樿涓哄厓锛涗嚎鍏冨姞 `_yi` 鍚庣紑 | `amount`(鍏?, `net_inflow_yi`(浜垮厓) | `net_inflow`(姝т箟) |
| **鏃堕棿鏍煎紡** | 鏃ユ湡鐢?`_date`锛屾椂闂寸敤 `_time` | `nav_date`, `publish_time` | `nav`, `time` |
| **浜ゆ槗鎵€鍚庣紑** | 鑲＄エ浠ｇ爜 `.SH`/`.SZ`锛屽€哄埜 `.BOND`锛屽熀閲?`.FUND` | `600000.SH`, `113000.BOND` | `600000`, `SH600000` |
| **涓枃鍚箟** | 瀛楁鍚嶅弽鏄犲叾璇箟鍚箟 | `open_interest`(鎸佷粨閲? | `oi` |
| **鍔ㄨ瘝杩囧幓寮?* | 琛ㄧず鍙樺姩鏃剁敤杩囧幓寮?| `previous_close` | `prev_close` |

---

## 涓夈€佹暟鎹被鍨嬫灇涓?

### 3.1 瀹屾暣鏋氫妇琛?

| data_type | 涓枃鍚嶇О | 鎵€灞為鍩?| 鏍稿績蹇呭～瀛楁 |
|-----------|---------|---------|-------------|
| `quote` | 瀹炴椂琛屾儏 | 鑲＄エ | open, high, low, close, volume |
| `kline` | 鍘嗗彶K绾?| 鑲＄エ | date, open, high, low, close |
| `financial` | 璐㈠姟鎶ヨ〃 | 鑲＄エ | report_date, type |
| `dividend` | 鍒嗙孩鏁版嵁 | 鑲＄エ | announcement_date, record_date, dividend_per_share |
| `lhb` | 榫欒檸姒?| 鑲＄エ | trade_date, reason |
| `northbound` | 鍖楀悜璧勯噾 | 鑲＄エ | date, type |
| `stock_basic` | 鑲＄エ鍩虹淇℃伅 | 鑲＄エ | name, industry, list_date |
| `bond_yield` | 鍥藉€烘敹鐩婄巼 | 鍊哄埜 | date, bond_type, yield_rate |
| `bond_quote` | 鍊哄埜琛屾儏 | 鍊哄埜 | bond_code, bond_name, price, yield_rate |
| `convertible` | 鍙浆鍊?| 鍊哄埜 | convert_code, stock_code, premium_rate |
| `bond_info` | 鍊哄埜鍩虹淇℃伅 | 鍊哄埜 | bond_code, name, bond_type |
| `fund_nav` | 鍩洪噾鍑€鍊?| 鍩洪噾 | nav_date, nav, accumulated_nav |
| `fund_holdings` | 鍩洪噾鎸佷粨 | 鍩洪噾 | report_date, stock_code, stock_name, shares, market_value, weight |
| `fund_rank` | 鍩洪噾鎺掕姒?| 鍩洪噾 | rank, fund_code, fund_name |
| `fund_info` | 鍩洪噾鍩虹淇℃伅 | 鍩洪噾 | fund_code, name, fund_type |
| `futures_quote` | 鏈熻揣琛屾儏 | 鏈熻揣 | contract_code, open, high, low, close, settlement, volume, open_interest |
| `futures_kline` | 鏈熻揣K绾?| 鏈熻揣 | date, open, high, low, close, volume, open_interest |
| `futures_position` | 鏈熻揣鎸佷粨 | 鏈熻揣 | date, long_holdings, short_holdings |
| `futures_info` | 鏈熻揣鍚堢害淇℃伅 | 鏈熻揣 | contract_code, exchange |
| `index_quote` | 鎸囨暟琛屾儏 | 鎸囨暟 | open, high, low, close, volume, amount |
| `index_kline` | 鎸囨暟K绾?| 鎸囨暟 | date, open, high, low, close, volume, amount |
| `macro_gdp` | GDP鏁版嵁 | 瀹忚 | quarter, gdp, yoy |
| `macro_cpi` | CPI鏁版嵁 | 瀹忚 | date, cpi |
| `macro_pmi` | PMI鏁版嵁 | 瀹忚 | date, pmi |
| `forex_quote` | 澶栨眹琛屾儏 | 澶栨眹 | currency_pair, rate, change_pct |
| `crypto_quote` | 鍔犲瘑璐у竵琛屾儏 | 鍔犲瘑璐у竵 | symbol, price, volume_24h, market_cap |
| `etf_quote` | ETF琛屾儏 | ETF | open, high, low, close, volume, amount |
| `etf_kline` | ETF K绾?| ETF | date, open, high, low, close, volume |
| `news` | 鏂伴椈璧勮 | 璧勮 | title, publish_time, source |
| `sentiment` | 甯傚満鎯呯华 | 璧勮 | date, sentiment_score, sentiment_label |
| `social` | 绀句氦鏁版嵁 | 璧勮 | post_id, content, publish_time, author |

### 3.2 鏁版嵁婧愭灇涓?

| source | 璇存槑 | 涓昏瑕嗙洊 |
|--------|------|---------|
| `akshare` | AKShare寮€婧愯储缁忔帴鍙?| 鍏ㄩ鍩熷厤璐规暟鎹?|
| `tushare` | Tushare Pro鎺ュ彛 | 鍏ㄩ鍩熶粯璐规暟鎹?|
| `eastmoney` | 涓滄柟璐㈠瘜缃戦〉/CDP | A鑲?鍩洪噾/鏈熻揣/鍊哄埜 |
| `sina` | 鏂版氮璐㈢粡API | A鑲″疄鏃惰鎯?|
| `tencent` | 鑵捐璐㈢粡API | A鑲″疄鏃惰鎯?|
| `netease` | 缃戞槗璐㈢粡API | A鑲¤鎯呮暟鎹?|
| `binance` | Binance浜ゆ槗鎵€ | 鍔犲瘑璐у竵 |
| `coingecko` | CoinGecko鏁版嵁 | 鍔犲瘑璐у竵 |
| `okx` | OKX浜ゆ槗鎵€ | 鍔犲瘑璐у竵 |
| `yahoo` | Yahoo Finance | 缇庤偂/娓偂/鍏ㄧ悆甯傚満 |
| `lexicon` | 鏈湴璇嶅吀娉曡垎鎯?| 鎯呮劅鍒嗘瀽(鏈湴) |

---

## 鍥涖€佹爣鐨勪唬鐮佹爣鍑嗗寲鏍煎紡

| 璧勪骇绫诲埆 | 鏍煎紡瑙勫垯 | 绀轰緥 |
|---------|---------|------|
| A鑲?| `{6浣嶄唬鐮亇.{浜ゆ槗鎵€}` | `600000.SH`, `000001.SZ`, `300750.SZ` |
| 鍊哄埜 | `{6浣嶄唬鐮亇.BOND` | `113000.BOND`, `127000.BOND` |
| 鍩洪噾 | `{6浣嶄唬鐮亇.FUND` | `000001.FUND`, `110011.FUND` |
| 鏈熻揣 | `{鍝佺}{骞存湀}.FUT` | `CU2401.FUT`, `IF2403.FUT` |
| 鎸囨暟 | `{浜ゆ槗鎵€}{浠ｇ爜}` | `SH000001`, `SZ399001`, `SZ399006` |
| ETF | `{6浣嶄唬鐮亇.{浜ゆ槗鎵€}` | `510300.SH`, `159915.SZ` |
| 澶栨眹 | `{璐у竵瀵箎` | `USD-CNY`, `EUR-USD` |
| 鍔犲瘑璐у竵 | `{甯佺}-{璁′环}` | `BTC-USDT`, `ETH-USDT` |
| 瀹忚鏁版嵁 | 绌哄瓧绗︿覆 `""` | `""` |
| 鍖楀悜璧勯噾 | 閫氶厤绗?`"*"` | `"*"` |

### 4.1 浜ゆ槗鎵€鍚庣紑瀵圭収琛?

| 鍚庣紑 | 浜ゆ槗鎵€ | 甯傚満 |
|------|--------|------|
| `.SH` | 涓婃捣璇佸埜浜ゆ槗鎵€ | A鑲?ETF/鎸囨暟 |
| `.SZ` | 娣卞湷璇佸埜浜ゆ槗鎵€ | A鑲?ETF/鎸囨暟 |
| `.BOND` | 閾惰闂村€哄埜甯傚満 | 鍊哄埜 |
| `.FUT` | 鏈熻揣浜ゆ槗鎵€ | 鏈熻揣鍚堢害 |
| `.HK` | 棣欐腐浜ゆ槗鎵€ | 娓偂 |
| `.US` | 缇庡浗浜ゆ槗鎵€ | 缇庤偂 |

---

## 浜斻€佹暟鎹ā鍨嬭缁嗗畾涔?

### 5.1 鑲＄エ瀹炴椂琛屾儏 (quote)

**JSON 绀轰緥锛?*
```json
{
  "source": "akshare",
  "data_type": "quote",
  "symbol": "600000.SH",
  "timestamp": "2024-01-15T10:30:00Z",
  "payload": {
    "name": "娴﹀彂閾惰",
    "open": 8.50,
    "high": 8.75,
    "low": 8.45,
    "close": 8.70,
    "pre_close": 8.50,
    "change": 0.20,
    "change_pct": 2.35,
    "volume": 15000000,
    "amount": 129750000.0,
    "turnover_rate": 0.45,
    "pe_ratio": 5.2,
    "pb_ratio": 0.48,
    "total_mv": 174000000000.0,
    "circ_mv": 174000000000.0,
    "bid1": 8.69,
    "ask1": 8.71,
    "bid1_vol": 5000,
    "ask1_vol": 3200
  },
  "meta": {
    "fetch_time_ms": 150.5,
    "retry_count": 0,
    "quality_score": 0.98,
    "warnings": []
  }
}
```

**瀛楁瀹氫箟锛?*

| 瀛楁 | 绫诲瀷 | 蹇呭～ | 鑼冨洿绾︽潫 | 璇存槑 |
|------|------|------|---------|------|
| `name` | str | 鍚?| 闀垮害1-50 | 鑲＄エ鍚嶇О |
| `open` | float | 鏄?| > 0 | 寮€鐩樹环(鍏? |
| `high` | float | 鏄?| >= open | 鏈€楂樹环(鍏? |
| `low` | float | 鏄?| <= open, > 0 | 鏈€浣庝环(鍏? |
| `close` | float | 鏄?| > 0 | 鏀剁洏浠?鍏? |
| `pre_close` | float | 鍚?| > 0 | 鏄ㄦ敹浠?鍏? |
| `change` | float | 鍚?| close - pre_close | 娑ㄨ穼棰?鍏? |
| `change_pct` | float | 鍚?| -100 ~ 100 | 娑ㄨ穼骞?%)(鐢眂hange/pre_close璁＄畻) |
| `volume` | int | 鏄?| >= 0 | 鎴愪氦閲?鑲? |
| `amount` | float | 鍚?| >= 0 | 鎴愪氦棰?鍏? |
| `turnover_rate` | float | 鍚?| 0 ~ 100 | 鎹㈡墜鐜?%)(褰撴棩鎴愪氦閲?娴侀€氳偂鏈? |
| `pe_ratio` | float | 鍚?| > 0 | 甯傜泩鐜?鍔?(TTM) |
| `pb_ratio` | float | 鍚?| >= 0 | 甯傚噣鐜?|
| `total_mv` | float | 鍚?| >= 0 | 鎬诲競鍊?鍏? |
| `circ_mv` | float | 鍚?| >= 0 | 娴侀€氬競鍊?鍏? |
| `bid1` | float | 鍚?| > 0 | 涔颁竴浠?鍏? |
| `ask1` | float | 鍚?| > 0 | 鍗栦竴浠?鍏? |
| `bid1_vol` | int | 鍚?| >= 0 | 涔颁竴閲?鑲? |
| `ask1_vol` | int | 鍚?| >= 0 | 鍗栦竴閲?鑲? |

---

### 5.2 鑲＄エK绾?(kline)

**JSON 绀轰緥锛?*
```json
{
  "source": "sina",
  "data_type": "kline",
  "symbol": "000001.SZ",
  "timestamp": "2024-01-15T00:00:00+08:00",
  "payload": {
    "date": "2024-01-15",
    "period": "daily",
    "open": 12.30,
    "high": 12.80,
    "low": 12.20,
    "close": 12.65,
    "volume": 5000000,
    "amount": 62500000.0,
    "amplitude": 4.88,
    "change_pct": 2.85,
    "adjust_type": "qfq"
  }
}
```

**瀛楁瀹氫箟锛?*

| 瀛楁 | 绫诲瀷 | 蹇呭～ | 鑼冨洿绾︽潫 | 璇存槑 |
|------|------|------|---------|------|
| `date` | str | 鏄?| YYYY-MM-DD | K绾挎棩鏈?|
| `period` | str | 鍚?| 1m/5m/15m/30m/60m/daily/weekly/monthly | K绾垮懆鏈?|
| `open` | float | 鏄?| > 0 | 寮€鐩樹环(鍏? |
| `high` | float | 鏄?| >= open | 鏈€楂樹环(鍏? |
| `low` | float | 鏄?| <= open, > 0 | 鏈€浣庝环(鍏? |
| `close` | float | 鏄?| > 0 | 鏀剁洏浠?鍏? |
| `volume` | int | 鍚?| >= 0 | 鎴愪氦閲?鑲? |
| `amount` | float | 鍚?| >= 0 | 鎴愪氦棰?鍏? |
| `amplitude` | float | 鍚?| 0 ~ 100 | 鎸箙(%)(楂樹綆鐨勫樊/鏄ㄦ敹脳100) |
| `change_pct` | float | 鍚?| -100 ~ 100 | 娑ㄨ穼骞?%)(杈冨墠涓€鏃ユ敹鐩? |
| `adjust_type` | str | 鍚?| qfq/hfq/none | 澶嶆潈鏂瑰紡 |

---

### 5.3 璐㈠姟鎶ヨ〃 (financial)

**JSON 绀轰緥锛?*
```json
{
  "source": "akshare",
  "data_type": "financial",
  "symbol": "600000.SH",
  "timestamp": "2024-01-15T00:00:00Z",
  "payload": {
    "type": "income_statement",
    "report_date": "2023Q4",
    "report_type": "Q",
    "publish_date": "2024-03-30",
    "revenue": 15000000000.0,
    "net_profit": 2000000000.0,
    "gross_profit": 5000000000.0,
    "total_assets": 50000000000.0,
    "total_liabilities": 40000000000.0,
    "equity": 10000000000.0,
    "roe": 20.0,
    "roa": 4.0,
    "gross_margin": 33.33,
    "net_margin": 13.33,
    "operating_cashflow": 3000000000.0,
    "investing_cashflow": -1000000000.0,
    "financing_cashflow": -500000000.0,
    "eps": 1.25
  }
}
```

**瀛楁瀹氫箟锛?*

| 瀛楁 | 绫诲瀷 | 蹇呭～ | 鑼冨洿绾︽潫 | 璇存槑 |
|------|------|------|---------|------|
| `type` | str | 鏄?| income_statement/balance_sheet/cash_flow | 鎶ヨ〃绫诲瀷 |
| `report_date` | str | 鏄?| YYYYQ1/Q2/Q3/Q4/YYYY | 鎶ュ憡鏈?|
| `report_type` | str | 鍚?| Q/S/A | 鎶ュ憡鏈熺被鍨?瀛?涓姤/骞? |
| `publish_date` | str | 鍚?| YYYY-MM-DD | 鍙戝竷鏃ユ湡 |
| `revenue` | float | 鏄?| >= 0 | 钀ヤ笟鎬绘敹鍏?鍏? |
| `net_profit` | float | 鏄?| >= 0 | 鍑€鍒╂鼎(鍏? |
| `gross_profit` | float | 鍚?| >= 0 | 姣涘埄娑?鍏? |
| `total_assets` | float | 鍚?| >= 0 | 鎬昏祫浜?鍏? |
| `total_liabilities` | float | 鍚?| >= 0 | 鎬昏礋鍊?鍏? |
| `equity` | float | 鍚?| >= 0 | 鑲′笢鏉冪泭(鍏? |
| `roe` | float | 鍚?| 0 ~ 100 | 鍑€璧勪骇鏀剁泭鐜?%)(鍑€鍒╂鼎/鍑€璧勪骇) |
| `roa` | float | 鍚?| 0 ~ 100 | 鎬昏祫浜ф敹鐩婄巼(%)(鍑€鍒╂鼎/鎬昏祫浜? |
| `gross_margin` | float | 鍚?| 0 ~ 100 | 姣涘埄鐜?%)(姣涘埄/钀ユ敹) |
| `net_margin` | float | 鍚?| 0 ~ 100 | 鍑€鍒╃巼(%)(鍑€鍒?钀ユ敹) |
| `operating_cashflow` | float | 鍚?| -inf ~ +inf | 缁忚惀娲诲姩鐜伴噾娴?鍏? |
| `investing_cashflow` | float | 鍚?| -inf ~ +inf | 鎶曡祫娲诲姩鐜伴噾娴?鍏? |
| `financing_cashflow` | float | 鍚?| -inf ~ +inf | 绛硅祫娲诲姩鐜伴噾娴?鍏? |
| `eps` | float | 鍚?| >= 0 | 姣忚偂鏀剁泭(鍏? |

---

### 5.4 鍒嗙孩鏁版嵁 (dividend)

**JSON 绀轰緥锛?*
```json
{
  "source": "akshare",
  "data_type": "dividend",
  "symbol": "600000.SH",
  "timestamp": "2024-01-15T00:00:00Z",
  "payload": {
    "announcement_date": "2024-03-30",
    "record_date": "2024-04-15",
    "ex_dividend_date": "2024-04-16",
    "payment_date": "2024-04-25",
    "dividend_per_share": 0.35,
    "bonus_share_ratio": 0.0,
    "transfer_share_ratio": 0.0,
    "dividend_yield": 4.12
  }
}
```

**瀛楁瀹氫箟锛?*

| 瀛楁 | 绫诲瀷 | 蹇呭～ | 鑼冨洿绾︽潫 | 璇存槑 |
|------|------|------|---------|------|
| `announcement_date` | str | 鏄?| YYYY-MM-DD | 鍏憡鏃ユ湡 |
| `record_date` | str | 鏄?| YYYY-MM-DD | 鑲℃潈鐧昏鏃?|
| `ex_dividend_date` | str | 鏄?| YYYY-MM-DD | 闄ゆ潈闄ゆ伅鏃?|
| `payment_date` | str | 鍚?| YYYY-MM-DD | 娲炬伅鏃?|
| `dividend_per_share` | float | 鏄?| >= 0 | 姣忚偂鍒嗙孩(鍏? |
| `bonus_share_ratio` | float | 鍚?| >= 0 | 閫佽偂姣斾緥(濡?.1琛ㄧず姣?0鑲￠€?鑲? |
| `transfer_share_ratio` | float | 鍚?| >= 0 | 杞姣斾緥(濡?.2琛ㄧず姣?0鑲¤浆澧?鑲? |
| `dividend_yield` | float | 鍚?| 0 ~ 20 | 鑲℃伅鐜?%)(姣忚偂鍒嗙孩/鑲′环脳100) |

---

### 5.5 榫欒檸姒?(lhb)

**JSON 绀轰緥锛?*
```json
{
  "source": "eastmoney",
  "data_type": "lhb",
  "symbol": "000001.SZ",
  "timestamp": "2024-01-15T15:00:00Z",
  "payload": {
    "trade_date": "2024-01-15",
    "reason": "杩炵画3涓氦鏄撴棩鍐呮敹鐩樹环鏍兼定骞呭亸绂诲€肩疮璁¤揪鍒?0%",
    "buy_amount": 150000000.0,
    "sell_amount": 120000000.0,
    "net_buy_amount": 30000000.0,
    "turnover_rate": 8.5,
    "close_price": 25.60,
    "change_pct": 10.02
  }
}
```

**瀛楁瀹氫箟锛?*

| 瀛楁 | 绫诲瀷 | 蹇呭～ | 鑼冨洿绾︽潫 | 璇存槑 |
|------|------|------|---------|------|
| `trade_date` | str | 鏄?| YYYY-MM-DD | 浜ゆ槗鏃ユ湡 |
| `reason` | str | 鏄?| 闀垮害1-200 | 涓婃鍘熷洜 |
| `buy_amount` | float | 鍚?| >= 0 | 涔板叆閲戦(鍏? |
| `sell_amount` | float | 鍚?| >= 0 | 鍗栧嚭閲戦(鍏? |
| `net_buy_amount` | float | 鏄?| -inf ~ +inf | 鍑€涔板叆閲戦(鍏?(buy-sell) |
| `turnover_rate` | float | 鍚?| 0 ~ 100 | 鎹㈡墜鐜?%)(褰撴棩鎴愪氦閲?娴侀€氳偂鏈? |
| `close_price` | float | 鍚?| > 0 | 鏀剁洏浠?鍏? |
| `change_pct` | float | 鍚?| -100 ~ 100 | 娑ㄨ穼骞?%)(杈冨墠涓€鏃ユ敹鐩? |

---

### 5.6 鍖楀悜璧勯噾 (northbound)

**JSON 绀轰緥锛?*
```json
{
  "source": "akshare",
  "data_type": "northbound",
  "symbol": "*",
  "timestamp": "2024-01-15T16:00:00Z",
  "payload": {
    "date": "2024-01-15",
    "type": "total",
    "sh_hk_buy": 45.2,
    "sh_hk_sell": 38.5,
    "sh_hk_net": 6.7,
    "sz_hk_buy": 52.1,
    "sz_hk_sell": 48.3,
    "sz_hk_net": 3.8,
    "total_net": 10.5
  }
}
```

**瀛楁瀹氫箟锛?*

| 瀛楁 | 绫诲瀷 | 蹇呭～ | 鑼冨洿绾︽潫 | 璇存槑 |
|------|------|------|---------|------|
| `date` | str | 鏄?| YYYY-MM-DD | 鏃ユ湡 |
| `type` | str | 鏄?| sh_hk/sz_hk/total | 璧勯噾娴佸悜绫诲瀷 |
| `sh_hk_buy` | float | 鍚?| >= 0 | 娌偂閫氫拱鍏?浜垮厓) |
| `sh_hk_sell` | float | 鍚?| >= 0 | 娌偂閫氬崠鍑?浜垮厓) |
| `sh_hk_net` | float | 鍚?| -inf ~ +inf | 娌偂閫氬噣娴佸叆(浜垮厓)(buy-sell) |
| `sz_hk_buy` | float | 鍚?| >= 0 | 娣辫偂閫氫拱鍏?浜垮厓) |
| `sz_hk_sell` | float | 鍚?| >= 0 | 娣辫偂閫氬崠鍑?浜垮厓) |
| `sz_hk_net` | float | 鍚?| -inf ~ +inf | 娣辫偂閫氬噣娴佸叆(浜垮厓)(buy-sell) |
| `total_net` | float | 鍚?| -inf ~ +inf | 鍖楀悜璧勯噾鍑€娴佸叆鍚堣(浜垮厓) |

---

### 5.7 鑲＄エ鍩虹淇℃伅 (stock_basic)

**JSON 绀轰緥锛?*
```json
{
  "source": "akshare",
  "data_type": "stock_basic",
  "symbol": "600000.SH",
  "timestamp": "2024-01-15T00:00:00Z",
  "payload": {
    "name": "娴﹀彂閾惰",
    "industry": "閾惰",
    "area": "涓婃捣",
    "list_date": "1999-11-10",
    "market": "SSE",
    "status": "active"
  }
}
```

**瀛楁瀹氫箟锛?*

| 瀛楁 | 绫诲瀷 | 蹇呭～ | 鑼冨洿绾︽潫 | 璇存槑 |
|------|------|------|---------|------|
| `name` | str | 鏄?| 闀垮害1-50 | 鑲＄エ鍚嶇О |
| `industry` | str | 鏄?| 闀垮害1-100 | 鎵€灞炶涓?鐢充竾涓€绾? |
| `area` | str | 鍚?| 闀垮害1-50 | 鎵€鍦ㄥ湴鍖?|
| `list_date` | str | 鏄?| YYYY-MM-DD | 涓婂競鏃ユ湡 |
| `market` | str | 鍚?| SSE/SZSE/BSE | 涓婂競浜ゆ槗鎵€ |
| `status` | str | 鍚?| active/delisted/suspended | 涓婂競鐘舵€?|

---

### 5.8 鍥藉€烘敹鐩婄巼 (bond_yield)

**JSON 绀轰緥锛?*
```json
{
  "source": "akshare",
  "data_type": "bond_yield",
  "symbol": "*",
  "timestamp": "2024-01-15T00:00:00Z",
  "payload": {
    "date": "2024-01-15",
    "bond_type": "treasury_10y",
    "yield_rate": 2.45,
    "change": 0.02
  }
}
```

**瀛楁瀹氫箟锛?*

| 瀛楁 | 绫诲瀷 | 蹇呭～ | 鑼冨洿绾︽潫 | 璇存槑 |
|------|------|------|---------|------|
| `date` | str | 鏄?| YYYY-MM-DD | 鏃ユ湡 |
| `bond_type` | str | 鏄?| treasury_1y/2y/3y/5y/7y/10y/30y/corporate_aa/corporate_a | 鍊哄埜绫诲瀷 |
| `yield_rate` | float | 鏄?| 0 ~ 20 | 鍒版湡鏀剁泭鐜?%)(鍗佸勾鏈熷浗鍊轰负鍩哄噯) |
| `change` | float | 鍚?| -inf ~ +inf | 杈冧笂鏈熷彉鍔?BP)(1BP=0.01%) |

---

### 5.9 鍊哄埜琛屾儏 (bond_quote)

**JSON 绀轰緥锛?*
```json
{
  "source": "eastmoney",
  "data_type": "bond_quote",
  "symbol": "113000.BOND",
  "timestamp": "2024-01-15T10:30:00Z",
  "payload": {
    "bond_code": "113000",
    "bond_name": "娴﹀彂杞€?,
    "price": 105.50,
    "yield_rate": 3.25,
    "coupon_rate": 0.50,
    "volume": 5000,
    "amount": 5275000.0,
    "change_pct": 1.25
  }
}
```

**瀛楁瀹氫箟锛?*

| 瀛楁 | 绫诲瀷 | 蹇呭～ | 鑼冨洿绾︽潫 | 璇存槑 |
|------|------|------|---------|------|
| `bond_code` | str | 鏄?| 6浣嶆暟瀛?| 鍊哄埜浠ｇ爜 |
| `bond_name` | str | 鏄?| 闀垮害1-50 | 鍊哄埜鍚嶇О |
| `price` | float | 鏄?| > 0 | 鏀剁洏浠?鍏?鐧惧厓闈㈠€? |
| `yield_rate` | float | 鏄?| 0 ~ 20 | 鍒版湡鏀剁泭鐜?%)(骞村寲) |
| `coupon_rate` | float | 鍚?| >= 0 | 绁ㄩ潰鍒╃巼(%)(骞村寲) |
| `volume` | int | 鍚?| >= 0 | 鎴愪氦閲?鎵?涓囧厓) |
| `amount` | float | 鍚?| >= 0 | 鎴愪氦棰?鍏? |
| `change_pct` | float | 鍚?| -100 ~ 100 | 娑ㄨ穼骞?%)(杈冨墠浜ゆ槗鏃ユ敹鐩? |

---

### 5.10 鍙浆鍊?(convertible)

**JSON 绀轰緥锛?*
```json
{
  "source": "akshare",
  "data_type": "convertible",
  "symbol": "113000.BOND",
  "timestamp": "2024-01-15T10:30:00Z",
  "payload": {
    "convert_code": "113000",
    "convert_name": "娴﹀彂杞€?,
    "stock_code": "600000.SH",
    "stock_name": "娴﹀彂閾惰",
    "price": 105.50,
    "stock_price": 8.70,
    "conversion_price": 7.50,
    "premium_rate": 21.43,
    "conversion_value": 116.00,
    "yield_to_maturity": 2.85,
    "volume": 8000,
    "amount": 8440000.0
  }
}
```

**瀛楁瀹氫箟锛?*

| 瀛楁 | 绫诲瀷 | 蹇呭～ | 鑼冨洿绾︽潫 | 璇存槑 |
|------|------|------|---------|------|
| `convert_code` | str | 鏄?| 6浣嶆暟瀛?| 鍙浆鍊轰唬鐮?|
| `convert_name` | str | 鏄?| 闀垮害1-50 | 鍙浆鍊哄悕绉?|
| `stock_code` | str | 鏄?| 6浣嶆暟瀛楁垨甯︿氦鏄撴墍鍚庣紑 | 姝ｈ偂浠ｇ爜 |
| `stock_name` | str | 鏄?| 闀垮害1-50 | 姝ｈ偂鍚嶇О |
| `price` | float | 鏄?| > 0 | 杞€轰环鏍?鍏?鐧惧厓闈㈠€? |
| `stock_price` | float | 鍚?| > 0 | 姝ｈ偂浠锋牸(鍏? |
| `conversion_price` | float | 鏄?| > 0 | 杞偂浠?鍏? |
| `premium_rate` | float | 鍚?| -inf ~ +inf | 杞偂婧环鐜?%)((杞€轰环-杞偂浠峰€?/杞偂浠峰€济?00) |
| `conversion_value` | float | 鍚?| > 0 | 杞偂浠峰€?鍏?(姝ｈ偂浠?杞偂浠访?00) |
| `yield_to_maturity` | float | 鍚?| 0 ~ 30 | 鍒版湡鏀剁泭鐜?%)(鎸佹湁鑷冲埌鏈熷勾鍖栨敹鐩? |
| `volume` | int | 鍚?| >= 0 | 鎴愪氦閲?鎵? |
| `amount` | float | 鍚?| >= 0 | 鎴愪氦棰?鍏? |

---

### 5.11 鍊哄埜鍩虹淇℃伅 (bond_info)

**JSON 绀轰緥锛?*
```json
{
  "source": "akshare",
  "data_type": "bond_info",
  "symbol": "113000.BOND",
  "timestamp": "2024-01-15T00:00:00Z",
  "payload": {
    "bond_code": "113000",
    "bond_name": "娴﹀彂杞€?,
    "bond_type": "convertible",
    "issuer": "娴﹀彂閾惰",
    "rating": "AA+",
    "issue_size": 80.0,
    "issue_date": "2020-04-20",
    "maturity_date": "2025-04-20",
    "term_years": 5.0,
    "coupon_rate": 0.50,
    "coupon_type": "fixed",
    "listing_date": "2020-05-08",
    "exchange": "SSE"
  }
}
```

**瀛楁瀹氫箟锛?*

| 瀛楁 | 绫诲瀷 | 蹇呭～ | 鑼冨洿绾︽潫 | 璇存槑 |
|------|------|------|---------|------|
| `bond_code` | str | 鏄?| 6浣嶆暟瀛?| 鍊哄埜浠ｇ爜 |
| `bond_name` | str | 鏄?| 闀垮害1-50 | 鍊哄埜鍚嶇О |
| `bond_type` | str | 鏄?| treasury/corporate/convertible | 鍊哄埜绫诲瀷 |
| `issuer` | str | 鍚?| 闀垮害1-100 | 鍙戣浜哄悕绉?|
| `rating` | str | 鍚?| 闀垮害1-20 | 淇＄敤璇勭骇(AAA/AA+/AA/...) |
| `issue_size` | float | 鍚?| > 0 | 鍙戣瑙勬ā(浜垮厓) |
| `issue_date` | str | 鍚?| YYYY-MM-DD | 鍙戣鏃ユ湡 |
| `maturity_date` | str | 鍚?| YYYY-MM-DD | 鍒版湡鏃ユ湡 |
| `term_years` | float | 鍚?| > 0 | 鏈熼檺(骞? |
| `coupon_rate` | float | 鍚?| >= 0 | 绁ㄩ潰鍒╃巼(%)(骞村寲) |
| `coupon_type` | str | 鍚?| fixed/floating | 浠樻伅鏂瑰紡 |
| `listing_date` | str | 鍚?| YYYY-MM-DD | 涓婂競鏃ユ湡 |
| `exchange` | str | 鍚?| SSE/SZSE/閾惰闂?| 涓婂競浜ゆ槗鎵€ |

---

### 5.12 鍩洪噾鍑€鍊?(fund_nav)

**JSON 绀轰緥锛?*
```json
{
  "source": "akshare",
  "data_type": "fund_nav",
  "symbol": "000001.FUND",
  "timestamp": "2024-01-15T00:00:00Z",
  "payload": {
    "fund_name": "鍗庡鎴愰暱娣峰悎",
    "fund_type": "娣峰悎鍨?,
    "nav_date": "2024-01-15",
    "nav": 1.2500,
    "accumulated_nav": 1.8500,
    "daily_return": 0.40,
    "risk_level": "涓珮椋庨櫓"
  }
}
```

**瀛楁瀹氫箟锛?*

| 瀛楁 | 绫诲瀷 | 蹇呭～ | 鑼冨洿绾︽潫 | 璇存槑 |
|------|------|------|---------|------|
| `fund_name` | str | 鍚?| 闀垮害1-100 | 鍩洪噾鍚嶇О |
| `fund_type` | str | 鍚?| 鑲＄エ鍨?娣峰悎鍨?鍊哄埜鍨?璐у竵鍨?鎸囨暟鍨?QDII | 鍩洪噾绫诲瀷 |
| `nav_date` | str | 鏄?| YYYY-MM-DD | 鍑€鍊兼棩鏈?|
| `nav` | float | 鏄?| > 0 | 鍗曚綅鍑€鍊?鍏? |
| `accumulated_nav` | float | 鏄?| >= nav | 绱鍑€鍊?鍏? |
| `daily_return` | float | 鍚?| -100 ~ 100 | 鏃ユ敹鐩婄巼(%)(杈冨墠涓€鏃ュ噣鍊? |
| `risk_level` | str | 鍚?| 浣庨闄?涓綆椋庨櫓/涓闄?涓珮椋庨櫓/楂橀闄?| 椋庨櫓绛夌骇 |

---

### 5.13 鍩洪噾鎸佷粨 (fund_holdings)

**JSON 绀轰緥锛?*
```json
{
  "source": "akshare",
  "data_type": "fund_holdings",
  "symbol": "000001.FUND",
  "timestamp": "2024-01-15T00:00:00Z",
  "payload": {
    "fund_name": "鍗庡鎴愰暱娣峰悎",
    "report_date": "2023Q4",
    "stock_code": "600000.SH",
    "stock_name": "娴﹀彂閾惰",
    "shares": 500.00,
    "market_value": 4350.00,
    "weight": 3.50,
    "change": "鏂板"
  }
}
```

**瀛楁瀹氫箟锛?*

| 瀛楁 | 绫诲瀷 | 蹇呭～ | 鑼冨洿绾︽潫 | 璇存槑 |
|------|------|------|---------|------|
| `fund_name` | str | 鍚?| 闀垮害1-100 | 鍩洪噾鍚嶇О |
| `report_date` | str | 鏄?| YYYYQ1/Q2/Q3/Q4/YYYY | 鎶ュ憡鏈?|
| `stock_code` | str | 鏄?| 6浣嶆暟瀛楁垨甯︿氦鏄撴墍鍚庣紑 | 鑲＄エ浠ｇ爜 |
| `stock_name` | str | 鏄?| 闀垮害1-50 | 鑲＄エ鍚嶇О |
| `shares` | float | 鏄?| >= 0 | 鎸佽偂鏁伴噺(涓囪偂) |
| `market_value` | float | 鏄?| >= 0 | 甯傚€?涓囧厓) |
| `weight` | float | 鏄?| 0 ~ 100 | 鍗犲噣鍊兼瘮(%)(鍗曞彧鑲＄エ鍗犲熀閲戝噣鍊兼瘮渚? |
| `change` | str | 鍚?| 澧炲姞/鍑忓皯/鏂板/閫€鍑?| 鎸佷粨鍙樺姩鏂瑰悜 |

---

### 5.14 鍩洪噾鎺掕姒?(fund_rank)

**JSON 绀轰緥锛?*
```json
{
  "source": "akshare",
  "data_type": "fund_rank",
  "symbol": "000001.FUND",
  "timestamp": "2024-01-15T00:00:00Z",
  "payload": {
    "rank": 15,
    "fund_code": "000001",
    "fund_name": "鍗庡鎴愰暱娣峰悎",
    "nav": 1.2500,
    "acc_nav": 1.8500,
    "nav_date": "2024-01-15",
    "return_1y": 15.50,
    "return_3y": 45.20,
    "return_5y": 80.30,
    "fund_type": "娣峰悎鍨?
  }
}
```

**瀛楁瀹氫箟锛?*

| 瀛楁 | 绫诲瀷 | 蹇呭～ | 鑼冨洿绾︽潫 | 璇存槑 |
|------|------|------|---------|------|
| `rank` | int | 鏄?| >= 1 | 鎺掑悕(浠?寮€濮? |
| `fund_code` | str | 鏄?| 6浣嶆暟瀛?| 鍩洪噾浠ｇ爜 |
| `fund_name` | str | 鏄?| 闀垮害1-100 | 鍩洪噾鍚嶇О |
| `nav` | float | 鍚?| > 0 | 鏈€鏂板噣鍊?鍏? |
| `acc_nav` | float | 鍚?| >= nav | 绱鍑€鍊?鍏? |
| `nav_date` | str | 鍚?| YYYY-MM-DD | 鍑€鍊兼棩鏈?|
| `return_1y` | float | 鍚?| -inf ~ +inf | 杩?骞存敹鐩婄巼(%)(澶嶆潈) |
| `return_3y` | float | 鍚?| -inf ~ +inf | 杩?骞存敹鐩婄巼(%)(澶嶆潈) |
| `return_5y` | float | 鍚?| -inf ~ +inf | 杩?骞存敹鐩婄巼(%)(澶嶆潈) |
| `fund_type` | str | 鍚?| 鑲＄エ鍨?娣峰悎鍨?鍊哄埜鍨?璐у竵鍨?鎸囨暟鍨?| 鍩洪噾绫诲瀷 |

---

### 5.15 鍩洪噾鍩虹淇℃伅 (fund_info)

**JSON 绀轰緥锛?*
```json
{
  "source": "akshare",
  "data_type": "fund_info",
  "symbol": "000001.FUND",
  "timestamp": "2024-01-15T00:00:00Z",
  "payload": {
    "fund_name": "鍗庡鎴愰暱娣峰悎",
    "fund_type": "娣峰悎鍨?,
    "risk_level": "涓珮椋庨櫓",
    "management_company": "鍗庡鍩洪噾绠＄悊鏈夐檺鍏徃",
    "manager": "閮戠厹",
    "establish_date": "2001-12-18",
    "fund_size": 120.50,
    "min_purchase": 10.0,
    "management_fee": 1.50,
    "custodian_fee": 0.25,
    "sales_service_fee": 0.40
  }
}
```

**瀛楁瀹氫箟锛?*

| 瀛楁 | 绫诲瀷 | 蹇呭～ | 鑼冨洿绾︽潫 | 璇存槑 |
|------|------|------|---------|------|
| `fund_name` | str | 鏄?| 闀垮害1-100 | 鍩洪噾鍚嶇О |
| `fund_type` | str | 鏄?| 鑲＄エ鍨?娣峰悎鍨?鍊哄埜鍨?璐у竵鍨?鎸囨暟鍨?QDII | 鍩洪噾绫诲瀷 |
| `risk_level` | str | 鍚?| 浣庨闄?涓綆椋庨櫓/涓闄?涓珮椋庨櫓/楂橀闄?| 椋庨櫓绛夌骇 |
| `management_company` | str | 鍚?| 闀垮害1-100 | 绠＄悊鍏徃鍚嶇О |
| `manager` | str | 鍚?| 闀垮害1-50 | 鍩洪噾缁忕悊濮撳悕 |
| `establish_date` | str | 鍚?| YYYY-MM-DD | 鎴愮珛鏃ユ湡 |
| `fund_size` | float | 鍚?| > 0 | 鍩洪噾瑙勬ā(浜垮厓) |
| `min_purchase` | float | 鍚?| > 0 | 鏈€浣庣敵璐噾棰?鍏? |
| `management_fee` | float | 鍚?| 0 ~ 3 | 绠＄悊璐圭巼(%)(骞村寲) |
| `custodian_fee` | float | 鍚?| 0 ~ 1 | 鎵樼璐圭巼(%)(骞村寲) |
| `sales_service_fee` | float | 鍚?| 0 ~ 1 | 閿€鍞湇鍔¤垂鐜?%)(骞村寲) |

---

### 5.16 鏈熻揣琛屾儏 (futures_quote)

**JSON 绀轰緥锛?*
```json
{
  "source": "akshare",
  "data_type": "futures_quote",
  "symbol": "CU2401.FUT",
  "timestamp": "2024-01-15T10:30:00Z",
  "payload": {
    "contract_code": "CU2401",
    "contract_name": "娌摐2401",
    "exchange": "SHFE",
    "open": 68500.0,
    "high": 69200.0,
    "low": 68100.0,
    "close": 68900.0,
    "settlement": 68750.0,
    "pre_settlement": 68600.0,
    "volume": 125000,
    "open_interest": 850000,
    "change": 300.0,
    "change_pct": 0.44,
    "bid_price": 68850.0,
    "ask_price": 68950.0,
    "bid_volume": 15,
    "ask_volume": 8
  }
}
```

**瀛楁瀹氫箟锛?*

| 瀛楁 | 绫诲瀷 | 蹇呭～ | 鑼冨洿绾︽潫 | 璇存槑 |
|------|------|------|---------|------|
| `contract_code` | str | 鏄?| 瀛楁瘝+鏁板瓧 | 鍚堢害浠ｇ爜 |
| `contract_name` | str | 鍚?| 闀垮害1-50 | 鍚堢害鍚嶇О |
| `exchange` | str | 鍚?| SHFE/DCE/CZCE/INE/CFFEX | 浜ゆ槗鎵€ |
| `open` | float | 鏄?| > 0 | 寮€鐩樹环 |
| `high` | float | 鏄?| >= open | 鏈€楂樹环 |
| `low` | float | 鏄?| <= open, > 0 | 鏈€浣庝环 |
| `close` | float | 鏄?| > 0 | 鏀剁洏浠?|
| `settlement` | float | 鍚?| > 0 | 缁撶畻浠?|
| `pre_settlement` | float | 鍚?| > 0 | 鏄ㄧ粨绠椾环 |
| `volume` | int | 鏄?| >= 0 | 鎴愪氦閲?鎵? |
| `open_interest` | int | 鍚?| >= 0 | 鎸佷粨閲?鎵? |
| `change` | float | 鍚?| -inf ~ +inf | 娑ㄨ穼棰?|
| `change_pct` | float | 鍚?| -100 ~ 100 | 娑ㄨ穼骞?%)(杈冩槰缁撶畻) |
| `bid_price` | float | 鍚?| > 0 | 涔颁竴浠?|
| `ask_price` | float | 鍚?| > 0 | 鍗栦竴浠?|
| `bid_volume` | int | 鍚?| >= 0 | 涔颁竴閲?鎵? |
| `ask_volume` | int | 鍚?| >= 0 | 鍗栦竴閲?鎵? |

---

### 5.17 鏈熻揣K绾?(futures_kline)

**JSON 绀轰緥锛?*
```json
{
  "source": "akshare",
  "data_type": "futures_kline",
  "symbol": "IF2403.FUT",
  "timestamp": "2024-01-15T00:00:00+08:00",
  "payload": {
    "date": "2024-01-15",
    "period": "daily",
    "open": 3850.0,
    "high": 3890.0,
    "low": 3840.0,
    "close": 3875.0,
    "settlement": 3865.0,
    "volume": 50000,
    "open_interest": 1200000,
    "amplitude": 1.30
  }
}
```

**瀛楁瀹氫箟锛?*

| 瀛楁 | 绫诲瀷 | 蹇呭～ | 鑼冨洿绾︽潫 | 璇存槑 |
|------|------|------|---------|------|
| `date` | str | 鏄?| YYYY-MM-DD | K绾挎棩鏈?|
| `period` | str | 鍚?| 1m/5m/15m/30m/60m/daily | K绾垮懆鏈?|
| `open` | float | 鏄?| > 0 | 寮€鐩樹环 |
| `high` | float | 鏄?| >= open | 鏈€楂樹环 |
| `low` | float | 鏄?| <= open, > 0 | 鏈€浣庝环 |
| `close` | float | 鏄?| > 0 | 鏀剁洏浠?|
| `settlement` | float | 鍚?| > 0 | 缁撶畻浠?|
| `volume` | int | 鏄?| >= 0 | 鎴愪氦閲?鎵? |
| `open_interest` | int | 鍚?| >= 0 | 鎸佷粨閲?鎵? |
| `amplitude` | float | 鍚?| 0 ~ 100 | 鎸箙(%)(楂樹綆宸?鏄ㄧ粨绠椕?00) |

---

### 5.18 鏈熻揣鎸佷粨 (futures_position)

**JSON 绀轰緥锛?*
```json
{
  "source": "cffex",
  "data_type": "futures_position",
  "symbol": "IF2403.FUT",
  "timestamp": "2024-01-15T15:30:00+08:00",
  "payload": {
    "date": "2024-01-15",
    "contract_code": "IF2403",
    "long_holdings": 680000,
    "short_holdings": 720000,
    "net_holdings": -40000,
    "total_holdings": 1400000,
    "long_change": 5000,
    "short_change": -3000,
    "net_change": 8000,
    "margin": 544000000.0,
    "open_interest_change_pct": 0.57
  }
}
```

**瀛楁瀹氫箟锛?*

| 瀛楁 | 绫诲瀷 | 蹇呭～ | 鑼冨洿绾︽潫 | 璇存槑 |
|------|------|------|---------|------|
| `date` | str | 鏄?| YYYY-MM-DD | 鏃ユ湡 |
| `contract_code` | str | 鏄?| 瀛楁瘝+鏁板瓧 | 鍚堢害浠ｇ爜 |
| `long_holdings` | int | 鏄?| >= 0 | 澶氬ご鎸佷粨閲?鎵? |
| `short_holdings` | int | 鏄?| >= 0 | 绌哄ご鎸佷粨閲?鎵? |
| `net_holdings` | int | 鍚?| -inf ~ +inf | 鍑€鎸佷粨(闀?绌? |
| `total_holdings` | int | 鍚?| >= 0 | 鎬绘寔浠撻噺(鎵?(闀?绌? |
| `long_change` | int | 鍚?| -inf ~ +inf | 澶氬ご鎸佷粨鍙樺寲(鎵? |
| `short_change` | int | 鍚?| -inf ~ +inf | 绌哄ご鎸佷粨鍙樺寲(鎵? |
| `net_change` | int | 鍚?| -inf ~ +inf | 鍑€鎸佷粨鍙樺寲(鎵? |
| `margin` | float | 鍚?| >= 0 | 淇濊瘉閲?鍏? |
| `open_interest_change_pct` | float | 鍚?| -100 ~ 100 | 鎸佷粨鍙樺寲鐜?%)(杈冧笂鏈? |

---

### 5.19 鏈熻揣鍚堢害淇℃伅 (futures_info)

**JSON 绀轰緥锛?*
```json
{
  "source": "akshare",
  "data_type": "futures_info",
  "symbol": "CU2401.FUT",
  "timestamp": "2024-01-15T00:00:00Z",
  "payload": {
    "contract_code": "CU2401",
    "contract_name": "娌摐2401",
    "exchange": "SHFE",
    "exchange_full": "涓婃捣鏈熻揣浜ゆ槗鎵€",
    "underlying": "閾?,
    "contract_type": "commodity",
    "lot_size": 5,
    "tick_size": 10.0,
    "trading_hours": "9:00-15:00",
    "list_date": "2023-09-18",
    "delivery_date": "2024-01-18",
    "maturity_date": "2024-01-18",
    "margin_rate": 12.0
  }
}
```

**瀛楁瀹氫箟锛?*

| 瀛楁 | 绫诲瀷 | 蹇呭～ | 鑼冨洿绾︽潫 | 璇存槑 |
|------|------|------|---------|------|
| `contract_code` | str | 鏄?| 瀛楁瘝+鏁板瓧 | 鍚堢害浠ｇ爜 |
| `contract_name` | str | 鍚?| 闀垮害1-50 | 鍚堢害鍚嶇О |
| `exchange` | str | 鏄?| SHFE/DCE/CZCE/INE/CFFEX | 浜ゆ槗鎵€缂╁啓 |
| `exchange_full` | str | 鍚?| 闀垮害1-50 | 浜ゆ槗鎵€鍏ㄧО |
| `underlying` | str | 鏄?| 闀垮害1-20 | 鏍囩殑鍝佺鍚嶇О |
| `contract_type` | str | 鍚?| commodity/financial | 鍚堢害绫诲瀷 |
| `lot_size` | int | 鏄?| >= 1 | 鍚堢害鍗曚綅(鎵?寮? |
| `tick_size` | float | 鏄?| > 0 | 鏈€灏忓彉鍔ㄤ环浣?|
| `trading_hours` | str | 鍚?| 闀垮害1-100 | 浜ゆ槗鏃堕棿 |
| `list_date` | str | 鍚?| YYYY-MM-DD | 涓婂競鏃ユ湡 |
| `delivery_date` | str | 鍚?| YYYY-MM-DD | 浜ゅ壊鏃ユ湡 |
| `maturity_date` | str | 鍚?| YYYY-MM-DD | 鍒版湡鏃ユ湡 |
| `margin_rate` | float | 鍚?| 0 ~ 50 | 淇濊瘉閲戞瘮渚?%)(浜ゆ槗鎵€鏍囧噯) |

---

### 5.20 鎸囨暟琛屾儏 (index_quote)

**JSON 绀轰緥锛?*
```json
{
  "source": "sina",
  "data_type": "index_quote",
  "symbol": "SH000001",
  "timestamp": "2024-01-15T10:30:00Z",
  "payload": {
    "name": "涓婅瘉鎸囨暟",
    "open": 2950.50,
    "high": 2975.20,
    "low": 2940.80,
    "close": 2968.50,
    "pre_close": 2945.00,
    "volume": 350000000,
    "amount": 450000000000.0,
    "change": 23.50,
    "change_pct": 0.80
  }
}
```

**瀛楁瀹氫箟锛?*

| 瀛楁 | 绫诲瀷 | 蹇呭～ | 鑼冨洿绾︽潫 | 璇存槑 |
|------|------|------|---------|------|
| `name` | str | 鍚?| 闀垮害1-50 | 鎸囨暟鍚嶇О |
| `open` | float | 鏄?| > 0 | 寮€鐩樹环 |
| `high` | float | 鏄?| >= open | 鏈€楂樹环 |
| `low` | float | 鏄?| <= open, > 0 | 鏈€浣庝环 |
| `close` | float | 鏄?| > 0 | 鏀剁洏浠?|
| `pre_close` | float | 鍚?| > 0 | 鏄ㄦ敹浠?|
| `volume` | int | 鍚?| >= 0 | 鎴愪氦閲?鑲? |
| `amount` | float | 鍚?| >= 0 | 鎴愪氦棰?鍏? |
| `change` | float | 鍚?| -inf ~ +inf | 娑ㄨ穼鐐?|
| `change_pct` | float | 鍚?| -100 ~ 100 | 娑ㄨ穼骞?%)(杈冨墠鏀? |

---

### 5.21 鎸囨暟K绾?(index_kline)

**JSON 绀轰緥锛?*
```json
{
  "source": "sina",
  "data_type": "index_kline",
  "symbol": "SZ399001",
  "timestamp": "2024-01-15T00:00:00+08:00",
  "payload": {
    "date": "2024-01-15",
    "period": "daily",
    "open": 11250.50,
    "high": 11320.80,
    "low": 11200.00,
    "close": 11295.30,
    "volume": 280000000,
    "amount": 380000000000.0
  }
}
```

**瀛楁瀹氫箟锛?*

| 瀛楁 | 绫诲瀷 | 蹇呭～ | 鑼冨洿绾︽潫 | 璇存槑 |
|------|------|------|---------|------|
| `date` | str | 鏄?| YYYY-MM-DD | K绾挎棩鏈?|
| `period` | str | 鍚?| daily/weekly/monthly | K绾垮懆鏈?|
| `open` | float | 鏄?| > 0 | 寮€鐩樹环 |
| `high` | float | 鏄?| >= open | 鏈€楂樹环 |
| `low` | float | 鏄?| <= open, > 0 | 鏈€浣庝环 |
| `close` | float | 鏄?| > 0 | 鏀剁洏浠?|
| `volume` | int | 鍚?| >= 0 | 鎴愪氦閲?鑲? |
| `amount` | float | 鍚?| >= 0 | 鎴愪氦棰?鍏? |

---

### 5.22 GDP鏁版嵁 (macro_gdp)

**JSON 绀轰緥锛?*
```json
{
  "source": "akshare",
  "data_type": "macro_gdp",
  "symbol": "*",
  "timestamp": "2024-01-15T00:00:00Z",
  "payload": {
    "quarter": "2023Q4",
    "gdp": 1211000.0,
    "yoy": 5.2,
    "qoq": 1.1,
    "primary_industry": 97000.0,
    "secondary_industry": 445000.0,
    "tertiary_industry": 669000.0
  }
}
```

**瀛楁瀹氫箟锛?*

| 瀛楁 | 绫诲瀷 | 蹇呭～ | 鑼冨洿绾︽潫 | 璇存槑 |
|------|------|------|---------|------|
| `quarter` | str | 鏄?| YYYYQ1/Q2/Q3/Q4 | 瀛ｅ害 |
| `gdp` | float | 鏄?| > 0 | GDP(浜垮厓)(褰撳鍊? |
| `yoy` | float | 鏄?| -100 ~ 100 | 鍚屾瘮澧為€?%)(杈冧笂骞村悓鏈? |
| `qoq` | float | 鍚?| -100 ~ 100 | 鐜瘮澧為€?%)(杈冧笂瀛ｅ害) |
| `primary_industry` | float | 鍚?| >= 0 | 绗竴浜т笟澧炲姞鍊?浜垮厓) |
| `secondary_industry` | float | 鍚?| >= 0 | 绗簩浜т笟澧炲姞鍊?浜垮厓) |
| `tertiary_industry` | float | 鍚?| >= 0 | 绗笁浜т笟澧炲姞鍊?浜垮厓) |

---

### 5.23 CPI鏁版嵁 (macro_cpi)

**JSON 绀轰緥锛?*
```json
{
  "source": "akshare",
  "data_type": "macro_cpi",
  "symbol": "*",
  "timestamp": "2024-01-15T00:00:00Z",
  "payload": {
    "date": "2024-01-15",
    "cpi": 100.3,
    "yoy": 0.5,
    "qoq": 0.1,
    "food_cpi": 99.8,
    "core_cpi": 100.1
  }
}
```

**瀛楁瀹氫箟锛?*

| 瀛楁 | 绫诲瀷 | 蹇呭～ | 鑼冨洿绾︽潫 | 璇存槑 |
|------|------|------|---------|------|
| `date` | str | 鏄?| YYYY-MM-DD | 鏃ユ湡 |
| `cpi` | float | 鏄?| > 0 | CPI鎸囨暟(涓婂勾=100) |
| `yoy` | float | 鏄?| -100 ~ 100 | 鍚屾瘮娑ㄥ箙(%)(杈冧笂骞村悓鏈? |
| `qoq` | float | 鍚?| -100 ~ 100 | 鐜瘮娑ㄥ箙(%)(杈冧笂鏈? |
| `food_cpi` | float | 鍚?| > 0 | 椋熷搧CPI |
| `core_cpi` | float | 鍚?| > 0 | 鏍稿績CPI(鍓旈櫎椋熷搧鍜岃兘婧? |

---

### 5.24 PMI鏁版嵁 (macro_pmi)

**JSON 绀轰緥锛?*
```json
{
  "source": "akshare",
  "data_type": "macro_pmi",
  "symbol": "*",
  "timestamp": "2024-01-15T00:00:00Z",
  "payload": {
    "date": "2024-01-15",
    "pmi": 50.1,
    "manufacturing_pmi": 50.1,
    "non_manufacturing_pmi": 50.8,
    "business_activity": 54.2
  }
}
```

**瀛楁瀹氫箟锛?*

| 瀛楁 | 绫诲瀷 | 蹇呭～ | 鑼冨洿绾︽潫 | 璇存槑 |
|------|------|------|---------|------|
| `date` | str | 鏄?| YYYY-MM-DD | 鏃ユ湡 |
| `pmi` | float | 鏄?| 0 ~ 100 | PMI缁煎悎鎸囨暟 |
| `manufacturing_pmi` | float | 鍚?| 0 ~ 100 | 鍒堕€犱笟PMI |
| `non_manufacturing_pmi` | float | 鍚?| 0 ~ 100 | 闈炲埗閫犱笟PMI |
| `business_activity` | float | 鍚?| 0 ~ 100 | 涓氬姟娲诲姩鎸囨暟 |

---

### 5.25 澶栨眹琛屾儏 (forex_quote)

**JSON 绀轰緥锛?*
```json
{
  "source": "akshare",
  "data_type": "forex_quote",
  "symbol": "USD-CNY",
  "timestamp": "2024-01-15T10:30:00Z",
  "payload": {
    "currency_pair": "USD-CNY",
    "rate": 7.1250,
    "change_pct": 0.15,
    "bid": 7.1230,
    "ask": 7.1270,
    "high": 7.1300,
    "low": 7.1180
  }
}
```

**瀛楁瀹氫箟锛?*

| 瀛楁 | 绫诲瀷 | 蹇呭～ | 鑼冨洿绾︽潫 | 璇存槑 |
|------|------|------|---------|------|
| `currency_pair` | str | 鏄?| 闀垮害3-10 | 璐у竵瀵?濡俇SD-CNY) |
| `rate` | float | 鏄?| > 0 | 姹囩巼(1鍗曚綅澶栧竵=锛熸湰甯? |
| `change_pct` | float | 鏄?| -100 ~ 100 | 娑ㄨ穼骞?%)(杈冨墠鏀? |
| `bid` | float | 鍚?| > 0 | 涔板叆浠?|
| `ask` | float | 鍚?| > 0 | 鍗栧嚭浠?|
| `high` | float | 鍚?| > 0 | 鏈€楂樹环 |
| `low` | float | 鍚?| > 0 | 鏈€浣庝环 |

---

### 5.26 鍔犲瘑璐у竵琛屾儏 (crypto_quote)

**JSON 绀轰緥锛?*
```json
{
  "source": "binance",
  "data_type": "crypto_quote",
  "symbol": "BTC-USDT",
  "timestamp": "2024-01-15T10:30:00Z",
  "payload": {
    "symbol": "BTC-USDT",
    "price": 43250.50,
    "volume_24h": 2850000000.0,
    "market_cap": 845000000000.0,
    "change_pct_24h": 2.35,
    "high_24h": 43800.00,
    "low_24h": 42100.00
  }
}
```

**瀛楁瀹氫箟锛?*

| 瀛楁 | 绫诲瀷 | 蹇呭～ | 鑼冨洿绾︽潫 | 璇存槑 |
|------|------|------|---------|------|
| `symbol` | str | 鏄?| 闀垮害3-20 | 浜ゆ槗瀵?濡侭TC-USDT) |
| `price` | float | 鏄?| > 0 | 鏈€鏂颁环鏍?璁′环璐у竵) |
| `volume_24h` | float | 鏄?| >= 0 | 24灏忔椂鎴愪氦閲?|
| `market_cap` | float | 鏄?| >= 0 | 甯傚€?|
| `change_pct_24h` | float | 鍚?| -100 ~ 100 | 24灏忔椂娑ㄨ穼骞?%)(杈冨墠鏀? |
| `high_24h` | float | 鍚?| > 0 | 24灏忔椂鏈€楂樹环 |
| `low_24h` | float | 鍚?| > 0 | 24灏忔椂鏈€浣庝环 |

---

### 5.27 ETF琛屾儏 (etf_quote)

**JSON 绀轰緥锛?*
```json
{
  "source": "eastmoney",
  "data_type": "etf_quote",
  "symbol": "510300.SH",
  "timestamp": "2024-01-15T10:30:00Z",
  "payload": {
    "name": "鍗庢嘲鏌忕憺娌繁300ETF",
    "open": 3.850,
    "high": 3.880,
    "low": 3.840,
    "close": 3.870,
    "pre_close": 3.840,
    "volume": 50000000,
    "amount": 193500000.0,
    "change_pct": 0.78,
    "nav": 3.865,
    "premium_discount": 0.13
  }
}
```

**瀛楁瀹氫箟锛?*

| 瀛楁 | 绫诲瀷 | 蹇呭～ | 鑼冨洿绾︽潫 | 璇存槑 |
|------|------|------|---------|------|
| `name` | str | 鍚?| 闀垮害1-50 | ETF鍚嶇О |
| `open` | float | 鏄?| > 0 | 寮€鐩樹环 |
| `high` | float | 鏄?| >= open | 鏈€楂樹环 |
| `low` | float | 鏄?| <= open, > 0 | 鏈€浣庝环 |
| `close` | float | 鏄?| > 0 | 鏀剁洏浠?|
| `pre_close` | float | 鍚?| > 0 | 鏄ㄦ敹浠?|
| `volume` | int | 鏄?| >= 0 | 鎴愪氦閲?浠? |
| `amount` | float | 鍚?| >= 0 | 鎴愪氦棰?鍏? |
| `change_pct` | float | 鍚?| -100 ~ 100 | 娑ㄨ穼骞?%)(杈冨墠鏀? |
| `nav` | float | 鍚?| > 0 | 鍙傝€冨噣鍊?鍏? |
| `premium_discount` | float | 鍚?| -100 ~ 100 | 婧环鐜?%)((甯備环-鍑€鍊?/鍑€鍊济?00) |

---

### 5.28 ETF K绾?(etf_kline)

**JSON 绀轰緥锛?*
```json
{
  "source": "akshare",
  "data_type": "etf_kline",
  "symbol": "510300.SH",
  "timestamp": "2024-01-15T00:00:00+08:00",
  "payload": {
    "date": "2024-01-15",
    "period": "daily",
    "open": 3.850,
    "high": 3.880,
    "low": 3.840,
    "close": 3.870,
    "volume": 45000000,
    "amount": 174150000.0
  }
}
```

**瀛楁瀹氫箟锛?*

| 瀛楁 | 绫诲瀷 | 蹇呭～ | 鑼冨洿绾︽潫 | 璇存槑 |
|------|------|------|---------|------|
| `date` | str | 鏄?| YYYY-MM-DD | K绾挎棩鏈?|
| `period` | str | 鍚?| 1m/5m/15m/30m/60m/daily/weekly | K绾垮懆鏈?|
| `open` | float | 鏄?| > 0 | 寮€鐩樹环 |
| `high` | float | 鏄?| >= open | 鏈€楂樹环 |
| `low` | float | 鏄?| <= open, > 0 | 鏈€浣庝环 |
| `close` | float | 鏄?| > 0 | 鏀剁洏浠?|
| `volume` | int | 鍚?| >= 0 | 鎴愪氦閲?浠? |
| `amount` | float | 鍚?| >= 0 | 鎴愪氦棰?鍏? |

---

### 5.29 鏂伴椈璧勮 (news)

**JSON 绀轰緥锛?*
```json
{
  "source": "eastmoney",
  "data_type": "news",
  "symbol": "600000.SH",
  "timestamp": "2024-01-15T10:30:00Z",
  "payload": {
    "title": "娴﹀彂閾惰2023骞村墠涓夊搴﹀噣鍒╂鼎鍚屾瘮澧為暱8.5%",
    "content": "娴﹀彂閾惰鎶湶2023骞村墠涓夊搴︿笟缁╅鍛?..",
    "url": "https://example.com/news/12345",
    "publish_time": "2024-01-15T09:30:00+08:00",
    "source": "涓滄柟璐㈠瘜",
    "tags": ["涓氱哗", "閾惰"],
    "sentiment_score": 0.35
  }
}
```

**瀛楁瀹氫箟锛?*

| 瀛楁 | 绫诲瀷 | 蹇呭～ | 鑼冨洿绾︽潫 | 璇存槑 |
|------|------|------|---------|------|
| `title` | str | 鏄?| 闀垮害1-500 | 鏂伴椈鏍囬 |
| `content` | str | 鍚?| 闀垮害1-10000 | 鏂伴椈鍐呭鎽樿 |
| `url` | str | 鍚?| URL鏍煎紡 | 鍘熸枃閾炬帴 |
| `publish_time` | str | 鏄?| ISO 8601 | 鍙戝竷鏃堕棿 |
| `source` | str | 鏄?| 闀垮害1-50 | 鏉ユ簮濯掍綋 |
| `tags` | List[str] | 鍚?| 姣忛」1-20瀛?| 鏍囩鍒楄〃 |
| `sentiment_score` | float | 鍚?| -1 ~ 1 | 鎯呮劅寰楀垎(-1鎮茶~1涔愯) |

---

### 5.30 甯傚満鎯呯华 (sentiment)

**JSON 绀轰緥锛?*
```json
{
  "source": "akshare",
  "data_type": "sentiment",
  "symbol": "*",
  "timestamp": "2024-01-15T16:00:00Z",
  "payload": {
    "date": "2024-01-15",
    "sentiment_score": 0.25,
    "sentiment_label": "涔愯",
    "fear_greed_index": 62,
    "social_volume": 150000,
    "news_sentiment": 0.30
  }
}
```

**瀛楁瀹氫箟锛?*

| 瀛楁 | 绫诲瀷 | 蹇呭～ | 鑼冨洿绾︽潫 | 璇存槑 |
|------|------|------|---------|------|
| `date` | str | 鏄?| YYYY-MM-DD | 鏃ユ湡 |
| `sentiment_score` | float | 鏄?| -1 ~ 1 | 缁煎悎鎯呯华寰楀垎(-1鎮茶~1涔愯) |
| `sentiment_label` | str | 鏄?| 鏋佸害鎮茶/鎮茶/涓€?涔愯/鏋佸害涔愯 | 鎯呯华鏍囩 |
| `fear_greed_index` | int | 鍚?| 0 ~ 100 | 鎭愭儳璐┆鎸囨暟(0鏋佸害鎭愭儳~100鏋佸害璐┆) |
| `social_volume` | int | 鍚?| >= 0 | 绀句氦璁ㄨ閲?褰撴棩甯栧瓙鏁? |
| `news_sentiment` | float | 鍚?| -1 ~ 1 | 鏂伴椈鎯呯华寰楀垎 |

---

### 5.31 绀句氦鏁版嵁 (social)

**JSON 绀轰緥锛?*
```json
{
  "source": "eastmoney",
  "data_type": "social",
  "symbol": "600000.SH",
  "timestamp": "2024-01-15T14:30:00Z",
  "payload": {
    "post_id": "em_guba_20240115_001",
    "content": "娴﹀彂閾惰浼板€间綆锛屽垎绾㈢ǔ瀹氾紝鍊煎緱闀挎湡鎸佹湁...",
    "publish_time": "2024-01-15T14:25:00+08:00",
    "author": "浠峰€兼姇璧勮€?,
    "likes": 128,
    "comments": 35,
    "shares": 12,
    "sentiment_score": 0.65
  }
}
```

**瀛楁瀹氫箟锛?*

| 瀛楁 | 绫诲瀷 | 蹇呭～ | 鑼冨洿绾︽潫 | 璇存槑 |
|------|------|------|---------|------|
| `post_id` | str | 鏄?| 闀垮害1-100 | 甯栧瓙鍞竴ID |
| `content` | str | 鏄?| 闀垮害1-5000 | 甯栧瓙鍐呭 |
| `publish_time` | str | 鏄?| ISO 8601 | 鍙戝竷鏃堕棿 |
| `author` | str | 鏄?| 闀垮害1-50 | 浣滆€呮樀绉?|
| `likes` | int | 鍚?| >= 0 | 鐐硅禐鏁?|
| `comments` | int | 鍚?| >= 0 | 璇勮鏁?|
| `shares` | int | 鍚?| >= 0 | 杞彂鏁?|
| `sentiment_score` | float | 鍚?| -1 ~ 1 | 鎯呮劅寰楀垎(-1鎮茶~1涔愯) |

---

## 鍏€佹暟鎹被鍨嬩笌浠ｇ爜妯″瀷鏄犲皠

| data_type | Python 妯″瀷绫?| 宸ュ巶鍑芥暟 | 妯″潡璺緞 |
|-----------|-------------|---------|----------|
| `quote` | `StockQuote` | `create_stock_quote()` | `finance_toolkit/models/stock_models.py` |
| `kline` | `KLine` | `create_kline()` | `finance_toolkit/models/stock_models.py` |
| `financial` | `StockFinancial` | `create_financial()` | `finance_toolkit/models/stock_models.py` |
| `dividend` | `Dividend` | `create_dividend()` | `finance_toolkit/models/stock_models.py` |
| `lhb` | (payload鐩村嚭) | - | `finance_toolkit/models/stock_models.py` |
| `northbound` | `NorthboundFlow` | `create_northbound_flow()` | `finance_toolkit/models/stock_models.py` |
| `stock_basic` | (payload鐩村嚭) | - | `finance_toolkit/models/stock_models.py` |
| `bond_yield` | `BondYield` | `create_bond_yield()` | `finance_toolkit/models/bond_models.py` |
| `bond_quote` | `BondQuote` | `create_bond_quote()` | `finance_toolkit/models/bond_models.py` |
| `convertible` | `ConvertibleBond` | `create_convertible_bond()` | `finance_toolkit/models/bond_models.py` |
| `bond_info` | `BondInfo` | `create_bond_info()` | `finance_toolkit/models/bond_models.py` |
| `fund_nav` | `FundNav` | `create_fund_nav()` | `finance_toolkit/models/fund_models.py` |
| `fund_holdings` | `FundHolding` | `create_fund_holding()` | `finance_toolkit/models/fund_models.py` |
| `fund_rank` | `FundRank` | `create_fund_rank()` | `finance_toolkit/models/fund_models.py` |
| `fund_info` | `FundInfo` | `create_fund_info()` | `finance_toolkit/models/fund_models.py` |
| `futures_quote` | `FuturesQuote` | `create_futures_quote()` | `finance_toolkit/models/futures_models.py` |
| `futures_kline` | `FuturesKLine` | `create_futures_kline()` | `finance_toolkit/models/futures_models.py` |
| `futures_position` | `FuturesPosition` | `create_futures_position()` | `finance_toolkit/models/futures_models.py` |
| `futures_info` | `FuturesInfo` | `create_futures_info()` | `finance_toolkit/models/futures_models.py` |
| `index_quote` | (payload鐩村嚭) | - | `finance_toolkit/models/stock_models.py` |
| `index_kline` | (payload鐩村嚭) | - | `finance_toolkit/models/stock_models.py` |
| `macro_gdp` | (payload鐩村嚭) | - | - |
| `macro_cpi` | (payload鐩村嚭) | - | - |
| `macro_pmi` | (payload鐩村嚭) | - | - |
| `forex_quote` | (payload鐩村嚭) | - | - |
| `crypto_quote` | (payload鐩村嚭) | - | - |
| `etf_quote` | (payload鐩村嚭) | - | - |
| `etf_kline` | (payload鐩村嚭) | - | - |
| `news` | (payload鐩村嚭) | - | - |
| `sentiment` | (payload鐩村嚭) | - | - |
| `social` | (payload鐩村嚭) | - | - |

**璇存槑锛?* 鏍囨敞 `(payload鐩村嚭)` 鐨勬暟鎹被鍨嬬洰鍓嶆棤涓撶敤 dataclass锛岀洿鎺ュ皢 payload 鍐欏叆 `FinanceData`銆傚悗缁彲鎵╁睍瀵瑰簲妯″瀷绫汇€?

---

## 涓冦€佸瓧娈靛畬鏁存€ф鏌ヨ鍒?

### 7.1 蹇呭～瀛楁鏍￠獙

鎵€鏈夋暟鎹被鍨嬪湪鍐欏叆鍓嶅繀椤绘弧瓒充互涓嬪畬鏁存€ц姹傦細

| 鏍￠獙椤?| 瑙勫垯 |
|--------|------|
| **椤跺眰蹇呭～** | `source`, `data_type`, `symbol`, `timestamp`, `payload` 涓嶅緱涓虹┖ |
| **payload闈炵┖** | `payload` 瀛楀吀鑷冲皯鍖呭惈璇ョ被鍨嬪畾涔夎〃涓爣娉ㄤ负"鏄?鐨勫繀濉瓧娈?|
| **绗﹀彿鏍煎紡** | `symbol` 蹇呴』绗﹀悎绗洓鑺傚畾涔夌殑鏍囧噯鍖栨牸寮?|
| **鏃堕棿鏍煎紡** | `timestamp` 蹇呴』鏄?ISO 8601 鏍煎紡 UTC 鏃堕棿鎴?|
| **鏁版嵁婧愭湁鏁?* | `source` 蹇呴』鏄涓夎妭鏋氫妇琛ㄤ腑瀹氫箟鐨勫悎娉曞€?|
| **鏁版嵁绫诲瀷鏈夋晥** | `data_type` 蹇呴』鏄涓夎妭鏋氫妇琛ㄤ腑瀹氫箟鐨勫悎娉曞€?|

### 7.2 鑼冨洿绾︽潫鏍￠獙

| 鏍￠獙椤?| 瑙勫垯 |
|--------|------|
| **鏁板€艰寖鍥?* | float/int 瀛楁蹇呴』绗﹀悎瀵瑰簲琛ㄧ殑鑼冨洿绾︽潫 |
| **閫昏緫涓€鑷?* | `high >= open >= low`锛宍close > 0`锛宍accumulated_nav >= nav` 绛?|
| **鍏宠仈璁＄畻** | `change_pct 鈮?(close - pre_close) / pre_close * 100` (瀹瑰樊卤0.01%) |
| **鏃跺尯缁熶竴** | 鎵€鏈夋椂闂村瓧娈典娇鐢?UTC锛屾樉绀烘椂鍙浆鎹㈡湰鍦版椂鍖?|

### 7.3 璐ㄩ噺璇勫垎寤鸿

| 鍦烘櫙 | quality_score |
|------|--------------|
| 鎵€鏈夊繀濉瓧娈靛畬鏁达紝鏃犲紓甯稿€?| `0.95 ~ 1.0` |
| 閮ㄥ垎鍙€夊瓧娈电己澶?| `0.80 ~ 0.95` |
| 鏁版嵁鏉ユ簮寤惰繜瓒呰繃1澶?| `0.60 ~ 0.80` |
| 瀛樺湪寮傚父鍊兼垨閫昏緫鐭涚浘 | `0.40 ~ 0.60` |
| 鏁版嵁涓ラ噸缂哄け鎴栨潵婧愪笉鍙潬 | `< 0.40` (鏍囪涓轰綆璐ㄩ噺) |

---

## 鍏€佸閲忔洿鏂扮瓥鐣?

| 鏁版嵁绫诲瀷 | 鏇存柊棰戠巼 | 鍘婚噸閿?| 鍙樻洿妫€娴嬭鍒?|
|---------|---------|--------|-------------|
| `quote` | 瀹炴椂(浜ゆ槗鏃舵姣?绉? | symbol + timestamp | timestamp 涓ユ牸閫掑 |
| `kline` | 鏃ョ粓(鐩樺悗鎵归噺) | symbol + date | date 涓ユ牸閫掑 |
| `financial` | 瀛ｆ姤鍙戝竷鍚?4h鍐?| symbol + report_date | report_date 涓ユ牸閫掑 |
| `dividend` | 鍏憡鍙戝竷鍚庡疄鏃?| symbol + record_date | record_date 涓ユ牸閫掑 |
| `northbound` | 浜ゆ槗鏃ユ敹鐩樺悗 | date | date 涓ユ牸閫掑 |
| `bond_yield` | 姣忔棩鐩樺悗 | date + bond_type | date 涓ユ牸閫掑 |
| `fund_nav` | 鍩洪噾鍑€鍊煎叕甯冨悗瀹炴椂 | fund_code + nav_date | nav_date 涓ユ牸閫掑 |
| `futures_quote` | 瀹炴椂(浜ゆ槗鏃舵) | contract_code + timestamp | timestamp 涓ユ牸閫掑 |
| `macro_*` | 鎸夊彂甯冨懆鏈?鏈?瀛? | quarter/date | 鏃堕棿瀛楁涓ユ牸閫掑 |
| `news/sentiment/social` | 瀹炴椂娴佸紡閲囬泦 | post_id / news_id | post_id 鍏ㄥ眬鍞竴 |

---

## 涔濄€佸彉鏇磋褰?

| 鐗堟湰 | 鏃ユ湡 | 鍙樻洿璇存槑 |
|------|------|----------|
| v1.0 | 2024-01-15 | 鍒濆鐗堟湰锛屽畾涔?FinanceData 鏍稿績缁撴瀯鍜岃偂绁?鍊哄埜/鍩洪噾/鏈熻揣鍩虹绫诲瀷 |
| v1.1 | 2024-01-20 | 鏂板瀹忚( GDP/CPI/PMI )銆佸姹囥€佸姞瀵嗚揣甯佹暟鎹被鍨?|
| v1.2 | 2024-08-09 | 瀹屽杽 JSON Schema 瀹氫箟锛屽鍔犲畬鏁村瓧娈垫牎楠岃鍒欏拰鍛藉悕瑙勮寖 |
| v2.0 | 2026-08-12 | 鎵╁厖璧勮棰嗗煙(news/sentiment/social)锛屾柊澧?ETF 绫诲瀷锛岀粺涓€鏍囩殑浠ｇ爜鏍煎紡瑙勮寖 |

---

## 鍗併€佺浉鍏虫枃妗?

- **JSON Schema**: `docs/data-schema.json` 鈥?鏈哄櫒鍙鐨勫畬鏁?Schema 瀹氫箟
- **浣跨敤鎸囧崡**: `docs/data-format-guide.md` 鈥?鍚勫瓧娈靛～鍐欒鑼冨拰绀轰緥
- **楠岃瘉瑙勫垯**: `docs/validation-rules-v2.md` 鈥?鏁版嵁璐ㄩ噺鏍￠獙瑙勫垯娓呭崟
- **鏁版嵁婧愭帴鍙?*: `docs/data-source-interface-spec.md` 鈥?鍚勬暟鎹簮鎺ュ彛瑙勮寖
- **缁熶竴鎶撳彇鍣ㄦ帴鍙?*: `docs/unified-fetcher-interface.md` 鈥?鏍囧噯鎶撳彇鍣ㄥ崗璁?

---

*鏈枃妗ｇ敱 finance-data-toolkit v3.0 鑷姩鐢熸垚锛屾渶鍚庢洿鏂? 2026-08-12*
