# -*- coding: utf-8 -*-
"""
数据格式验证器 - 基于 JSON Schema 验证 FinanceData 输出
"""

import json
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import is_dataclass, asdict

try:
    import jsonschema
    _JSONSCHEMA_AVAILABLE = True
except ImportError:
    _JSONSCHEMA_AVAILABLE = False


class SchemaValidationError:
    """验证错误信息"""
    
    def __init__(self, path: str, message: str, severity: str = "error"):
        self.path = path
        self.message = message
        self.severity = severity  # error, warning
    
    def __str__(self):
        return f"[{self.severity.upper()}] {self.path}: {self.message}"
    
    def to_dict(self):
        return {
            "path": self.path,
            "message": self.message,
            "severity": self.severity
        }


class ValidationResult:
    """验证结果"""
    
    def __init__(self, is_valid: bool, errors: List[SchemaValidationError], 
                 warnings: List[SchemaValidationError]):
        self.is_valid = is_valid
        self.errors = errors
        self.warnings = warnings
    
    @property
    def all_issues(self) -> List[SchemaValidationError]:
        return self.errors + self.warnings
    
    def __bool__(self):
        return self.is_valid
    
    def __str__(self):
        if self.is_valid:
            return "验证通过"
        errors_str = "; ".join(str(e) for e in self.errors[:5])
        return f"验证失败: {errors_str}"


