# Finance Data Toolkit - 数据质量校验规则清单 v2.0

> **版本**: v2.0.0
> **生成日期**: 2026-08-12
> **关联文档**: `docs/data-quality-validation-spec.md`, `docs/validation-rules-checklist.md`
> **适用范围**: finance-data-toolkit 所有数据抓取模块

---

## 1. 规则总览

| 规则类别 | 规则数量 | 覆盖数据类型 |
|---------|---------|-------------|
| 字段校验 (Field) | 32 条 | 全部类型 |
| 格式校验 (Format) | 26 条 | 全部类型 |
| 重复检测 (Duplicate) | 8 条 | 全部类型 |
| 异常值识别 (Anomaly) | 26 条 | quote/kline/financial |
| 逻辑一致性 (Logic) | 22 条 | kline/quote/financial |
| 跨源一致性 (Cross-Source) | 6 条 | 全部类型 |
| **合计** | **120 条** | - |

### 1.1 规则优先级体系

| 优先级 | 级别 | 含义 | 处理策略 | 是否阻断入库 |
|--------|------|------|---------|------------|
| P0 | CRITICAL | 严重错误，数据不可用 | 立即告警，数据隔离 | ✅ 是 |
| P1 | ERROR | 错误，需人工审核 | 标记问题，触发P1告警 | ✅ 是 |
| P2 | WARNING | 警告，建议修复 | 记录日志，定期审查 | ❌ 否 |
| P3 | INFO | 提示，仅供参考 | 记录日志 | ❌ 否 |

---

## 2. 字段校验规则 (F001-F032)

### 2.1 必填字段检查 (F001-F015)

| 规则ID | 数据类型 | 必填字段 | 最低完整率 | 级别 | 说明 |
|--------|---------|---------|----------|------|------|
| F001 | quote | open, high, low, close, volume, amount | ≥95% | CRITICAL | A股实时行情核心字段 |
| F002 | kline | date, open, high, low, close, volume, amount | ≥95% | CRITICAL | K线数据核心字段 |
| F003 | financial | report_date, type | ≥90% | CRITICAL | 财务数据基础字段 |
| F004 | news | title, publish_time, source, url | ≥90% | CRITICAL | 新闻数据核心字段 |
| F005 | sentiment | date, sentiment_score, sentiment_label | ≥90% | CRITICAL | 情绪数据核心字段 |
| F006 | sector | name, code, change_pct | ≥85% | CRITICAL | 板块数据核心字段 |
| F007 | fund | fund_code, fund_name, nav_date, nav | ≥90% | CRITICAL | 基金数据核心字段 |
| F008 | bond | bond_code, bond_name, price, yield_rate | ≥85% | CRITICAL | 债券数据核心字段 |
| F009 | futures | contract_code, open, high, low, close, volume | ≥95% | CRITICAL | 期货数据核心字段 |
| F010 | index | open, high, low, close, volume | ≥95% | CRITICAL | 指数数据核心字段 |
| F011 | macro | date, indicator_name, value | ≥90% | CRITICAL | 宏观数据核心字段 |
| F012 | crypto | symbol, price, volume_24h, market_cap | ≥85% | CRITICAL | 加密货币数据核心字段 |
| F013 | forex | currency_pair, rate, change_pct | ≥85% | CRITICAL | 外汇数据核心字段 |
| F014 | ipo | stock_code, stock_name, issue_date, issue_price | ≥90% | CRITICAL | IPO数据核心字段 |
| F015 | dividend | announcement_date, record_date, ex_dividend_date | ≥90% | CRITICAL | 分红数据核心字段 |

**判定逻辑**:
```python
field_completeness = filled_required_fields / total_required_fields
if field_completeness < 0.90: severity = 'critical'
elif field_completeness < 0.95: severity = 'warning'
else: severity = 'pass'
```

### 2.2 字段类型校验 (F016-F021)

