# Finance Data Toolkit 数据存储规范 v1.0

**版本**: v1.0
**生效日期**: 2026-08-15
**适用范围**: finance-data-toolkit 所有模块的数据存储与读写操作
**关联文档**: 
- `docs/data-format-spec.md` — 数据格式契约
- `docs/deliverables-naming-convention.md` — 产出物命名规范
- `finance_toolkit/data_storage/storage.py` — 存储实现

---

## 一、设计原则

### 1.1 核心原则

| 原则 | 说明 |
|------|------|
| **单一职责** | 存储模块只负责数据的持久化，不做业务逻辑处理 |
| **向后兼容** | 保留旧版存储路径兼容性，新旧代码可同时运行 |
| **可追溯性** | 每条数据包含来源、时间戳、质量评分等元信息 |
| **可迁移性** | 支持 JSON/SQLite/内存等多种后端，按需切换 |
| **幂等写入** | 相同输入多次写入结果一致，支持去重 |

### 1.2 存储后端选择

| 场景 | 推荐后端 | 说明 |
|------|---------|------|
| 本地开发/测试 | `memory` | 无IO开销，速度快 |
| 小规模数据（<1万条） | `json` | 简单直观，版本可控 |
| 大规模数据/生产环境 | `sqlite` | 支持复杂查询，事务保障 |
| 缓存/临时数据 | `memory` | 进程内，自动清理 |

---

## 二、目录结构规范

### 2.1 根目录布局

```
.claude/skills/finance-data-toolkit/
├── data/                              # 数据根目录
│   ├── raw/                           # 原始数据（保留30天）
│   │   ├── akshare/                   # 按数据源分目录
│   │   │   └── 2026-08-15/
│   │   │       ├── quote_600000.json
│   │   │       └── kline_600000_daily.json
│   │   ├── tencent/
│   │   ├── eastmoney/
│   │   └── cninfo/
│   │
│   ├── processed/                     # 处理后数据（永久保留）
│   │   ├── quote/                     # 按数据类型分目录
│   │   │   └── 600000.SH_2026-08-15.json
│   │   ├── kline/
│   │   │   └── 600000.SH_daily_2026-08-15.json
│   │   ├── financial/
│   │   ├── northbound/
│   │   └── news/
│   │
│   ├── index/                         # 索引文件
│   │   ├── manifest_2026-08-15.json   # 每日抓取清单
│   │   ├── symbol_index.json          # 标的索引
│   │   └── source_index.json          # 数据源索引
│   │
│   ├── cache/                         # 缓存数据（保留7天）
│   │   ├── etag_cache.json            # 缓存校验
│   │   └── rate_limit_cache.json
│   │
│   ├── archive/                       # 归档数据（压缩）
│   │   └── 2026-Q3/
│   │       └── quotes_2026-07.zip
│   │
│   └── temp/                          # 临时文件（进程级）
│
├── db/                                # SQLite数据库
│   ├── finance_data.db                # 主数据库
│   ├── finance_data.db-wal            # 预写日志
│   └── finance_data.db-shm            # 共享内存
│
├── logs/                              # 运行日志
│   ├── fetch_20260815.log
│   └── error_20260815.log
│
└── research/                          # 研究分析产出
    └── stock_analyse/
        └── cycle_0XXX/
            └── latest.json
```

### 2.2 目录职责说明

| 目录 | 职责 | 保留策略 | 访问权限 |
|------|------|---------|---------|
| `data/raw/` | 存储各数据源的原始响应 | 30天自动清理 | 只读 |
| `data/processed/` | 存储标准化后的数据 | 永久保留 | 读写 |
| `data/index/` | 存储索引和清单文件 | 永久保留 | 读写 |
| `data/cache/` | 存储缓存数据 | 7天自动清理 | 读写 |
| `data/archive/` | 存储压缩归档数据 | 永久保留 | 只读 |
| `db/` | SQLite数据库文件 | 永久保留 | 读写 |
| `logs/` | 运行日志 | 90天轮转 | 只写 |
| `research/` | 研究分析报告 | 永久保留 | 读写 |

---

## 三、文件命名规范

### 3.1 通用命名规则

```
{data_type}_{symbol}[_{extra}][_YYYYMMDD].{ext}
```

