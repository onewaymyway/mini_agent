# 常用数据源 API 端点速查

> 仅列出高频使用的核心端点，完整参数/返回字段/错误码/限流策略请查阅 `references/full-api-docs/`

---

## 1. 东方财富

### 1.1 实时行情
```
GET https://push2.eastmoney.com/api/qt/stock/get
```
| 参数 | 必填 | 说明 |
|------|------|------|
| `secid` | 是 | 代码格式：`1.600000`(沪市) / `0.000001`(深市) |
| `fields` | 否 | 返回字段，逗号分隔，如 `f43,f44,f45,f46,f47,f48,f49,f50,f51,f52` |
| `ut` | 否 | 固定 `fa5fd1943c7b386f172d6893dbfba10b` |
| `fltt` | 否 | 复权类型：`1`前复权 `2`后复权 `0`不复权 |

**返回字段速查**：
- `f43` 今开 | `f44` 最高 | `f45` 最低 | `f46` 今收 | `f47` 成交量 | `f48` 成交额
- `f49` 振幅 | `f50` 换手率 | `f51` 市盈率(动) | `f52` 市净率
- `f57` 涨跌幅 | `f58` 涨跌额 | `f60` 总市值 | `f61` 流通市值

### 1.2 历史 K 线
```
GET https://push2his.eastmoney.com/api/qt/stock/kline/get
```
| 参数 | 必填 | 说明 |
|------|------|------|
| `secid` | 是 | 同上 |
| `klt` | 是 | 周期：`101`日 `102`周 `103`月 `1`分钟 `5`5分钟 `15`15分钟 `30`30分钟 `60`60分钟 |
| `fqt` | 否 | 复权：`0`不复权 `1`前复权 `2`后复权 |
| `beg` / `end` | 否 | 开始/结束日期，格式 `20240101` |
| `fields1` | 否 | `f1,f2,f3,f4,f5,f6` (基础信息) |
| `fields2` | 否 | `f51,f52,f53,f54,f55,f56,f57,f58` (K线数据) |

**K线字段**：`f51`日期 `f52`开盘 `f53`收盘 `f54`最高 `f55`最低 `f56`成交量 `f57`成交额 `f58`振幅

### 1.3 股吧帖子列表
```
GET https://guba.eastmoney.com/interface/GetData.aspx
```
| 参数 | 必填 | 说明 |
|------|------|------|
| `path` | 是 | 接口路径，如 `reply/api/Reply/ArticleNewList` |
| `p` | 否 | 页码 |
| `ps` | 否 | 每页条数 |
| `type` | 否 | 板块类型 |
| `sort` | 否 | 排序：`1`最新 `2`热度 |

**签名机制**：需携带 `sign` 参数，算法为 `md5(path + param_str + secret)`，secret 需逆向获取

### 1.4 龙虎榜
```
GET https://datacenter-web.eastmoney.com/api/data/v1/get
```
| 参数 | 必填 | 说明 |
|------|------|------|
| `reportName` | 是 | `RPT_BILLBOARD_DAILY` |
| `columns` | 否 | 返回列 |
| `filter` | 否 | 过滤条件，如 `(TRADE_DATE>='2024-01-01')` |
| `pageNumber` / `pageSize` | 否 | 分页 |
| `sortColumns` / `sortTypes` | 否 | 排序 |

---

## 2. 同花顺

### 2.1 实时行情
```
GET https://d.10jqka.com.cn/v2/line/hs_<code>/last.js
```
- 返回 JS 变量，需正则提取 JSON
- 需 Referer: `https://stockpage.10jqka.com.cn/`

### 2.2 历史 K 线
```
GET https://d.10jqka.com.cn/v2/line/hs_<code>/<period>.js
```
- `period`: `01`日 `02`周 `03`月 `05`5分钟 `15`15分钟 `30`30分钟 `60`60分钟

### 2.3 财务报表
```
GET https://basic.10jqka.com.cn/api/stock/finance/<code>.html
```
- 返回 HTML，需解析表格

---

## 3. 新浪财经

### 3.1 实时行情
```
GET https://hq.sinajs.cn/list=<code_list>
```
- `code_list`: 逗号分隔，格式 `sh600000,sz000001`
- 返回格式：`var hq_str_sh600000="浦发银行,10.50,10.45,...";`
- 字段：名称、今开、昨收、当前价、最高、最低、成交量(手)、成交额(万)、日期、时间...

### 3.2 历史 K 线
```
GET https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData
```
| 参数 | 必填 | 说明 |
|------|------|------|
| `symbol` | 是 | 如 `sh600000` |
| `scale` | 是 | 周期：`5`5分 `15`15分 `30`30分 `60`60分 `240`日 `1200`周 `7200`月 |
| `ma` | 否 | 均线，如 `ma5,ma10,ma20` |
| `datalen` | 否 | 返回条数 |

### 3.3 新闻列表
```
GET https://feed.mix.sina.com.cn/api/roll/get
```
| 参数 | 必填 | 说明 |
|------|------|------|
| `pageid` | 是 | 页面ID |
| `lid` | 是 | 栏目ID |
| `k` | 否 | 关键词 |
| `num` | 否 | 条数 |
| `page` | 否 | 页码 |

---

## 4. 雪球

### 4.1 实时行情
```
GET https://stock.xueqiu.com/v5/stock/realtime/quotec.json
```
| 参数 | 必填 | 说明 |
|------|------|------|
| `symbol` | 是 | 如 `SH600000,SZ000001` |

**需登录 Cookie**：`xq_a_token`、`xqat`、`xq_r_token`

