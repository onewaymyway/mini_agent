# -*- coding: utf-8 -*-
"""
数据源注册表

统一管理所有数据源的注册、发现和查询。
"""

import logging
from typing import Dict, List, Optional, Type, Any
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


# ============== 数据源能力描述 ==============

@dataclass
class SourceCapability:
    """数据源能力描述"""
    source: str
    supported_types: Dict[str, bool] = field(default_factory=dict)
    requires_auth: bool = False
    rate_limit: int = 60  # 每分钟最大请求数
    description: str = ""
    
    def is_supported(self, data_type: str) -> bool:
        """检查是否支持指定数据类型"""
        return self.supported_types.get(data_type, False)
    
    def get_supported_types(self) -> List[str]:
        """获取支持的数据类型列表"""
        return [t for t, v in self.supported_types.items() if v]


# ============== 数据源健康状态 ==============

class HealthStatus:
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class SourceHealth:
    """单个数据源的健康状态"""
    
    def __init__(self, source: str):
        self.source = source
        self.status = HealthStatus.UNKNOWN
        self.last_check: Optional[datetime] = None
        self.last_success: Optional[datetime] = None
        self.last_error: Optional[str] = None
        self.consecutive_failures = 0
        self.total_requests = 0
        self.total_failures = 0
    
    def record_success(self):
        self.status = HealthStatus.HEALTHY
        self.last_success = datetime.utcnow()
        self.consecutive_failures = 0
        self.total_requests += 1
    
    def record_failure(self, error: str):
        self.consecutive_failures += 1
        self.total_failures += 1
        self.last_error = error
        self.total_requests += 1
        
        if self.consecutive_failures >= 5:
            self.status = HealthStatus.UNHEALTHY
        elif self.consecutive_failures >= 3:
            self.status = HealthStatus.DEGRADED
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'source': self.source,
            'status': self.status,
            'last_check': self.last_check.isoformat() if self.last_check else None,
            'last_success': self.last_success.isoformat() if self.last_success else None,
            'last_error': self.last_error,
            'consecutive_failures': self.consecutive_failures,
            'total_requests': self.total_requests,
            'total_failures': self.total_failures,
            'success_rate': round(
                (self.total_requests - self.total_failures) / self.total_requests * 100, 2
            ) if self.total_requests > 0 else 0.0,
        }


# ============== 数据源注册表 ==============

