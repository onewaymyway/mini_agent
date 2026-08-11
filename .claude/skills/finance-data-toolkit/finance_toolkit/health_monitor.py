# -*- coding: utf-8 -*-
"""
Finance Data Toolkit - 数据源健康监控模块

提供数据源健康状态监控、告警和自动恢复功能。

使用示例：
    from finance_toolkit.health_monitor import HealthMonitor
    
    monitor = HealthMonitor()
    monitor.start()
    
    # 检查数据源状态
    status = monitor.get_status("akshare")
    print(f"健康状态: {status['healthy']}")
    print(f"连续失败次数: {status['consecutive_failures']}")
    
    # 获取所有数据源状态
    all_status = monitor.get_all_status()
"""

import asyncio
import logging
import time
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, field
from enum import Enum

from .exceptions import SourceHealthError, SourceUnavailableError

logger = logging.getLogger(__name__)


class HealthStatus(Enum):
    """健康状态枚举"""
    HEALTHY = "healthy"           # 健康
    DEGRADED = "degraded"         # 降级
    UNHEALTHY = "unhealthy"       # 不健康
    UNKNOWN = "unknown"           # 未知


@dataclass
class SourceHealthMetrics:
    """数据源健康指标"""
    # 基础指标
    healthy: bool = True
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    
    # 性能指标
    avg_latency_ms: float = 0.0
    min_latency_ms: float = float('inf')
    max_latency_ms: float = 0.0
    latency_samples: int = 0
    
    # 成功率指标
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    success_rate: float = 100.0
    
    # 时间指标
    last_check_time: float = 0.0
    last_success_time: float = 0.0
    last_failure_time: float = 0.0
    
    # 错误信息
    last_error: str = ""
    error_history: List[str] = field(default_factory=list)
    
    def update_latency(self, latency_ms: float):
        """更新延迟指标"""
        self.latency_samples += 1
        self.min_latency_ms = min(self.min_latency_ms, latency_ms)
        self.max_latency_ms = max(self.max_latency_ms, latency_ms)
        self.avg_latency_ms = (
            self.avg_latency_ms * (self.latency_samples - 1) + latency_ms
        ) / self.latency_samples
    
    def record_success(self):
        """记录成功请求"""
        self.total_requests += 1
        self.successful_requests += 1
        self.consecutive_failures = 0
        self.consecutive_successes += 1
        self.last_success_time = time.time()
        self.success_rate = (
            self.successful_requests / self.total_requests * 100
            if self.total_requests > 0 else 100.0
        )
    
    def record_failure(self, error: str = ""):
        """记录失败请求"""
        self.total_requests += 1
        self.failed_requests += 1
        self.consecutive_successes = 0
        self.consecutive_failures += 1
        self.last_failure_time = time.time()
        self.last_error = error
        self.error_history.append(error)
        if len(self.error_history) > 10:
            self.error_history.pop(0)
        self.success_rate = (
            self.successful_requests / self.total_requests * 100
            if self.total_requests > 0 else 0.0
        )
    
    def get_health_status(self) -> HealthStatus:
        """获取健康状态"""
        if not self.healthy:
            return HealthStatus.UNHEALTHY
        
        if self.consecutive_failures >= 3:
            return HealthStatus.DEGRADED
        
        if self.success_rate < 90:
            return HealthStatus.DEGRADED
        
        if self.avg_latency_ms > 5000:  # 5秒
            return HealthStatus.DEGRADED
        
        return HealthStatus.HEALTHY