### 4.2 历史 K 线
```
GET https://stock.xueqiu.com/v5/stock/chart/kline.json
```
| 参数 | 必填 | 说明 |
|------|------|------|
| `symbol` | 是 | 同上 |
| `period` | 是 | `1d`日 `1w`周 `1M`月 `1m`1分 `5m`5分 `15m`15分 `30m`30分 `60m`60分 |
| `type` | 否 | `before`前复权 `after`后复权 `normal`不复权 |
| `count` | 否 | 条数 |
| `begin` / `end` | 否 | 时间戳(毫秒) |

### 4.3 讨论/舆情
```
GET https://xueqiu.com/statuses/search.json
```
| 参数 | 必填 | 说明 |
|------|------|------|
| `q` | 是 | 关键词 |
| `count` | 否 | 条数 |
| `page` | 否 | 页码 |
| `type` | 否 | `11`股票 `12`基金 `13`债券 |

---

## 5. AKShare (Python 库)

```python
import akshare as ak

# 实时行情
ak.stock_zh_a_spot_em()

# 历史 K 线
ak.stock_zh_a_hist(symbol="600000", period="daily", start_date="20240101", end_date="20241231", adjust="qfq")

# 财务报表
ak.stock_financial_report_sina(stock="600000", symbol="资产负债表")

# 龙虎榜
ak.stock_lhb_detail_em(symbol="600000")

# 北向资金
ak.stock_hsgt_hist_em(symbol="沪股通")
```

**常用模块**：`stock_zh_a_*` A股、`stock_hk_*` 港股、`stock_us_*` 美股、`fund_*` 基金、`bond_*` 债券、`macro_*` 宏观、`futures_*` 期货

---

## 6. Tushare Pro

```python
import tushare as ts
pro = ts.pro_api('your_token')

# 基础信息
pro.stock_basic(exchange='', list_status='L', fields='ts_code,symbol,name,area,industry,list_date')

# 日线行情
pro.daily(ts_code='600000.SH', start_date='20240101', end_date='20241231')

# 财务报表
pro.income(ts_code='600000.SH', period='20240331')
pro.balancesheet(ts_code='600000.SH', period='20240331')
pro.cashflow(ts_code='600000.SH', period='20240331')

# 资金流向
pro.moneyflow(ts_code='600000.SH', start_date='20240101', end_date='20241231')

# 龙虎榜
pro.top_inst(trade_date='20240101')
```

**积分限制**：基础积分 120 分/分钟，高频接口需更高积分

---

## 7. 财联社

### 7.1 电报/快讯
```
GET https://www.cls.cn/api/sw?app=CailianpressWeb&os=web&sv=8.4.6
```
| 参数 | 必填 | 说明 |
|------|------|------|
| `last_time` | 否 | 上一条时间戳 |
| `limit` | 否 | 条数 |
| `type` | 否 | `telegram`电报 `article`深度 |

### 7.2 深度文章
```
GET https://www.cls.cn/api/telegraphs
```
| 参数 | 必填 | 说明 |
|------|------|------|
| `page` | 否 | 页码 |
| `size` | 否 | 每页条数 |
| `type` | 否 | 类型筛选 |

---

## 8. 华尔街见闻

```
GET https://api-one-wscn.awtmt.com/apiv1/content/articles
```
| 参数 | 必填 | 说明 |
|------|------|------|
| `channel` | 否 | 频道：`global` `china` `stock` `fund` |
| `limit` | 否 | 条数 |
| `cursor` | 否 | 游标 |
| `accept` | 否 | `application/json` |

**需 Header**：`Authorization: Bearer <token>` (登录后获取)

---

## 9. Wind / Choice (需终端授权)

```python
# Wind
from WindPy import w
w.start()
w.wsd("600000.SH", "open,high,low,close,volume", "2024-01-01", "2024-12-31", "")

# Choice
import choice as cs
cs.start()
cs.css("600000.SH", "open,high,low,close,volume", "2024-01-01", "2024-12-31")
```

---

## 10. 限流与反爬汇总表

| 数据源 | 限流策略 | 反爬等级 | 推荐方案 |
|--------|----------|----------|----------|
| 东方财富 | IP+Cookie 限流，约 60 req/min | 中 | 代理池 + Cookie 池 + 签名逆向 |
| 同花顺 | Referer 检查 + JS 混淆 | 中高 | CDP 浏览器模式 |
| 新浪财经 | 无明显限流，高频会封 IP | 低 | 直接 requests + 适当延迟 |
| 雪球 | 必须登录，Cookie 失效快 | 高 | CDP 登录维持 + 代理轮换 |
| 财联社 | Token 机制，频率限制 | 中 | Token 缓存 + 定时刷新 |
| 华尔街见闻 | Bearer Token，严格限流 | 中高 | 登录获取 Token + 代理 |
| AKShare | 无限流(本地库) | 无 | 直接调用 |
| Tushare | 积分制，120分/分钟 | 无 | 积分管理 + 缓存 |
| Wind/Choice | 终端授权，无网络限流 | 无 | 官方 SDK |

---

## 11. 常用错误码速查

| 数据源 | 错误码 | 含义 | 处理 |
|--------|--------|------|------|
| 东方财富 | 403 | IP 封禁/签名错误 | 换代理、更新签名算法 |
| 东方财富 | 返回空数据 | 参数错误/股票代码格式错 | 检查 secid 格式 |
| 同花顺 | 返回乱码/空 | Referer 缺失/JS 解析失败 | 加 Referer、用 CDP |
| 雪球 | 401/403 | Cookie 失效/未登录 | 重新登录获取 Cookie |
| 财联社 | 401 | Token 过期 | 刷新 Token |
| Tushare | -200 | 积分不足 | 等待积分恢复/升级权限 |
| AKShare | 各类异常 | 接口变更/网络错误 | 更新库版本、加重试 |

---

> **完整 API 手册**请查阅 `references/full-api-docs/` 目录下的各数据源详细文档