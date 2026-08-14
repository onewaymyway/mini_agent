# -*- coding: utf-8 -*-
"""
统一 Fetcher 抽象基类与多源路由

设计目标：
1. 所有数据抓取器必须继承 BaseFetcher
2. 实现 get_source_name() 和 get_supported_types()
3. fetch() 方法返回 List[FinanceData]
4. MultiSourceFetcher 支持按优先级自动降级
"""

import json
import logging
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from datetime import datetime

from ..interface import FinanceData
from ..resilience import CircuitBreaker
from ..data_source_config import SOURCE_PRIORITY

logger = logging.getLogger(__name__)


class BaseFetcher(ABC):
    """
    所有数据抓取器的统一抽象基类
    
    实现规范：
    1. 子类必须实现 get_source_name() 和 get_supported_types()
    2. fetch(data_type, symbols, **kwargs) 必须返回 List[FinanceData]
    3. health_check() 返回 True/False
    4. fetch() 内部调用具体的 fetch_{data_type}() 方法
    """
    
    @classmethod
    @abstractmethod
    def get_source_name(cls) -> str:
        """数据源名称标识（小写，如 'akshare', 'coingecko'）"""
        pass
    
    @classmethod
    @abstractmethod
    def get_supported_types(cls) -> List[str]:
        """支持的数据类型列表"""
        pass
    
    @abstractmethod
    def fetch(self, data_type: str, symbols: List[str], **kwargs) -> List[FinanceData]:
        """
        统一入口方法 - 根据 data_type 分派到具体实现
        
        Args:
            data_type: 数据类型（如 'quote', 'kline', 'crypto_quote'）
            symbols: 标的代码列表
            **kwargs: 其他参数
        
        Returns:
            List[FinanceData]
        """
        pass
    
    @abstractmethod
    def health_check(self) -> bool:
        """健康检查"""
        pass
    
    def get_priority(self) -> int:
        """优先级（越小越高），默认根据 SUPPORTED_TYPES 数量计算"""
        return len(self.get_supported_types())
    
    def get_capabilities(self) -> Dict[str, Any]:
        """获取数据源能力描述"""
        return {
            'source': self.get_source_name(),
            'supported_types': self.get_supported_types(),
            'priority': self.get_priority(),
        }


class MultiSourceFetcher(BaseFetcher):
    """
    多数据源聚合 Fetcher - 自动按优先级降级
    
    使用方式：
        router = DataSourceRouter()
        results = router.fetch('crypto_quote', ['BTC', 'ETH'])
    """
    
    def __init__(self, default_source: str = 'akshare', 
                 failure_threshold: int = 5,
                 reset_timeout: float = 60.0):
        self.default_source = default_source
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self._circuit_breakers: Dict[str, CircuitBreaker] = {}
        self._registered_fetchers: Dict[str, BaseFetcher] = {}
        self._initialize_default_circuit_breakers()
    
    def _initialize_default_circuit_breakers(self):
        """为默认股票/基金数据源初始化熔断器"""
        for source in SOURCE_PRIORITY.get('stock_quote', [])[:3]:
            self._circuit_breakers[source] = CircuitBreaker(
                source=source,
                failure_threshold=self.failure_threshold,
                reset_timeout=self.reset_timeout
            )
    
    def register(self, fetcher: BaseFetcher):
        """注册数据源抓取器"""
        name = fetcher.get_source_name()
        self._registered_fetchers[name] = fetcher
        if name not in self._circuit_breakers:
            self._circuit_breakers[name] = CircuitBreaker(
                source=name,
                failure_threshold=self.failure_threshold,
                reset_timeout=self.reset_timeout
            )
        logger.info(
            f"已注册数据源: {name} (支持: {fetcher.get_supported_types()})"
        )
    
    def get_source_name(self) -> str:
        return 'multi_source_router'
    
    def get_supported_types(self) -> List[str]:
        """返回所有注册数据源支持的类型集合"""
        all_types = set()
        for fb in self._registered_fetchers.values():
            all_types.update(fb.get_supported_types())
        return list(all_types)
    
    def fetch(self, data_type: str, symbols: List[str], **kwargs) -> List[FinanceData]:
        """
        统一 fetch 入口 - 自动按优先级降级
        """
        # 1. 检查是否指定了源
        source = kwargs.pop('source', None)
        if source:
            return self._fetch_from_source(source, data_type, symbols, **kwargs)
        
        # 2. 按优先级尝试各数据源
        sources = SOURCE_PRIORITY.get(data_type, [])
        
        # 3. 追加 default_source（如果不在优先级列表中）
        if self.default_source not in sources:
            sources.append(self.default_source)
        
        errors = []
        results = []
        
        for src in sources:
            try:
                fetcher = self._registered_fetchers.get(src)
                if not fetcher:
                    logger.warning(f"[{src}] 未注册，跳过")
                    continue
                
                # 检查熔断器
                cb = self._circuit_breakers.get(src)
                if cb and cb.state == 'open':
                    elapsed = datetime.now().timestamp() - cb.last_failure_time
                    if elapsed > cb.reset_timeout:
                        cb.state = 'half_open'
                        logger.info(f"[{src}] 熔断器半开，尝试恢复")
                    else:
                        remaining = cb.reset_timeout - elapsed
                        logger.warning(f"[{src}] 熔断器开启，跳过 ({remaining:.0f}s后重试)")
                        continue
                
                # 执行抓取
                data = fetcher.fetch(data_type, symbols, **kwargs)
                
                if data:
                    if cb:
                        cb._on_success()
                    results.extend(data)
                    logger.info(
                        f"[{src}] {data_type} 获取成功: {len(data)} 条"
                    )
                    return results
                
            except Exception as e:
                err_info = {
                    'source': src,
                    'error_type': type(e).__name__,
                    'message': str(e)[:200],
                    'timestamp': datetime.now().isoformat(),
                }
                errors.append(err_info)
                logger.warning(f"数据源 {src} 失败: {e}")
                if src in self._circuit_breakers:
                    self._circuit_breakers[src]._on_failure()

        if not results:
            from ..exceptions import FallbackError
            # 构建结构化错误报告
            error_report = {
                'data_type': data_type,
                'symbols': symbols,
                'total_sources': len(sources),
                'failed_sources': len(errors),
                'errors': {e['source']: e['message'] for e in errors},
            }
            # FallbackError 需要 (primary_source, fallback_sources, errors)
            primary_source = sources[0] if sources else 'unknown'
            fallback_sources = [s for s in sources if s != primary_source]
            raise FallbackError(primary_source, fallback_sources, error_report['errors'])
        return results
    
    def _fetch_from_source(
        self,
        source: str,
        data_type: str,
        symbols: List[str],
        **kwargs
    ) -> List[FinanceData]:
        """从指定源获取数据"""
        fetcher = self._registered_fetchers.get(source)
        if not fetcher:
            from ..exceptions import SourceUnavailableError
            raise SourceUnavailableError(
                source=source,
                message=f"数据源 '{source}' 未注册",
                code=404
            )
        
        cb = self._circuit_breakers.get(source)
        if cb and cb.state == 'open':
            raise SourceUnavailableError(
                source=source,
                message=f"熔断器开启，等待恢复",
                code=503
            )
        
        try:
            results = fetcher.fetch(data_type, symbols, **kwargs)
            if cb:
                cb._on_success()
            return results
        except Exception as e:
            if cb:
                cb._on_failure()
            raise
    
    def health_check(self) -> bool:
        """聚合健康检查 - 所有注册源都正常才返回 True"""
        healthy_count = sum(
            1 for name, fb in self._registered_fetchers.items()
            if fb.health_check()
        )
        total = len(self._registered_fetchers)
        logger.info(
            f"多源路由健康检查: {healthy_count}/{total} 正常"
        )
        return total > 0 and healthy_count == total
    
    def get_health_report(self) -> Dict[str, Dict[str, Any]]:
        """获取所有数据源健康报告"""
        report = {}
        for name, cb in self._circuit_breakers.items():
            report[name] = {
                'state': cb.state,
                'failure_count': cb.failure_count,
                'last_failure': datetime.fromtimestamp(
                    cb.last_failure_time
                ).isoformat() if cb.last_failure_time else None,
            }
        return report
    
    def reset_circuit_breaker(self, source: str):
        """手动重置指定数据源的熔断器"""
        if source in self._circuit_breakers:
            self._circuit_breakers[source].reset()
            logger.info(f"熔断器 [{source}] 已手动重置")