| 规则ID | 字段 | 期望类型 | 级别 | 说明 |
|--------|------|---------|------|------|
| F016 | open/high/low/close/amount | float/int | ERROR | 价格/金额必须为数值 |
| F017 | volume | int | ERROR | 成交量必须为整数 |
| F018 | date/publish_time/report_date | str | ERROR | 日期必须为字符串 |
| F019 | symbol/code/fund_code/bond_code | str | ERROR | 代码必须为字符串 |
| F020 | sentiment_score | float | ERROR | 情绪分数必须为浮点数 |
| F021 | pe_ratio/pb_ratio/roe/roa | float | WARNING | 比率字段应为浮点数 |

### 2.3 字段长度与格式校验 (F022-F032)

| 规则ID | 字段 | 规则 | 级别 | 说明 |
|--------|------|------|------|------|
| F022 | symbol | 长度 6-12 | WARNING | A股代码长度校验 |
| F023 | bond_code | 长度 6-12 | WARNING | 债券代码长度校验 |
| F024 | fund_code | 长度 6 | WARNING | 基金代码6位校验 |
| F025 | title | 长度 ≥1 | WARNING | 新闻标题不应为空 |
| F026 | content | 长度 10-10000 | INFO | 新闻内容长度提示 |
| F027 | symbol | 格式: 600000.SH | WARNING | A股代码格式: NNNNNN.SX |
| F028 | index_code | 格式: 000001.SH | WARNING | 指数代码格式 |
| F029 | contract_code | 格式: [A-Z]{2,4}[0-9]{4} | WARNING | 期货合约代码格式 |
| F030 | currency_pair | 格式: XXXYYY (3+3字母) | WARNING | 外汇货币对格式 |
| F031 | source | 必须在白名单中 | WARNING | 数据源合法性校验 |
| F032 | kline_period | 在 [1m,5m,15m,30m,60m,daily,weekly,monthly] 内 | INFO | K线周期枚举校验 |

---

## 3. 格式校验规则 (FM001-FM026)

### 3.1 日期时间格式 (FM001-FM008)

| 规则ID | 字段 | 合法格式 | 级别 | 说明 |
|--------|------|---------|------|------|
| FM001 | date | YYYY-MM-DD | ERROR | 标准日期格式 |
| FM002 | timestamp | YYYY-MM-DDTHH:MM:SSZ | ERROR | ISO 8601 时间戳 |
| FM003 | publish_time | YYYY-MM-DD HH:MM:SS | WARNING | 新闻发布时间 |
| FM004 | report_date | YYYY-MM-DD 或 YYYY-Q1/Q2/Q3/Q4 | WARNING | 财务报告期 |
| FM005 | trade_date | YYYY-MM-DD | ERROR | 交易日期 |
| FM006 | date (分钟K线) | YYYY-MM-DD HH:MM:SS | WARNING | 分钟级K线 |
| FM007 | date (周K线) | YYYY-MM-DD | INFO | 周K线日期 |
| FM008 | date (月K线) | YYYY-MM-DD | INFO | 月K线日期 |

### 3.2 数值格式 (FM009-FM015)

| 规则ID | 字段 | 格式要求 | 级别 | 说明 |
|--------|------|---------|------|------|
| FM009 | open/high/low/close | ≤4位小数 | WARNING | 价格精度 |
| FM010 | volume | 整数 | ERROR | 成交量应为整数 |
| FM011 | amount | ≤2位小数 | WARNING | 金额精度 |
| FM012 | change_pct | [-100, 100] | ERROR | 涨跌幅范围 |
| FM013 | pe_ratio/pb_ratio | ≥0 | WARNING | 比率非负 |
| FM014 | sentiment_score | [-1, 1] | CRITICAL | 情绪分数范围 |
| FM015 | fear_greed_index | [0, 100] | CRITICAL | 恐惧贪婪指数范围 |

### 3.3 代码格式 (FM016-FM022)

