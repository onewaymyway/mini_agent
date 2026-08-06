"""
操作日志系统

提供结构化的操作日志记录功能，支持：
- 结构化日志输出（JSON 格式）
- 日志文件轮转
- 日志级别控制
- 错误追踪和上下文关联
"""

import logging
import json
import os
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional


class StructuredFormatter(logging.Formatter):
    """
    结构化日志格式化器，输出 JSON 格式日志。
    """
    
    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        
        # 添加额外字段
        if hasattr(record, "extra_data"):
            log_data["data"] = record.extra_data
        
        # 添加异常信息
        if record.exc_info and record.exc_info[0] is not None:
            log_data["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
                "traceback": self.formatException(record.exc_info),
            }
        
        return json.dumps(log_data, ensure_ascii=False)


class OperationLogger:
    """
    操作日志记录器。
    
    提供结构化的操作日志记录，支持：
    - 操作开始/结束记录
    - 错误追踪
    - 上下文关联
    - 日志文件轮转
    """
    
    def __init__(
        self,
        name: str = "browser_cdp",
        log_dir: Optional[str] = None,
        max_bytes: int = 10 * 1024 * 1024,  # 10MB
        backup_count: int = 5,
        enable_file_logging: bool = True,
        enable_console_logging: bool = True,
    ):
        self.name = name
        self._logger = logging.getLogger(name)
        self._logger.setLevel(logging.DEBUG)
        
        # 避免重复添加 handler
        if self._logger.handlers:
            return
        
        # 结构化格式化器
        formatter = StructuredFormatter()
        
        # 控制台 handler
        if enable_console_logging:
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.INFO)
            console_handler.setFormatter(formatter)
            self._logger.addHandler(console_handler)
        
        # 文件 handler（轮转）
        if enable_file_logging:
            if log_dir is None:
                log_dir = str(Path(__file__).parent.parent.parent / "logs")
            
            log_file = Path(log_dir) / f"{name}.log"
            log_file.parent.mkdir(parents=True, exist_ok=True)
            
            file_handler = RotatingFileHandler(
                str(log_file),
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(formatter)
            self._logger.addHandler(file_handler)
    
    def _log(
        self,
        level: int,
        message: str,
        operation: str = "",
        context: Optional[Dict[str, Any]] = None,
        error: Optional[Exception] = None,
        **kwargs,
    ):
        """内部日志方法"""
        extra = {"extra_data": {}}
        
        if operation:
            extra["extra_data"]["operation"] = operation
        if context:
            extra["extra_data"]["context"] = context
        if kwargs:
            extra["extra_data"].update(kwargs)
        
        if error:
            self._logger.log(
                level,
                message,
                extra=extra,
                exc_info=error,
            )
        else:
            self._logger.log(level, message, extra=extra)
    
    def info(self, message: str, **kwargs):
        """记录信息日志"""
        self._log(logging.INFO, message, **kwargs)
    
    def debug(self, message: str, **kwargs):
        """记录调试日志"""
        self._log(logging.DEBUG, message, **kwargs)
    
    def warning(self, message: str, **kwargs):
        """记录警告日志"""
        self._log(logging.WARNING, message, **kwargs)
    
    def error(self, message: str, **kwargs):
        """记录错误日志"""
        self._log(logging.ERROR, message, **kwargs)
    
    def operation_start(self, operation: str, context: Optional[Dict[str, Any]] = None):
        """记录操作开始"""
        self.info(f"Operation started: {operation}", operation=operation, context=context)
    
    def operation_end(self, operation: str, duration: float, success: bool = True):
        """记录操作结束"""
        status = "success" if success else "failed"
        self.info(
            f"Operation {operation} completed in {duration:.2f}s ({status})",
            operation=operation,
            duration=duration,
            status=status,
        )
    
    def operation_error(self, operation: str, error: Exception, context: Optional[Dict[str, Any]] = None):
        """记录操作错误"""
        self.error(
            f"Operation {operation} failed: {error}",
            operation=operation,
            error_message=str(error),
            error_type=type(error).__name__,
            context=context,
            error=error,
        )
    
    def get_log_files(self) -> List[Dict[str, Any]]:
        """获取日志文件信息"""
        log_dir = Path(__file__).parent.parent.parent / "logs"
        if not log_dir.exists():
            return []
        
        files = []
        for log_file in log_dir.glob(f"{self.name}*.log*"):
            files.append({
                "path": str(log_file),
                "size_bytes": log_file.stat().st_size,
                "modified": datetime.fromtimestamp(log_file.stat().st_mtime).isoformat(),
            })
        
        return sorted(files, key=lambda x: x["modified"], reverse=True)


# 全局日志实例
_global_logger: Optional[OperationLogger] = None


def get_logger(name: str = "browser_cdp") -> OperationLogger:
    """获取操作日志记录器"""
    global _global_logger
    if _global_logger is None:
        _global_logger = OperationLogger(name)
    return _global_logger


def reset_logger():
    """重置全局日志记录器"""
    global _global_logger
    _global_logger = None