class HealthMonitor:
    """
    数据源健康监控器
    
    监控多个数据源的健康状态，提供告警和自动恢复功能。
    """
    
    def __init__(
        self,
        check_interval: int = 60,
        degradation_threshold: int = 3,
        unhealthy_threshold: int = 5,
        recovery_timeout: int = 300,
        alert_callbacks: Optional[List[Callable]] = None
    ):
        self.check_interval = check_interval
        self.degradation_threshold = degradation_threshold
        self.unhealthy_threshold = unhealthy_threshold
        self.recovery_timeout = recovery_timeout
        self.alert_callbacks = alert_callbacks or []
        
        # 健康指标存储
        self._metrics: Dict[str, SourceHealthMetrics] = {}
        self._check_funcs: Dict[str, Callable] = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None
    
    def register_source(
        self,
        source_name: str,
        check_func: Callable,
        initial_metrics: Optional[SourceHealthMetrics] = None
    ):
        """
        注册数据源
        
        参数：
            source_name: 数据源名称
            check_func: 健康检查函数（可异步）
            initial_metrics: 初始健康指标
        """
        self._check_funcs[source_name] = check_func
        if source_name not in self._metrics:
            self._metrics[source_name] = initial_metrics or SourceHealthMetrics()
        logger.info(f"注册数据源健康监控：{source_name}")
    
    def unregister_source(self, source_name: str):
        """注销数据源"""
        self._check_funcs.pop(source_name, None)
        self._metrics.pop(source_name, None)
        logger.info(f"注销数据源健康监控：{source_name}")
    
    async def check_source(self, source_name: str) -> SourceHealthMetrics:
        """
        检查单个数据源健康状态
        
        参数：
            source_name: 数据源名称
        
        返回：
            健康指标
        """
        if source_name not in self._check_funcs:
            raise ValueError(f"未注册数据源：{source_name}")
        
        metrics = self._metrics[source_name]
        start_time = time.time()
        
        try:
            check_func = self._check_funcs[source_name]
            if asyncio.iscoroutinefunction(check_func):
                await check_func()
            else:
                check_func()
            
            elapsed_ms = (time.time() - start_time) * 1000
            metrics.update_latency(elapsed_ms)
            metrics.record_success()
            metrics.healthy = True
            
            logger.debug(f"数据源 {source_name} 健康检查通过（{elapsed_ms:.1f}ms）")
            
            # 检查是否需要恢复告警
            if metrics.consecutive_successes == 1 and metrics.consecutive_failures > 0:
                await self._trigger_alert(source_name, "recovered")
            
        except Exception as e:
            elapsed_ms = (time.time() - start_time) * 1000
            metrics.update_latency(elapsed_ms)
            metrics.record_failure(str(e))
            metrics.healthy = False
            
            logger.warning(f"数据源 {source_name} 健康检查失败：{e}")
            
            # 检查是否需要告警
            if metrics.consecutive_failures == 1:
                await self._trigger_alert(source_name, "degraded")
            elif metrics.consecutive_failures >= self.unhealthy_threshold:
                await self._trigger_alert(source_name, "unhealthy")
        
        metrics.last_check_time = time.time()
        return metrics
    
    async def check_all(self) -> Dict[str, SourceHealthMetrics]:
        """检查所有数据源健康状态"""
        results = {}
        for source_name in self._check_funcs:
            results[source_name] = await self.check_source(source_name)
        return results
    
    def get_status(self, source_name: str) -> Optional[SourceHealthMetrics]:
        """获取数据源状态"""
        return self._metrics.get(source_name)
    
    def get_all_status(self) -> Dict[str, SourceHealthMetrics]:
        """获取所有数据源状态"""
        return dict(self._metrics)
    
    def is_healthy(self, source_name: str) -> bool:
        """检查数据源是否健康"""
        metrics = self._metrics.get(source_name)
        return metrics and metrics.healthy
    
    def get_unhealthy_sources(self) -> List[str]:
        """获取不健康的数据源列表"""
        return [
            name for name, metrics in self._metrics.items()
            if not metrics.healthy
        ]
    
    def get_degraded_sources(self) -> List[str]:
        """获取降级状态的数据源列表"""
        return [
            name for name, metrics in self._metrics.items()
            if metrics.get_health_status() == HealthStatus.DEGRADED
        ]
    
    async def start(self):
        """启动健康监控"""
        if self._running:
            return
        
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info(f"健康监控已启动，检查间隔 {self.check_interval} 秒")
    
    async def _monitor_loop(self):
        """监控循环"""
        while self._running:
            try:
                await self.check_all()
            except Exception as e:
                logger.error(f"健康检查循环出错：{e}")
            await asyncio.sleep(self.check_interval)
    
    def stop(self):
        """停止健康监控"""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("健康监控已停止")
    
    async def _trigger_alert(
        self,
        source_name: str,
        alert_type: str,
        metrics: Optional[SourceHealthMetrics] = None
    ):
        """触发告警"""
        alert_data = {
            "source": source_name,
            "type": alert_type,
            "timestamp": time.time(),
            "metrics": metrics or self._metrics.get(source_name)
        }
        
        logger.warning(f"数据源告警：{source_name} - {alert_type}")
        
        for callback in self.alert_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(alert_data)
                else:
                    callback(alert_data)
            except Exception as e:
                logger.error(f"告警回调执行失败：{e}")
    
    def add_alert_callback(self, callback: Callable):
        """添加告警回调"""
        self.alert_callbacks.append(callback)
    
    def remove_alert_callback(self, callback: Callable):
        """移除告警回调"""
        if callback in self.alert_callbacks:
            self.alert_callbacks.remove(callback)
    
    def get_summary(self) -> Dict[str, Any]:
        """获取健康监控摘要"""
        return {
            "total_sources": len(self._metrics),
            "healthy_sources": sum(
                1 for m in self._metrics.values() if m.healthy
            ),
            "unhealthy_sources": self.get_unhealthy_sources(),
            "degraded_sources": self.get_degraded_sources(),
            "check_interval_seconds": self.check_interval,
            "is_running": self._running
        }


