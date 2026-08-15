# FinanceData 数据验证中间件使用指南

## 概述

`validators.py` 提供了三层验证体系，确保 `FinanceData` 对象符合统一契约规范。

## 架构

```
FinanceDataValidator（入口）
├── FieldValidator   (L1)  FinanceData 必填字段完整性校验
├── TypeValidator    (L2)  payload 内部字段 Python 类型校验
└── RangeValidator   (L3)  payload 数值字段业务范围校验
    └── DataValidator (底层) 97条规则全量验证（可选）
```

## 使用示例

### 基本用法

```python
from finance_toolkit.cleaning.validators import validate_finance_data
from finance_toolkit.models.finance_data import FinanceData
from finance_toolkit.plugins.types import DataType

# 创建 FinanceData 对象
fd = FinanceData(
    source='akshare',
    data_type=DataType.QUOTE,
    symbol='600000.SH',
    timestamp='2026-08-15T10:30:00+08:00',
    payload={
        'open': 10.5,
        'high': 11.2,
        'low': 10.3,
        'close': 11.0,
        'volume': 1000000,
        'amount': 11000000,
    },
)

# 验证
result = validate_finance_data(fd)
print(result.summary())
# FinanceData 验证结果 [✓] 健康评分: 100.0/100
#   字段校验: 0 个问题
#   类型校验: 0 个问题
#   范围校验: 0 个问题
```

### 批量验证

```python
from finance_toolkit.cleaning.validators import validate_finance_data_batch

items = [fd1, fd2, fd3]
results = validate_finance_data_batch(items)
for r in results:
    print(r.summary())
```

### 验证并自动规范化

```python
# 自动修复可修复的类型问题（如 str → float）
normalized_fd = validator.validate_and_normalize(fd)
```

## 校验规则

### L1: FieldValidator（字段完整性）

| 字段 | 要求 | 说明 |
|------|------|------|
| `source` | non-empty str | 数据源标识，如 'akshare', 'eastmoney' |
| `data_type` | DataType 枚举 | 使用 `finance_toolkit.plugins.types.DataType` |
| `symbol` | non-empty str | 股票代码格式：`600000.SH` / `000001.SZ` |
| `timestamp` | ISO 8601 string | 如 `2026-08-15T10:30:00+08:00` |
| `payload` | non-empty dict | 包含业务字段的字典 |

### L2: TypeValidator（类型校验）

对以下常见金融字段进行 Python 类型校验：

| 字段 | 期望类型 | 说明 |
|------|----------|------|
| open, high, low, close, price | int, float | 价格字段 |
| volume, amount | int, float | 成交量/额 |
| change_pct, pct_chg | int, float | 涨跌幅 |
| turnover_rate, amplitude | int, float | 换手率/振幅 |
| pe_ratio, pb_ratio | int, float | 估值指标 |
| date, datetime | str | K线日期 |
| revenue, net_profit | int, float | 财务指标 |
| eps, bps, roe | int, float | 每股指标 |

### L3: RangeValidator（范围校验）

基于 `DataValidator.VALUE_RANGES` 中的约束：

| 字段 | 合法范围 | 说明 |
|------|----------|------|
| sentiment_score | [-1, 1] | 情绪指数 |
| change_pct | [-100, 100] | 涨跌幅 |
| pe_ratio | [0, 1000] | 市盈率 |
| pb_ratio | [0, 100] | 市净率 |
| roe | [-100, 100] | 净资产收益率 |
| turnover_rate | [0, 100] | 换手率（%） |
| amplitude | [0, 100] | 振幅（%） |

## 验证结果

```python
result = validate_finance_data(fd)

# 综合属性
result.is_valid          # bool: 是否通过验证
result.health_score      # float: 0-100 健康评分
result.total_issues      # int: 总问题数

# 分类统计
result.field_issues      # List[FieldIssue]: 字段问题
result.type_issues       # List[FieldIssue]: 类型问题
result.range_issues      # List[FieldIssue]: 范围问题

# 输出
result.summary()         # str: 可读摘要
result.to_dict()         # dict: 序列化结果
```

## 错误处理

当数据不满足规范时，验证器不会抛出异常，而是返回包含问题列表的 `FinanceDataValidationResult`。调用方可以根据 `is_valid` 字段决定是否丢弃或修复数据。

## 集成到 Pipeline

```python
from finance_toolkit.cleaning.pipeline import CleanPipeline
from finance_toolkit.cleaning.validators import (
    QuoteValidator,
    FinancialValidator,
    FinanceDataValidator,
)

# 组装清洗流水线
pipeline = CleanPipeline([
    QuoteValidator(),           # L1: 结构标准化
    FinancialValidator(),       # L2: 字段映射
    FinanceDataValidator(),     # L3: 契约验证
])

# 执行
result = pipeline.run(raw_data)
```

## 文件位置

- 源码：`.claude/skills/finance-data-toolkit/finance_toolkit/cleaning/validators.py`
- 模型：`.claude/skills/finance-data-toolkit/finance_toolkit/models/finance_data.py`
- 枚举：`.claude/skills/finance-data-toolkit/finance_toolkit/plugins/types.py`
- 底层验证：`.claude/skills/finance-data-toolkit/finance_toolkit/data_validator.py`
