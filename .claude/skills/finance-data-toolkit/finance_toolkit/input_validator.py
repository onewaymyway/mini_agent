# -*- coding: utf-8 -*-
"""
基础输入校验模块
=================
定义最基础的输入校验与格式检查规则（最小验证集）

支持的数据类型：
- stock（股票）
- crypto（加密货币）
- index（指数）
- fund（基金）
- futures（期货）
- bond（债券）
- forex（外汇）
- etf（ETF）
- news（资讯）
- sector（板块）

使用示例：
    from finance_toolkit.input_validator import InputValidator

    validator = InputValidator()
    result = validator.validate(data, schema='stock')
    if result.is_valid:
        print('数据验证通过')
    else:
        print(f'发现 {len(result.errors)} 个错误')
"""

import re
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime


# ============== 校验结果 ==============

@dataclass
class ValidationResult:
    """单条数据的校验结果"""
    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    schema: str = ''
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            'valid': self.valid,
            'errors': self.errors,
            'warnings': self.warnings,
            'schema': self.schema,
            'timestamp': self.timestamp,
        }

    def __str__(self) -> str:
        status = "✓ 通过" if self.is_valid else "✗ 失败"
        lines = [f"校验结果 [{status}] 类型: {self.schema or '未指定'}"]
        if self.errors:
            lines.append("  错误:")
            for e in self.errors:
                lines.append(f"    - {e}")
        if self.warnings:
            lines.append("  警告:")
            for w in self.warnings:
                lines.append(f"    - {w}")
        return "\n".join(lines)


@dataclass
class BatchResult:
    """批量校验结果"""
    total: int
    passed: int
    failed: int
    results: List[ValidationResult] = field(default_factory=list)
    summary: Dict[str, int] = field(default_factory=dict)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total > 0 else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            'total': self.total,
            'passed': self.passed,
            'failed': self.failed,
            'pass_rate': round(self.pass_rate, 4),
            'summary': self.summary,
        }


# ============== 格式检查函数 ==============

def check_stock_code(code: str) -> Optional[str]:
    """检查股票代码格式
    支持的格式: 600519.SH / 000001.SZ / 300750.SZ / 688981.SH
    """
    if not code or not isinstance(code, str):
        return "股票代码不能为空"
    code = code.strip()
    pattern = r'^\d{6}\.(SH|SZ|BJ)$'
    if not re.match(pattern, code):
        return f"股票代码格式不正确: {code}（应为6位数字+.SH/.SZ/.BJ）"
    return None


def check_crypto_symbol(symbol: str) -> Optional[str]:
    """检查加密货币符号格式"""
    if not symbol or not isinstance(symbol, str):
        return "加密货币符号不能为空"
    symbol = symbol.strip().upper()
    # 支持: BTC, ETH, BTCUSDT, ETH_USD 等常见格式
    pattern = r'^[A-Z]{2,10}(_?[A-Z]{2,10})?$'
    if not re.match(pattern, symbol):
        return f"加密货币符号格式不正确: {symbol}"
    return None


def check_date(date_str: str) -> Optional[str]:
    """检查日期格式（支持 YYYY-MM-DD / YYYY/MM/DD）"""
    if not date_str or not isinstance(date_str, str):
        return "日期不能为空"
    date_str = date_str.strip()
    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%Y%m%d'):
        try:
            datetime.strptime(date_str, fmt)
            return None
        except ValueError:
            continue
    return f"日期格式不正确: {date_str}（应为 YYYY-MM-DD）"


def check_numeric(value: Any, field_name: str, allow_negative: bool = False) -> Optional[str]:
    """检查数值类型"""
    if value is None:
        return f"{field_name}不能为None"
    if isinstance(value, (int, float)):
        if not allow_negative and value < 0:
            return f"{field_name}不能为负数: {value}"
        return None
    return f"{field_name}应为数值类型，实际为 {type(value).__name__}"


def check_required_fields(data: Dict[str, Any], fields: List[str]) -> List[str]:
    """检查必填字段"""
    errors = []
    for f in fields:
        val = data.get(f)
        if val is None or (isinstance(val, str) and val.strip() == ''):
            errors.append(f"缺少必填字段: {f}")
    return errors


def check_price_range(price: float, min_val: float = 0, max_val: float = float('inf'), field_name: str = '价格') -> Optional[str]:
    """检查价格范围"""
    if price < min_val:
        return f"{field_name}低于合理范围（最低{min_val}）: {price}"
    if price > max_val:
        return f"{field_name}超过合理范围（最高{max_val}）: {price}"
    return None


# ============== Schema 定义 ==============

