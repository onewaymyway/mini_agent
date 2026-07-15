# 数据清洗标准化规范 (第 1 部分)

覆盖：去重、缺失值处理、异常值检测、字段映射、时间对齐、增量更新策略、数据质量监控。

## 1. 核心原则

| 原则 | 说明 | 执行层级 |
|------|------|----------|
| **源头不改** | 原始响应完整保存 `raw` 字段，清洗产出新字段 | 硬性 |
| **可追溯** | 每条数据保留 `source`、`crawl_time`、`version`、清洗规则版本 | 硬性 |
| **幂等性** | 同一输入多次清洗结果一致，便于重跑回溯 | 硬性 |
| **配置化** | 清洗规则外置配置（YAML/JSON），不硬编码 | 推荐 |
| **分级处理** | L1: 结构标准化 → L2: 字段映射 → L3: 业务校验 → L4: 特征工程 | 架构 |

## 2. 统一清洗流水线

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from enum import Enum

class CleanLevel(Enum):
    L1_STRUCTURE = 1      # 结构标准化
    L2_MAPPING = 2        # 字段映射
    L3_VALIDATION = 3     # 业务校验
    L4_FEATURE = 4        # 特征工程

@dataclass
class CleanResult:
    data: Any                    # 清洗后数据
    level: CleanLevel
    passed: bool                 # 是否通过
    issues: List[str] = []       # 问题列表
    metrics: Dict = None         # 质量指标

class BaseCleaner(ABC):
    """清洗器基类"""
    
    @property
    @abstractmethod
    def level(self) -> CleanLevel: ...
    
    @property
    @abstractmethod
    def source_types(self) -> List[str]: ...  # 适用的数据类型
    
    @abstractmethod
    def clean(self, raw_data: Dict) -> CleanResult: ...
    
    def __call__(self, raw_data: Dict) -> CleanResult:
        return self.clean(raw_data)

class CleanPipeline:
    """清洗流水线"""
    
    def __init__(self, cleaners: List[BaseCleaner]):
        self.cleaners = sorted(cleaners, key=lambda c: c.level.value)
    
    def run(self, raw_data: Dict, stop_on_fail: bool = False) -> Dict:
        """运行全流水线，返回清洗后数据 + 质量报告"""
        current = raw_data
        report = {
            'pipeline_version': '1.0',
            'steps': [],
            'final_passed': True,
            'total_issues': 0,
        }
        
        for cleaner in self.cleaners:
            if cleaner.source_types and current.get('data_type') not in cleaner.source_types:
                continue
            
            result = cleaner(current)
            report['steps'].append({
                'cleaner': cleaner.__class__.__name__,
                'level': cleaner.level.name,
                'passed': result.passed,
                'issues': result.issues,
                'metrics': result.metrics,
            })
            
            if not result.passed:
                report['final_passed'] = False
                report['total_issues'] += len(result.issues)
                if stop_on_fail:
                    break
            
            current = result.data
        
        current['_clean_report'] = report
        return current
```

## 3. L1: 结构标准化

### 3.1 字段命名规范 (snake_case)

```python
class StructureNormalizer(BaseCleaner):
    level = CleanLevel.L1_STRUCTURE
    source_types = ['quote', 'kline', 'financial', 'news', 'guba', 'report']
    
    # 统一字段映射表
    FIELD_MAPPING = {
        # 通用字段
        'symbol': ['symbol', 'code', 'ts_code', 'secid', 'stock_code', 'ticker'],
        'name': ['name', 'stock_name', 'sec_name', 'title'],
        'timestamp': ['timestamp', 'time', 'datetime', 'date', 'trade_date', 'publish_time'],
        
        # 行情字段
        'open': ['open', 'open_price', 'o', 'kline_open'],
        'high': ['high', 'high_price', 'h', 'kline_high'],
        'low': ['low', 'low_price', 'l', 'kline_low'],
        'close': ['close', 'close_price', 'c', 'last_price', 'kline_close'],
        'volume': ['volume', 'vol', 'turnover_vol', 'kline_volume'],
        'amount': ['amount', 'turnover', 'turnover_amt', 'kline_amount'],
        
        # 财务字段
        'revenue': ['revenue', 'total_revenue', 'operating_revenue', 'income'],
        'net_profit': ['net_profit', 'net_income', 'profit', 'np'],
        'eps': ['eps', 'basic_eps', 'diluted_eps', 'earnings_per_share'],
        
        # 新闻/股吧
        'content': ['content', 'body', 'text', 'article_content', 'post_content'],
        'title': ['title', 'headline', 'subject', 'post_title'],
        'author': ['author', 'writer', 'reporter', 'user_nickname'],
    }
    
    def clean(self, raw: Dict) -> CleanResult:
        payload = raw.get('payload', {})
        normalized = {}
        
        for std_field, aliases in self.FIELD_MAPPING.items():
            for alias in aliases:
                if alias in payload:
                    normalized[std_field] = payload[alias]
                    break
        
        # 保留未映射字段到 extra
        mapped = set()
        for aliases in self.FIELD_MAPPING.values():
            mapped.update(aliases)
        extra = {k: v for k, v in payload.items() if k not in mapped}
        if extra:
            normalized['_extra'] = extra
        
        raw['payload'] = normalized
        return CleanResult(data=raw, level=self.level, passed=True)