**约束条件：**
1. 全部小写，使用 `_` 分隔单词
2. 禁止使用空格、中文、特殊符号（`/\:*?"<>|`）
3. 日期后缀使用 `YYYYMMDD` 格式
4. 扩展名固定为 `.json`

### 3.2 各数据类型命名

| 数据类型 | 命名格式 | 示例 |
|---------|---------|------|
| 实时行情 | `{symbol}_{date}.json` | `600000.SH_2026-08-15.json` |
| K线数据 | `{symbol}_{period}_{date}.json` | `600000.SH_daily_2026-08-15.json` |
| 财务数据 | `{symbol}_{report_type}.json` | `600000.SH_Q2_2026.json` |
| 北向资金 | `northbound_{date}.json` | `northbound_2026-08-15.json` |
| 新闻资讯 | `news_{date}_{seq}.json` | `news_2026-08-15_001.json` |
| 分红数据 | `{symbol}_dividend_{date}.json` | `600000.SH_dividend_2026-08-15.json` |
| 龙虎榜 | `{symbol}_lhb_{date}.json` | `600000.SH_lhb_2026-08-15.json` |
| ETF行情 | `{symbol}_etf_{date}.json` | `510050.SH_etf_2026-08-15.json` |
| 板块数据 | `sector_{code}_{date}.json` | `sector_bank_2026-08-15.json` |
| Manifest | `manifest_{date}.json` | `manifest_2026-08-15.json` |
| 状态文件 | `{component}_state.json` | `scheduler_state.json` |
| 索引文件 | `{index_name}.json` | `symbol_index.json` |

### 3.3 交易所后缀规范

| 交易所 | 后缀 | 示例 |
|-------|------|------|
| 上海证券交易所 | `.SH` | `600000.SH` |
| 深圳证券交易所 | `.SZ` | `000001.SZ` |
| 北京证券交易所 | `.BJ` | `830799.BJ` |
| 中国债券 | `.BOND` | `113000.BOND` |
| 基金 | `.FUND` | `159915.SZ` |

---

## 四、数据文件内容规范

### 4.1 FinanceData 标准结构

所有存储文件必须遵循统一的 FinanceData 数据结构：

```json
{
  "source": "akshare",
  "data_type": "quote",
  "symbol": "600000.SH",
  "timestamp": "2026-08-15T10:30:00+08:00",
  "data_time": "2026-08-15",
  "quality_score": 1.0,
  "quality_issues": [],
  "payload": {
    "name": "浦发银行",
    "price": 10.50,
    "open": 10.20,
    "high": 10.80,
    "low": 10.10,
    "pre_close": 10.30,
    "change": 0.20,
    "change_pct": 1.94,
    "volume": 1500000,
    "amount": 157500000
  },
  "meta": {
    "fetch_time_ms": 150.5,
    "retry_count": 0,
    "proxy_ip": "192.168.1.1",
    "version": "v1.0.0",
    "data_completeness": 0.95
  }
}
```

### 4.2 payload 字段规范

- `payload` 字段存储业务数据，字段命名遵循 `data-format-spec.md` 定义
- 数值类型统一使用浮点数（float），金额单位为元
- 布尔值使用 JSON 原生 `true`/`false`
- 时间字段使用 ISO 8601 格式

---

## 五、Manifest 文件规范

### 5.1 结构与示例

每次抓取任务完成后生成一份 Manifest 文件，记录本次任务的完整信息：

```json
{
  "version": "2.0",
  "date": "2026-08-15",
  "cycle": 228,
  "status": "success",
  "summary": {
    "total_records": 5000,
    "data_types": {
      "quote": 1500,
      "kline": 3000,
      "news": 500
    },
    "sources": ["akshare", "tencent"],
    "quality_avg": 0.95,
    "duration_seconds": 45.2
  },
  "files": {
    "raw_dir": "data/raw/akshare/2026-08-15/",
    "processed_dir": "data/processed/2026-08-15/",
    "reports_dir": "research/stock_analyse/cycle_0228/"
  },
  "errors": [],
  "warnings": [],
  "metadata": {
    "created_at": "2026-08-15T10:30:00+08:00",
    "created_by": "daemon_cycle_228",
    "environment": "production"
  }
}
```

