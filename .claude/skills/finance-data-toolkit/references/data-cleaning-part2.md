# 数据清洗标准化规范 (第 2 部分)

## 7. 去重策略

```python
class Deduplicator:
    """多级去重：精确去重 + 近似去重 + 业务键去重"""
    
    def __init__(self, storage):
        self.storage = storage  # Redis / DB
    
    async def dedup_exact(self, data: Dict) -> bool:
        """精确去重：基于内容哈希"""
        content = json.dumps(data.get('payload', {}), sort_keys=True)
        fingerprint = hashlib.sha256(content.encode()).hexdigest()[:16]
        key = f"dedup:exact:{fingerprint}"
        
        if await self.storage.exists(key):
            return True  # 是重复
        await self.storage.setex(key, 86400 * 7, '1')  # 保留 7 天
        return False
    
    async def dedup_business_key(self, data: Dict) -> bool:
        """业务键去重：按数据类型定义唯一键"""
        data_type = data.get('data_type')
        payload = data.get('payload', {})
        
        key_map = {
            'quote': f"{payload.get('symbol')}:{payload.get('timestamp')}",
            'kline': f"{payload.get('symbol')}:{payload.get('date')}:{payload.get('period', 'daily')}",
            'financial': f"{payload.get('symbol')}:{payload.get('report_date')}:{payload.get('report_type')}",
            'news': f"{data.get('source')}:{payload.get('news_id')}",
            'guba': f"guba:{payload.get('post_id')}",
            'report': f"{data.get('source')}:{payload.get('report_id')}",
        }
        
        key = key_map.get(data_type)
        if not key:
            return False
        
        full_key = f"dedup:biz:{key}"
        if await self.storage.exists(full_key):
            return True
        await self.storage.setex(full_key, 86400 * 30, '1')
        return False
    
    async def dedup_simhash(self, data: Dict, threshold: int = 3) -> bool:
        """近似去重：SimHash 文本指纹 (针对新闻/股吧正文)"""
        text = data.get('payload', {}).get('content', '')
        if len(text) < 100:
            return False
        
        # 计算 SimHash (简化版)
        simhash = self._compute_simhash(text)
        
        # 在 Redis 中查找相似指纹 (需 RedisBloom 或 Lua 脚本)
        # 这里简化：仅存储指纹，实际生产用专用近似去重库
        key = f"dedup:simhash:{simhash}"
        if await self.storage.exists(key):
            return True
        await self.storage.setex(key, 86400 * 30, '1')
        return False
    
    def _compute_simhash(self, text: str) -> str:
        """简化 SimHash 实现"""
        import jieba
        words = jieba.lcut(text)
        v = [0] * 64
        for w in words:
            h = hashlib.md5(w.encode()).hexdigest()
            for i, bit in enumerate(h):
                v[i % 64] += 1 if bit in '89abcdef' else -1
        return ''.join('1' if x > 0 else '0' for x in v)
```

## 8. 缺失值处理策略

| 字段类型 | 策略 | 示例 |
|----------|------|------|
| **价格/数值** | 前向填充 → 线性插值 → 标记 NaN | 收盘价缺失用前值，成交量缺失用 0 |
| **时间序列** | 重采样对齐 (resample) | 分钟线缺失按前值填充 |
| **财务指标** | 不填充，保留 NaN + 质量标记 | ROE 缺失不强行填充 |
| **文本字段** | 空字符串 + 标记 | 摘要为空置 "" |
| **分类字段** | "unknown" 类别 | 行业分类缺失置 "unknown" |

```python
class MissingValueHandler(BaseCleaner):
    level = CleanLevel.L3_VALIDATION
    source_types = ['quote', 'kline', 'financial']
    
    STRATEGY = {
        'quote': {
            'price': 'ffill',
            'volume': 'zero',
            'amount': 'zero',
            'pct_chg': 'calc',  # 从 pre_close/close 计算
        },
        'kline': {
            'open': 'ffill',
            'high': 'ffill',
            'low': 'ffill',
            'close': 'ffill',
            'volume': 'zero',
            'amount': 'zero',
        },
        'financial': {
            # 财务指标不填充，仅标记
        },
    }
    
    def clean(self, raw: Dict) -> CleanResult:
        # 实际实现需结合历史数据上下文，此处仅定义策略
        return CleanResult(data=raw, level=self.level, passed=True)
```

## 9. 时间对齐 (多源数据同步)