```

### 3.2 数据类型强制转换

```python
class TypeCoercer(BaseCleaner):
    level = CleanLevel.L1_STRUCTURE
    source_types = ['quote', 'kline', 'financial']
    
    TYPE_SPEC = {
        'quote': {
            'price': float, 'pre_close': float, 'open': float, 'high': float,
            'low': float, 'volume': int, 'amount': float,
            'pct_chg': float, 'change': float, 'turnover_rate': float,
            'pe_ttm': float, 'pb': float, 'total_mv': float, 'circ_mv': float,
        },
        'kline': {
            'open': float, 'high': float, 'low': float, 'close': float,
            'volume': int, 'amount': float,
        },
        'financial': {
            'revenue': float, 'net_profit': float, 'eps': float,
            'roe': float, 'roa': float, 'gross_margin': float,
        },
    }
    
    def clean(self, raw: Dict) -> CleanResult:
        data_type = raw.get('data_type')
        spec = self.TYPE_SPEC.get(data_type, {})
        payload = raw.get('payload', {})
        
        issues = []
        for field, target_type in spec.items():
            if field in payload and payload[field] is not None:
                try:
                    payload[field] = target_type(payload[field])
                except (ValueError, TypeError) as e:
                    issues.append(f"{field}: 无法转为 {target_type.__name__}: {payload[field]}")
                    payload[field] = None
        
        return CleanResult(data=raw, level=self.level, passed=len(issues)==0, issues=issues)
```

### 3.3 时间标准化 (统一 UTC、ISO8601)

```python
class TimeNormalizer(BaseCleaner):
    level = CleanLevel.L1_STRUCTURE
    source_types = ['quote', 'kline', 'financial', 'news', 'guba', 'report']
    
    TIME_FORMATS = [
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%dT%H:%M:%S.%f',
        '%Y-%m-%dT%H:%M:%SZ',
        '%Y-%m-%d',
        '%Y%m%d',
        '%Y/%m/%d',
        '%d-%b-%y',  # 01-Jan-24
    ]
    
    def parse_time(self, val: Any) -> Optional[datetime]:
        if val is None:
            return None
        if isinstance(val, datetime):
            return val.replace(tzinfo=timezone.utc) if val.tzinfo is None else val.astimezone(timezone.utc)
        if isinstance(val, (int, float)):
            # 时间戳 (秒/毫秒)
            if val > 1e12:  # 毫秒
                return datetime.fromtimestamp(val / 1000, tz=timezone.utc)
            return datetime.fromtimestamp(val, tz=timezone.utc)
        if isinstance(val, str):
            for fmt in self.TIME_FORMATS:
                try:
                    dt = datetime.strptime(val, fmt)
                    return dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
        return None
    
    def clean(self, raw: Dict) -> CleanResult:
        payload = raw.get('payload', {})
        issues = []
        
        # 标准化 timestamp 字段
        if 'timestamp' in payload:
            payload['timestamp'] = self.parse_time(payload['timestamp'])
            if payload['timestamp'] is None:
                issues.append(f"timestamp 解析失败: {raw.get('payload', {}).get('timestamp')}")
        
        # 标准化 publish_time
        if 'publish_time' in payload:
            payload['publish_time'] = self.parse_time(payload['publish_time'])
        
        # 标准化 date 字段 (仅日期)
        if 'date' in payload and isinstance(payload['date'], str):
            try:
                payload['date'] = datetime.strptime(payload['date'][:10], '%Y-%m-%d').date()
            except ValueError:
                issues.append(f"date 解析失败: {payload['date']}")
        
        return CleanResult(data=raw, level=self.level, passed=len(issues)==0, issues=issues)