# ============== 便捷函数 ==============

def get_fetcher(source: str) -> Optional[BaseFetcher]:
    """根据名称获取已注册的抓取器（通过全局单例）"""
    from . import _global_router
    return _global_router._registered_fetchers.get(source)


def create_default_router() -> MultiSourceFetcher:
    """创建默认路由实例（一次性调用）"""
    from .crypto_fetcher_v2 import CryptoFetcher
    from .bond_fetcher import BondDataFetcher
    from .fund_fetcher import FundDataFetcher
    from .forex_fetcher import ForexFetcher
    from .futures_fetcher import FuturesDataFetcher
    from .tushare_bond_fetcher import TushareBondFetcher

    router = MultiSourceFetcher(default_source='akshare')
    # 注：stock_fetcher.py 和 eastmoney_fetcher.py 中无 StockDataFetcher / EastMoneyDataFetcher，已跳过
    try:
        router.register(CryptoFetcher())
    except Exception as e:
        logger.warning(f"CryptoFetcher 注册失败: {e}")
    try:
        router.register(BondDataFetcher())
    except Exception as e:
        logger.warning(f"BondDataFetcher 注册失败: {e}")
    try:
        router.register(FundDataFetcher())
    except Exception as e:
        logger.warning(f"FundDataFetcher 注册失败: {e}")
    try:
        router.register(ForexFetcher())
    except Exception as e:
        logger.warning(f"ForexFetcher 注册失败: {e}")
    try:
        router.register(FuturesDataFetcher())
    except Exception as e:
        logger.warning(f"FuturesDataFetcher 注册失败: {e}")
    try:
        router.register(TushareBondFetcher())
    except Exception as e:
        logger.warning(f"TushareBondFetcher 注册失败: {e}")
    return router


# 全局单例路由（懒加载，避免循环导入）
_global_router = None


def get_global_router():
    """获取全局路由器（懒加载）"""
    global _global_router
    if _global_router is None:
        try:
            _global_router = create_default_router()
            logger.info("全局路由器已初始化")
        except Exception as e:
            logger.error(f"全局路由器初始化失败: {e}")
            raise
    return _global_router


# 兼容直接访问（通过懒加载函数获取，避免循环导入）
def get_fetcher(source: str) -> Optional[BaseFetcher]:
    """根据名称获取已注册的抓取器"""
    try:
        router = get_global_router()
        return router._registered_fetchers.get(source)
    except Exception as e:
        logger.warning(f"获取抓取器 {source} 失败: {e}")
        return None
