"""
日志查询与分析模块

提供结构化日志的查询、过滤、聚合分析功能，
支持按时间范围、操作类型、错误分类等维度查询。
"""

import json
import re
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class LogQuery:
    """
    日志查询器。
    
    支持：
    - 按时间范围查询
    - 按操作类型过滤
    - 按错误分类过滤
    - 聚合统计分析
    """
    
    def __init__(self, log_dir: Optional[str] = None):
        if log_dir is None:
            log_dir = str(Path(__file__).parent.parent.parent / "logs")
        self.log_dir = Path(log_dir)
    
    def query(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        operation: Optional[str] = None,
        level: Optional[str] = None,
        error_category: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        查询日志。
        
        Args:
            start_time: 开始时间
            end_time: 结束时间
            operation: 操作类型过滤
            level: 日志级别过滤
            error_category: 错误分类过滤
            limit: 最大返回条数
        
        Returns:
            日志条目列表
        """
        results = []
        
        # 解析时间范围
        if start_time:
            start_ts = start_time.timestamp()
        else:
            start_ts = 0
        
        if end_time:
            end_ts = end_time.timestamp()
        else:
            end_ts = float('inf')
        
        # 遍历日志文件
        log_files = sorted(self.log_dir.glob("browser_cdp*.log*"), reverse=True)
        
        for log_file in log_files:
            if len(results) >= limit:
                break
            
            try:
                with open(log_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        
                        # 时间过滤
                        try:
                            entry_time = datetime.fromisoformat(entry.get('timestamp', ''))
                            entry_ts = entry_time.timestamp()
                        except (ValueError, TypeError):
                            continue
                        
                        if entry_ts < start_ts or entry_ts > end_ts:
                            continue
                        
                        # 操作类型过滤
                        if operation:
                            data = entry.get('data', {})
                            if data.get('operation') != operation:
                                continue
                        
                        # 日志级别过滤
                        if level and entry.get('level') != level:
                            continue
                        
                        # 错误分类过滤
                        if error_category:
                            data = entry.get('data', {})
                            if data.get('category') != error_category:
                                continue
                        
                        results.append(entry)
                        
                        if len(results) >= limit:
                            break
            except Exception as e:
                logger.warning(f"Failed to read log file {log_file}: {e}")
        
        return results
    
    def aggregate(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        group_by: str = "hour",
    ) -> Dict[str, Any]:
        """
        聚合统计分析。
        
        Args:
            start_time: 开始时间
            end_time: 结束时间
            group_by: 聚合维度（hour/day/operation/error_category）
        
        Returns:
            聚合统计结果
        """
        logs = self.query(start_time=start_time, end_time=end_time, limit=10000)
        
        stats = {
            "total_entries": len(logs),
            "by_level": {},
            "by_operation": {},
            "by_error_category": {},
            "time_series": {},
        }
        
        for entry in logs:
            # 按级别统计
            level = entry.get('level', 'UNKNOWN')
            stats["by_level"][level] = stats["by_level"].get(level, 0) + 1
            
            # 按操作统计
            data = entry.get('data', {})
            operation = data.get('operation', 'unknown')
            stats["by_operation"][operation] = stats["by_operation"].get(operation, 0) + 1
            
            # 按错误分类统计
            category = data.get('category', 'unknown')
            if entry.get('level') in ('ERROR', 'WARNING'):
                stats["by_error_category"][category] = stats["by_error_category"].get(category, 0) + 1
            
            # 时间序列
            try:
                entry_time = datetime.fromisoformat(entry.get('timestamp', ''))
                if group_by == "hour":
                    key = entry_time.strftime("%Y-%m-%d %H:00")
                elif group_by == "day":
                    key = entry_time.strftime("%Y-%m-%d")
                else:
                    key = entry_time.isoformat()
                
                if key not in stats["time_series"]:
                    stats["time_series"][key] = {"count": 0, "errors": 0}
                stats["time_series"][key]["count"] += 1
                
                if entry.get('level') in ('ERROR', 'WARNING'):
                    stats["time_series"][key]["errors"] += 1
            except (ValueError, TypeError):
                continue
        
        return stats
    
    def get_error_summary(
        self,
        hours: int = 24,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """
        获取错误摘要。
        
        Args:
            hours: 查询最近多少小时的错误
            limit: 最大返回条数
        
        Returns:
            错误摘要
        """
        start_time = datetime.now() - timedelta(hours=hours)
        logs = self.query(
            start_time=start_time,
            level="ERROR",
            limit=limit,
        )
        
        summary = {
            "total_errors": len(logs),
            "errors": [],
            "by_type": {},
            "by_category": {},
        }
        
        for entry in logs:
            data = entry.get('data', {})
            error_type = data.get('error_type', 'unknown')
            category = data.get('category', 'unknown')
            
            summary["by_type"][error_type] = summary["by_type"].get(error_type, 0) + 1
            summary["by_category"][category] = summary["by_category"].get(category, 0) + 1
            
            summary["errors"].append({
                "timestamp": entry.get('timestamp'),
                "message": entry.get('message', ''),
                "error_type": error_type,
                "category": category,
                "operation": data.get('operation', ''),
            })
        
        return summary
    
    def get_operation_stats(
        self,
        hours: int = 24,
    ) -> Dict[str, Any]:
        """
        获取操作统计。
        
        Args:
            hours: 查询最近多少小时的操作
        
        Returns:
            操作统计
        """
        start_time = datetime.now() - timedelta(hours=hours)
        logs = self.query(start_time=start_time, limit=10000)
        
        stats = {
            "total_operations": 0,
            "success_count": 0,
            "failure_count": 0,
            "by_operation": {},
        }
        
        for entry in logs:
            data = entry.get('data', {})
            operation = data.get('operation', '')
            
            if not operation:
                continue
            
            stats["total_operations"] += 1
            
            if operation not in stats["by_operation"]:
                stats["by_operation"][operation] = {
                    "count": 0,
                    "success": 0,
                    "failure": 0,
                }
            
            stats["by_operation"][operation]["count"] += 1
            
            status = data.get('status', '')
            if status == 'success':
                stats["success_count"] += 1
                stats["by_operation"][operation]["success"] += 1
            elif status == 'failed':
                stats["failure_count"] += 1
                stats["by_operation"][operation]["failure"] += 1
        
        return stats


# 全局日志查询器实例
_global_log_query: Optional[LogQuery] = None


def get_log_query(log_dir: Optional[str] = None) -> LogQuery:
    """获取日志查询器实例"""
    global _global_log_query
    if _global_log_query is None:
        _global_log_query = LogQuery(log_dir)
    return _global_log_query


def reset_log_query():
    """重置全局日志查询器"""
    global _global_log_query
    _global_log_query = None