### 5.2 Manifest 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `version` | str | ✅ | Manifest 版本号（当前为 `2.0`） |
| `date` | str | ✅ | 抓取日期（YYYY-MM-DD） |
| `cycle` | int | ✅ | 任务周期编号 |
| `status` | str | ✅ | 任务状态：`success`/`partial`/`failed` |
| `summary.total_records` | int | ✅ | 总记录数 |
| `summary.data_types` | dict | ✅ | 各数据类型记录数 |
| `summary.quality_avg` | float | ✅ | 平均质量评分 |
| `summary.duration_seconds` | float | ❌ | 抓取耗时 |
| `errors` | list | ✅ | 错误列表 |
| `warnings` | list | ✅ | 警告列表 |
| `metadata.created_by` | str | ✅ | 任务来源标识 |

---

## 六、索引文件规范

### 6.1 symbol_index.json

维护所有已知标的的全局索引：

```json
{
  "version": "1.0",
  "updated_at": "2026-08-15T10:30:00+08:00",
  "symbols": {
    "600000.SH": {
      "name": "浦发银行",
      "market": "SH",
      "industry": "银行",
      "registered_at": "2026-01-01",
      "last_update": "2026-08-15T10:30:00+08:00",
      "data_types": ["quote", "kline", "financial"]
    }
  },
  "stats": {
    "total_symbols": 5000,
    "by_market": {
      "SH": 2500,
      "SZ": 2480,
      "BJ": 20
    }
  }
}
```

### 6.2 source_index.json

维护数据源的健康状态索引：

```json
{
  "version": "1.0",
  "updated_at": "2026-08-15T10:30:00+08:00",
  "sources": {
    "akshare": {
      "status": "healthy",
      "last_success": "2026-08-15T10:28:00+08:00",
      "total_records": 15000,
      "avg_latency_ms": 120.5,
      "error_rate": 0.02
    },
    "tencent": {
      "status": "healthy",
      "last_success": "2026-08-15T10:29:00+08:00",
      "total_records": 12000,
      "avg_latency_ms": 85.3,
      "error_rate": 0.01
    }
  }
}
```

---

## 七、存储后端实现

### 7.1 JSON 存储后端（默认）

**位置**: `finance_toolkit/data_storage/storage.py` → `JSONStorage`

```python
from finance_toolkit.data_storage import JSONStorage

storage = JSONStorage(base_dir='./data')

# 保存数据
storage.save('quote', '600000.SH', data)

# 加载最新数据
result = storage.load('quote', '600000.SH')

# 删除数据
storage.delete('quote', '600000.SH')
```

**特点**:
- 每个标的一个文件，便于版本控制
- 文件路径: `{base_dir}/{data_type}/{symbol}_{date}.json`
- 自动创建父目录
- 支持 UTF-8 编码中文

### 7.2 SQLite 存储后端

**位置**: `finance_toolkit/storage/storage.py` → `FinanceDatabase`

```python
from finance_toolkit.storage import FinanceDatabase

db = FinanceDatabase(db_path='db/finance_data.db')

# 保存股票行情
db.save_stock_quote(data)

# 查询股票行情
quotes = db.get_stock_quotes(symbol='600000.SH', limit=10)

# 导出为 JSON
db.export_to_json('stock_quotes', 'data/export/quotes.json')
```

**表结构**:
- `stock_quotes`: 股票实时行情
- `sector_quotes`: 行业板块行情
- `margin_data`: 融资融券数据
- `capital_flow`: 资金流向数据
- `sector_capital_flow`: 板块资金流向
- `fetch_log`: 抓取日志

### 7.3 内存存储后端

**位置**: `finance_toolkit/data_storage/storage.py` → `MemoryStorage`

```python
from finance_toolkit.data_storage import MemoryStorage

storage = MemoryStorage()
storage.save('quote', '600000.SH', data)
result = storage.load('quote', '600000.SH')
```

**用途**: 单元测试、临时缓存、快速验证。

### 7.4 统一访问接口

```python
from finance_toolkit.data_storage import DataStorage

# 创建存储实例（默认 JSON）
storage = DataStorage(backend='json', base_dir='./data')

# 或使用 SQLite
storage = DataStorage(backend='sqlite', db_path='db/finance_data.db')

# 统一 API
storage.save(data_type, symbol, data)
storage.load(data_type, symbol)
storage.delete(data_type, symbol)
```

