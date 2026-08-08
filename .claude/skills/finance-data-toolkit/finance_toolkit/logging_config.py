# -*- coding: utf-8 -*-
"""
统一日志配置模块

提供标准化的日志配置，支持：
- 结构化日志输出
- 日志级别控制
- 日志格式统一
- 日志文件轮转

使用示例：
    from finance_toolkit.logging_config import get_logger, setup_logging
    
    # 配置日志
    setup_logging(level='INFO', log_file='finance_toolkit.log')
    
    # 获取日志器
    logger = get_logger(__name__)
    logger.info("获取数据成功")
    logger.error("数据获取失败", exc_info=True)
"""

import logging
import sys
from pathlib import Path
from typing import Optional, Dict, Any
from logging.handlers import RotatingFileHandler


class FinanceFormatter(logging.Formatter):
    """
    金融数据工具箱专用日志格式化器
    
    格式：[时间] [级别] [模块名] 消息
    """
    
    # 日志级别颜色（支持 ANSI）
    COLORS = {
        'DEBUG': '\033[36m',      # 青色
        'INFO': '\033[32m',       # 绿色
        'WARNING': '\033[33m',    # 黄色
        'ERROR': '\033[31m',      # 红色
        'CRITICAL': '\033[35m',   # 紫色
    }
    RESET = '\033[0m'
    
    def __init__(self, use_color: bool = True):
        super().__init__(
            fmt='[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        self.use_color = use_color and sys.stderr.isatty()
    
    def format(self, record: logging.LogRecord) -> str:
        # 保存原始级别名
        original_levelname = record.levelname
        
        # 添加颜色
        if self.use_color:
            color = self.COLORS.get(original_levelname, '')
            record.levelname = f"{color}{original_levelname}{self.RESET}"
        
        # 调用父类格式化
        result = super().format(record)
        
        # 恢复原始级别名
        record.levelname = original_levelname
        
        return result


class JSONFormatter(logging.Formatter):
    """
    JSON 格式日志格式化器
    
    用于结构化日志输出，便于日志收集和分析。
    """
    
    def format(self, record: logging.LogRecord) -> str:
        import json
        
        log_data = {
            'timestamp': self.formatTime(record, self.datefmt),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno,
        }
        
        # 添加异常信息
        if record.exc_info and record.exc_info[0] is not None:
            log_data['exception'] = {
                'type': record.exc_info[0].__name__,
                'message': str(record.exc_info[1]),
                'traceback': self.formatException(record.exc_info)
            }
        
        # 添加额外字段
        if hasattr(record, 'extra_data'):
            log_data['data'] = record.extra_data
        
        return json.dumps(log_data, ensure_ascii=False, default=str)


def get_logger(
    name: str,
    level: int = logging.INFO,
    log_file: Optional[str] = None,
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5,
    use_json: bool = False
) -> logging.Logger:
    """
    获取配置好的日志器
    
    Args:
        name: 日志器名称（通常用 __name__）
        level: 日志级别
        log_file: 日志文件路径（可选）
        max_bytes: 单个日志文件最大大小（字节）
        backup_count: 备份文件数量
        use_json: 是否使用 JSON 格式
    
    Returns:
        配置好的 logging.Logger 实例
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # 避免重复添加 handler
    if logger.handlers:
        return logger
    
    # 创建格式化器
    if use_json:
        formatter = JSONFormatter()
    else:
        formatter = FinanceFormatter(use_color=True)
    
    # 添加控制台 handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # 添加文件 handler（如果指定）
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding='utf-8'
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    return logger


def setup_logging(
    level: int = logging.INFO,
    log_file: Optional[str] = None,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    use_json: bool = False,
    propagate: bool = False
):
    """
    配置全局日志系统
    
    Args:
        level: 默认日志级别
        log_file: 日志文件路径
        max_bytes: 单个日志文件最大大小
        backup_count: 备份文件数量
        use_json: 是否使用 JSON 格式
        propagate: 是否传播到根日志器
    """
    # 配置根日志器
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    # 清除现有 handler
    root_logger.handlers.clear()
    
    # 创建格式化器
    if use_json:
        formatter = JSONFormatter()
    else:
        formatter = FinanceFormatter(use_color=True)
    
    # 添加控制台 handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # 添加文件 handler
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding='utf-8'
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    
    # 设置传播行为
    for handler in root_logger.handlers:
        handler.propagate = propagate


def log_exception(
    logger: logging.Logger,
    exc: Exception,
    level: int = logging.ERROR,
    context: Optional[Dict[str, Any]] = None
):
    """
    记录异常日志（带完整堆栈）
    
    Args:
        logger: 日志器实例
        exc: 异常对象
        level: 日志级别
        context: 额外上下文信息
    """
    extra = {}
    if context:
        extra['extra_data'] = context
    
    logger.log(
        level,
        f"异常发生：{type(exc).__name__}: {exc}",
        exc_info=True,
        extra=extra
    )


def log_data_fetch(
    logger: logging.Logger,
    source: str,
    symbol: str,
    data_type: str,
    record_count: int,
    duration: float,
    success: bool = True,
    error: Optional[str] = None
):
    """
    记录数据获取日志
    
    Args:
        logger: 日志器实例
        source: 数据源
        symbol: 标的代码
        data_type: 数据类型
        record_count: 记录数量
        duration: 耗时（秒）
        success: 是否成功
        error: 错误信息（失败时）
    """
    if success:
        logger.info(
            f"数据获取成功：source={source}, symbol={symbol}, "
            f"type={data_type}, count={record_count}, duration={duration:.2f}s"
        )
    else:
        logger.error(
            f"数据获取失败：source={source}, symbol={symbol}, "
            f"type={data_type}, error={error}, duration={duration:.2f}s"
        )


def log_retry(
    logger: logging.Logger,
    func_name: str,
    attempt: int,
    max_retries: int,
    error: str,
    wait_time: float
):
    """
    记录重试日志
    
    Args:
        logger: 日志器实例
        func_name: 函数名
        attempt: 当前尝试次数
        max_retries: 最大重试次数
        error: 错误信息
        wait_time: 等待时间（秒）
    """
    logger.warning(
        f"重试 [{func_name}] 第 {attempt}/{max_retries} 次，"
        f"错误：{error[:100]}，等待 {wait_time}s"
    )


def log_circuit_breaker(
    logger: logging.Logger,
    source: str,
    state: str,
    failure_count: int,
    reset_after: int = 60
):
    """
    记录熔断器状态变化日志
    
    Args:
        logger: 日志器实例
        source: 数据源
        state: 当前状态 (CLOSED/OPEN/HALF_OPEN)
        failure_count: 失败次数
        reset_after: 重置时间（秒）
    """
    if state == 'OPEN':
        logger.warning(
            f"熔断器触发：source={source}, failures={failure_count}, "
            f"reset_after={reset_after}s"
        )
    elif state == 'HALF_OPEN':
        logger.info(
            f"熔断器半开：source={source}, 尝试恢复连接"
        )
    elif state == 'CLOSED':
        logger.info(
            f"熔断器恢复：source={source}, 失败次数={failure_count}"
        )


# 预配置的日志器
_default_logger: Optional[logging.Logger] = None


def get_default_logger() -> logging.Logger:
    """获取默认日志器"""
    global _default_logger
    if _default_logger is None:
        _default_logger = get_logger('finance_toolkit')
    return _default_logger


# 模块级便捷函数
logger = get_default_logger()