```python
class TimeAligner:
    """多源数据时间对齐到统一时间网格"""
    
    @staticmethod
    def align_to_grid(df: pd.DataFrame, freq: str = '1min',
                       method: str = 'ffill') -> pd.DataFrame:
        """将不规则时间序列对齐到固定频率网格"""
        df = df.copy()
        df.index = pd.to_datetime(df.index)
        df = df.tz_convert('UTC') if df.index.tz else df.tz_localize('UTC')
        
        # 生成完整时间网格 (仅交易时间)
        if freq.endswith('min') or freq.endswith('H'):
            # 交易时段: 9:30-11:30, 13:00-15:00
            grid = TimeAligner._generate_trading_grid(df.index.min(), df.index.max(), freq)
        else:
            grid = pd.date_range(df.index.min(), df.index.max(), freq=freq, tz='UTC')
        
        df = df.reindex(grid)
        
        if method == 'ffill':
            df = df.ffill()
        elif method == 'interpolate':
            df = df.interpolate(time_index=True)
        
        return df
    
    @staticmethod
    def _generate_trading_grid(start: pd.Timestamp, end: pd.Timestamp, freq: str) -> pd.DatetimeIndex:
        """生成仅包含交易时段的时间网格"""
        grids = []
        current = start.normalize()
        while current <= end:
            if current.weekday() < 5:  # 周一至周五
                morning = pd.date_range(current + pd.Timedelta('9:30'),
                                        current + pd.Timedelta('11:30'),
                                        freq=freq, tz='UTC')
                afternoon = pd.date_range(current + pd.Timedelta('13:00'),
                                          current + pd.Timedelta('15:00'),
                                          freq=freq, tz='UTC')
                grids.append(morning)
                grids.append(afternoon)
            current += pd.Timedelta('1D')
        return pd.DatetimeIndex(pd.concat(grids)).sort_values()
```

## 10. 增量更新策略

```python
class IncrementalUpdater:
    """增量更新：仅处理变更数据"""
    
    def __init__(self, storage):
        self.storage = storage
    
    async def get_last_state(self, data_type: str, symbol: str) -> Optional[Dict]:
        """获取上次成功处理的状态"""
        key = f"incr:{data_type}:{symbol}"
        return await self.storage.get(key)
    
    async def update_state(self, data_type: str, symbol: str, state: Dict):
        """更新处理状态"""
        key = f"incr:{data_type}:{symbol}"
        await self.storage.set(key, json.dumps(state))
    
    def should_process(self, data: Dict, last_state: Dict) -> bool:
        """判断是否需要处理 (基于版本号/时间戳/内容哈希)"""
        data_type = data.get('data_type')
        payload = data.get('payload', {})
        
        if not last_state:
            return True  # 首次处理
        
        # 版本号比较
        if 'version' in payload and 'version' in last_state:
            return payload['version'] > last_state['version']
        
        # 时间戳比较
        if 'timestamp' in payload and 'timestamp' in last_state:
            return payload['timestamp'] > last_state['timestamp']
        
        # 内容哈希比较
        content_hash = hashlib.md5(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        return content_hash != last_state.get('content_hash')
```

## 11. 数据质量监控指标

```python
class QualityMetrics:
    """数据质量指标计算与告警"""
    
    METRICS = [
        'completeness',    # 完整性: 非空字段比例
        'validity',        # 有效性: 通过校验规则比例
        'consistency',     # 一致性: 多源数据一致性
        'timeliness',      # 时效性: 数据延迟
        'accuracy',        # 准确性: 与基准源偏差
    ]
    
    @staticmethod
    def compute(batch: List[Dict]) -> Dict:
        """计算批次质量指标"""
        total = len(batch)
        if total == 0:
            return {m: 0 for m in QualityMetrics.METRICS}
        
        passed = sum(1 for d in batch if d.get('_clean_report', {}).get('final_passed', False))
        
        # 完整性
        non_null_fields = 0
        total_fields = 0
        for d in batch:
            payload = d.get('payload', {})
            for v in payload.values():
                total_fields += 1
                if v is not None and v != '':
                    non_null_fields += 1
        
        return {
            'completeness': non_null_fields / total_fields if total_fields else 0,
            'validity': passed / total,
            'consistency': 0,  # 需多源对比
            'timeliness': 0,   # 需对比源头时间
            'accuracy': 0,     # 需基准源
            'total_records': total,
            'passed_records': passed,
            'failed_records': total - passed,
        }
    
    @staticmethod
    def check_alerts(metrics: Dict, thresholds: Dict = None) -> List[str]:
        """检查是否触发告警"""
        thresholds = thresholds or {
            'completeness': 0.95,
            'validity': 0.98,
            'timeliness_hours': 2,
        }
        alerts = []
        if metrics['completeness'] < thresholds['completeness']:
            alerts.append(f"完整性过低: {metrics['completeness']:.2%} < {thresholds['completeness']:.2%}")
        if metrics['validity'] < thresholds['validity']:
            alerts.append(f"有效性过低: {metrics['validity']:.2%} < {thresholds['validity']:.2%}")
        return alerts
```