SCHEMA_RULES: Dict[str, Dict[str, Any]] = {
    'stock': {
        'required_fields': ['code', 'date'],
        'optional_fields': ['name', 'open', 'high', 'low', 'close', 'volume', 'amount', 'change_pct'],
        'format_checkers': {
            'code': check_stock_code,
            'date': check_date,
        },
        'range_checks': {
            'open': {'min': 0},
            'high': {'min': 0},
            'low': {'min': 0},
            'close': {'min': 0},
            'volume': {'min': 0},
            'amount': {'min': 0},
        },
    },
    'crypto': {
        'required_fields': ['symbol'],
        'optional_fields': ['name', 'price', 'volume_24h', 'market_cap', 'change_pct_24h'],
        'format_checkers': {
            'symbol': check_crypto_symbol,
        },
        'range_checks': {
            'price': {'min': 0},
            'volume_24h': {'min': 0},
            'market_cap': {'min': 0},
        },
    },
    'index': {
        'required_fields': ['code', 'date'],
        'optional_fields': ['name', 'open', 'high', 'low', 'close', 'change_pct'],
        'format_checkers': {
            'code': check_stock_code,
            'date': check_date,
        },
        'range_checks': {
            'open': {'min': 0},
            'high': {'min': 0},
            'low': {'min': 0},
            'close': {'min': 0},
        },
    },
    'fund': {
        'required_fields': ['code', 'date'],
        'optional_fields': ['name', 'nav', 'prev_nav', 'change_pct'],
        'format_checkers': {
            'code': check_stock_code,
            'date': check_date,
        },
        'range_checks': {
            'nav': {'min': 0},
            'prev_nav': {'min': 0},
        },
    },
    'futures': {
        'required_fields': ['contract', 'date'],
        'optional_fields': ['open', 'high', 'low', 'close', 'settlement', 'volume', 'open_interest'],
        'format_checkers': {
            'contract': lambda v: None if v and isinstance(v, str) and len(v.strip()) > 0 else '合约代码不能为空',
            'date': check_date,
        },
        'range_checks': {
            'open': {'min': 0},
            'high': {'min': 0},
            'low': {'min': 0},
            'close': {'min': 0},
        },
    },
    'bond': {
        'required_fields': ['code', 'date'],
        'optional_fields': ['name', 'yield_rate', 'price', 'rating'],
        'format_checkers': {
            'code': check_stock_code,
            'date': check_date,
        },
        'range_checks': {
            'yield_rate': {'min': -100, 'max': 100},
            'price': {'min': 0},
        },
    },
    'forex': {
        'required_fields': ['pair', 'date'],
        'optional_fields': ['buy', 'sell', 'mid', 'change_pct'],
        'format_checkers': {
            'pair': lambda v: None if v and isinstance(v, str) and len(v.strip()) >= 3 else '货币对不能为空',
            'date': check_date,
        },
        'range_checks': {
            'buy': {'min': 0},
            'sell': {'min': 0},
            'mid': {'min': 0},
        },
    },
    'etf': {
        'required_fields': ['code', 'date'],
        'optional_fields': ['name', 'nav', 'price', 'volume', 'change_pct'],
        'format_checkers': {
            'code': check_stock_code,
            'date': check_date,
        },
        'range_checks': {
            'nav': {'min': 0},
            'price': {'min': 0},
            'volume': {'min': 0},
        },
    },
    'news': {
        'required_fields': ['title'],
        'optional_fields': ['source', 'url', 'publish_time', 'category', 'content'],
        'format_checkers': {
            'title': lambda v: None if v and isinstance(v, str) and len(v.strip()) >= 2 else '标题不能为空且至少2个字符',
            'publish_time': check_date,
        },
    },
    'sector': {
        'required_fields': ['name', 'date'],
        'optional_fields': ['change_pct', 'leading_stock', 'volume'],
        'format_checkers': {
            'name': lambda v: None if v and isinstance(v, str) and len(v.strip()) >= 1 else '板块名称不能为空',
            'date': check_date,
        },
        'range_checks': {
            'change_pct': {'min': -100, 'max': 100},
            'volume': {'min': 0},
        },
    },
}


# ============== 输入校验器 ==============

