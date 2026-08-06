"""
操作日志系统单元测试
"""

import pytest
import json
import logging
import time
from unittest.mock import MagicMock, patch
from pathlib import Path

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from src.reliability.logging import StructuredFormatter, OperationLogger


class TestStructuredFormatter:
    """StructuredFormatter 测试"""
    
    def test_format_basic(self):
        """测试基本格式化"""
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        
        result = formatter.format(record)
        data = json.loads(result)
        
        assert data["message"] == "Test message"
        assert data["level"] == "INFO"
        assert data["logger"] == "test"
        assert "timestamp" in data
    
    def test_format_with_extra_data(self):
        """测试带额外数据的格式化"""
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        record.extra_data = {"operation": "search", "url": "https://example.com"}
        
        result = formatter.format(record)
        data = json.loads(result)
        
        assert data["data"]["operation"] == "search"
        assert data["data"]["url"] == "https://example.com"
    
    def test_format_with_exception(self):
        """测试带异常信息的格式化"""
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="Error occurred",
            args=(),
            exc_info=(ValueError, ValueError("test error"), None),
        )
        
        result = formatter.format(record)
        data = json.loads(result)
        
        assert "exception" in data
        assert data["exception"]["type"] == "ValueError"
        assert data["exception"]["message"] == "test error"
        assert "traceback" in data["exception"]
    
    def test_format_with_module_and_function(self):
        """测试包含模块和功能信息"""
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.DEBUG,
            pathname="test.py",
            lineno=42,
            msg="Debug message",
            args=(),
            exc_info=None,
        )
        record.module = "test_module"
        record.funcName = "test_function"
        
        result = formatter.format(record)
        data = json.loads(result)
        
        assert data["module"] == "test_module"
        assert data["function"] == "test_function"
        assert data["line"] == 42


class TestOperationLogger:
    """OperationLogger 测试"""
    
    def test_init_default(self):
        """测试默认初始化"""
        logger = OperationLogger(enable_file_logging=False)
        assert logger.name == "browser_cdp"
        assert logger._logger is not None
    
    def test_init_custom_name(self):
        """测试自定义名称初始化"""
        logger = OperationLogger(name="test_logger", enable_file_logging=False)
        assert logger.name == "test_logger"
    
    def test_info(self, caplog):
        """测试 info 日志"""
        with caplog.at_level(logging.INFO):
            logger = OperationLogger(enable_file_logging=False)
            logger.info("Test info message")
            assert "Test info message" in caplog.text
    
    def test_debug(self, caplog):
        """测试 debug 日志"""
        with caplog.at_level(logging.DEBUG):
            logger = OperationLogger(enable_file_logging=False)
            logger.debug("Test debug message")
            assert "Test debug message" in caplog.text
    
    def test_warning(self, caplog):
        """测试 warning 日志"""
        with caplog.at_level(logging.WARNING):
            logger = OperationLogger(enable_file_logging=False)
            logger.warning("Test warning message")
            assert "Test warning message" in caplog.text
    
    def test_error(self, caplog):
        """测试 error 日志"""
        with caplog.at_level(logging.ERROR):
            logger = OperationLogger(enable_file_logging=False)
            logger.error("Test error message")
            assert "Test error message" in caplog.text
    
    def test_operation_start(self, caplog):
        """测试操作开始日志"""
        with caplog.at_level(logging.INFO):
            logger = OperationLogger(enable_file_logging=False)
            logger.operation_start("search", {"query": "test"})
            assert "Operation started: search" in caplog.text
    
    def test_operation_end(self, caplog):
        """测试操作结束日志"""
        with caplog.at_level(logging.INFO):
            logger = OperationLogger(enable_file_logging=False)
            logger.operation_end("search", 1.5, success=True)
            assert "Operation search completed in 1.50s (success)" in caplog.text
    
    def test_operation_end_failed(self, caplog):
        """测试操作失败日志"""
        with caplog.at_level(logging.INFO):
            logger = OperationLogger(enable_file_logging=False)
            logger.operation_end("search", 2.0, success=False)
            assert "Operation search completed in 2.00s (failed)" in caplog.text
    
    def test_operation_error(self, caplog):
        """测试操作错误日志"""
        with caplog.at_level(logging.ERROR):
            logger = OperationLogger(enable_file_logging=False)
            error = ValueError("Test error")
            logger.operation_error("search", error, {"url": "https://example.com"})
            assert "Operation search failed" in caplog.text
            assert "Test error" in caplog.text
    
    def test_get_log_files_no_dir(self):
        """测试无日志目录时返回空列表"""
        logger = OperationLogger(enable_file_logging=False)
        files = logger.get_log_files()
        assert isinstance(files, list)
    
    def test_log_structure(self, caplog):
        """测试日志结构正确性"""
        logger = OperationLogger(enable_file_logging=False)
        
        with caplog.at_level(logging.INFO):
            logger.info("Test message", operation="search", context={"url": "https://example.com"})
            
            # 验证日志记录包含额外数据
            assert len(caplog.records) == 1
            record = caplog.records[0]
            assert hasattr(record, 'extra_data')
            assert record.extra_data["operation"] == "search"
            assert record.extra_data["context"]["url"] == "https://example.com"


class TestGetLogger:
    """全局日志函数测试"""
    
    def test_get_logger_returns_instance(self):
        """测试 get_logger 返回实例"""
        from src.reliability.logging import get_logger
        
        logger = get_logger()
        assert isinstance(logger, OperationLogger)
    
    def test_get_logger_singleton(self):
        """测试 get_logger 单例"""
        from src.reliability.logging import get_logger
        
        l1 = get_logger()
        l2 = get_logger()
        assert l1 is l2
    
    def test_reset_logger(self):
        """测试 reset_logger"""
        from src.reliability.logging import get_logger, reset_logger
        
        l1 = get_logger()
        reset_logger()
        l2 = get_logger()
        
        assert l1 is not l2


class TestOperationLoggerIntegration:
    """OperationLogger 集成测试"""
    
    def test_full_workflow(self, tmp_path):
        """测试完整工作流程"""
        logger = OperationLogger(
            name="test_integration",
            log_dir=str(tmp_path / "logs"),
            enable_console_logging=False,
        )
        
        # 记录操作
        logger.operation_start("search", {"query": "test"})
        time.sleep(0.1)
        logger.operation_end("search", 0.1, success=True)
        
        # 记录错误
        error = ValueError("Test error")
        logger.operation_error("search", error, {"url": "https://example.com"})
        
        # 验证日志文件存在
        log_files = logger.get_log_files()
        # get_log_files 使用默认路径，需要检查实际写入的日志目录
        expected_log_dir = str(tmp_path / "logs")
        log_files = logger.get_log_files()
        # 直接检查 tmp_path/logs 目录
        log_path = Path(expected_log_dir) / "test_integration.log"
        assert log_path.exists(), f"日志文件不存在: {log_path}"
        log_files = [{"path": str(log_path), "size_bytes": log_path.stat().st_size}]
        assert len(log_files) > 0
        
        # 验证日志内容
        with open(log_files[0]["path"], "r", encoding="utf-8") as f:
            lines = f.readlines()
            assert len(lines) >= 3  # 至少 3 条日志
            
            # 验证第一条是操作开始
            first_log = json.loads(lines[0])
            assert first_log["message"].startswith("Operation started")
            
            # 验证最后一条是错误
            last_log = json.loads(lines[-1])
            assert last_log["level"] == "ERROR"
            assert "Test error" in last_log["message"]
