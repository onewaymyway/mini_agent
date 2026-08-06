"""
日志查询模块单元测试
"""
import json
import time
import pytest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'src'))

from reliability.log_query import LogQuery, get_log_query, reset_log_query


class TestLogQuery:
    """LogQuery 类单元测试"""
    
    def test_init_default(self):
        """测试默认初始化"""
        query = LogQuery()
        # 默认路径应指向 src/reliability/../../logs
        assert 'logs' in str(query.log_dir)
    
    def test_init_custom_log_dir(self, tmp_path):
        """测试自定义日志目录"""
        custom_dir = tmp_path / 'custom_logs'
        custom_dir.mkdir()
        query = LogQuery(log_dir=str(custom_dir))
        assert query.log_dir == custom_dir
    
    def test_query_empty_log_dir(self, tmp_path):
        """测试空日志目录查询"""
        query = LogQuery(log_dir=str(tmp_path))
        results = query.query()
        assert results == []
    
    def test_query_with_log_files(self, tmp_path):
        """测试带日志文件的查询"""
        # 创建测试日志文件
        log_file = tmp_path / 'browser_cdp_test.log'
        entries = [
            {'timestamp': '2024-01-01T10:00:00', 'level': 'INFO', 'message': 'Test message', 'data': {'operation': 'navigate'}},
            {'timestamp': '2024-01-01T11:00:00', 'level': 'ERROR', 'message': 'Error message', 'data': {'operation': 'click', 'category': 'timeout'}},
        ]
        with open(log_file, 'w', encoding='utf-8') as f:
            for entry in entries:
                f.write(json.dumps(entry) + '\n')
        
        query = LogQuery(log_dir=str(tmp_path))
        results = query.query()
        assert len(results) == 2
    
    def test_query_with_time_filter(self, tmp_path):
        """测试时间范围过滤"""
        log_file = tmp_path / 'browser_cdp_test.log'
        entries = [
            {'timestamp': '2024-01-01T10:00:00', 'level': 'INFO', 'message': 'Early message', 'data': {}},
            {'timestamp': '2024-01-01T12:00:00', 'level': 'INFO', 'message': 'Middle message', 'data': {}},
            {'timestamp': '2024-01-01T14:00:00', 'level': 'INFO', 'message': 'Late message', 'data': {}},
        ]
        with open(log_file, 'w', encoding='utf-8') as f:
            for entry in entries:
                f.write(json.dumps(entry) + '\n')
        
        query = LogQuery(log_dir=str(tmp_path))
        start = datetime(2024, 1, 1, 11, 0, 0)
        end = datetime(2024, 1, 1, 13, 0, 0)
        results = query.query(start_time=start, end_time=end)
        assert len(results) == 1
        assert results[0]['message'] == 'Middle message'
    
    def test_query_with_operation_filter(self, tmp_path):
        """测试操作类型过滤"""
        log_file = tmp_path / 'browser_cdp_test.log'
        entries = [
            {'timestamp': '2024-01-01T10:00:00', 'level': 'INFO', 'message': 'Navigate', 'data': {'operation': 'navigate'}},
            {'timestamp': '2024-01-01T11:00:00', 'level': 'INFO', 'message': 'Click', 'data': {'operation': 'click'}},
            {'timestamp': '2024-01-01T12:00:00', 'level': 'INFO', 'message': 'Type', 'data': {'operation': 'type'}},
        ]
        with open(log_file, 'w', encoding='utf-8') as f:
            for entry in entries:
                f.write(json.dumps(entry) + '\n')
        
        query = LogQuery(log_dir=str(tmp_path))
        results = query.query(operation='click')
        assert len(results) == 1
        assert results[0]['message'] == 'Click'
    
    def test_query_with_level_filter(self, tmp_path):
        """测试日志级别过滤"""
        log_file = tmp_path / 'browser_cdp_test.log'
        entries = [
            {'timestamp': '2024-01-01T10:00:00', 'level': 'INFO', 'message': 'Info message', 'data': {}},
            {'timestamp': '2024-01-01T11:00:00', 'level': 'ERROR', 'message': 'Error message', 'data': {}},
            {'timestamp': '2024-01-01T12:00:00', 'level': 'WARNING', 'message': 'Warning message', 'data': {}},
        ]
        with open(log_file, 'w', encoding='utf-8') as f:
            for entry in entries:
                f.write(json.dumps(entry) + '\n')
        
        query = LogQuery(log_dir=str(tmp_path))
        results = query.query(level='ERROR')
        assert len(results) == 1
        assert results[0]['level'] == 'ERROR'
    
    def test_query_with_error_category_filter(self, tmp_path):
        """测试错误分类过滤"""
        log_file = tmp_path / 'browser_cdp_test.log'
        entries = [
            {'timestamp': '2024-01-01T10:00:00', 'level': 'ERROR', 'message': 'Timeout error', 'data': {'category': 'timeout'}},
            {'timestamp': '2024-01-01T11:00:00', 'level': 'ERROR', 'message': 'Connection error', 'data': {'category': 'connection'}},
            {'timestamp': '2024-01-01T12:00:00', 'level': 'ERROR', 'message': 'Timeout error 2', 'data': {'category': 'timeout'}},
        ]
        with open(log_file, 'w', encoding='utf-8') as f:
            for entry in entries:
                f.write(json.dumps(entry) + '\n')
        
        query = LogQuery(log_dir=str(tmp_path))
        results = query.query(error_category='timeout')
        assert len(results) == 2
    
    def test_query_with_limit(self, tmp_path):
        """测试限制返回数量"""
        log_file = tmp_path / 'browser_cdp_test.log'
        entries = [
            {'timestamp': '2024-01-01T10:00:00', 'level': 'INFO', 'message': 'Message 1', 'data': {}},
            {'timestamp': '2024-01-01T11:00:00', 'level': 'INFO', 'message': 'Message 2', 'data': {}},
            {'timestamp': '2024-01-01T12:00:00', 'level': 'INFO', 'message': 'Message 3', 'data': {}},
        ]
        with open(log_file, 'w', encoding='utf-8') as f:
            for entry in entries:
                f.write(json.dumps(entry) + '\n')
        
        query = LogQuery(log_dir=str(tmp_path))
        results = query.query(limit=2)
        assert len(results) == 2
    
    def test_aggregate_by_hour(self, tmp_path):
        """测试按小时聚合"""
        log_file = tmp_path / 'browser_cdp_test.log'
        entries = [
            {'timestamp': '2024-01-01T10:00:00', 'level': 'INFO', 'message': 'Msg 1', 'data': {'operation': 'nav'}},
            {'timestamp': '2024-01-01T10:30:00', 'level': 'ERROR', 'message': 'Err 1', 'data': {'operation': 'click', 'category': 'timeout'}},
            {'timestamp': '2024-01-01T11:00:00', 'level': 'INFO', 'message': 'Msg 2', 'data': {'operation': 'nav'}},
        ]
        with open(log_file, 'w', encoding='utf-8') as f:
            for entry in entries:
                f.write(json.dumps(entry) + '\n')
        
        query = LogQuery(log_dir=str(tmp_path))
        stats = query.aggregate(group_by='hour')
        
        assert stats['total_entries'] == 3
        assert stats['by_level']['INFO'] == 2
        assert stats['by_level']['ERROR'] == 1
        assert '2024-01-01 10:00' in stats['time_series']
        assert '2024-01-01 11:00' in stats['time_series']
    
    def test_aggregate_by_day(self, tmp_path):
        """测试按天聚合"""
        log_file = tmp_path / 'browser_cdp_test.log'
        entries = [
            {'timestamp': '2024-01-01T10:00:00', 'level': 'INFO', 'message': 'Msg 1', 'data': {}},
            {'timestamp': '2024-01-02T10:00:00', 'level': 'INFO', 'message': 'Msg 2', 'data': {}},
        ]
        with open(log_file, 'w', encoding='utf-8') as f:
            for entry in entries:
                f.write(json.dumps(entry) + '\n')
        
        query = LogQuery(log_dir=str(tmp_path))
        stats = query.aggregate(group_by='day')
        
        assert stats['total_entries'] == 2
        assert '2024-01-01' in stats['time_series']
        assert '2024-01-02' in stats['time_series']
    
    def test_get_error_summary(self, tmp_path):
        """测试错误摘要"""
        log_file = tmp_path / 'browser_cdp_test.log'
        now = datetime.now()
        entries = [
            {'timestamp': now.isoformat(), 'level': 'ERROR', 'message': 'Timeout error', 'data': {'error_type': 'timeout', 'category': 'timeout', 'operation': 'navigate'}},
            {'timestamp': now.isoformat(), 'level': 'ERROR', 'message': 'Connection error', 'data': {'error_type': 'connection', 'category': 'connection', 'operation': 'click'}},
            {'timestamp': now.isoformat(), 'level': 'INFO', 'message': 'Normal info', 'data': {'operation': 'navigate'}},
        ]
        with open(log_file, 'w', encoding='utf-8') as f:
            for entry in entries:
                f.write(json.dumps(entry) + '\n')
        
        query = LogQuery(log_dir=str(tmp_path))
        summary = query.get_error_summary(hours=24)
        
        assert summary['total_errors'] == 2
        assert summary['by_type']['timeout'] == 1
        assert summary['by_type']['connection'] == 1
        assert len(summary['errors']) == 2
    
    def test_get_operation_stats(self, tmp_path):
        """测试操作统计"""
        log_file = tmp_path / 'browser_cdp_test.log'
        now = datetime.now()
        entries = [
            {'timestamp': now.isoformat(), 'level': 'INFO', 'message': 'Success', 'data': {'operation': 'navigate', 'status': 'success'}},
            {'timestamp': now.isoformat(), 'level': 'ERROR', 'message': 'Failed', 'data': {'operation': 'click', 'status': 'failed'}},
            {'timestamp': now.isoformat(), 'level': 'INFO', 'message': 'Success 2', 'data': {'operation': 'navigate', 'status': 'success'}},
        ]
        with open(log_file, 'w', encoding='utf-8') as f:
            for entry in entries:
                f.write(json.dumps(entry) + '\n')
        
        query = LogQuery(log_dir=str(tmp_path))
        stats = query.get_operation_stats(hours=24)
        
        assert stats['total_operations'] == 3
        assert stats['success_count'] == 2
        assert stats['failure_count'] == 1
        assert stats['by_operation']['navigate']['count'] == 2
        assert stats['by_operation']['click']['count'] == 1
    
    def test_query_invalid_json_lines(self, tmp_path):
        """测试跳过无效 JSON 行"""
        log_file = tmp_path / 'browser_cdp_test.log'
        with open(log_file, 'w', encoding='utf-8') as f:
            f.write('This is not JSON\n')
            f.write(json.dumps({'timestamp': '2024-01-01T10:00:00', 'level': 'INFO', 'message': 'Valid', 'data': {}}) + '\n')
            f.write('Another invalid line\n')
        
        query = LogQuery(log_dir=str(tmp_path))
        results = query.query()
        assert len(results) == 1
        assert results[0]['message'] == 'Valid'
    
    def test_query_no_matching_files(self, tmp_path):
        """测试无匹配日志文件"""
        # 创建非 browser_cdp 前缀的日志文件
        other_log = tmp_path / 'other.log'
        other_log.write_text(json.dumps({'timestamp': '2024-01-01T10:00:00', 'level': 'INFO', 'message': 'Test', 'data': {}}) + '\n')
        
        query = LogQuery(log_dir=str(tmp_path))
        results = query.query()
        assert len(results) == 0