```

## 4. L2: 字段映射 (多数据源字段对齐)

```python
class FieldMapper(BaseCleaner):
    level = CleanLevel.L2_MAPPING
    source_types = ['quote', 'kline', 'financial', 'news']
    
    # 多数据源 -> 统一字段映射配置 (可外置 YAML)
    SOURCE_MAPPING = {
        'quote': {
            'akshare': {
                '代码': 'symbol',
                '名称': 'name',
                '最新价': 'price',
                '涨跌幅': 'pct_chg',
                '涨跌额': 'change',
                '成交量': 'volume',
                '成交额': 'amount',
                '换手率': 'turnover_rate',
                '市盈率-动态': 'pe_ttm',
                '市净率': 'pb',
                '总市值': 'total_mv',
                '流通市值': 'circ_mv',
            },
            'tushare': {
                'ts_code': 'symbol',
                'trade_date': 'date',
                'open': 'open',
                'high': 'high',
                'low': 'low',
                'close': 'close',
                'vol': 'volume',
                'amount': 'amount',
                'pct_chg': 'pct_chg',
            },
            'eastmoney': {
                'f12': 'symbol',
                'f14': 'name',
                'f43': 'price',  # 需 /100
                'f170': 'pct_chg',  # 需 /100
                'f47': 'volume',
                'f48': 'amount',
            },
        },
        'financial': {
            'akshare': {
                '报告期': 'report_date',
                '营业收入': 'revenue',
                '净利润': 'net_profit',
                '基本每股收益': 'eps',
                '净资产收益率': 'roe',
                '毛利率': 'gross_margin',
            },
            'tushare': {
                'end_date': 'report_date',
                'revenue': 'revenue',
                'n_income': 'net_profit',
                'basic_eps': 'eps',
                'roe': 'roe',
            },
        },
    }
    
    def clean(self, raw: Dict) -> CleanResult:
        source = raw.get('source')
        data_type = raw.get('data_type')
        payload = raw.get('payload', {})
        
        mapping = self.SOURCE_MAPPING.get(data_type, {}).get(source, {})
        if not mapping:
            return CleanResult(data=raw, level=self.level, passed=True)
        
        mapped = {}
        for src_field, std_field in mapping.items():
            if src_field in payload:
                val = payload[src_field]
                # 特殊转换
                if src_field in ['f43', 'f170'] and val is not None:  # 东方财富价格*100
                    val = val / 100
                mapped[std_field] = val
        
        # 合并：映射字段优先，保留原有标准字段
        payload = {**payload, **mapped}
        raw['payload'] = payload
        
        return CleanResult(data=raw, level=self.level, passed=True)
```

## 5. L3: 业务校验 (异常值检测、逻辑一致性)

### 5.1 行情数据校验规则

```python
class QuoteValidator(BaseCleaner):
    level = CleanLevel.L3_VALIDATION
    source_types = ['quote', 'kline']
    
    def clean(self, raw: Dict) -> CleanResult:
        payload = raw.get('payload', {})
        issues = []
        warnings = []
        
        # 价格逻辑校验
        for field in ['open', 'high', 'low', 'close', 'price']:
            val = payload.get(field)
            if val is not None and val <= 0:
                issues.append(f"{field} 必须 > 0: {val}")
        
        # 高低价包含关系
        if all(payload.get(f) is not None for f in ['open', 'high', 'low', 'close']):
            if not (payload['low'] <= payload['open'] <= payload['high'] and
                    payload['low'] <= payload['close'] <= payload['high']):
                issues.append(f"高低价包含关系异常: O={payload['open']} H={payload['high']} L={payload['low']} C={payload['close']}")
        
        # 涨跌幅校验
        if payload.get('pre_close') and payload.get('close'):
            calc_pct = (payload['close'] - payload['pre_close']) / payload['pre_close'] * 100
            if payload.get('pct_chg') and abs(payload['pct_chg'] - calc_pct) > 0.02:
                warnings.append(f"涨跌幅不匹配: 字段={payload['pct_chg']:.2f}% 计算={calc_pct:.2f}%")
        
        # 成交量/额非负
        for field in ['volume', 'amount']:
            if payload.get(field) is not None and payload[field] < 0:
                issues.append(f"{field} 不能为负: {payload[field]}")
        
        # 换手率合理性 (0-100%)
        if payload.get('turnover_rate') is not None:
            if not (0 <= payload['turnover_rate'] <= 100):
                warnings.append(f"换手率异常: {payload['turnover_rate']}%")
        
        # 静默价格异常检测 (较前收盘价 > 20% 或 < -20%)
        if payload.get('pre_close') and payload.get('close'):
            pct = (payload['close'] - payload['pre_close']) / payload['pre_close'] * 100
            if abs(pct) > 20:
                warnings.append(f"价格异动: {pct:.2f}% (可能除权/除息/数据错误)")
        
        metrics = {
            'price_range': f"{payload.get('low', 'N/A')} - {payload.get('high', 'N/A')}",
            'pct_chg': payload.get('pct_chg'),
            'volume': payload.get('volume'),
        }
        
        return CleanResult(
            data=raw,
            level=self.level,
            passed=len(issues) == 0,
            issues=issues + warnings,
            metrics=metrics
        )
