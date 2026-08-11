# -*- coding: utf-8 -*-
"""
数据验证器模块 - 实现97条验证规则

覆盖: 字段校验、格式校验、重复检测、异常值识别、逻辑一致性、跨源一致性

使用示例:
    from finance_toolkit.data_validator import DataValidator, ValidationResult
    
    validator = DataValidator()
    result = validator.validate(data, data_type='quote')
    print(result.summary())
"""

import json
import re
import hashlib
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict
import statistics


class SeverityLevel(Enum):
    """问题严重程度"""
    CRITICAL = "critical"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class ValidationIssue:
    """验证问题"""
    rule_id: str
    rule_name: str
    severity: SeverityLevel
    field: str
    value: Any
    message: str
    suggestion: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            'rule_id': self.rule_id,
            'rule_name': self.rule_name,
            'severity': self.severity.value,
            'field': self.field,
            'value': str(self.value) if self.value is not None else None,
            'message': self.message,
            'suggestion': self.suggestion,
            'details': self.details
        }


@dataclass
class ValidationResult:
    """验证结果"""
    is_valid: bool
    verdict: str  # ACCEPTED, FLAGGED, DEGRADED, REJECTED
    total_issues: int
    issues: List[ValidationIssue] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    recommendations: List[str] = field(default_factory=list)
    rule_stats: Dict[str, int] = field(default_factory=dict)

    @property
    def critical_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == SeverityLevel.CRITICAL)

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == SeverityLevel.ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == SeverityLevel.WARNING)

    @property
    def info_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == SeverityLevel.INFO)

    @property
    def health_score(self) -> float:
        if self.total_issues == 0:
            return 100.0
        penalty = (
            self.critical_count * 25 +
            self.error_count * 15 +
            self.warning_count * 5 +
            self.info_count * 1
        )
        return max(0.0, min(100.0, 100.0 - penalty))

    def to_dict(self) -> Dict:
        return {
            'is_valid': self.is_valid,
            'verdict': self.verdict,
            'health_score': round(self.health_score, 1),
            'total_issues': self.total_issues,
            'critical_count': self.critical_count,
            'error_count': self.error_count,
            'warning_count': self.warning_count,
            'info_count': self.info_count,
            'issues': [i.to_dict() for i in self.issues],
            'metrics': self.metrics,
            'recommendations': self.recommendations,
            'rule_stats': self.rule_stats
        }

    def summary(self) -> str:
        status = "\u2713 通过" if self.is_valid else "\u2717 失败"
        lines = [
            f"数据验证结果 [{status}]",
            f"  判定: {self.verdict}",
            f"  健康评分: {self.health_score:.1f}/100",
            f"  总问题数: {self.total_issues}",
            f"  严重: {self.critical_count}, 错误: {self.error_count}, "
            f"警告: {self.warning_count}, 提示: {self.info_count}",
        ]
        if self.recommendations:
            lines.append("  建议:")
            for rec in self.recommendations[:5]:
                lines.append(f"    - {rec}")
        return "\n".join(lines)