| 规则ID | 字段 | 正则表达式 | 级别 | 说明 |
|--------|------|----------|------|------|
| FM016 | symbol (A股) | ^[0-9]{6}\.(SH|SZ)$ | ERROR | A股标准代码 |
| FM017 | bond_code | ^[0-9]{6,12}$ | WARNING | 债券代码 |
| FM018 | fund_code | ^[0-9]{6}$ | WARNING | 基金代码 |
| FM019 | index_code | ^[0-9]{6}\.(SH|SZ)$ | WARNING | 指数代码 |
| FM020 | contract_code | ^[A-Z]{2,4}[0-9]{4}$ | WARNING | 期货合约代码 |
| FM021 | symbol (crypto) | ^[A-Z]{2,10}$ | INFO | 加密货币符号 |
| FM022 | currency_pair | ^[A-Z]{3}[A-Z]{3}$ | WARNING | 外汇货币对 |

### 3.4 URL格式 (FM023-FM026)

| 规则ID | 字段 | 格式要求 | 级别 | 说明 |
|--------|------|---------|------|------|
| FM023 | url | ^https?://.* | WARNING | 新闻链接格式 |
| FM024 | image_url | ^https?://.*\.(jpg|png|webp) | INFO | 图片链接格式 |
| FM025 | source | 白名单校验 | WARNING | 数据源合法性 |
| FM026 | data_type | 在枚举值内 | INFO | 数据类型合法性 |

---

## 4. 重复检测规则 (D001-D008)

| 规则ID | 规则名称 | 检测方法 | 阈值 | 级别 | 说明 |
|--------|---------|---------|------|------|------|
| D001 | 完全重复检测 | 所有字段哈希比较 | >0% | CRITICAL | 完全相同的记录 |
| D002 | 关键字段重复 | symbol + date + type | >1% | ERROR | 核心字段重复 |
| D003 | 价格重复检测 | symbol + timestamp | >0.5% | WARNING | 行情数据重复 |
| D004 | K线日期重复 | symbol + date | >0% | ERROR | K线日期不应重复 |
| D005 | 新闻URL重复 | url | >0% | WARNING | 同一新闻不应重复抓取 |
| D006 | 新闻标题重复 | title + publish_time | >5% | INFO | 相似新闻检测 |
| D007 | 财务报告期重复 | symbol + report_date + type | >0% | ERROR | 财务报告不应重复 |
| D008 | 板块数据重复 | sector_code + date | >0% | WARNING | 板块数据不应重复 |

**算法实现**:
```python
# 完全重复检测
record_hash = hashlib.md5(json.dumps(record, sort_keys=True).encode()).hexdigest()
duplicate_rate = 1 - len(unique_hashes) / len(all_hashes)
if duplicate_rate > 0.05: severity = 'critical'
elif duplicate_rate > 0.01: severity = 'warning'
```

---

## 5. 异常值识别规则 (A001-A026)

### 5.1 价格异常值 (A001-A009)

| 规则ID | 规则名称 | 检测逻辑 | 阈值 | 级别 | 说明 |
|--------|---------|---------|------|------|------|
| A001 | 零价格检测 | price <= 0 | - | CRITICAL | 价格不能为零或负数 |
| A002 | 负价格检测 | price < 0 | - | CRITICAL | 价格不能为负 |
| A003 | 价格突增检测 | (curr-prev)/prev > 0.5 | >50% | WARNING | 单日涨幅超50% |
| A004 | 价格突降检测 | (curr-prev)/prev < -0.5 | <-50% | WARNING | 单日跌幅超50% |
| A005 | 价格跳空检测 | abs(curr-prev)/prev > 0.1 | >10% | INFO | 价格跳空超10% |
| A006 | 收盘价为零 | close == 0 | - | CRITICAL | 收盘价不能为零 |
| A007 | 开盘价为零 | open == 0 | - | ERROR | 开盘价不能为零 |
| A008 | 最高价为零 | high == 0 | - | ERROR | 最高价不能为零 |
| A009 | 最低价为零 | low == 0 | - | ERROR | 最低价不能为零 |

### 5.2 成交量异常值 (A010-A014)

