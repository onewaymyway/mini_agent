"""
监控指标体系

提供可靠性保障机制的指标收集和统计功能，
支持 Prometheus 格式导出和 JSON 报告生成。
"""

import time
import json
import threading
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional


class ReliabilityMetrics:
    """
    可靠性指标收集器。
    
    收集以下指标：
    - 重试次数和成功率
    - 熔断器触发次数
    - 连接丢失次数
    - 错误分类统计
    - 等待策略成功率
    - 操作耗时统计
    """
    
    def __init__(self):
        self._lock = threading.Lock()
        self._start_time = time.time()
        
        # 重试指标
        self.retry_count = 0
        self.retry_success_count = 0
        self.retry_failure_count = 0
        
        # 熔断器指标
        self.circuit_breaker_trips = 0
        self.circuit_breaker_resets = 0
        
        # 连接指标
        self.connection_losses = 0
        self.connection_recovered = 0
        
        # 错误分类统计
        self.error_by_category: Dict[str, int] = defaultdict(int)
        self.error_by_type: Dict[str, int] = defaultdict(int)
        
        # 等待策略统计
        self.wait_strategy_success: Dict[str, int] = defaultdict(int)
        self.wait_strategy_failure: Dict[str, int] = defaultdict(int)
        
        # 操作耗时统计
        self.operation_durations: Dict[str, List[float]] = defaultdict(list)
        
        # 错误日志（最近 100 条）
        self._error_log: List[Dict[str, Any]] = []
        self._max_log_size = 100
    
    def record_retry(self, success: bool, operation: str = "unknown"):
        """记录重试结果"""
        with self._lock:
            self.retry_count += 1
            if success:
                self.retry_success_count += 1
            else:
                self.retry_failure_count += 1
    
    def record_circuit_breaker_trip(self):
        """记录熔断器触发"""
        with self._lock:
            self.circuit_breaker_trips += 1
    
    def record_circuit_breaker_reset(self):
        """记录熔断器重置"""
        with self._lock:
            self.circuit_breaker_resets += 1
    
    def record_connection_loss(self):
        """记录连接丢失"""
        with self._lock:
            self.connection_losses += 1
    
    def record_connection_recovered(self):
        """记录连接恢复"""
        with self._lock:
            self.connection_recovered += 1
    
    def record_error(self, category: str, error_type: str, message: str = ""):
        """记录错误"""
        with self._lock:
            self.error_by_category[category] += 1
            self.error_by_type[error_type] += 1
            
            entry = {
                "timestamp": datetime.fromtimestamp(time.time()).isoformat(),
                "category": category,
                "error_type": error_type,
                "message": message,
            }
            self._error_log.append(entry)
            if len(self._error_log) > self._max_log_size:
                self._error_log.pop(0)
    
    def record_wait_strategy(self, strategy: str, success: bool):
        """记录等待策略结果"""
        with self._lock:
            if success:
                self.wait_strategy_success[strategy] += 1
            else:
                self.wait_strategy_failure[strategy] += 1
    
    def record_operation_duration(self, operation: str, duration: float):
        """记录操作耗时"""
        with self._lock:
            durations = self.operation_durations[operation]
            durations.append(duration)
            # 只保留最近 1000 条记录
            if len(durations) > 1000:
                self.operation_durations[operation] = durations[-1000:]
    
    def get_metrics(self) -> Dict[str, Any]:
        """获取当前指标快照"""
        with self._lock:
            uptime = time.time() - self._start_time
            
            # 计算重试成功率
            retry_success_rate = 0.0
            if self.retry_count > 0:
                retry_success_rate = self.retry_success_count / self.retry_count
            
            # 计算连接恢复率
            connection_recovery_rate = 0.0
            if self.connection_losses > 0:
                connection_recovery_rate = self.connection_recovered / self.connection_losses
            
            # 计算操作耗时统计
            duration_stats = {}
            for op, durations in self.operation_durations.items():
                if durations:
                    duration_stats[op] = {
                        "count": len(durations),
                        "avg": sum(durations) / len(durations),
                        "min": min(durations),
                        "max": max(durations),
                    }
            
            return {
                "uptime_seconds": round(uptime, 1),
                "timestamp": datetime.fromtimestamp(time.time()).isoformat(),
                "retry": {
                    "total": self.retry_count,
                    "success": self.retry_success_count,
                    "failure": self.retry_failure_count,
                    "success_rate": round(retry_success_rate, 4),
                },
                "circuit_breaker": {
                    "trips": self.circuit_breaker_trips,
                    "resets": self.circuit_breaker_resets,
                },
                "connection": {
                    "losses": self.connection_losses,
                    "recovered": self.connection_recovered,
                    "recovery_rate": round(connection_recovery_rate, 4),
                },
                "errors_by_category": dict(self.error_by_category),
                "errors_by_type": dict(self.error_by_type),
                "wait_strategies": {
                    "success": dict(self.wait_strategy_success),
                    "failure": dict(self.wait_strategy_failure),
                },
                "operation_durations": duration_stats,
                "recent_errors": self._error_log[-10:],  # 最近 10 条错误
            }
    
    def to_prometheus_format(self) -> str:
        """转换为 Prometheus 格式"""
        metrics = self.get_metrics()
        lines = []
        
        # 重试指标
        lines.append(f'# HELP browser_cdp_retry_total Total retry attempts')
        lines.append(f'# TYPE browser_cdp_retry_total counter')
        lines.append(f'browser_cdp_retry_total {{operation="all"}} {metrics["retry"]["total"]}')
        lines.append(f'browser_cdp_retry_success_total {{operation="all"}} {metrics["retry"]["success"]}')
        lines.append(f'browser_cdp_retry_failure_total {{operation="all"}} {metrics["retry"]["failure"]}')
        
        # 熔断器指标
        lines.append(f'# HELP browser_cdp_circuit_breaker_trips Total circuit breaker trips')
        lines.append(f'# TYPE browser_cdp_circuit_breaker_trips counter')
        lines.append(f'browser_cdp_circuit_breaker_trips {metrics["circuit_breaker"]["trips"]}')
        
        # 连接指标
        lines.append(f'# HELP browser_cdp_connection_losses Total connection losses')
        lines.append(f'# TYPE browser_cdp_connection_losses counter')
        lines.append(f'browser_cdp_connection_losses {metrics["connection"]["losses"]}')
        
        # 错误分类指标
        for category, count in metrics["errors_by_category"].items():
            lines.append(f'# HELP browser_cdp_errors_by_category Errors by category')
            lines.append(f'# TYPE browser_cdp_errors_by_category counter')
            lines.append(f'browser_cdp_errors_by_category {{category="{category}"}} {count}')
        
        return "\n".join(lines)
    
    def to_json(self, pretty: bool = True) -> str:
        """转换为 JSON 格式"""
        metrics = self.get_metrics()
        if pretty:
            return json.dumps(metrics, indent=2, ensure_ascii=False)
        return json.dumps(metrics, ensure_ascii=False)
    
    def reset(self):
        """重置所有指标"""
        with self._lock:
            self._start_time = time.time()
            self.retry_count = 0
            self.retry_success_count = 0
            self.retry_failure_count = 0
            self.circuit_breaker_trips = 0
            self.circuit_breaker_resets = 0
            self.connection_losses = 0
            self.connection_recovered = 0
            self.error_by_category.clear()
            self.error_by_type.clear()
            self.wait_strategy_success.clear()
            self.wait_strategy_failure.clear()
            self.operation_durations.clear()
            self._error_log.clear()


# 全局指标实例
_global_metrics = ReliabilityMetrics()


def get_metrics() -> ReliabilityMetrics:
    """获取全局指标实例"""
    return _global_metrics


def reset_metrics():
    """重置全局指标"""
    _global_metrics.reset()