class InputValidator:
    """基础输入校验器

    提供最小验证集的输入校验功能，包括：
    1. 必填字段检查
    2. 格式检查（股票代码、日期、数值等）
    3. 数值范围检查
    4. 类型检查
    """

    def __init__(self):
        self.schemas = dict(SCHEMA_RULES)

    def validate(self, data: Any, schema: str = '') -> ValidationResult:
        """校验单条数据"""
        if data is None:
            return ValidationResult(
                valid=False,
                errors=['输入数据不能为None'],
                schema=schema,
            )

        # 非字典类型直接返回格式错误
        if not isinstance(data, dict):
            return ValidationResult(
                valid=False,
                errors=[f'输入数据应为字典类型，实际为 {type(data).__name__}'],
                schema=schema,
            )

        # 如果没有指定 schema，尝试自动识别
        if not schema:
            schema = self._infer_schema(data)
            if not schema:
                return ValidationResult(
                    valid=False,
                    errors=['无法识别数据类型，请指定 schema 参数'],
                )

        errors, warnings = self._validate_data(data, schema)
        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            schema=schema,
        )

    def validate_batch(self, data_list: List[Any], schema: str = '') -> BatchResult:
        """批量校验"""
        results = []
        for item in data_list:
            result = self.validate(item, schema)
            results.append(result)

        passed = sum(1 for r in results if r.is_valid)
        failed = len(results) - passed

        # 统计各类型错误数量
        error_summary = {}
        for r in results:
            for e in r.errors:
                # 按错误前缀归类
                prefix = e.split(':')[0][:20] if ':' in e else e[:20]
                error_summary[prefix] = error_summary.get(prefix, 0) + 1

        return BatchResult(
            total=len(results),
            passed=passed,
            failed=failed,
            results=results,
            summary=error_summary,
        )

    def _validate_data(self, data: Dict[str, Any], schema: str) -> tuple:
        """执行具体校验逻辑"""
        if schema not in self.schemas:
            return ([f'未知的数据类型: {schema}'], [])

        rules = self.schemas[schema]
        errors = []
        warnings = []

        # 1. 必填字段检查
        missing = check_required_fields(data, rules.get('required_fields', []))
        errors.extend(missing)

        # 2. 格式检查
        format_checkers = rules.get('format_checkers', {})
        for field_name, checker in format_checkers.items():
            value = data.get(field_name)
            if value is not None:
                err = checker(value)
                if err:
                    errors.append(err)

        # 3. 数值范围检查
        range_checks = rules.get('range_checks', {})
        for field_name, range_rule in range_checks.items():
            value = data.get(field_name)
            if value is not None:
                err = check_numeric(value, field_name)
                if err:
                    errors.append(err)
                    continue
                min_val = range_rule.get('min')
                max_val = range_rule.get('max')
                if min_val is not None:
                    err = check_price_range(value, min_val=min_val, field_name=field_name)
                    if err:
                        errors.append(err)
                if max_val is not None:
                    err = check_price_range(value, max_val=max_val, field_name=field_name)
                    if err:
                        warnings.append(err)  # 超出上限作为警告

        # 4. 可选字段的类型检查
        for field_name, value in data.items():
            if value is not None and not isinstance(value, (str, int, float, list, dict)):
                warnings.append(f'{field_name} 类型异常: {type(value).__name__}')

        return errors, warnings

    def _infer_schema(self, data: Dict[str, Any]) -> Optional[str]:
        """根据数据内容自动推断数据类型"""
        # 股票代码特征
        if 'code' in data and isinstance(data['code'], str):
            code = data['code']
            if re.match(r'^\d{6}\.(SH|SZ|BJ)$', code):
                if 'symbol' in data or 'price' in data and 'change_pct_24h' in data:
                    return 'crypto'
                return 'stock'

        # 加密货币特征
        if 'symbol' in data and isinstance(data['symbol'], str):
            return 'crypto'

        # 货币对特征
        if 'pair' in data:
            return 'forex'

        # 资讯特征
        if 'title' in data or 'source' in data:
            return 'news'

        # 期货特征
        if 'contract' in data:
            return 'futures'

        return None

    def list_schemas(self) -> List[str]:
        """获取所有已注册的 schema 类型"""
        return list(self.schemas.keys())

    def get_schema_rules(self, schema: str) -> Optional[Dict]:
        """获取指定类型的校验规则"""
        return self.schemas.get(schema)


# ============== 全局实例 ==============

input_validator = InputValidator()


# ============== 便捷函数 ==============

def validate(data: Any, schema: str = '') -> ValidationResult:
    """便捷校验函数"""
    return input_validator.validate(data, schema)


def validate_batch(data_list: List[Any], schema: str = '') -> BatchResult:
    """便捷批量校验函数"""
    return input_validator.validate_batch(data_list, schema)


def list_schemas() -> List[str]:
    """获取所有支持的 schema 类型"""
    return input_validator.list_schemas()


if __name__ == '__main__':
    # 快速测试
    print("=== 基础输入校验模块测试 ===")

    # 测试股票数据
    stock_ok = {'code': '600519.SH', 'date': '2024-01-15', 'open': 1680.0, 'high': 1700.0, 'low': 1670.0, 'close': 1695.0}
    stock_bad = {'code': 'INVALID', 'date': '2024/01/15', 'open': -100}

    r1 = validate(stock_ok, 'stock')
    r2 = validate(stock_bad, 'stock')
    print(f"正常股票数据: {r1}")
    print(f"异常股票数据: {r2}")

    # 测试加密货币
    crypto_ok = {'symbol': 'BTC', 'price': 45000.0, 'change_pct_24h': 2.5}
    r3 = validate(crypto_ok, 'crypto')
    print(f"正常加密货币: {r3}")

    # 测试批量
    batch = validate_batch([stock_ok, stock_bad, crypto_ok], 'stock')
    print(f"\n批量校验: 通过{batch.passed}/{batch.total}, 通过率{batch.pass_rate:.1%}")

    print(f"\n支持的类型: {list_schemas()}")
