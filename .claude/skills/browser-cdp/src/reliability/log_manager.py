# -*- coding: utf-8 -*-
"""
日志管理模块

提供生产/测试日志分离、结构化日志格式和自动清理机制。
"""

import json
import logging
import os
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional


class StructuredFormatter(logging.Formatter):
    """结构化日志格式化器 - JSON 格式"""
    
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
        
        if record.exc_info and record.exc_info[0] is not None:
            log_data["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
                "traceback": self.formatException(record.exc_info),
            }
        
        if hasattr(record, 'log_data'):
            log_data["data"] = record.log_data
        
        return json.dumps(log_data, ensure_ascii=False, default=str)


class LogManager:
    """日志管理器 - 支持生产/测试环境分离"""
    
    LOG_DIRS = {
        "production": "logs/production",
        "test": "logs/test",
        "archive": "logs/archive",
    }
    
    RETENTION_DAYS = {
        "production": 30,
        "test": 7,
        "archive": 90,
    }
    
    MAX_LOG_SIZE = 10 * 1024 * 1024
    BACKUP_COUNT = 5
    
    def __init__(self, env: str = "production"):
        self.env = env
        self.log_dir = self.LOG_DIRS.get(env, self.LOG_DIRS["production"])
        self._loggers: Dict[str, logging.Logger] = {}
        self._setup_logging()
    
    def _setup_logging(self):
        """初始化日志配置"""
        Path(self.log_dir).mkdir(parents=True, exist_ok=True)
        
        log_file = os.path.join(self.log_dir, "browser_cdp.log")
        handler = RotatingFileHandler(
            log_file,
            maxBytes=self.MAX_LOG_SIZE,
            backupCount=self.BACKUP_COUNT,
            encoding='utf-8'
        )
        handler.setFormatter(StructuredFormatter())
        
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        
        logger = logging.getLogger("browser_cdp")
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        if self.env == "test":
            logger.addHandler(console_handler)
        
        self._loggers["browser_cdp"] = logger
    
    def get_logger(self, name: str = "browser_cdp") -> logging.Logger:
        """获取 logger 实例"""
        if name not in self._loggers:
            logger = logging.getLogger(name)
            if not logger.handlers:
                logger.setLevel(logging.INFO)
                log_file = os.path.join(self.log_dir, f"{name}.log")
                handler = RotatingFileHandler(
                    log_file, maxBytes=self.MAX_LOG_SIZE,
                    backupCount=self.BACKUP_COUNT, encoding='utf-8'
                )
                handler.setFormatter(StructuredFormatter())
                logger.addHandler(handler)
            self._loggers[name] = logger
        return self._loggers[name]
    
    def log_operation(self, logger: logging.Logger, level: str, operation: str,
                      url: str = "", query: str = "", error_type: str = "",
                      error_message: str = "", retry_count: int = 0, duration_ms: float = 0.0):
        """记录结构化操作日志"""
        log_data = {
            "operation": operation, "url": url, "query": query,
            "error_type": error_type, "error_message": error_message,
            "retry_count": retry_count, "duration_ms": duration_ms,
        }
        log_method = getattr(logger, level.lower(), logger.info)
        log_method(f"[{operation}] {log_data}", extra={"log_data": log_data})
    
    def cleanup_old_logs(self, max_age_days: Optional[int] = None) -> int:
        """清理过期日志文件"""
        max_age = max_age_days or self.RETENTION_DAYS.get(self.env, 30)
        cutoff = time.time() - (max_age * 86400)
        cleaned = 0
        for log_file in Path(self.log_dir).glob("*.log*"):
            if log_file.stat().st_mtime < cutoff:
                log_file.unlink()
                cleaned += 1
        if cleaned > 0:
            logging.getLogger("browser_cdp").info(f"Cleaned up {cleaned} old log files")
        return cleaned
    
    def get_status(self) -> Dict[str, Any]:
        return {
            "env": self.env, "log_dir": self.log_dir,
            "retention_days": self.RETENTION_DAYS.get(self.env, 30),
            "loggers": list(self._loggers.keys()),
        }


_global_log_manager: Optional[LogManager] = None


def get_log_manager(env: str = "production") -> LogManager:
    """获取全局日志管理器实例"""
    global _global_log_manager
    if _global_log_manager is None or _global_log_manager.env != env:
        _global_log_manager = LogManager(env=env)
    return _global_log_manager


def get_logger(name: str = "browser_cdp") -> logging.Logger:
    """便捷函数：获取 logger"""
    return get_log_manager().get_logger(name)