class SourceRegistry:
    """数据源注册表"""
    
    def __init__(self):
        self._sources: Dict[str, SourceCapability] = {}
        self._health: Dict[str, SourceHealth] = {}
        self._initialized = False
    
    def register(self, capability: SourceCapability):
        """注册数据源"""
        self._sources[capability.source] = capability
        self._health[capability.source] = SourceHealth(capability.source)
        logger.debug(f"注册数据源: {capability.source}")
    
    def get(self, source: str) -> Optional[SourceCapability]:
        """获取数据源能力"""
        return self._sources.get(source)
    
    def get_health(self, source: str) -> Optional[SourceHealth]:
        """获取数据源健康状态"""
        return self._health.get(source)
    
    def list_sources(self) -> Dict[str, SourceCapability]:
        """列出所有已注册数据源"""
        return dict(self._sources)
    
    def list_health(self) -> Dict[str, SourceHealth]:
        """列出所有数据源健康状态"""
        return dict(self._health)
    
    def get_supported_types(self, source: str) -> List[str]:
        """获取数据源支持的数据类型"""
        cap = self._sources.get(source)
        if not cap:
            return []
        return cap.get_supported_types()
    
    def find_sources(self, data_type: str) -> List[str]:
        """查找支持指定数据类型的所有源"""
        return [
            name for name, cap in self._sources.items()
            if cap.is_supported(data_type)
        ]
    
    def get_priority_sources(self, data_type: str, priority_list: List[str]) -> List[str]:
        """按优先级过滤支持的源"""
        supported = set(self.find_sources(data_type))
        return [s for s in priority_list if s in supported]
    
    def record_success(self, source: str):
        """记录成功请求"""
        if source in self._health:
            self._health[source].record_success()
    
    def record_failure(self, source: str, error: str = ""):
        """记录失败请求"""
        if source in self._health:
            self._health[source].record_failure(error)
    
    def get_recommendations(self) -> Dict[str, str]:
        """根据健康状态返回推荐操作"""
        recs = {}
        for name, health in self._health.items():
            if health.status == HealthStatus.UNHEALTHY:
                recs[name] = f"连续失败 {health.consecutive_failures} 次，建议禁用或切换备用源"
            elif health.status == HealthStatus.DEGRADED:
                recs[name] = f"稳定性下降，建议监控或降低优先级"
        return recs
    
    def get_availability_rate(self) -> float:
        """计算整体可用率"""
        if not self._health:
            return 0.0
        healthy_count = sum(
            1 for h in self._health.values()
            if h.status in (HealthStatus.HEALTHY, HealthStatus.UNKNOWN)
        )
        return round(healthy_count / len(self._health) * 100, 1)
    
    def initialize(self):
        """初始化默认数据源注册"""
        if self._initialized:
            return
        
        # 注册默认数据源
        default_sources = [
            SourceCapability(
                source='akshare',
                supported_types={
                    'quote': True, 'kline': True, 'financial': True,
                    'fund': True, 'bond': True, 'futures': True,
                    'index': True, 'macro': True, 'news': True,
                    'sentiment': True, 'capital_flow': True,
                    'northbound': True, 'margin': True, 'lhb': True,
                    'ipo': True, 'stock_basic': True, 'sector': True,
                    'concept': True,
                },
                requires_auth=False,
                rate_limit=60,
                description='AKShare - 开源财经数据接口'
            ),
            SourceCapability(
                source='eastmoney',
                supported_types={
                    'quote': True, 'kline': True, 'financial': True,
                    'fund': True, 'bond': True, 'futures': True,
                    'index': True, 'macro': True, 'news': True,
                    'sentiment': True, 'capital_flow': True,
                    'northbound': True, 'margin': True, 'lhb': True,
                    'ipo': True, 'stock_basic': True, 'sector': True,
                    'concept': True,
                },
                requires_auth=False,
                rate_limit=30,
                description='东方财富 - 专业财经数据'
            ),
            SourceCapability(
                source='sina',
                supported_types={
                    'quote': True, 'kline': True, 'news': True,
                },
                requires_auth=False,
                rate_limit=60,
                description='新浪财经 - 实时行情和新闻'
            ),
            SourceCapability(
                source='tencent',
                supported_types={
                    'quote': True, 'kline': True,
                },
                requires_auth=False,
                rate_limit=60,
                description='腾讯财经 - 实时行情'
            ),
            SourceCapability(
                source='tushare',
                supported_types={
                    'quote': True, 'kline': True, 'financial': True,
                    'fund': True, 'bond': True, 'futures': True,
                    'index': True, 'macro': True, 'news': True,
                    'sentiment': True, 'capital_flow': True,
                    'northbound': True, 'margin': True, 'lhb': True,
                    'ipo': True, 'stock_basic': True,
                },
                requires_auth=True,
                rate_limit=200,
                description='Tushare - 专业金融数据平台'
            ),
            SourceCapability(
                source='yahoo',
                supported_types={
                    'quote': True, 'kline': True,
                },
                requires_auth=False,
                rate_limit=30,
                description='Yahoo Finance - 美股和国际市场'
            ),
            SourceCapability(
                source='netease',
                supported_types={
                    'quote': True,
                },
                requires_auth=False,
                rate_limit=60,
                description='网易财经 - 实时行情'
            ),
            SourceCapability(
                source='guba',
                supported_types={
                    'sentiment': True,
                },
                requires_auth=False,
                rate_limit=10,
                description='东方财富股吧 - 市场情绪数据'
            ),
            SourceCapability(
                source='xuangubao',
                supported_types={
                    'quote': True,
                },
                requires_auth=False,
                rate_limit=30,
                description='选股宝 - 实时行情'
            ),
            SourceCapability(
                source='cls',
                supported_types={
                    'quote': True, 'news': True,
                },
                requires_auth=False,
                rate_limit=30,
                description='财联社 - 实时行情和新闻'
            ),
        ]
        
        for cap in default_sources:
            self.register(cap)
        
        self._initialized = True
        logger.info(f"已注册 {len(default_sources)} 个数据源")
    
    def get_status_summary(self) -> Dict[str, Any]:
        """获取状态摘要"""
        self.initialize()
        
        total = len(self._sources)
        healthy = sum(
            1 for h in self._health.values()
            if h.status in (HealthStatus.HEALTHY, HealthStatus.UNKNOWN)
        )
        degraded = sum(
            1 for h in self._health.values()
            if h.status == HealthStatus.DEGRADED
        )
        unhealthy = sum(
            1 for h in self._health.values()
            if h.status == HealthStatus.UNHEALTHY
        )
        
        return {
            'total_sources': total,
            'healthy': healthy,
            'degraded': degraded,
            'unhealthy': unhealthy,
            'availability_rate': round(healthy / total * 100, 1) if total > 0 else 0,
            'recommendations': self.get_recommendations(),
        }


# ============== 全局注册表实例 ==============

_registry: Optional[SourceRegistry] = None


def get_registry() -> SourceRegistry:
    """获取全局注册表实例"""
    global _registry
    if _registry is None:
        _registry = SourceRegistry()
        _registry.initialize()
    return _registry


def reset_registry():
    """重置注册表（用于测试）"""
    global _registry
    _registry = None


if __name__ == '__main__':
    # 测试
    reg = get_registry()
    summary = reg.get_status_summary()
    print(f"数据源总数: {summary['total_sources']}")
    print(f"可用率: {summary['availability_rate']}%")
    print(f"健康: {summary['healthy']}, 降级: {summary['degraded']}, 不健康: {summary['unhealthy']}")
    
    # 测试查询
    print(f"\n支持 quote 的源: {reg.find_sources('quote')}")
    print(f"支持 kline 的源: {reg.find_sources('kline')}")
    print(f"akshare 支持: {reg.get_supported_types('akshare')}")