class FinanceDataSchemaValidator:
    """
    FinanceData JSON Schema 验证器
    
    验证规则：
    1. 顶层字段必填校验
    2. data_type 与 payload 结构匹配校验
    3. 数值范围校验（如 sentiment_score 在 -1 到 1 之间）
    4. 时间格式校验（ISO 8601）
    5. symbol 格式校验
    """
    
    # 数据类型与必填字段的映射
    REQUIRED_FIELDS = {
        "quote": ["open", "high", "low", "close", "volume", "amount"],
        "kline": ["date", "open", "high", "low", "close", "volume", "amount"],
        "financial": ["report_date", "type"],
        "dividend": ["announcement_date", "record_date", "ex_dividend_date", "payment_date", "dividend_per_share"],
        "lhb": ["trade_date", "reason"],
        "northbound": ["date", "type"],
        "stock_basic": ["name", "industry", "list_date"],
        "fund_nav": ["nav_date", "nav", "accumulated_nav"],
        "fund_holdings": ["report_date", "stock_code", "stock_name", "shares", "market_value", "weight"],
        "bond_yield": ["date", "bond_type", "yield_rate"],
        "bond_quote": ["bond_code", "bond_name", "price", "yield_rate"],
        "futures_quote": ["contract_code", "open", "high", "low", "close", "settlement", "volume", "open_interest"],
        "futures_kline": ["date", "open", "high", "low", "close", "volume", "open_interest"],
        "index_quote": ["open", "high", "low", "close", "volume", "amount"],
        "index_kline": ["date", "open", "high", "low", "close", "volume", "amount"],
        "macro_gdp": ["quarter", "gdp", "yoy"],
        "macro_cpi": ["date", "cpi"],
        "macro_pmi": ["date", "pmi"],
        "forex_quote": ["currency_pair", "rate", "change_pct"],
        "crypto_quote": ["symbol", "price", "volume_24h", "market_cap"],
        "etf_quote": ["open", "high", "low", "close", "volume", "amount"],
        "etf_kline": ["date", "open", "high", "low", "close", "volume"],
        "news": ["title", "publish_time", "source"],
        "sentiment": ["date", "sentiment_score", "sentiment_label"],
        "social": ["post_id", "content", "publish_time", "author"]
    }
    
    # 数值范围约束
    VALUE_RANGES = {
        "sentiment_score": (-1.0, 1.0),
        "quality_score": (0.0, 1.0),
        "premium_discount": (-100.0, 100.0),
        "change_pct": (-100.0, 1000.0),
        "pe_ratio": (0.0, 10000.0),
        "pb_ratio": (0.0, 1000.0),
        "roe": (-100.0, 100.0),
        "roa": (-100.0, 100.0),
        "gross_margin": (-100.0, 100.0),
        "net_margin": (-100.0, 100.0),
        "turnover_rate": (0.0, 1000.0),
        "dividend_yield": (0.0, 100.0),
        "yield_rate": (-10.0, 100.0),
        "fear_greed_index": (0, 100)
    }
    
    # 枚举值约束
    ENUM_CONSTRAINTS = {
        "data_type": [
            "quote", "kline", "financial", "dividend", "lhb", "northbound",
            "stock_basic", "fund_nav", "fund_holdings", "bond_yield", "bond_quote",
            "futures_quote", "futures_kline", "index_quote", "index_kline",
            "macro_gdp", "macro_cpi", "macro_pmi", "forex_quote", "crypto_quote",
            "etf_quote", "etf_kline", "news", "sentiment", "social"
        ],
        "source": ["akshare", "tushare", "eastmoney", "sina", "tencent", "netease", "lexicon", "binance", "coingecko", "yahoo"],
        "bond_type": ["treasury_1y", "treasury_5y", "treasury_10y", "corporate_aa", "corporate_a"],
        "sentiment_label": ["极度悲观", "悲观", "中性", "乐观", "极度乐观"],
        "financial_type": ["balance_sheet", "income_statement", "cash_flow"],
        "kline_period": ["1m", "5m", "15m", "30m", "60m", "daily", "weekly", "monthly"],
        "northbound_type": ["sh_hk", "sz_hk", "total"],
        "fund_type": ["stock", "bond", "mixed", "index", "money", "qdii"],
        "market": ["SSE", "SZSE", "BSE"],
        "stock_status": ["active", "delisted", "suspended"]
    }
    
    def __init__(self, schema_path: Optional[str] = None):
        """
        初始化验证器
        
        Args:
            schema_path: JSON Schema 文件路径，默认使用内置 schema
        """
        if schema_path:
            self.schema = self._load_schema(schema_path)
        else:
            self.schema = self._get_builtin_schema()
        
        self._jsonschema = None
        if _JSONSCHEMA_AVAILABLE:
            try:
                import jsonschema as js
                self._jsonschema = js
            except ImportError:
                pass
    
    def _load_schema(self, path: str) -> Dict:
        """加载 JSON Schema 文件"""
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def _get_builtin_schema(self) -> Dict:
        """获取内置 schema（简化版）"""
        return {
            "type": "object",
            "required": ["source", "data_type", "symbol", "timestamp", "payload"],
            "properties": {
                "source": {"type": "string"},
                "data_type": {"type": "string"},
                "symbol": {"type": "string"},
                "timestamp": {"type": "string"},
                "payload": {"type": "object"},
                "raw": {"type": ["object", "null"]},
                "meta": {"type": ["object", "null"]}
            }
        }
    
    def validate(self, data: Any) -> ValidationResult:
        """
        验证 FinanceData 对象或字典
        
        Args:
            data: FinanceData 对象或字典
        
        Returns:
            ValidationResult
        """
        errors = []
        warnings = []
        
        # 转换为字典
        if is_dataclass(data) and not isinstance(data, type):
            data_dict = asdict(data)
        elif isinstance(data, dict):
            data_dict = data
        else:
            errors.append(SchemaValidationError(
                "root", 
                f"Invalid data type: {type(data).__name__}, expected dict or FinanceData",
                "error"
            ))
            return ValidationResult(False, errors, warnings)
        
        # 1. 顶层字段校验
        self._validate_top_level(data_dict, errors, warnings)
        
        # 2. data_type 与 payload 匹配校验
        data_type = data_dict.get("data_type")
        payload = data_dict.get("payload", {})
        
        if data_type and payload is not None:
            self._validate_payload(data_type, payload, errors, warnings)
        
        # 3. JSON Schema 校验（如果可用）
        if self._jsonschema:
            try:
                self._jsonschema.validate(instance=data_dict, schema=self.schema)
            except self._jsonschema.ValidationError as e:
                errors.append(SchemaValidationError(
                    e.path[0] if e.path else "root",
                    e.message,
                    "error"
                ))
        
        is_valid = len(errors) == 0
        return ValidationResult(is_valid, errors, warnings)
    
    def _validate_top_level(self, data: Dict, errors: List, warnings: List):
        """校验顶层字段"""
        # 必填字段
        required_fields = ["source", "data_type", "symbol", "timestamp", "payload"]
        for field in required_fields:
            if field not in data or data[field] is None:
                errors.append(SchemaValidationError(
                    field,
                    f"Required field '{field}' is missing",
                    "error"
                ))
        
        # source 枚举校验
        source = data.get("source")
        if source and source not in self.ENUM_CONSTRAINTS["source"]:
            warnings.append(SchemaValidationError(
                "source",
                f"Unknown source: '{source}'",
                "warning"
            ))
        
        # data_type 枚举校验
        data_type = data.get("data_type")
        if data_type and data_type not in self.ENUM_CONSTRAINTS["data_type"]:
            errors.append(SchemaValidationError(
                "data_type",
                f"Invalid data_type: '{data_type}'",
                "error"
            ))
        
        # symbol 格式校验
        symbol = data.get("symbol")
        if symbol:
            if not isinstance(symbol, str) or len(symbol) < 3:
                errors.append(SchemaValidationError(
                    "symbol",
                    f"Invalid symbol format: '{symbol}'",
                    "error"
                ))
        
        # timestamp 格式校验
        timestamp = data.get("timestamp")
        if timestamp:
            if not self._is_valid_iso_timestamp(timestamp):
                warnings.append(SchemaValidationError(
                    "timestamp",
                    f"Timestamp may not be valid ISO 8601: '{timestamp}'",
                    "warning"
                ))
    
    def _validate_payload(self, data_type: str, payload: Dict, errors: List, warnings: List):
        """校验 payload 字段"""
        # 必填字段校验
        required = self.REQUIRED_FIELDS.get(data_type, [])
        for field in required:
            if field not in payload or payload[field] is None:
                errors.append(SchemaValidationError(
                    f"payload.{field}",
                    f"Required field '{field}' is missing in payload",
                    "error"
                ))
        
        # 数值范围校验
        for key, value in payload.items():
            if key in self.VALUE_RANGES and isinstance(value, (int, float)):
                min_val, max_val = self.VALUE_RANGES[key]
                if value < min_val or value > max_val:
                    errors.append(SchemaValidationError(
                        f"payload.{key}",
                        f"Value {value} out of range [{min_val}, {max_val}]",
                        "error"
                    ))
        
        # 枚举值校验
        if data_type == "financial":
            ftype = payload.get("type")
            if ftype and ftype not in self.ENUM_CONSTRAINTS["financial_type"]:
                errors.append(SchemaValidationError(
                    "payload.type",
                    f"Invalid financial type: '{ftype}'",
                    "error"
                ))
        
        if data_type == "sentiment":
            label = payload.get("sentiment_label")
            if label and label not in self.ENUM_CONSTRAINTS["sentiment_label"]:
                errors.append(SchemaValidationError(
                    "payload.sentiment_label",
                    f"Invalid sentiment label: '{label}'",
                    "error"
                ))
    
    def _is_valid_iso_timestamp(self, timestamp: str) -> bool:
        """校验 ISO 8601 时间戳格式"""
        from datetime import datetime
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
                    datetime.strptime(timestamp.replace("+00:00", "Z").rstrip("Z") + "Z" if timestamp.endswith("Z") else timestamp, fmt)
                    return True
                except ValueError:
                    continue
            return False
        except Exception:
            return False
    
    def validate_batch(self, data_list: List[Any]) -> List[ValidationResult]:
        """
        批量验证
        
        Args:
            data_list: 数据列表
        
        Returns:
            验证结果列表
        """
        return [self.validate(data) for data in data_list]
    
    def get_summary(self, results: List[ValidationResult]) -> Dict:
        """
        获取验证摘要
        
        Args:
            results: 验证结果列表
        
        Returns:
            摘要信息
        """
        total = len(results)
        valid = sum(1 for r in results if r.is_valid)
        invalid = total - valid
        
        error_count = sum(len(r.errors) for r in results)
        warning_count = sum(len(r.warnings) for r in results)
        
        return {
            "total": total,
            "valid": valid,
            "invalid": invalid,
            "error_count": error_count,
            "warning_count": warning_count,
            "valid_rate": valid / total if total > 0 else 0
        }


