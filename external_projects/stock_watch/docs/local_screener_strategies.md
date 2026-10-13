# 本地选股策略说明

本文档介绍 stock_watch 项目支持的本地选股策略，所有策略均基于 `data/kline.db` 本地数据库，无需网络连接。

---

## 策略概览

| 策略名称 | 文件 | 核心逻辑 | 适用场景 |
|---------|------|---------|---------|
| 多因子技术指标 | `run_local_screener.py` | MA/MACD/RSI/KDJ/量能/布林带综合评分 | 趋势跟踪、技术面选股 |
| 价格位置分析 | `run_price_position_screener.py` | 历史价格区间位置 + 风险收益比 | 超跌反弹、风险厌恶型 |

---

## 1. 多因子技术指标选股（run_local_screener.py）

### 策略原理

基于经典技术分析指标，计算每只股票在最近 N 天内的技术信号，按综合评分排序。

**技术指标列表：**

| 指标 | 信号类型 | 评分范围 | 说明 |
|------|---------|---------|------|
| MA 均线 | 金叉/死叉 | ±8分 | 短期均线上穿/下穿长期均线 |
| MACD | 金叉/死叉 | ±8分 | DIF 与 DEA 交叉 |
| RSI | 超买/超卖 | ±5分 | RSI(14) > 70 超买，< 30 超卖 |
| KDJ | 金叉/死叉/极值 | ±6分 | K/D 线交叉 + J 线极值判断 |
| 量能 | 放量突破 | ±6分 | 成交量 > 2倍均量 |
| 布林带 | 挤压突破 | ±7分 | 布林带宽度低位后价格突破 |

### 使用方法

```bash
# 基础用法：从 kline.db 全量标的筛选 Top 10
python entrypoints/run_local_screener.py --all

# 指定统计天数和输出数量
python entrypoints/run_local_screener.py --all --top-n 30 --lookback-days 120

# 从候选池筛选（algo 或 manual）
python entrypoints/run_local_screener.py --pool-type algo --top-n 20

# 指定特定标的
python entrypoints/run_local_screener.py --codes 600519 000001 300750
```

### 输出字段

- `symbol`: 股票代码
- `total_score`: 综合评分（所有信号得分之和）
- `signal_count`: 触发信号数量
- `latest_close`: 最新收盘价
- `top_signals`: 主要信号列表（含名称、分类、分数、说明）

### 示例输出

```
✅ 选股完成：共 30 只候选股
  1. 603855  评分=34.0  收盘=17.32  信号数=5
  2. 688156  评分=34.0  收盘=25.40  信号数=5
  ...
```

---

## 2. 价格位置分析选股（run_price_position_screener.py）

### 策略原理

分析股票在当前统计周期内的价格位置，计算风险收益比，筛选出"接近历史低点、上涨空间大"的强势股。

**核心公式：**

```
上涨空间 = (历史最高价 - 当前价) / 当前价
下跌空间 = (当前价 - 历史最低价) / 当前价
价格分位 = 当前价 / 历史最高价
预估分数 = 上涨空间 - 2 × 下跌空间
```

**策略特点：**
- 风险厌恶型权重（下跌风险乘数 2.0）
- 优先选择下跌空间小、上涨空间大的标的
- 适合超跌反弹、价值投资场景

### 使用方法

```bash
# 基础用法：全量筛选 Top 100（默认 200 天）
python entrypoints/run_price_position_screener.py --all

# 自定义统计天数
python entrypoints/run_price_position_screener.py --all --lookback 300

# 限制输出数量
python entrypoints/run_price_position_screener.py --all --top-n 50

# 使用候选池
python entrypoints/run_price_position_screener.py --pool-type manual
```

### 输出字段

- `symbol`: 股票代码
- `estimated_score`: 预估分数（上涨空间 - 2×下跌空间）
- `latest_close`: 最新收盘价
- `hist_high`: 统计周期内最高价
- `hist_low`: 统计周期内最低价
- `price_percentile`: 价格分位（0~1，越接近1越接近历史高点）
- `upside_pct`: 涨幅空间（百分比）
- `downside_pct`: 跌幅空间（百分比）
- `interpretation`: 解读（强势/中性/超跌）

### 示例输出

```
✅ 选股完成：共 30 只候选股
策略：价格位置分析（统计 200 天，风险厌恶权重 2.0）

排名   代码       预估分数       收盘         价格分位       解读
----------------------------------------------------------------------
1    301139   25.232     0.82       3.8%       超跌
2    002731   21.442     0.77       4.5%       超跌
...
```

---

## 策略对比

| 维度 | 多因子技术指标 | 价格位置分析 |
|------|--------------|------------|
| 数据要求 | ≥60 天 | ≥200 天（可配置） |
| 信号类型 | 技术面事件驱动 | 价格位置静态分析 |
| 适用风格 | 趋势跟踪、短线交易 | 超跌反弹、价值投资 |
| 风险偏好 | 中性 | 风险厌恶 |
| 输出数量 | 默认 10 只 | 默认 100 只 |

---

## 报告输出

两种策略均生成两类报告文件：

```
reports/
├── screener/                    # 多因子技术指标报告
│   └── local_screener_20261013_081614.md
│   └── local_screener_20261013_081614.json
└── price_position/              # 价格位置分析报告
    └── price_position_20261013_085008.md
    └── price_position_20261013_085008.json
```

- **Markdown 报告**：人类可读，含表格和解读
- **JSON 结果**：机器可读，可用于后续分析或导入

---

## 扩展新策略

如需添加新的选股策略，请参考以下结构：

1. **创建策略模块**：`stock_watch/your_strategy.py`
   ```python
   def analyze_your_strategy(db, symbols, **kwargs):
       results = []
       for symbol in symbols:
           df = db.get_kline(symbol, days=kwargs.get('lookback_days', 200))
           # 你的分析逻辑...
           results.append({...})
       return results
   ```

2. **创建命令行入口**：`entrypoints/run_your_strategy_screener.py`
   - 复用 `DailyKlineDB` 和已有的辅助函数
   - 保持与现有脚本一致的日志和输出格式

3. **注册到文档**：更新本文档

---

## 注意事项

1. **数据完整性**：策略依赖 `data/kline.db`，确保已运行 `python -m stock_watch.daily_kline_db --all` 完成全量数据下载
2. **运行方式**：必须从项目根目录运行，或使用 `PYTHONPATH` 环境变量
3. **统计周期**：价格位置分析默认使用 200 天数据，可根据需要调整（`--lookback` 参数）
4. **新股处理**：数据不足 200 天的新股会被自动跳过