class TestGetLogQuery:
    """全局日志查询器函数测试"""
    
    def test_get_log_query_returns_instance(self):
        """测试返回实例"""
        reset_log_query()
        query = get_log_query()
        assert isinstance(query, LogQuery)
        reset_log_query()
    
    def test_get_log_query_singleton(self):
        """测试单例模式"""
        reset_log_query()
        query1 = get_log_query()
        query2 = get_log_query()
        assert query1 is query2
        reset_log_query()
    
    def test_reset_log_query(self):
        """测试重置"""
        reset_log_query()
        query1 = get_log_query()
        reset_log_query()
        query2 = get_log_query()
        assert query1 is not query2


class TestLogQueryIntegration:
    """日志查询集成测试"""
    
    def test_full_workflow(self, tmp_path):
        """完整工作流测试"""
        # 创建测试日志
        log_file = tmp_path / 'browser_cdp_test.log'
        now = datetime.now()
        entries = [
            {'timestamp': (now - timedelta(hours=2)).isoformat(), 'level': 'INFO', 'message': 'Navigate start', 'data': {'operation': 'navigate', 'status': 'success'}},
            {'timestamp': (now - timedelta(hours=1)).isoformat(), 'level': 'ERROR', 'message': 'Click failed', 'data': {'operation': 'click', 'status': 'failed', 'category': 'timeout', 'error_type': 'timeout'}},
            {'timestamp': now.isoformat(), 'level': 'INFO', 'message': 'Type success', 'data': {'operation': 'type', 'status': 'success'}},
        ]
        with open(log_file, 'w', encoding='utf-8') as f:
            for entry in entries:
                f.write(json.dumps(entry) + '\n')
        
        query = LogQuery(log_dir=str(tmp_path))
        
        # 测试查询
        all_logs = query.query()
        assert len(all_logs) == 3
        
        # 测试聚合
        stats = query.aggregate(group_by='hour')
        assert stats['total_entries'] == 3
        
        # 测试错误摘要
        error_summary = query.get_error_summary(hours=24)
        assert error_summary['total_errors'] == 1
        
        # 测试操作统计
        op_stats = query.get_operation_stats(hours=24)
        assert op_stats['total_operations'] == 3
        assert op_stats['success_count'] == 2
        assert op_stats['failure_count'] == 1