# 全局验证器实例
_validator = FinanceDataSchemaValidator()


def validate_finance_data(data: Any) -> ValidationResult:
    """
    验证 FinanceData 对象或字典
    
    Args:
        data: FinanceData 对象或字典
    
    Returns:
        ValidationResult
    """
    return _validator.validate(data)


def validate_finance_data_batch(data_list: List[Any]) -> List[ValidationResult]:
    """
    批量验证 FinanceData
    
    Args:
        data_list: 数据列表
    
    Returns:
        验证结果列表
    """
    return _validator.validate_batch(data_list)


def get_validation_summary(results: List[ValidationResult]) -> Dict:
    """
    获取验证摘要
    
    Args:
        results: 验证结果列表
    
    Returns:
        摘要信息
    """
    return _validator.get_summary(results)


if __name__ == "__main__":
    # 测试示例
    from finance_toolkit.core import FinanceData
    
    # 有效数据
    valid_data = FinanceData(
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
            "amount": 10500000.0
        }
    )
    
    result = validate_finance_data(valid_data)
    print(f"有效数据验证: {result}")
    
    # 无效数据（缺少必填字段）
    invalid_data = FinanceData(
        source="akshare",
        data_type="quote",
        symbol="600000.SH",
        timestamp="2024-01-15T10:30:00Z",
        payload={"open": 10.50}  # 缺少必填字段
    )
    
    result = validate_finance_data(invalid_data)
    print(f"无效数据验证: {result}")
    for error in result.errors:
        print(f"  - {error}")
