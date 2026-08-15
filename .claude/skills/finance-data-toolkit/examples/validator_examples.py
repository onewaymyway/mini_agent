# FinanceData 验证器使用示例

## 示例1: 验证有效的股票行情数据

```python
from finance_toolkit.cleaning.validators import validate_finance_data
from finance_toolkit.models.finance_data import FinanceData
from finance_toolkit.plugins.types import DataType
from datetime import datetime, timezone

# 构建有效的 FinanceData 对象
fd = FinanceData(
    source='akshare',
    data_type=DataType.QUOTE,
    symbol='600000.SH',
    timestamp=datetime.now(timezone.utc).isoformat(),
    payload={
        'open': 10.50,
        'high': 11.20,
        'low': 10.30,
        'close': 11.00,
        'volume': 1500000,
        'amount': 16500000,
        'change_pct': 4.76,
        'turnover_rate': 2.3,
    },
)

# 执行验证
result = validate_finance_data(fd)
print(result.summary())
# 输出: FinanceData 验证结果 [✓] 健康评分: 100.0/100

# 检查是否为有效数据
if result.is_valid:
    print('数据通过验证，可以存储')
else:
    print(f'数据存在问题: {result.total_issues} 个')
    for issue in result.field_issues:
        print(f'  - {issue.field_path}: {issue.actual}')
```

## 示例2: 处理类型错误的数据

```python
from finance_toolkit.cleaning.validators import FinanceDataValidator
from finance_toolkit.models.finance_data import FinanceData
from finance_toolkit.plugins.types import DataType

# 模拟数据源返回的类型错误（open 为字符串）
fd = FinanceData(
    source='eastmoney',
    data_type=DataType.QUOTE,
    symbol='000001.SZ',
    timestamp='2026-08-15T14:30:00+08:00',
    payload={
        'open': '12.5',  # 应该是 float，但数据源返回了 string
        'close': 13.0,
        'volume': 500000,
    },
)

validator = FinanceDataValidator()
result = validator.validate(fd)
print(f'健康评分: {result.health_score}')
# 输出: 健康评分: 90.0 (1个类型问题)

# 自动修复
fixed_fd = validator.validate_and_normalize(fd)
print(type(fixed_fd.payload['open']))
# 输出: <class 'float'> - 已被自动转换
```

## 示例3: 范围越界检测

```python
from finance_toolkit.cleaning.validators import validate_finance_data
from finance_toolkit.models.finance_data import FinanceData
from finance_toolkit.plugins.types import DataType

# 模拟异常数据（涨跌幅超出合理范围）
fd = FinanceData(
    source='akshare',
    data_type=DataType.QUOTE,
    symbol='600000.SH',
    timestamp='2026-08-15T10:00:00+08:00',
    payload={
        'open': 10.0,
        'close': 30.0,  # 涨幅200%，超出正常范围
        'volume': 100000,
    },
)

result = validate_finance_data(fd)
print(result.summary())
# 输出包含 range_issues 中的警告
```

## 示例4: 批量验证多个数据对象

```python
from finance_toolkit.cleaning.validators import validate_finance_data_batch
from finance_toolkit.models.finance_data import FinanceData
from finance_toolkit.plugins.types import DataType
from datetime import datetime, timezone

# 准备一批数据
items = [
    FinanceData(source='akshare', data_type=DataType.QUOTE, symbol='600000.SH',
                timestamp=datetime.now(timezone.utc).isoformat(),
                payload={'open': 10.0, 'close': 10.5, 'volume': 100000}),
    FinanceData(source='akshare', data_type=DataType.QUOTE, symbol='',  # 缺少 symbol
                timestamp=datetime.now(timezone.utc).isoformat(),
                payload={'open': 10.0}),
    FinanceData(source='akshare', data_type=DataType.FINANCIAL, symbol='600000.SH',
                timestamp=datetime.now(timezone.utc).isoformat(),
                payload={'revenue': 'not_a_number'}),  # 类型错误
]

results = validate_finance_data_batch(items)
for i, r in enumerate(results):
    print(f'Item {i}: score={r.health_score}, valid={r.is_valid}')
```

## 示例5: 集成到数据抓取流程

```python
from finance_toolkit.cleaning.validators import FinanceDataValidator
from finance_toolkit.data_fetching.stock_fetcher import StockFetcher

# 初始化验证器和数据获取器
validator = FinanceDataValidator()
fetcher = StockFetcher()

# 获取数据并验证
raw_data = fetcher.fetch_quote('600000.SH')
fd = raw_data.to_finance_data()  # 转换为标准格式

result = validator.validate(fd)
if result.is_valid:
    # 存储到数据库或文件系统
    save_to_storage(fd)
else:
    # 记录问题并跳过
    log_validation_error(fd, result)
```

## 示例6: 自定义验证器配置

```python
from finance_toolkit.cleaning.validators import FinanceDataValidator

# 禁用底层 97 条规则验证（仅使用三层校验）
validator = FinanceDataValidator(enable_base_validator=False)

# 启用严格模式（触发更多警告）
validator = FinanceDataValidator(strict_mode=True)

# 仅验证字段完整性
from finance_toolkit.cleaning.validators import FieldValidator
field_v = FieldValidator()
issues = field_v.validate(fd)
```

## 运行示例

```bash
cd .claude/skills/finance-data-toolkit
python -m examples.validator_examples
```