## 12. 常见问题排查表

| 现象 | 可能原因 | 排查步骤 | 解决方案 |
|------|----------|----------|----------|
| 字段映射后为空 | 源字段名变更 | 对比 raw 字段、检查映射表 | 更新 SOURCE_MAPPING 配置 |
| 时间解析失败 | 新格式时间字符串 | 打印原始值、尝试所有格式 | 补充 TIME_FORMATS |
| 价格异常 (负值/0) | 除权除息/数据源错误 | 对比多源、检查前复权 | 引入 corporate_action 修正 |
| 去重误判 | 业务键定义不全 | 检查 key_map 覆盖度 | 补充唯一键字段 |
| 增量更新漏数据 | 版本号不单调 | 检查源版本号规则 | 改用内容哈希/时间戳 |
| 分钟线缺失大量 | 非交易时段/停牌 | 核对交易日历、停牌表 | 结合 calendar 过滤 |
| 财务指标 NaN 过多 | 报表未披露/字段缺失 | 核对报告期、数据源覆盖 | 标记质量、不强行填充 |
| 文本近似去重失效 | SimHash 阈值不当 | 统计汉明距离分布 | 调整 threshold 参数 |

## 13. 配置文件示例 (cleaning_rules.yaml)

```yaml
# 清洗规则外置配置
pipeline:
  - StructureNormalizer
  - TypeCoercer
  - TimeNormalizer
  - FieldMapper
  - QuoteValidator
  - FinancialValidator
  - NewsValidator
  - FeatureEngineer

field_mapping:
  quote:
    akshare:
      代码: symbol
      名称: name
      最新价: price
      # ...
    tushare:
      ts_code: symbol
      # ...

validation_rules:
  quote:
    price_positive: true
    high_low_contain: true
    pct_chg_tolerance: 0.02
    turnover_rate_range: [0, 100]
    price_jump_threshold: 20
  financial:
    report_date_future_days: 30
    net_margin_range: [-5, 1]
    roe_range: [-100, 100]
  news:
    min_title_len: 2
    min_content_len: 50
    max_content_len: 500000

missing_value_strategy:
  quote:
    price: ffill
    volume: zero
  kline:
    ohlc: ffill
    volume: zero
  financial: {}

dedup:
  exact_ttl_days: 7
  business_ttl_days: 30
  simhash_ttl_days: 30
  simhash_threshold: 3

time_alignment:
  freq: 1min
  method: ffill
  trading_hours_only: true

quality_thresholds:
  completeness: 0.95
  validity: 0.98
  timeliness_hours: 2
```

## 14. 使用示例

```python
# 完整清洗流水线使用
from finance_toolkit.cleaning import (
    CleanPipeline,
    StructureNormalizer,
    TypeCoercer,
    TimeNormalizer,
    FieldMapper,
    QuoteValidator,
    FinancialValidator,
    NewsValidator,
    FeatureEngineer,
    Deduplicator,
    QualityMetrics,
)

# 构建流水线
pipeline = CleanPipeline([
    StructureNormalizer(),
    TypeCoercer(),
    TimeNormalizer(),
    FieldMapper(),
    QuoteValidator(),
    FinancialValidator(),
    NewsValidator(),
    FeatureEngineer(),
])

# 处理单条数据
raw_data = {
    'source': 'akshare',
    'data_type': 'quote',
    'symbol': '000001.SZ',
    'timestamp': '2024-01-15 15:00:00',
    'payload': {'代码': '000001', '名称': '平安银行', '最新价': 10.56, ...},
    'crawl_time': datetime.utcnow(),
}

cleaned = pipeline.run(raw_data)
print(cleaned['_clean_report'])

# 批量处理 + 质量报告
batch = [raw_data1, raw_data2, ...]
cleaned_batch = [pipeline.run(d) for d in batch]
metrics = QualityMetrics.compute(cleaned_batch)
alerts = QualityMetrics.check_alerts(metrics)
if alerts:
    send_alert(alerts)
```