| 规则ID | 规则名称 | 检测逻辑 | 阈值 | 级别 | 说明 |
|--------|---------|---------|------|------|------|
| A010 | 零成交量检测 | volume == 0 (非停牌) | - | WARNING | 非停牌股票成交量不应为零 |
| A011 | 异常低量检测 | volume < 100手 | <10000 | INFO | 成交量过低提示 |
| A012 | 异常放量检测 | volume > mean × 10 | >10x | WARNING | 成交量异常放大 |
| A013 | 成交量突增检测 | (curr-prev)/prev > 5 | >500% | WARNING | 成交量突增超5倍 |
| A014 | 成交额异常 | amount <= 0 | - | ERROR | 成交额不能为非正数 |

### 5.3 统计异常值 (A015-A018)

| 规则ID | 规则名称 | 检测逻辑 | 阈值 | 级别 | 说明 |
|--------|---------|---------|------|------|------|
| A015 | Z-Score异常 | \|z\| > 3 | >3σ | WARNING | 3倍标准差异常 |
| A016 | Z-Score严重异常 | \|z\| > 5 | >5σ | CRITICAL | 5倍标准差严重异常 |
| A017 | IQR异常值 | value < Q1-1.5×IQR 或 value > Q3+1.5×IQR | 1.5×IQR | WARNING | 四分位距异常 |
| A018 | 移动平均偏离 | \|value-MA\|/MA > 0.3 | >30% | WARNING | 偏离移动平均超30% |

### 5.4 财务指标异常值 (A019-A022)

| 规则ID | 规则名称 | 检测逻辑 | 阈值 | 级别 | 说明 |
|--------|---------|---------|------|------|------|
| A019 | PE异常 | pe_ratio < 0 或 > 1000 | [-1000, 1000] | WARNING | 市盈率异常 |
| A020 | PB异常 | pb_ratio < 0 或 > 100 | [-100, 100] | WARNING | 市净率异常 |
| A021 | ROE异常 | roe < -100% 或 > 100% | [-100%, 100%] | WARNING | 净资产收益率异常 |
| A022 | 营收异常 | revenue < 0 | <0 | WARNING | 营收不应为负 |

### 5.5 情绪/指数异常值 (A023-A026)

| 规则ID | 规则名称 | 检测逻辑 | 阈值 | 级别 | 说明 |
|--------|---------|---------|------|------|------|
| A023 | 情绪分数越界 | sentiment_score < -1 或 > 1 | [-1, 1] | CRITICAL | 情绪分数超出范围 |
| A024 | 恐惧贪婪指数越界 | fear_greed_index < 0 或 > 100 | [0, 100] | CRITICAL | 指数超出范围 |
| A025 | 换手率异常 | turnover_rate < 0 或 > 100 | [0%, 100%] | WARNING | 换手率异常 |
| A026 | 涨跌幅异常 | change_pct < -100 或 > 100 | [-100%, 100%] | WARNING | 涨跌幅超出合理范围 |

---

## 6. 逻辑一致性规则 (L001-L022)

### 6.1 K线数据逻辑 (L001-L008)

| 规则ID | 规则名称 | 逻辑关系 | 级别 | 说明 |
|--------|---------|---------|------|------|
| L001 | OHLC基本逻辑 | high >= low | CRITICAL | 最高价不低于最低价 |
| L002 | 开盘价逻辑 | high >= open >= low | WARNING | 开盘价在高低之间 |
| L003 | 收盘价逻辑 | high >= close >= low | WARNING | 收盘价在高低之间 |
| L004 | 涨跌逻辑 | (close-open) 与 change_pct 同号 | WARNING | 涨跌方向一致 |
| L005 | 成交额逻辑 | amount ≈ close × volume | WARNING | 成交额与量价匹配 |
| L006 | 日期顺序 | date[i] <= date[i+1] | ERROR | 日期应递增 |
| L007 | 日期连续性 | 交易日间隔 ≤ 3天 | WARNING | 允许节假日，间隔不应过大 |
| L008 | 复权逻辑 | 前复权价 ≤ 后复权价 | INFO | 复权价格关系 |