---

## 八、数据生命周期管理

### 8.1 数据保留策略

| 数据类型 | 保留时长 | 清理时机 |
|---------|---------|---------|
| 原始数据 (raw/) | 30 天 | 每日凌晨 2:00 |
| 处理后数据 (processed/) | 永久 | 仅定期归档 |
| 缓存数据 (cache/) | 7 天 | 每次抓取前 |
| 日志文件 (logs/) | 90 天 | 按月轮转 |
| 归档数据 (archive/) | 永久 | 手动删除 |

### 8.2 归档格式

季度归档时使用 zip 压缩，文件名格式：

```
data/archive/2026-Q3/quotes_2026-07.zip
data/archive/2026-Q3/klines_2026-Q3.zip
```

---

## 九、异常处理规范

### 9.1 存储异常分类

| 异常类型 | 触发条件 | 处理方式 |
|---------|---------|---------|
| `StorageNotFoundError` | 文件不存在 | 返回 None，不抛出 |
| `StorageWriteError` | 磁盘空间不足/权限不足 | 记录日志，降级到内存存储 |
| `StorageParseError` | JSON 解析失败 | 记录日志，标记数据损坏 |
| `StorageConflictError` | 唯一键冲突 | 使用 UPSERT 策略覆盖 |

### 9.2 降级策略

当 JSON 存储失败时，自动降级到内存存储：

```python
try:
    storage.save('quote', symbol, data)
except StorageWriteError:
    logger.warning(f"JSON存储失败，降级到内存存储: {symbol}")
    memory_storage = MemoryStorage()
    memory_storage.save('quote', symbol, data)
```

---

## 十、迁移指南

### 10.1 从旧存储路径迁移

旧路径格式: `./data/{symbol}_{date}.json`
新路径格式: `./data/processed/{data_type}/{symbol}_{date}.json`

```python
import shutil
from pathlib import Path

# 迁移脚本示例
def migrate_old_data(old_dir: str, new_dir: str):
    old_path = Path(old_dir)
    new_path = Path(new_dir)
    
    for f in old_path.glob('*.json'):
        # 解析文件名确定 data_type
        parts = f.stem.split('_')
        symbol = parts[0]
        date = parts[1] if len(parts) > 1 else 'unknown'
        
        # 推断数据类型
        data_type = infer_data_type(f)
        
        # 迁移到新的目录结构
        target = new_path / data_type / f"{symbol}_{date}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(f), str(target))
```

### 10.2 兼容性声明

- v1.0 新增 `data/processed/{data_type}/` 子目录结构
- 旧的 `data/` 根目录 JSON 文件继续可用，但建议迁移
- `data/raw/` 下按数据源分目录，便于溯源

---

## 十一、附录

### A. 完整目录树（ASCII）

```
finance-data-toolkit/
├── data/
│   ├── raw/
│   │   ├── akshare/2026-08-15/
│   │   │   ├── quote_600000.json
│   │   │   └── kline_600000_daily.json
│   │   ├── tencent/2026-08-15/
│   │   ├── eastmoney/2026-08-15/
│   │   └── cninfo/2026-08-15/
│   ├── processed/
│   │   ├── quote/
│   │   │   ├── 600000.SH_2026-08-15.json
│   │   │   └── 000001.SZ_2026-08-15.json
│   │   ├── kline/
│   │   │   └── 600000.SH_daily_2026-08-15.json
│   │   ├── financial/
│   │   ├── northbound/
│   │   │   └── northbound_2026-08-15.json
│   │   └── news/
│   ├── index/
│   │   ├── manifest_2026-08-15.json
│   │   ├── symbol_index.json
│   │   └── source_index.json
│   ├── cache/
│   ├── archive/2026-Q3/
│   └── temp/
├── db/
│   └── finance_data.db
├── logs/
│   └── fetch_20260815.log
└── research/
    └── stock_analyse/
        └── cycle_0228/
```

### B. 变更日志

| 版本 | 日期 | 变更内容 |
|------|------|---------|
| v1.0 | 2026-08-15 | 初始版本，定义目录结构、命名规则和存储规范 |

---

*文档版本: v1.0*
*最后更新: 2026-08-15*