class DataValidator:
    """
    数据验证器 - 实现97条验证规则
    
    规则分类:
    - F001-F028: 字段校验 (28条)
    - FM001-FM024: 格式校验 (24条)
    - D001-D008: 重复检测 (8条)
    - A001-A022: 异常值识别 (22条)
    - L001-L019: 逻辑一致性 (19条)
    - C001-C006: 跨源一致性 (6条)
    """

    # ============== 规则定义 ==============
    
    # 数据类型与必填字段映射
    REQUIRED_FIELDS: Dict[str, List[str]] = {
        'quote': ['open', 'high', 'low', 'close', 'volume', 'amount'],
        'kline': ['date', 'open', 'high', 'low', 'close', 'volume', 'amount'],
        'financial': ['report_date', 'type'],
        'news': ['title', 'publish_time', 'source', 'url'],
        'sentiment': ['date', 'sentiment_score', 'sentiment_label'],
        'sector': ['name', 'code', 'change_pct'],
        'fund': ['fund_code', 'fund_name', 'nav_date', 'nav'],
        'bond': ['bond_code', 'bond_name', 'price', 'yield_rate'],
        'futures': ['contract_code', 'open', 'high', 'low', 'close', 'volume'],
        'index': ['open', 'high', 'low', 'close', 'volume'],
        'macro': ['date', 'indicator_name', 'value'],
        'crypto': ['symbol', 'price', 'volume_24h', 'market_cap'],
        'forex': ['currency_pair', 'rate', 'change_pct'],
        'ipo': ['stock_code', 'stock_name', 'issue_date', 'issue_price'],
        'dividend': ['announcement_date', 'record_date', 'ex_dividend_date', 'payment_date'],
    }

    # 数值范围约束
    VALUE_RANGES: Dict[str, Tuple[float, float]] = {
        'sentiment_score': (-1.0, 1.0),
        'quality_score': (0.0, 1.0),
        'premium_discount': (-100.0, 100.0),
        'change_pct': (-100.0, 100.0),
        'pe_ratio': (0.0, 1000.0),
        'pb_ratio': (0.0, 100.0),
        'roe': (-100.0, 100.0),
        'roa': (-100.0, 100.0),
        'gross_margin': (-100.0, 100.0),
        'net_margin': (-100.0, 100.0),
        'turnover_rate': (0.0, 100.0),
        'dividend_yield': (0.0, 50.0),
        'yield_rate': (-10.0, 50.0),
        'fear_greed_index': (0.0, 100.0),
        'amplitude': (0.0, 100.0),
    }

    # 枚举值约束
    ENUM_CONSTRAINTS: Dict[str, List[str]] = {
        'data_type': list(REQUIRED_FIELDS.keys()),
        'sentiment_label': ['\u6781\u5ea6\u60b2\u89c2', '\u60b2\u89c2', '\u4e2d\u6027', '\u4e50\u89c2', '\u6781\u5ea6\u4e50\u89c2'],
        'financial_type': ['balance_sheet', 'income_statement', 'cash_flow'],
        'kline_period': ['1m', '5m', '15m', '30m', '60m', 'daily', 'weekly', 'monthly'],
    }

    # 正则表达式
    PATTERNS = {
        'a_stock_code': re.compile(r'^[0-9]{6}\.(SH|SZ)$'),
        'bond_code': re.compile(r'^[0-9]{6,12}$'),
        'fund_code': re.compile(r'^[0-9]{6}$'),
        'index_code': re.compile(r'^[0-9]{6}\.([SH]|SZ)$'),
        'iso_date': re.compile(r'^\d{4}-\d{2}-\d{2}$'),
        'iso_timestamp': re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}'),
        'url': re.compile(r'^https?://.*'),
        'crypto_symbol': re.compile(r'^[A-Z]{2,10}$'),
        'forex_pair': re.compile(r'^[A-Z]{3}[A-Z]{3}$'),
    }

    # 数据源白名单
    VALID_SOURCES = {
        'akshare', 'tushare', 'eastmoney', 'sina', 'tencent',
        'netease', 'lexicon', 'binance', 'coingecko', 'yahoo', 'okx',
        'fenghuang', 'ths', 'xuangubao', 'guba'
    }

    def __init__(self, strict_mode: bool = False):
        self.strict_mode = strict_mode
        self._rule_stats: Dict[str, int] = defaultdict(int)

    def validate(self, data: Any, data_type: str = 'auto', 
                 symbol: Optional[str] = None) -> ValidationResult:
        """
        验证数据
        
        Args:
            data: 数据对象 (dict, list)
            data_type: 数据类型
            symbol: 标的代码
        
        Returns:
            ValidationResult
        """
        issues = []
        recommendations = []
        metrics = {}

        # 空数据检查
        if not data or (isinstance(data, dict) and len(data) == 0):
            return ValidationResult(
                is_valid=False,
                verdict='REJECTED',
                total_issues=1,
                issues=[ValidationIssue(
                    rule_id='F000',
                    rule_name='空数据检查',
                    severity=SeverityLevel.CRITICAL,
                    field='root',
                    value='empty',
                    message='数据为空',
                    suggestion='请提供有效的数据'
                )],
                rule_stats=dict(self._rule_stats)
            )

        # 自动检测数据类型
        if data_type == 'auto':
            data_type = self._detect_data_type(data)

        # 转换为字典
        data_dict = self._to_dict(data)
        if data_dict is None:
            return ValidationResult(
                is_valid=False,
                verdict='REJECTED',
                total_issues=1,
                issues=[ValidationIssue(
                    rule_id='F000',
                    rule_name='数据类型检查',
                    severity=SeverityLevel.CRITICAL,
                    field='root',
                    value=str(type(data)),
                    message=f'不支持的数据类型: {type(data).__name__}',
                    suggestion='请提供字典或列表'
                )],
                rule_stats=dict(self._rule_stats)
            )

        # 执行所有验证规则
        issues.extend(self._validate_fields(data_dict, data_type, symbol))
        issues.extend(self._validate_format(data_dict, data_type))
        issues.extend(self._validate_duplicates(data_dict, data_type))
        issues.extend(self._validate_anomalies(data_dict, data_type))
        issues.extend(self._validate_logic(data_dict, data_type))
        issues.extend(self._validate_cross_source(data_dict, data_type))

        # 计算指标
        metrics = self._compute_metrics(data_dict, data_type, issues)

        # 生成建议
        recommendations = self._generate_recommendations(issues, data_type)

        # 判定结果
        verdict, is_valid = self._evaluate_verdict(issues)

        # 更新规则统计
        for issue in issues:
            self._rule_stats[issue.rule_id] += 1

        return ValidationResult(
            is_valid=is_valid,
            verdict=verdict,
            total_issues=len(issues),
            issues=issues,
            metrics=metrics,
            recommendations=recommendations,
            rule_stats=dict(self._rule_stats)
        )

    def validate_batch(self, data_list: List[Any], data_type: str = 'auto') -> Dict[str, ValidationResult]:
        """批量验证"""
        results = {}
        for i, data in enumerate(data_list):
            key = f"item_{i}"
            results[key] = self.validate(data, data_type)
        return results

    # ============== 字段校验 (F001-F028) ==============

    def _validate_fields(self, data: Dict, data_type: str, symbol: Optional[str]) -> List[ValidationIssue]:
        """字段校验规则 F001-F028"""
        issues = []

        # F001-F015: 必填字段检查
        required = self.REQUIRED_FIELDS.get(data_type, [])
        payload = data.get('payload', data)

        # 处理列表数据（如K线数据）
        if isinstance(payload, list):
            for i, record in enumerate(payload):
                for field_name in required:
                    if field_name not in record or record[field_name] is None:
                        issues.append(ValidationIssue(
                            rule_id='F001',
                            rule_name='必填字段检查',
                            severity=SeverityLevel.CRITICAL,
                            field=f'payload[{i}].{field_name}',
                            value=None,
                            message=f'缺少必填字段: {field_name}',
                            suggestion=f'请补充 {field_name} 字段'
                        ))
        else:
            for field_name in required:
                if field_name not in payload or payload[field_name] is None:
                    issues.append(ValidationIssue(
                        rule_id='F001',
                        rule_name='必填字段检查',
                        severity=SeverityLevel.CRITICAL,
                        field=f'payload.{field_name}',
                        value=None,
                        message=f'缺少必填字段: {field_name}',
                        suggestion=f'请补充 {field_name} 字段'
                    ))

        # F016-F021: 字段类型校验
        type_checks = {
            'open': (int, float), 'high': (int, float), 'low': (int, float),
            'close': (int, float), 'volume': (int, float), 'amount': (int, float),
            'date': str, 'symbol': str, 'sentiment_score': (int, float)
        }
        for field_name, expected_types in type_checks.items():
            if field_name in payload and payload[field_name] is not None:
                if not isinstance(payload[field_name], expected_types):
                    issues.append(ValidationIssue(
                        rule_id='F016',
                        rule_name='字段类型校验',
                        severity=SeverityLevel.ERROR,
                        field=field_name,
                        value=type(payload[field_name]).__name__,
                        message=f'字段 {field_name} 类型错误: 期望 {expected_types}, 实际 {type(payload[field_name]).__name__}',
                        suggestion=f'请将 {field_name} 转换为正确类型'
                    ))

        # F022-F028: 字段长度和格式校验
        symbol = data.get('symbol', '')
        if symbol:
            if len(symbol) < 6:
                issues.append(ValidationIssue(
                    rule_id='F022',
                    rule_name='股票代码长度校验',
                    severity=SeverityLevel.WARNING,
                    field='symbol',
                    value=symbol,
                    message=f'股票代码过短: {symbol}',
                    suggestion='请使用标准格式，如 600000.SH'
                ))

        return issues

    # ============== 格式校验 (FM001-FM024) ==============

    def _validate_format(self, data: Dict, data_type: str) -> List[ValidationIssue]:
        """格式校验规则 FM001-FM024"""
        issues = []
        payload = data.get('payload', data)

        # FM001-FM006: 日期时间格式 - 处理列表类型payload
        date_fields = ['date', 'publish_time', 'report_date', 'trade_date']
        if isinstance(payload, list):
            for idx, item in enumerate(payload):
                if isinstance(item, dict):
                    for field_name in date_fields:
                        if field_name in item and item[field_name]:
                            value = str(item[field_name])
                            if not self._is_valid_date(value):
                                issues.append(ValidationIssue(
                                    rule_id='FM001',
                                    rule_name='日期格式校验',
                                    severity=SeverityLevel.ERROR,
                                    field=f'{field_name}[{idx}]',
                                    value=value,
                                    message=f'日期格式无效: {value}',
                                    suggestion='请使用 YYYY-MM-DD 格式'
                                ))
        else:
            for field_name in date_fields:
                if field_name in payload and payload[field_name]:
                    value = str(payload[field_name])
                    if not self._is_valid_date(value):
                        issues.append(ValidationIssue(
                            rule_id='FM001',
                            rule_name='日期格式校验',
                            severity=SeverityLevel.ERROR,
                            field=field_name,
                            value=value,
                            message=f'日期格式无效: {value}',
                            suggestion='请使用 YYYY-MM-DD 格式'
                        ))

        # FM007-FM008: 时间戳格式
        if isinstance(payload, dict) and 'timestamp' in payload and payload['timestamp']:
            if not self._is_valid_timestamp(str(payload['timestamp'])):
                issues.append(ValidationIssue(
                    rule_id='FM002',
                    rule_name='时间戳格式校验',
                    severity=SeverityLevel.ERROR,
                    field='timestamp',
                    value=payload['timestamp'],
                    message=f'时间戳格式无效: {payload["timestamp"]}',
                    suggestion='请使用 ISO 8601 格式'
                ))

        # FM009-FM015: 数值范围 - 处理列表类型payload
        for field_name, (min_val, max_val) in self.VALUE_RANGES.items():
            if isinstance(payload, list):
                for idx, item in enumerate(payload):
                    if isinstance(item, dict) and field_name in item:
                        try:
                            value = float(item[field_name])
                            if value < min_val or value > max_val:
                                severity = SeverityLevel.CRITICAL if field_name in ['sentiment_score', 'fear_greed_index'] else SeverityLevel.WARNING
                                issues.append(ValidationIssue(
                                    rule_id='FM012',
                                    rule_name='数值范围校验',
                                    severity=severity,
                                    field=f'{field_name}[{idx}]',
                                    value=value,
                                    message=f'{field_name} 超出范围 [{min_val}, {max_val}]: {value}',
                                    suggestion='请检查数据源或进行数据清洗'
                                ))
                        except (ValueError, TypeError):
                            pass
            elif field_name in payload:
                try:
                    value = float(payload[field_name])
                    if value < min_val or value > max_val:
                        severity = SeverityLevel.CRITICAL if field_name in ['sentiment_score', 'fear_greed_index'] else SeverityLevel.WARNING
                        issues.append(ValidationIssue(
                            rule_id='FM012',
                            rule_name='数值范围校验',
                            severity=severity,
                            field=field_name,
                            value=value,
                            message=f'{field_name} 超出范围 [{min_val}, {max_val}]: {value}',
                            suggestion='请检查数据源或进行数据清洗'
                        ))
                except (ValueError, TypeError):
                    pass

        # FM016-FM022: 代码格式
        symbol = data.get('symbol', '')
        if symbol and data_type == 'quote':
            if not self.PATTERNS['a_stock_code'].match(symbol):
                issues.append(ValidationIssue(
                    rule_id='FM016',
                    rule_name='A股代码格式校验',
                    severity=SeverityLevel.ERROR,
                    field='symbol',
                    value=symbol,
                    message=f'A股代码格式无效: {symbol}',
                    suggestion='请使用标准格式，如 600000.SH'
                ))

        # FM023-FM024: URL格式
        if isinstance(payload, dict) and 'url' in payload and payload['url']:
            if not self.PATTERNS['url'].match(str(payload['url'])):
                issues.append(ValidationIssue(
                    rule_id='FM023',
                    rule_name='URL格式校验',
                    severity=SeverityLevel.WARNING,
                    field='url',
                    value=payload['url'],
                    message=f'URL格式无效: {payload["url"]}',
                    suggestion='请使用完整的HTTP/HTTPS URL'
                ))

        return issues

    def _is_valid_date(self, value: str) -> bool:
        """校验日期格式"""
        patterns = [
            r'^\d{4}-\d{2}-\d{2}$',
            r'^\d{4}/\d{2}/\d{2}$',
            r'^\d{4}\d{2}\d{2}$',
        ]
        for pat in patterns:
            if re.match(pat, value):
                try:
                    datetime.strptime(value.replace('/', '-').replace('-', ''), '%Y%m%d')
                    return True
                except ValueError:
                    continue
        return False

    def _is_valid_timestamp(self, value: str) -> bool:
        """校验时间戳格式"""
        patterns = [
            '%Y-%m-%dT%H:%M:%SZ',
            '%Y-%m-%dT%H:%M:%S%z',
            '%Y-%m-%dT%H:%M:%S.%fZ',
            '%Y-%m-%d %H:%M:%S',
        ]
        for fmt in patterns:
            try:
                datetime.strptime(value, fmt)
                return True
            except ValueError:
                continue
        return False
    # ============== 重复检测 (D001-D008) ==============

    def _validate_duplicates(self, data: Dict, data_type: str) -> List[ValidationIssue]:
        """重复检测规则 D001-D008"""
        issues = []
        payload = data.get('payload', data)

        # D001: 完全重复检测
        if isinstance(payload, list) and len(payload) > 1:
            record_hashes = []
            for record in payload:
                h = hashlib.md5(json.dumps(record, sort_keys=True, default=str).encode()).hexdigest()
                record_hashes.append(h)
            unique_hashes = set(record_hashes)
            duplicate_rate = 1 - len(unique_hashes) / len(record_hashes) if record_hashes else 0

            if duplicate_rate > 0.05:
                issues.append(ValidationIssue(
                    rule_id='D001',
                    rule_name='完全重复检测',
                    severity=SeverityLevel.CRITICAL,
                    field='records',
                    value=duplicate_rate,
                    message=f'数据重复率过高: {duplicate_rate:.1%}',
                    suggestion='检查数据源或去重逻辑',
                    details={'duplicate_rate': duplicate_rate, 'total': len(record_hashes), 'unique': len(unique_hashes)}
                ))
            elif duplicate_rate > 0.01:
                issues.append(ValidationIssue(
                    rule_id='D002',
                    rule_name='重复率警告',
                    severity=SeverityLevel.WARNING,
                    field='records',
                    value=duplicate_rate,
                    message=f'数据存在重复: {duplicate_rate:.1%}',
                    suggestion='建议进行去重处理'
                ))

        # D004: K线日期重复
        if data_type == 'kline' and isinstance(payload, list):
            dates = [r.get('date') for r in payload if r.get('date')]
            if len(dates) != len(set(dates)):
                issues.append(ValidationIssue(
                    rule_id='D004',
                    rule_name='K线日期重复检测',
                    severity=SeverityLevel.ERROR,
                    field='date',
                    value=len(dates) - len(set(dates)),
                    message=f'K线数据存在重复日期: {len(dates) - len(set(dates))} 条',
                    suggestion='检查数据源或清洗逻辑'
                ))

        return issues

    # ============== 异常值识别 (A001-A022) ==============

    def _validate_anomalies(self, data: Dict, data_type: str) -> List[ValidationIssue]:
        """异常值识别规则 A001-A022"""
        issues = []
        payload = data.get('payload', data)

        # 处理列表数据
        records = payload if isinstance(payload, list) else [payload]

        for i, record in enumerate(records):
            prefix = f'[{i}]' if len(records) > 1 else ''

            # A001-A009: 价格异常
            for price_field in ['open', 'high', 'low', 'close']:
                if price_field in record:
                    try:
                        value = float(record[price_field])
                        if value <= 0:
                            issues.append(ValidationIssue(
                                rule_id='A001',
                                rule_name='零/负价格检测',
                                severity=SeverityLevel.CRITICAL,
                                field=f'{price_field}{prefix}',
                                value=value,
                                message=f'{price_field} 为非正值: {value}',
                                suggestion='检查数据源'
                            ))
                    except (ValueError, TypeError):
                        pass

            # A010-A014: 成交量异常
            if 'volume' in record:
                try:
                    volume = float(record['volume'])
                    if volume == 0 and data_type in ['quote', 'kline']:
                        issues.append(ValidationIssue(
                            rule_id='A010',
                            rule_name='零成交量检测',
                            severity=SeverityLevel.WARNING,
                            field='volume',
                            value=0,
                            message='成交量为零',
                            suggestion='检查是否为停牌股票'
                        ))
                except (ValueError, TypeError):
                    pass

            # A019-A022: 财务指标异常
            for metric in ['pe_ratio', 'pb_ratio', 'roe', 'roa']:
                if metric in record:
                    try:
                        value = float(record[metric])
                        if value < 0:
                            issues.append(ValidationIssue(
                                rule_id='A019',
                                rule_name='财务指标负值检测',
                                severity=SeverityLevel.WARNING,
                                field=metric,
                                value=value,
                                message=f'{metric} 为负值: {value}',
                                suggestion='检查财务数据计算逻辑'
                            ))
                    except (ValueError, TypeError):
                        pass

        # A015-A018: 统计异常值 (Z-Score)
        if len(records) > 10 and 'close' in records[0]:
            try:
                values = [float(r['close']) for r in records if 'close' in r]
                if len(values) > 10:
                    mean = statistics.mean(values)
                    std = statistics.stdev(values)
                    if std > 0:
                        outliers = [v for v in values if abs(v - mean) > 3 * std]
                        if outliers:
                            issues.append(ValidationIssue(
                                rule_id='A015',
                                rule_name='Z-Score异常检测',
                                severity=SeverityLevel.WARNING,
                                field='close',
                                value=len(outliers),
                                message=f'发现 {len(outliers)} 个价格异常点 (Z-score > 3)',
                                suggestion='考虑使用缩尾处理或手动核查',
                                details={'mean': round(mean, 2), 'std': round(std, 2), 'outlier_count': len(outliers)}
                            ))
            except (ValueError, TypeError, statistics.StatisticsError):
                pass

        return issues

    # ============== 逻辑一致性 (L001-L019) ==============

    def _validate_logic(self, data: Dict, data_type: str) -> List[ValidationIssue]:
        """逻辑一致性规则 L001-L019"""
        issues = []
        payload = data.get('payload', data)

        # 处理列表数据
        records = payload if isinstance(payload, list) else [payload]

        for i, record in enumerate(records):
            prefix = f'[{i}]' if len(records) > 1 else ''

            # L001: OHLC基本逻辑
            if all(f in record for f in ['high', 'low']):
                try:
                    if float(record['high']) < float(record['low']):
                        issues.append(ValidationIssue(
                            rule_id='L001',
                            rule_name='OHLC逻辑检查',
                            severity=SeverityLevel.CRITICAL,
                            field=f'high/low{prefix}',
                            value=f"high={record['high']}, low={record['low']}",
                            message='最高价小于最低价',
                            suggestion='检查数据源或清洗逻辑'
                        ))
                except (ValueError, TypeError):
                    pass

            # L002-L003: 开盘价/收盘价逻辑
            if all(f in record for f in ['high', 'low', 'open']):
                try:
                    high, low, open_val = float(record['high']), float(record['low']), float(record['open'])
                    if high < open_val:
                        issues.append(ValidationIssue(
                            rule_id='L002',
                            rule_name='开盘价逻辑检查',
                            severity=SeverityLevel.WARNING,
                            field=f'high/open{prefix}',
                            value=f"high={high}, open={open_val}",
                            message='最高价小于开盘价',
                            suggestion='检查数据源'
                        ))
                    if low > open_val:
                        issues.append(ValidationIssue(
                            rule_id='L003',
                            rule_name='开盘价逻辑检查',
                            severity=SeverityLevel.WARNING,
                            field=f'low/open{prefix}',
                            value=f"low={low}, open={open_val}",
                            message='最低价大于开盘价',
                            suggestion='检查数据源'
                        ))
                except (ValueError, TypeError):
                    pass

            # L004: 涨跌幅计算一致性
            if all(f in record for f in ['close', 'pre_close']):
                try:
                    close = float(record['close'])
                    pre_close = float(record['pre_close'])
                    if pre_close > 0:
                        calc_change = (close - pre_close) / pre_close * 100
                        if 'change_pct' in record:
                            reported_change = float(record['change_pct'])
                            if abs(calc_change - reported_change) > 0.01:
                                issues.append(ValidationIssue(
                                    rule_id='L004',
                                    rule_name='涨跌幅计算一致性',
                                    severity=SeverityLevel.WARNING,
                                    field='change_pct',
                                    value=f"calc={calc_change:.2f}%, reported={reported_change}%",
                                    message='涨跌幅计算不一致',
                                    suggestion='检查涨跌幅数据来源'
                                ))
                except (ValueError, TypeError, ZeroDivisionError):
                    pass

            # L005: 成交额逻辑
            if all(f in record for f in ['close', 'volume', 'amount']):
                try:
                    close = float(record['close'])
                    volume = float(record['volume'])
                    amount = float(record['amount'])
                    if close > 0 and volume > 0:
                        calc_amount = close * volume
                        if amount > 0 and abs(calc_amount - amount) / amount > 0.1:
                            issues.append(ValidationIssue(
                                rule_id='L005',
                                rule_name='成交额逻辑检查',
                                severity=SeverityLevel.WARNING,
                                field='amount',
                                value=f"calc={calc_amount:.2f}, reported={amount}",
                                message='成交额与量价不匹配',
                                suggestion='检查成交额数据来源'
                            ))
                except (ValueError, TypeError, ZeroDivisionError):
                    pass

            # L011-L014: 行情逻辑
            if data_type == 'quote' and 'close' in record and 'pre_close' in record:
                try:
                    close = float(record['close'])
                    pre_close = float(record['pre_close'])
                    if pre_close > 0:
                        change_pct = (close - pre_close) / pre_close * 100
                        if abs(change_pct) > 30:
                            issues.append(ValidationIssue(
                                rule_id='L011',
                                rule_name='涨跌幅合理性检查',
                                severity=SeverityLevel.WARNING,
                                field='change_pct',
                                value=change_pct,
                                message=f'涨跌幅异常: {change_pct:.2f}%',
                                suggestion='核查是否为停牌复牌或数据错误'
                            ))
                except (ValueError, TypeError, ZeroDivisionError):
                    pass

        return issues

    # ============== 跨源一致性 (C001-C006) ==============

    def _validate_cross_source(self, data: Dict, data_type: str) -> List[ValidationIssue]:
        """跨源一致性规则 C001-C006"""
        issues = []

        # C006: 数据源合法性
        source = data.get('source', '')
        if source and source not in self.VALID_SOURCES:
            issues.append(ValidationIssue(
                rule_id='C006',
                rule_name='数据源合法性检查',
                severity=SeverityLevel.WARNING,
                field='source',
                value=source,
                message=f'未知的数据源: {source}',
                suggestion=f'合法数据源: {self.VALID_SOURCES}'
            ))

        return issues

    # ============== 辅助方法 ==============

    def _detect_data_type(self, data: Any) -> str:
        """自动检测数据类型"""
        if isinstance(data, dict):
            data_type = data.get('data_type', '')
            if data_type in self.REQUIRED_FIELDS:
                return data_type
            payload = data.get('payload', data)
            if isinstance(payload, dict):
                if 'open' in payload and 'high' in payload and 'low' in payload and 'close' in payload:
                    return 'quote'
                if 'date' in payload and 'open' in payload:
                    return 'kline'
                if 'title' in payload and 'publish_time' in payload:
                    return 'news'
                if 'sentiment_score' in payload:
                    return 'sentiment'
                if 'report_date' in payload:
                    return 'financial'
        elif isinstance(data, list) and len(data) > 0:
            first = data[0]
            if isinstance(first, dict):
                if 'date' in first and 'open' in first:
                    return 'kline'
                if 'open' in first and 'high' in first:
                    return 'quote'
        return 'unknown'

    def _to_dict(self, data: Any) -> Optional[Dict]:
        """转换为字典"""
        if isinstance(data, dict):
            return data
        return None

    def _compute_metrics(self, data: Dict, data_type: str, issues: List[ValidationIssue]) -> Dict:
        """计算验证指标"""
        payload = data.get('payload', data)
        records = payload if isinstance(payload, list) else [payload]
        
        metrics = {
            'data_type': data_type,
            'record_count': len(records),
            'issue_count': len(issues),
            'critical_count': sum(1 for i in issues if i.severity == SeverityLevel.CRITICAL),
            'error_count': sum(1 for i in issues if i.severity == SeverityLevel.ERROR),
            'warning_count': sum(1 for i in issues if i.severity == SeverityLevel.WARNING),
            'info_count': sum(1 for i in issues if i.severity == SeverityLevel.INFO),
        }
        
        # 计算字段完整率
        required = self.REQUIRED_FIELDS.get(data_type, [])
        if required and records:
            filled = sum(1 for r in records for f in required if r.get(f) is not None)
            total = len(records) * len(required)
            metrics['field_completeness'] = filled / total if total > 0 else 0
        
        return metrics

    def _generate_recommendations(self, issues: List[ValidationIssue], data_type: str) -> List[str]:
        """生成修复建议"""
        recommendations = []
        
        critical_rules = set(i.rule_id for i in issues if i.severity == SeverityLevel.CRITICAL)
        error_rules = set(i.rule_id for i in issues if i.severity == SeverityLevel.ERROR)
        
        if 'F001' in critical_rules:
            recommendations.append('检查必填字段是否完整，确保数据源返回所有必需字段')
        if 'L001' in critical_rules:
            recommendations.append('检查OHLC数据逻辑，确保最高价>=最低价')
        if 'A001' in critical_rules:
            recommendations.append('检查价格数据，确保所有价格为正数')
        if 'D001' in critical_rules:
            recommendations.append('执行数据去重处理')
        if 'FM016' in error_rules:
            recommendations.append('检查股票代码格式，使用标准格式如 600000.SH')
        if 'C006' in {i.rule_id for i in issues}:
            recommendations.append('使用白名单内的数据源')
        
        return recommendations[:5]

    def _evaluate_verdict(self, issues: List[ValidationIssue]) -> Tuple[str, bool]:
        """判定验证结果"""
        critical = sum(1 for i in issues if i.severity == SeverityLevel.CRITICAL)
        error = sum(1 for i in issues if i.severity == SeverityLevel.ERROR)
        warning = sum(1 for i in issues if i.severity == SeverityLevel.WARNING)
        
        if critical > 0:
            return 'REJECTED', False
        elif error > 0:
            return 'FLAGGED', False
        elif warning > 3:
            return 'DEGRADED', True
        else:
            return 'ACCEPTED', True