class AlertHandler:
    """
    告警处理器
    
    提供日志告警、邮件告警等能力。
    """
    
    def __init__(self, log_level: int = logging.WARNING):
        self.log_level = log_level
        self._email_enabled = False
        self._webhook_url: Optional[str] = None
    
    def log_alert(self, alert_data: Dict[str, Any]):
        """记录告警日志"""
        source = alert_data.get("source", "unknown")
        alert_type = alert_data.get("type", "unknown")
        metrics = alert_data.get("metrics")
        
        if alert_type == "recovered":
            logger.info(f"[告警恢复] 数据源 {source} 已恢复健康")
        elif alert_type == "degraded":
            logger.warning(f"[告警] 数据源 {source} 状态降级")
            if metrics:
                logger.warning(f"  - 连续失败: {metrics.consecutive_failures}")
                logger.warning(f"  - 成功率: {metrics.success_rate:.1f}%")
        elif alert_type == "unhealthy":
            logger.error(f"[告警] 数据源 {source} 状态不健康")
            if metrics:
                logger.error(f"  - 连续失败: {metrics.consecutive_failures}")
                logger.error(f"  - 成功率: {metrics.success_rate:.1f}%")
                logger.error(f"  - 最后错误: {metrics.last_error}")
    
    def enable_email(self, smtp_config: Dict[str, str]):
        """启用邮件告警"""
        self._email_enabled = True
        self._smtp_config = smtp_config
        logger.info("邮件告警已启用")
    
    def enable_webhook(self, url: str):
        """启用 Webhook 告警"""
        self._webhook_url = url
        logger.info(f"Webhook 告警已启用：{url}")
    
    async def send_email_alert(self, alert_data: Dict[str, Any]):
        """发送邮件告警"""
        if not self._email_enabled:
            return
        
        # TODO: 实现邮件发送逻辑
        pass
    
    async def send_webhook_alert(self, alert_data: Dict[str, Any]):
        """发送 Webhook 告警"""
        if not self._webhook_url:
            return
        
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                await session.post(
                    self._webhook_url,
                    json=alert_data,
                    timeout=aiohttp.ClientTimeout(total=10)
                )
        except Exception as e:
            logger.error(f"Webhook 告警发送失败：{e}")


# 便捷函数
def create_default_monitor(
    check_interval: int = 60,
    sources: Optional[List[str]] = None
) -> HealthMonitor:
    """
    创建默认健康监控器
    
    参数：
        check_interval: 检查间隔（秒）
        sources: 数据源列表
    
    返回：
        HealthMonitor 实例
    """
    monitor = HealthMonitor(check_interval=check_interval)
    
    # 添加默认告警处理器
    alert_handler = AlertHandler()
    monitor.add_alert_callback(alert_handler.log_alert)
    
    # 注册默认数据源
    default_sources = sources or ["akshare", "eastmoney", "sina"]
    for source in default_sources:
        monitor.register_source(
            source,
            check_func=lambda s=source: _default_health_check(s)
        )
    
    return monitor


def _default_health_check(source: str):
    """默认健康检查函数"""
    # 简单的连通性检查
    import urllib.request
    urls = {
        "akshare": "https://www.akshare.org/",
        "eastmoney": "https://quote.eastmoney.com/",
        "sina": "https://finance.sina.com.cn/"
    }
    url = urls.get(source, "https://www.baidu.com/")
    try:
        urllib.request.urlopen(url, timeout=5)
    except Exception as e:
        raise SourceUnavailableError(source, f"连通性检查失败：{e}")