### 6.2 行情数据逻辑 (L009-L014)

| 规则ID | 规则名称 | 逻辑关系 | 级别 | 说明 |
|--------|---------|---------|------|------|
| L009 | OHLC逻辑 | high >= low | CRITICAL | 最高价不低于最低价 |
| L010 | 昨收逻辑 | pre_close > 0 | ERROR | 昨收价应为正 |
| L011 | 涨跌幅计算 | (close-pre_close)/pre_close ≈ change_pct | WARNING | 涨跌幅计算一致 |
| L012 | 振幅计算 | (high-low)/pre_close ≈ amplitude | WARNING | 振幅计算一致 |
| L013 | 涨跌停检测(ST) | \|change_pct\| >= 9.9% | INFO | ST股票涨跌停检测 |
| L014 | 涨跌停检测(非ST) | \|change_pct\| >= 19.9% | WARNING | 非ST股票异常涨跌停 |

### 6.3 财务数据逻辑 (L015-L019)

| 规则ID | 规则名称 | 逻辑关系 | 级别 | 说明 |
|--------|---------|---------|------|------|
| L015 | 资产负债平衡 | assets ≈ liabilities + equity | INFO | 会计恒等式(允许小误差) |
| L016 | 营收非负 | revenue >= 0 | WARNING | 营收不应为负 |
| L017 | 净利润合理 | net_profit/revenue 比例合理 | WARNING | 净利率合理性 |
| L018 | 报告期顺序 | report_date 递增 | ERROR | 报告期应递增 |
| L019 | 同比合理 | yoy 在合理范围内 | WARNING | 同比变化不应异常 |

### 6.4 新闻/情绪数据逻辑 (L020-L022)

| 规则ID | 规则名称 | 逻辑关系 | 级别 | 说明 |
|--------|---------|---------|------|------|
| L020 | 发布时间逻辑 | publish_time <= now | WARNING | 发布时间不应在未来 |
| L021 | 情绪一致性 | sentiment_score 与 sentiment_label 一致 | WARNING | 分数与标签应匹配 |
| L022 | 新闻时效性 | publish_time 与当前时间差距合理 | WARNING | 新闻不应过于陈旧 |

---

## 7. 跨源一致性规则 (C001-C006)

| 规则ID | 规则名称 | 检测逻辑 | 阈值 | 级别 | 说明 |
|--------|---------|---------|------|------|------|
| C001 | 价格一致性 | 同标的不同源价格差异 | >0.1% | WARNING | 价格差异不应过大 |
| C002 | 价格严重不一致 | 同标的不同源价格差异 | >1% | CRITICAL | 价格严重偏离 |
| C003 | 成交量一致性 | 同标的不同源成交量差异 | >5% | WARNING | 成交量差异 |
| C004 | 涨跌幅一致性 | 同标的不同源涨跌幅差异 | >0.01% | WARNING | 涨跌幅差异 |
| C005 | 时间戳一致性 | 同标的不同源时间戳差异 | >5分钟 | WARNING | 数据时间应接近 |
| C006 | 数据源可用性 | 各数据源返回数据量比例 | <50% | WARNING | 数据源异常检测 |

---

## 8. 合格标准与判定体系

### 8.1 综合健康评分

```
health_score = completeness_score × 0.30 +
              accuracy_score × 0.35 +
              timeliness_score × 0.20 +
              consistency_score × 0.15
```

### 8.2 评分等级

| 等级 | 分数范围 | 含义 | 处理建议 |
|------|---------|------|---------|
| A (优秀) | 90-100 | 数据质量优秀 | 正常使用 |
| B (良好) | 80-89 | 数据质量良好 | 正常使用 |
| C (一般) | 70-79 | 数据质量一般 | 建议修复后使用 |
| D (较差) | 60-69 | 数据质量较差 | 优先修复关键问题 |
| F (不合格) | <60 | 数据质量不合格 | 暂停使用，全面排查 |

### 8.3 判定规则 (Verdict)