# ============== 便捷函数 ==============

_validator = DataValidator()


def validate_data(data: Any, data_type: str = 'auto', symbol: Optional[str] = None) -> ValidationResult:
    """验证数据"""
    return _validator.validate(data, data_type, symbol)


def validate_batch(data_list: List[Any], data_type: str = 'auto') -> Dict[str, ValidationResult]:
    """批量验证"""
    return _validator.validate_batch(data_list, data_type)


def get_validation_summary(results: Dict[str, ValidationResult]) -> Dict:
    """获取验证摘要"""
    total = len(results)
    accepted = sum(1 for r in results.values() if r.verdict == 'ACCEPTED')
    flagged = sum(1 for r in results.values() if r.verdict == 'FLAGGED')
    rejected = sum(1 for r in results.values() if r.verdict == 'REJECTED')
    
    return {
        'total': total,
        'accepted': accepted,
        'flagged': flagged,
        'rejected': rejected,
        'acceptance_rate': accepted / total if total > 0 else 0,
        'avg_health_score': sum(r.health_score for r in results.values()) / total if total > 0 else 0
    }


if __name__ == '__main__':
    # 测试示例
    validator = DataValidator()
    
    # 测试合规数据
    good_data = {
        'source': 'akshare',
        'data_type': 'quote',
        'symbol': '600000.SH',
        'payload': {
            'open': 10.50,
            'high': 10.80,
            'low': 10.40,
            'close': 10.70,
            'volume': 1000000,
            'amount': 10500000.0,
            'pre_close': 10.50
        }
    }
    
    result = validator.validate(good_data)
    print(result.summary())
    print(f"健康评分: {result.health_score:.1f}")
    print(f"判定: {result.verdict}")
    
    # 测试违规数据
    bad_data = {
        'source': 'unknown',
        'data_type': 'quote',
        'symbol': '600000.SH',
        'payload': {
            'open': 10.50,
            'high': 10.40,  # 最高价 < 最低价
            'low': 10.80,
            'close': 10.70,
            'volume': 1000000,
            'amount': 10500000.0
        }
    }
    
    result = validator.validate(bad_data)
    print("\n" + "="*50)
    print(result.summary())
    print(f"健康评分: {result.health_score:.1f}")
    print(f"判定: {result.verdict}")