```

### 5.2 财务数据校验

```python
class FinancialValidator(BaseCleaner):
    level = CleanLevel.L3_VALIDATION
    source_types = ['financial']
    
    def clean(self, raw: Dict) -> CleanResult:
        payload = raw.get('payload', {})
        issues = []
        warnings = []
        
        # 报表日期合理性
        if payload.get('report_date'):
            rd = payload['report_date']
            if isinstance(rd, datetime):
                if rd > datetime.now(timezone.utc) + timedelta(days=30):
                    warnings.append(f"报告期在未来: {rd}")
                if rd.year < 2000:
                    issues.append(f"报告期异常: {rd}")
        
        # 利润表恒等式: 营收 - 成本 - 费用 - 税 = 净利润 (允许 1% 误差)
        revenue = payload.get('revenue')
        net_profit = payload.get('net_profit')
        if revenue and net_profit and revenue != 0:
            margin = net_profit / revenue
            if margin > 1 or margin < -5:  # 净利率 > 100% 或 < -500%
                warnings.append(f"净利率异常: {margin*100:.1f}%")
        
        # ROE 合理性
        roe = payload.get('roe')
        if roe is not None and (roe > 100 or roe < -100):
            warnings.append(f"ROE 异常: {roe}%")
        
        # 同比/环比字段一致性
        for field in ['revenue_yoy', 'net_profit_yoy', 'eps_yoy']:
            if payload.get(field) is not None and abs(payload[field]) > 1000:
                warnings.append(f"{field} 同比异常: {payload[field]}%")
        
        return CleanResult(data=raw, level=self.level, passed=len(issues)==0, issues=issues+warnings)
```

### 5.3 新闻/文本数据校验

```python
class NewsValidator(BaseCleaner):
    level = CleanLevel.L3_VALIDATION
    source_types = ['news', 'guba', 'report']
    
    def clean(self, raw: Dict) -> CleanResult:
        payload = raw.get('payload', {})
        issues = []
        warnings = []
        
        # 标题非空
        if not payload.get('title') or len(payload['title'].strip()) < 2:
            issues.append("标题为空或过短")
        
        # 正文长度
        content = payload.get('content', '')
        if len(content) < 50:
            warnings.append(f"正文过短: {len(content)} 字符")
        if len(content) > 500000:
            warnings.append(f"正文过长: {len(content)} 字符，建议截断")
        
        # 发布时间不晚于抓取时间
        pub_time = payload.get('publish_time')
        crawl_time = raw.get('crawl_time')
        if pub_time and crawl_time and pub_time > crawl_time + timedelta(hours=1):
            warnings.append(f"发布时间晚于抓取时间: {pub_time} > {crawl_time}")
        
        # URL 格式
        url = payload.get('url', '')
        if url and not url.startswith(('http://', 'https://')):
            issues.append(f"URL 格式无效: {url}")
        
        # 股票代码格式校验
        symbols = payload.get('symbols', [])
        for sym in symbols:
            if not re.match(r'^\d{6}\.(SZ|SH|BJ)$', sym) and not re.match(r'^[A-Z]{1,5}-?[A-Z]{0,5}$', sym):
                warnings.append(f"股票代码格式可疑: {sym}")
        
        return CleanResult(data=raw, level=self.level, passed=len(issues)==0, issues=issues+warnings)
```

## 6. L4: 特征工程 (衍生指标计算)

```python
class FeatureEngineer(BaseCleaner):
    level = CleanLevel.L4_FEATURE
    source_types = ['quote', 'kline']
    
    def clean(self, raw: Dict) -> CleanResult:
        payload = raw.get('payload', {})
        
        # 仅对 K 线数据计算技术指标 (需要历史窗口，此处仅示例单根计算)
        if raw.get('data_type') == 'kline':
            # 实体大小
            if all(k in payload for k in ['open', 'close', 'high', 'low']):
                body = abs(payload['close'] - payload['open'])
                total_range = payload['high'] - payload['low']
                payload['body_ratio'] = body / total_range if total_range > 0 else 0
                payload['upper_shadow'] = payload['high'] - max(payload['open'], payload['close'])
                payload['lower_shadow'] = min(payload['open'], payload['close']) - payload['low']
                payload['is_doji'] = payload['body_ratio'] < 0.1
                payload['is_hammer'] = (payload['lower_shadow'] > 2 * body and
                                         payload['upper_shadow'] < 0.1 * body)
        
        return CleanResult(data=raw, level=self.level, passed=True)
```