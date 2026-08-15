# 数据格式校验工具使用指南

**版本**: v1.0
**文档日期**: 2026-08-15
**脚本位置**: `scripts/format_validator.py`

---

## 一、工具概述

`format_validator.py` 是一个用于验证金融数据输出是否符合 FinanceData Toolkit 存储规范的命令行工具。

### 核心功能

| 功能 | 说明 |
|------|------|
| 文件格式校验 | 验证 JSON 语法、字段结构 |
| 命名规范检查 | 验证文件名、目录层级是否符合规范 |
| FinanceData 结构验证 | 检查 source/data_type/symbol/payload 等必填字段 |
| 批量目录扫描 | 支持递归扫描整个数据目录 |
| 双模式支持 | strict(严格)/lenient(宽松) 两种校验强度 |

---

## 二、使用方法

### 基本语法

```bash
python scripts/format_validator.py <路径> [选项]
```

### 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `path` | positional | 必填 | 要校验的文件或目录路径 |
| `--recursive`, `-r` | flag | False | 递归扫描子目录 |
| `--strict` | flag | True | 严格模式（默认） |
| `--lenient` | flag | False | 宽松模式 |
| `--json` | flag | False | 输出 JSON 格式结果 |

### 使用示例

#### 1. 校验单个文件

```bash
python scripts/format_validator.py data/daily/test_cache_stock_data_20260810.json
```

输出:
```
Validation: ❌ FAIL
Errors (5):
  -   缺少必填字段: timestamp
  -   缺少必填字段: source
  -   缺少必填字段: data_type
  -   缺少必填字段: symbol
  - 缺少 payload 字段
```

#### 2. 校验整个目录

```bash
python scripts/format_validator.py data/daily
```

输出:
```
============================================================
数据格式校验报告
============================================================
总计: 3 个文件
通过: 0 个
失败: 3 个
0/3 files passed validation
...
```

#### 3. 递归扫描

```bash
python scripts/format_validator.py data --recursive
```

#### 4. JSON 格式输出

```bash
python scripts/format_validator.py data/daily --json
```

输出:
```json
{
  "valid": false,
  "errors": ["缺少必填字段: timestamp", ...],
  "warnings": []
}
```

#### 5. 宽松模式

```bash
python scripts/format_validator.py data/daily --lenient
```

---

## 三、校验规则说明

### 3.1 必填字段检查

FinanceData 标准结构要求以下字段必须存在：

| 字段 | 类型 | 说明 |
|------|------|------|
| `source` | string | 数据来源标识（如 akshare, tencent） |
| `data_type` | string | 数据类型（quote, kline, financial 等） |
| `symbol` | string | 标的代码（如 600000.SH） |
| `timestamp` | string | ISO 8601 格式时间戳 |
| `payload` | object | 业务数据载荷 |

### 3.2 命名规范检查

| 规则 | 说明 |
|------|------|
| 扩展名 | 必须为 `.json` |
| 日期格式 | 支持 `YYYY-MM-DD` 或 `YYYYMMDD` |
| 目录路径 | 建议包含 `data/processed/` 或 `data/raw/` 路径层级 |

### 3.3 数据类型校验

支持的 `data_type` 枚举值：

```
quote, kline, financial, northbound, news, dividend,
lhb, etf, sector, bond, fund, futures, macro,
sentiment, social, index, commodity, forex, crypto
```

### 3.4 Symbol 格式校验

支持的交易所后缀：

```
.SH (上海证券交易所)
.SZ (深圳证券交易所)
.BJ (北京证券交易所)
.BOND (中国债券)
.FUND (基金)
```

---

## 四、集成到工作流

### 4.1 抓取后自动校验

```python
from scripts.format_validator import DataFormatValidator
from pathlib import Path

validator = DataFormatValidator(strict=True)
result = validator.validate_directory(Path('data/processed'))

if result['failed'] > 0:
    print(f'发现 {result["failed"]} 个格式错误文件')
    # 记录到日志或发送告警
```

### 4.2 CI/CD 集成

在 pipeline 中添加校验步骤：

```yaml
# .github/workflows/validate.yml
- name: Validate Data Format
  run: |
    python scripts/format_validator.py data/ --recursive
    python scripts/format_validator.py data/raw/ --recursive
```

### 4.3 定时校验任务

```bash
# 每日凌晨校验前一天数据
0 2 * * * cd /path/to/project && python scripts/format_validator.py data/processed --recursive
```

---

## 五、退出码说明

| 退出码 | 含义 |
|--------|------|
| 0 | 所有文件校验通过 |
| 1 | 发现格式错误 |
| 2 | 参数错误或路径不存在 |

---

## 六、与存储规范的关系

本工具严格遵循 `docs/storage-spec.md` 定义的数据存储规范，校验规则包括：

1. **目录结构规范** - 验证文件是否在正确的数据目录下
2. **文件命名规范** - 验证 `{data_type}_{symbol}[_{extra}][_YYYYMMDD].json` 格式
3. **FinanceData 标准结构** - 验证必填字段和类型约束
4. **Manifest 文件规范** - 支持 manifest 特殊格式校验

---

## 七、已知限制

| 限制 | 说明 | 解决建议 |
|------|------|---------|
| 仅支持 JSON 格式 | 不支持 CSV/Excel 等格式校验 | 如需扩展可添加解析器 |
| 静态校验 | 不检查业务逻辑正确性（如价格是否为负） | 需配合业务校验器使用 |
| 无自动修复 | 仅报告错误，不修改文件 | 建议配合编辑工具使用 |

---

*文档版本: v1.0*
*最后更新: 2026-08-15*