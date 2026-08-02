# -*- coding: utf-8 -*-
"""
Finance Data Toolkit - 自定义异常类

提供结构化的异常体系，便于调用方区分错误类型并做出相应处理。

使用示例：
    from finance_toolkit.exceptions import SourceUnavailableError, DataQualityError
    
    try:
        data = fetch_realtime_quote(['600000.SH'])
    except SourceUnavailableError as e:
        print(f"数据源不可用，尝试切换：{e.source}")
        # 切换到备用数据源
    except DataQualityError as e:
        print(f"数据质量问题：{e.issues}")
        # 触发数据清洗流程
"""

from typing import List, Optional, Dict, Any
from datetime import datetime


class FinanceError(Exception):
    """金融数据工具箱基础异常类"""
    
    def __init__(self, message: str, code: str = "FINANCE_ERROR", details: Optional[Dict] = None):
        self.message = message
        self.code = code
        self.details = details or {}
        self.timestamp = datetime.utcnow()
        super().__init__(self.message)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
            "timestamp": self.timestamp.isoformat()
        }


class SourceError(FinanceError):
    """数据源相关错误"""
    
    def __init__(self, message: str, source: str, code: str = "SOURCE_ERROR", details: Optional[Dict] = None):
        self.source = source
        super().__init__(message, code, details)


class SourceUnavailableError(SourceError):
    """数据源不可用（连接失败、超时、API 变更等）"""
    
    def __init__(self, source: str, reason: str, details: Optional[Dict] = None):
        super().__init__(
            message=f"数据源 '{source}' 不可用：{reason}",
            source=source,
            code="SOURCE_UNAVAILABLE",
            details=details
        )


class SourceRateLimitedError(SourceError):
    """数据源触发限流"""
    
    def __init__(self, source: str, retry_after: Optional[int] = None):
        super().__init__(
            message=f"数据源 '{source}' 触发限流",
            source=source,
            code="SOURCE_RATE_LIMITED",
            details={"retry_after_seconds": retry_after}
        )


class SourceAuthError(SourceError):
    """数据源认证失败（Token 无效、过期等）"""
    
    def __init__(self, source: str, reason: str = "认证失败"):
        super().__init__(
            message=f"数据源 '{source}' 认证失败：{reason}",
            source=source,
            code="SOURCE_AUTH_FAILED",
            details={"reason": reason}
        )


class DataError(FinanceError):
    """数据相关错误"""
    
    def __init__(self, message: str, data_type: str, code: str = "DATA_ERROR", details: Optional[Dict] = None):
        self.data_type = data_type
        super().__init__(message, code, details)


class DataNotFoundError(DataError):
    """未找到所需数据"""
    
    def __init__(self, data_type: str, symbol: Optional[str] = None, date_range: Optional[Dict] = None):
        details = {"data_type": data_type}
        if symbol:
            details["symbol"] = symbol
        if date_range:
            details["date_range"] = date_range
        
        super().__init__(
            message=f"未找到 {data_type} 数据" + (f" (标的：{symbol})" if symbol else ""),
            data_type=data_type,
            code="DATA_NOT_FOUND",
            details=details
        )


class DataQualityError(DataError):
    """数据质量问题（缺失值、异常值、格式错误等）"""
    
    def __init__(self, data_type: str, issues: List[str], symbol: Optional[str] = None):
        details = {
            "data_type": data_type,
            "issues": issues,
            "issue_count": len(issues)
        }
        if symbol:
            details["symbol"] = symbol
        
        super().__init__(
            message=f"{data_type} 数据存在质量问题：{', '.join(issues[:3])}" + ("..." if len(issues) > 3 else ""),
            data_type=data_type,
            code="DATA_QUALITY_ISSUE",
            details=details
        )


class DataValidationError(DataError):
    """数据校验失败（业务规则不满足）"""
    
    def __init__(self, data_type: str, validation_rule: str, actual_value: Any):
        super().__init__(
            message=f"数据校验失败：{validation_rule}，实际值：{actual_value}",
            data_type=data_type,
            code="DATA_VALIDATION_FAILED",
            details={
                "validation_rule": validation_rule,
                "actual_value": str(actual_value)
            }
        )


class CircuitBreakerError(FinanceError):
    """熔断器触发"""
    
    def __init__(self, source: str, failure_count: int, reset_after: int):
        super().__init__(
            message=f"数据源 '{source}' 已触发熔断（失败 {failure_count} 次，{reset_after}秒后恢复）",
            code="CIRCUIT_BREAKER_OPEN",
            details={
                "source": source,
                "failure_count": failure_count,
                "reset_after_seconds": reset_after
            }
        )


class FallbackError(FinanceError):
    """降级失败（所有备用源都不可用）"""
    
    def __init__(self, primary_source: str, fallback_sources: List[str], errors: Dict[str, str]):
        super().__init__(
            message=f"主数据源 '{primary_source}' 及所有备用源均不可用",
            code="FALLBACK_FAILED",
            details={
                "primary_source": primary_source,
                "fallback_sources": fallback_sources,
                "errors": errors
            }
        )


class ConfigError(FinanceError):
    """配置错误"""
    
    def __init__(self, message: str, config_key: Optional[str] = None):
        details = {"config_key": config_key} if config_key else {}
        super().__init__(
            message=message,
            code="CONFIG_ERROR",
            details=details
        )


# 便捷异常创建函数
def raise_source_unavailable(source: str, reason: str, details: Optional[Dict] = None):
    """抛出数据源不可用异常"""
    raise SourceUnavailableError(source, reason, details)


def raise_data_quality(data_type: str, issues: List[str], symbol: Optional[str] = None):
    """抛出资数据质量异常"""
    raise DataQualityError(data_type, issues, symbol)


def raise_circuit_breaker(source: str, failure_count: int, reset_after: int = 60):
    """抛出熔断器触发异常"""
    raise CircuitBreakerError(source, failure_count, reset_after)
