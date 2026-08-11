# -*- coding: utf-8 -*-
"""
数据合规检查器模块

提供对 FinanceData 数据的合规性检查，识别合规与违规数据。

合规规则：
1. 必填字段完整性检查
2. 数据类型正确性检查
3. 数值范围合理性检查
4. 时间戳格式检查
5. 数据源合法性检查
6. 业务逻辑一致性检查

使用示例：
    from finance_toolkit.compliance_checker import (
        ComplianceChecker,
        ComplianceResult,
        check_compliance,
    )
    
    checker = ComplianceChecker()
    result = checker.check(data)
    if result.is_compliant:
        print("数据合规")
    else:
        print(f"违规原因：{result.violations}")
"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum


class ViolationLevel(Enum):
    """违规严重程度"""
    CRITICAL = "critical"      # 严重违规，数据不可用
    ERROR = "error"            # 错误，数据需要修复
    WARNING = "warning"        # 警告，数据可能有问题
    INFO = "info"              # 信息，仅供参考


class ComplianceStatus(Enum):
    """合规状态"""
    COMPLIANT = "compliant"           # 合规
    NON_COMPLIANT = "non_compliant"   # 违规
    NEEDS_REVIEW = "needs_review"     # 需要人工审核


@dataclass
class Violation:
    """违规记录"""
    rule_id: str
    rule_name: str
    field: str
    value: Any
    message: str
    level: ViolationLevel
    suggestion: str = ""
    
    def to_dict(self) -> Dict:
        return {
            'rule_id': self.rule_id,
            'rule_name': self.rule_name,
            'field': self.field,
            'value': str(self.value) if self.value is not None else None,
            'message': self.message,
            'level': self.level.value,
            'suggestion': self.suggestion
        }


@dataclass
class ComplianceResult:
    """合规检查结果"""
    is_compliant: bool
    status: ComplianceStatus
    violations: List[Violation] = field(default_factory=list)
    checks_performed: List[str] = field(default_factory=list)
    score: float = 1.0  # 合规评分，1.0 为完全合规
    
    def add_violation(self, violation: Violation):
        """添加违规记录"""
        self.violations.append(violation)
        # 根据违规级别调整评分
        penalty = {
            ViolationLevel.CRITICAL: 0.4,
            ViolationLevel.ERROR: 0.2,
            ViolationLevel.WARNING: 0.05,
            ViolationLevel.INFO: 0.01
        }
        self.score = max(0.0, self.score - penalty.get(violation.level, 0.0))
        
        # 更新状态
        if violation.level in [ViolationLevel.CRITICAL, ViolationLevel.ERROR, ViolationLevel.WARNING]:
            self.status = ComplianceStatus.NON_COMPLIANT
            self.is_compliant = False
        elif violation.level == ViolationLevel.INFO and self.status == ComplianceStatus.COMPLIANT:
            self.status = ComplianceStatus.NEEDS_REVIEW
    
    def to_dict(self) -> Dict:
        return {
            'is_compliant': self.is_compliant,
            'status': self.status.value,
            'score': round(self.score, 4),
            'violations': [v.to_dict() for v in self.violations],
            'checks_performed': self.checks_performed
        }
    
    def __str__(self) -> str:
        status_str = "✓ 合规" if self.is_compliant else "✗ 违规"
        lines = [
            f"合规检查结果 [{status_str}]",
            f"  合规评分：{self.score:.2%}",
            f"  违规数量：{len(self.violations)}"
        ]
        
        if self.violations:
            lines.append("  违规详情：")
            for v in self.violations:
                lines.append(f"    [{v.level.value.upper()}] {v.rule_name}: {v.message}")
        
        return "\n".join(lines)


class ComplianceChecker:
    """
    数据合规检查器
    
    检查规则：
    1. 必填字段完整性
    2. 数据类型正确性
    3. 数值范围合理性
    4. 时间戳格式
    5. 数据源合法性
    6. 业务逻辑一致性
    """
    
    # 数据源白名单
    VALID_SOURCES = {
        'akshare', 'tushare', 'eastmoney', 'sina', 'tencent', 
        'netease', 'lexicon', 'binance', 'coingecko', 'yahoo', 'okx'
    }
    
    # 数据类型与必填字段映射
    REQUIRED_FIELDS = {
        'quote': ['open', 'high', 'low', 'close', 'volume', 'amount'],
        'kline': ['date', 'open', 'high', 'low', 'close', 'volume', 'amount'],
        'financial': ['report_date', 'type'],
        'dividend': ['announcement_date', 'record_date', 'ex_dividend_date', 'payment_date', 'dividend_per_share'],
        'lhb': ['trade_date', 'reason'],
        'northbound': ['date', 'type'],
        'stock_basic': ['name', 'industry', 'list_date'],
        'fund_nav': ['nav_date', 'nav', 'accumulated_nav'],
        'fund_holdings': ['report_date', 'stock_code', 'stock_name', 'shares', 'market_value', 'weight'],
        'bond_yield': ['date', 'bond_type', 'yield_rate'],
        'bond_quote': ['bond_code', 'bond_name', 'price', 'yield_rate'],
        'futures_quote': ['contract_code', 'open', 'high', 'low', 'close', 'settlement', 'volume', 'open_interest'],
        'futures_kline': ['date', 'open', 'high', 'low', 'close', 'volume', 'open_interest'],
        'index_quote': ['open', 'high', 'low', 'close', 'volume', 'amount'],
        'index_kline': ['date', 'open', 'high', 'low', 'close', 'volume', 'amount'],
        'macro_gdp': ['quarter', 'gdp', 'yoy'],
        'macro_cpi': ['date', 'cpi'],
        'macro_pmi': ['date', 'pmi'],
        'forex_quote': ['currency_pair', 'rate', 'change_pct'],
        'crypto_quote': ['symbol', 'price', 'volume_24h', 'market_cap'],
        'etf_quote': ['open', 'high', 'low', 'close', 'volume', 'amount'],
        'etf_kline': ['date', 'open', 'high', 'low', 'close', 'volume'],
        'news': ['title', 'publish_time', 'source'],
        'sentiment': ['date', 'sentiment_score', 'sentiment_label'],
        'social': ['post_id', 'content', 'publish_time', 'author']
    }
    
    # 数值范围约束
    VALUE_RANGES = {
        'sentiment_score': (-1.0, 1.0),
        'quality_score': (0.0, 1.0),
        'premium_discount': (-100.0, 100.0),
        'change_pct': (-30.0, 30.0),  # A股涨跌幅限制
        'pe_ratio': (0.0, 1000.0),
        'pb_ratio': (0.0, 100.0),
        'roe': (-100.0, 100.0),
        'roa': (-100.0, 100.0),
        'gross_margin': (-100.0, 100.0),
        'net_margin': (-100.0, 100.0),
        'turnover_rate': (0.0, 100.0),
        'dividend_yield': (0.0, 50.0),
        'yield_rate': (-10.0, 50.0),
        'fear_greed_index': (0, 100)
    }
    
    # 枚举值约束
    ENUM_CONSTRAINTS = {
        'data_type': list(REQUIRED_FIELDS.keys()),
        'source': list(VALID_SOURCES),
        'bond_type': ['treasury_1y', 'treasury_5y', 'treasury_10y', 'corporate_aa', 'corporate_a'],
        'sentiment_label': ['极度悲观', '悲观', '中性', '乐观', '极度乐观'],
        'financial_type': ['balance_sheet', 'income_statement', 'cash_flow'],
        'kline_period': ['1m', '5m', '15m', '30m', '60m', 'daily', 'weekly', 'monthly'],
        'northbound_type': ['sh_hk', 'sz_hk', 'total'],
        'fund_type': ['stock', 'bond', 'mixed', 'index', 'money', 'qdii'],
        'market': ['SSE', 'SZSE', 'BSE'],
        'stock_status': ['active', 'delisted', 'suspended']
    }
    
    def __init__(self, strict_mode: bool = False):
        """
        初始化合规检查器
        
        Args:
            strict_mode: 严格模式，开启更严格的检查规则
        """
        self.strict_mode = strict_mode
    
    def check(self, data: Any) -> ComplianceResult:
        """
        检查数据合规性
        
        Args:
            data: FinanceData 对象或字典
        
        Returns:
            ComplianceResult
        """
        result = ComplianceResult(
            is_compliant=True,
            status=ComplianceStatus.COMPLIANT
        )
        
        # 转换为字典
        data_dict = self._to_dict(data)
        if data_dict is None:
            result.add_violation(Violation(
                rule_id='R001',
                rule_name='数据类型检查',
                field='root',
                value=str(type(data)),
                message=f'不支持的数据类型: {type(data).__name__}',
                level=ViolationLevel.CRITICAL,
                suggestion='请提供字典或 FinanceData 对象'
            ))
            return result
        
        # 执行各项检查
        self._check_required_fields(data_dict, result)
        self._check_data_type(data_dict, result)
        self._check_source(data_dict, result)
        self._check_timestamp(data_dict, result)
        self._check_symbol(data_dict, result)
        self._check_payload_values(data_dict, result)
        self._check_business_logic(data_dict, result)
        
        # 更新合规状态
        # 检查是否有 WARNING 或更高级别的违规
        has_warning_or_worse = any(
            v.level in [ViolationLevel.CRITICAL, ViolationLevel.ERROR, ViolationLevel.WARNING]
            for v in result.violations
        )
        
        if has_warning_or_worse:
            result.status = ComplianceStatus.NON_COMPLIANT
            result.is_compliant = False
        elif result.score >= 0.8:
            result.status = ComplianceStatus.COMPLIANT
            result.is_compliant = True
        elif result.score >= 0.5:
            result.status = ComplianceStatus.NEEDS_REVIEW
            result.is_compliant = False
        else:
            result.status = ComplianceStatus.NON_COMPLIANT
            result.is_compliant = False
        
        return result
    
    def _to_dict(self, data: Any) -> Optional[Dict]:
        """转换为字典"""
        from dataclasses import is_dataclass, asdict
        
        if isinstance(data, dict):
            return data
        elif is_dataclass(data) and not isinstance(data, type):
            return asdict(data)
        else:
            return None
    
    def _check_required_fields(self, data: Dict, result: ComplianceResult):
        """检查必填字段"""
        result.checks_performed.append('required_fields')
        
        data_type = data.get('data_type')
        if not data_type:
            result.add_violation(Violation(
                rule_id='R002',
                rule_name='数据类型检查',
                field='data_type',
                value=None,
                message='缺少 data_type 字段',
                level=ViolationLevel.CRITICAL,
                suggestion='请提供数据类型标识'
            ))
            return
        
        required = self.REQUIRED_FIELDS.get(data_type, [])
        payload = data.get('payload', {})
        
        for field_name in required:
            if field_name not in payload or payload[field_name] is None:
                result.add_violation(Violation(
                    rule_id='R003',
                    rule_name='必填字段检查',
                    field=f'payload.{field_name}',
                    value=None,
                    message=f'缺少必填字段: {field_name}',
                    level=ViolationLevel.ERROR,
                    suggestion=f'请补充 {field_name} 字段'
                ))
    
    def _check_data_type(self, data: Dict, result: ComplianceResult):
        """检查数据类型"""
        result.checks_performed.append('data_type')
        
        data_type = data.get('data_type')
        if data_type and data_type not in self.ENUM_CONSTRAINTS['data_type']:
            result.add_violation(Violation(
                rule_id='R004',
                rule_name='数据类型枚举检查',
                field='data_type',
                value=data_type,
                message=f'无效的数据类型: {data_type}',
                level=ViolationLevel.ERROR,
                suggestion=f'合法的数据类型: {self.ENUM_CONSTRAINTS["data_type"]}'
            ))
    
    def _check_source(self, data: Dict, result: ComplianceResult):
        """检查数据源"""
        result.checks_performed.append('source')
        
        source = data.get('source')
        if source and source not in self.VALID_SOURCES:
            result.add_violation(Violation(
                rule_id='R005',
                rule_name='数据源合法性检查',
                field='source',
                value=source,
                message=f'未知的数据源: {source}',
                level=ViolationLevel.WARNING,
                suggestion=f'合法的数据源: {self.VALID_SOURCES}'
            ))
    
    def _check_timestamp(self, data: Dict, result: ComplianceResult):
        """检查时间戳格式"""
        result.checks_performed.append('timestamp')
        
        timestamp = data.get('timestamp')
        if timestamp:
            if not self._is_valid_iso_timestamp(timestamp):
                result.add_violation(Violation(
                    rule_id='R006',
                    rule_name='时间戳格式检查',
                    field='timestamp',
                    value=timestamp,
                    message=f'无效的时间戳格式: {timestamp}',
                    level=ViolationLevel.ERROR,
                    suggestion='请使用 ISO 8601 格式，如 2024-01-15T10:30:00Z'
                ))
    
    def _check_symbol(self, data: Dict, result: ComplianceResult):
        """检查标的代码格式"""
        result.checks_performed.append('symbol')
        
        symbol = data.get('symbol')
        if symbol:
            if not isinstance(symbol, str) or len(symbol) < 3:
                result.add_violation(Violation(
                    rule_id='R007',
                    rule_name='标的代码格式检查',
                    field='symbol',
                    value=symbol,
                    message=f'无效的标的代码格式: {symbol}',
                    level=ViolationLevel.WARNING,
                    suggestion='请使用标准格式，如 600000.SH'
                ))
    
    def _check_payload_values(self, data: Dict, result: ComplianceResult):
        """检查数值范围"""
        result.checks_performed.append('payload_values')
        
        data_type = data.get('data_type')
        payload = data.get('payload', {})
        
        for key, value in payload.items():
            if key in self.VALUE_RANGES and isinstance(value, (int, float)):
                min_val, max_val = self.VALUE_RANGES[key]
                if value < min_val or value > max_val:
                    result.add_violation(Violation(
                        rule_id='R008',
                        rule_name='数值范围检查',
                        field=f'payload.{key}',
                        value=value,
                        message=f'数值 {value} 超出范围 [{min_val}, {max_val}]',
                        level=ViolationLevel.ERROR,
                        suggestion=f'请检查数据源或进行数据清洗'
                    ))
    
    def _check_business_logic(self, data: Dict, result: ComplianceResult):
        """检查业务逻辑一致性"""
        result.checks_performed.append('business_logic')
        
        data_type = data.get('data_type')
        payload = data.get('payload', {})
        
        # K线数据逻辑检查
        if data_type == 'kline':
            self._check_kline_logic(payload, result)
        
        # 行情数据逻辑检查
        elif data_type == 'quote':
            self._check_quote_logic(payload, result)
        
        # 财务数据逻辑检查
        elif data_type == 'financial':
            self._check_financial_logic(payload, result)
    
    def _check_kline_logic(self, payload: Dict, result: ComplianceResult):
        """检查K线数据逻辑"""
        # 最高价 >= 最低价
        if 'high' in payload and 'low' in payload:
            try:
                if float(payload['high']) < float(payload['low']):
                    result.add_violation(Violation(
                        rule_id='R009',
                        rule_name='K线逻辑检查',
                        field='payload.high/low',
                        value=f"high={payload['high']}, low={payload['low']}",
                        message='最高价小于最低价',
                        level=ViolationLevel.ERROR,
                        suggestion='检查数据源或清洗逻辑'
                    ))
            except (ValueError, TypeError):
                pass
        
        # 最高价 >= 开盘价、收盘价
        # 最低价 <= 开盘价、收盘价
        for ref_field in ['open', 'close']:
            if ref_field in payload:
                try:
                    ref_val = float(payload[ref_field])
                    if 'high' in payload and float(payload['high']) < ref_val:
                        result.add_violation(Violation(
                            rule_id='R010',
                            rule_name='K线逻辑检查',
                            field=f'payload.high/{ref_field}',
                            value=f"high={payload['high']}, {ref_field}={payload[ref_field]}",
                            message=f'最高价小于{ref_field}',
                            level=ViolationLevel.WARNING,
                            suggestion='检查数据源'
                        ))
                    if 'low' in payload and float(payload['low']) > ref_val:
                        result.add_violation(Violation(
                            rule_id='R011',
                            rule_name='K线逻辑检查',
                            field=f'payload.low/{ref_field}',
                            value=f"low={payload['low']}, {ref_field}={payload[ref_field]}",
                            message=f'最低价大于{ref_field}',
                            level=ViolationLevel.WARNING,
                            suggestion='检查数据源'
                        ))
                except (ValueError, TypeError):
                    pass
    
    def _check_quote_logic(self, payload: Dict, result: ComplianceResult):
        """检查行情数据逻辑"""
        # 最高价 >= 最低价
        if 'high' in payload and 'low' in payload:
            try:
                if float(payload['high']) < float(payload['low']):
                    result.add_violation(Violation(
                        rule_id='R009',
                        rule_name='行情逻辑检查',
                        field='payload.high/low',
                        value=f"high={payload['high']}, low={payload['low']}",
                        message='最高价小于最低价',
                        level=ViolationLevel.ERROR,
                        suggestion='检查数据源或清洗逻辑'
                    ))
            except (ValueError, TypeError):
                pass
        
        # 涨跌幅合理性检查
        if 'close' in payload and 'pre_close' in payload:
            try:
                close = float(payload['close'])
                pre_close = float(payload['pre_close'])
                if pre_close > 0:
                    change_pct = (close - pre_close) / pre_close * 100
                    if abs(change_pct) > 30:  # A股涨跌幅限制
                        result.add_violation(Violation(
                            rule_id='R012',
                            rule_name='涨跌幅合理性检查',
                            field='payload.change_pct',
                            value=change_pct,
                            message=f'涨跌幅异常: {change_pct:.2f}%',
                            level=ViolationLevel.WARNING,
                            suggestion='核查是否为停牌复牌或数据错误'
                        ))
            except (ValueError, TypeError, ZeroDivisionError):
                pass
    
    def _check_financial_logic(self, payload: Dict, result: ComplianceResult):
        """检查财务数据逻辑"""
        # 财务指标合理性检查
        for key in ['roe', 'roa', 'gross_margin', 'net_margin']:
            if key in payload and isinstance(payload[key], (int, float)):
                if payload[key] < -100 or payload[key] > 100:
                    result.add_violation(Violation(
                        rule_id='R013',
                        rule_name='财务指标合理性检查',
                        field=f'payload.{key}',
                        value=payload[key],
                        message=f'{key} 值异常: {payload[key]}',
                        level=ViolationLevel.WARNING,
                        suggestion='检查财务数据计算逻辑'
                    ))
    
    def _is_valid_iso_timestamp(self, timestamp: str) -> bool:
        """校验 ISO 8601 时间戳格式"""
        try:
            # 尝试解析常见格式
            for fmt in [
                "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S.%fZ",
                "%Y-%m-%dT%H:%M:%S.%f%z",
                "%Y-%m-%d %H:%M:%S"
            ]:
                try:
                    datetime.strptime(timestamp, fmt)
                    return True
                except ValueError:
                    continue
            return False
        except Exception:
            return False
    
    def check_batch(self, data_list: List[Any]) -> List[ComplianceResult]:
        """
        批量检查数据合规性
        
        Args:
            data_list: 数据列表
        
        Returns:
            合规检查结果列表
        """
        return [self.check(data) for data in data_list]
    
    def get_summary(self, results: List[ComplianceResult]) -> Dict:
        """
        获取合规检查摘要
        
        Args:
            results: 合规检查结果列表
        
        Returns:
            摘要信息
        """
        total = len(results)
        compliant = sum(1 for r in results if r.is_compliant)
        non_compliant = total - compliant
        
        violation_count = sum(len(r.violations) for r in results)
        critical_count = sum(
            1 for r in results 
            for v in r.violations 
            if v.level == ViolationLevel.CRITICAL
        )
        
        return {
            'total': total,
            'compliant': compliant,
            'non_compliant': non_compliant,
            'compliance_rate': compliant / total if total > 0 else 0,
            'violation_count': violation_count,
            'critical_count': critical_count,
            'avg_score': sum(r.score for r in results) / total if total > 0 else 0
        }


# ============== 便捷函数 ==============

_checker = ComplianceChecker()


def check_compliance(data: Any) -> ComplianceResult:
    """
    检查数据合规性
    
    Args:
        data: FinanceData 对象或字典
    
    Returns:
        ComplianceResult
    """
    return _checker.check(data)


def check_compliance_batch(data_list: List[Any]) -> List[ComplianceResult]:
    """
    批量检查数据合规性
    
    Args:
        data_list: 数据列表
    
    Returns:
        合规检查结果列表
    """
    return _checker.check_batch(data_list)


def get_compliance_summary(results: List[ComplianceResult]) -> Dict:
    """
    获取合规检查摘要
    
    Args:
        results: 合规检查结果列表
    
    Returns:
        摘要信息
    """
    return _checker.get_summary(results)


if __name__ == "__main__":
    # 测试示例
    from finance_toolkit.core import FinanceData
    
    # 合规数据
    compliant_data = FinanceData(
        source="akshare",
        data_type="quote",
        symbol="600000.SH",
        timestamp="2024-01-15T10:30:00Z",
        payload={
            "open": 10.50,
            "high": 10.80,
            "low": 10.40,
            "close": 10.70,
            "volume": 1000000,
            "amount": 10500000.0,
            "pre_close": 10.50
        }
    )
    
    result = check_compliance(compliant_data)
    print(result)
    
    # 违规数据
    non_compliant_data = FinanceData(
        source="unknown_source",
        data_type="quote",
        symbol="600000.SH",
        timestamp="invalid-timestamp",
        payload={
            "open": 10.50,
            "high": 10.40,  # 最高价 < 最低价
            "low": 10.80,
            "close": 10.70,
            "volume": 1000000,
            "amount": 10500000.0
        }
    )
    
    result = check_compliance(non_compliant_data)
    print(result)