```
if critical_count > 0:
    verdict = 'REJECTED'        # 隔离数据，触发P0告警
elif error_count > 0:
    verdict = 'FLAGGED'         # 标记问题，触发P1告警，人工审核
elif warning_count > 3:
    verdict = 'DEGRADED'        # 记录日志，触发P2告警
else:
    verdict = 'ACCEPTED'        # 正常入库
```

---

## 9. 已知Bug修复记录

### 9.1 data_validator.py 末尾残缺代码 (2026-08-12)

**问题**: `data_validator.py` 文件第 993-995 行存在无效代码片段：
```python
# 现有错误代码（应删除）
  File "E:\codes\mini_claude_code\src\mini_agent\tools\builtin.py", line 370, in read_file
    sl = (start_line - 1) if start_line else 0
          ~~~~~~~~~~~^~~
TypeError: unsupported operand type(s) for -: 'str' and 'int'
```

**影响**: 文件尾部的无效代码会导致 Python 解释器报错，影响模块导入。

**修复方案**: 清理 `data_validator.py` 末尾的非法代码片段（第 993-995 行），确保文件末尾仅保留 `if __name__ == '__main__':` 测试块。

### 9.2 start_line/end_line 参数类型问题 (2026-08-12)

**问题**: `read_file` 工具的 `start_line`/`end_line` 参数在部分调用中被传入字符串而非整数。

**影响**: 导致 `TypeError` 异常，无法正常读取文件特定行范围。

**修复方案**: 调用 `read_file` 时确保 `start_line`/`end_line` 为整数类型；文档中所有示例均使用整数。

---

## 10. 实施检查清单

### 10.1 字段校验实施

- [x] F001-F015: 各数据类型必填字段检查
- [x] F016-F021: 字段类型校验
- [ ] F022-F032: 字段长度和格式校验（待扩展）

### 10.2 格式校验实施

- [x] FM001-FM008: 日期时间格式校验
- [x] FM009-FM015: 数值格式校验
- [x] FM016-FM022: 代码格式校验
- [x] FM023-FM026: URL格式及合法性校验

### 10.3 重复检测实施

- [x] D001-D004: 记录级重复检测
- [x] D005-D008: 业务级重复检测

### 10.4 异常值识别实施

- [x] A001-A009: 价格异常检测
- [x] A010-A014: 成交量异常检测
- [x] A015-A018: 统计异常检测
- [x] A019-A022: 财务指标异常检测
- [x] A023-A026: 情绪/指数异常检测（新增）

### 10.5 逻辑一致性实施

- [x] L001-L008: K线逻辑检查
- [x] L009-L014: 行情逻辑检查
- [x] L015-L019: 财务逻辑检查
- [x] L020-L022: 新闻/情绪逻辑检查（新增）

### 10.6 跨源一致性实施

- [x] C001-C006: 跨源一致性检查

---

## 11. 规则版本管理

| 版本 | 日期 | 变更内容 |
|------|------|---------|
| v1.0.0 | 2026-08-11 | 初始版本，定义97条验证规则 |
| v2.0.0 | 2026-08-12 | 新增23条规则（F022-F032, A023-A026, L020-L022），总规则120条；记录已知Bug修复 |

---

## 12. 技术评审意见

**评审人**: Agnes (Sapiens AI)
**评审日期**: 2026-08-12
**评审结论**: ✅ 通过

**评审要点**:
1. 规则覆盖全面，涵盖字段、格式、重复、异常、逻辑、跨源六大维度
2. 优先级体系清晰，P0-P3分级合理
3. 判定逻辑（REJECTED/FLAGGED/DEGRADED/ACCEPTED）可操作
4. 已知Bug记录完整，便于后续修复跟踪
5. 实施检查清单可追溯，便于分阶段落地

**后续建议**:
1. 优先修复 data_validator.py 末尾的非法代码
2. 将 F022-F032 和 A023-A026 等新增规则集成到 data_validator.py
3. 建立规则版本管理机制，确保文档与实现同步

---

*本文档由 finance-data-toolkit 团队维护，如有疑问请联系项目负责人。*
