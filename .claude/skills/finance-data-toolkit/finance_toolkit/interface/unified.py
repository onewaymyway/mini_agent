# -*- coding: utf-8 -*-
"""
统一数据源抽象接口实现

提供:
1. BaseFetcher 协议定义
2. MultiSourceFetcher 多源路由
3. 全局路由单例
4. 便捷调用函数

使用示例:
    from finance_toolkit.interface.unified import fetch_by_type
    
    # 获取加密货币行情
    result = fetch_by_type('crypto_quote', ['BTC', 'ETH'])
    
    # 获取债券收益率
    result = fetch_by_type('bond_yield', [])
"""

import logging
from typing import List, Dict, Any, Optional, TypeVar

logger = logging.getLogger(__name__)

# 延迟导入，避免循环依赖
_FetcherT = TypeVar('_FetcherT')


class BaseFetcher:
    """
    所有数据抓取器的统一抽象基类
    
    子类必须实现:
    - get_source_name() -> str
    - get_supported_types() -> List[str]
    - fetch(data_type, symbols, **kwargs) -> List[FinanceData]
    - health_check() -> bool
    """
    
    @classmethod
    def get_source_name(cls) -> str:
        """数据源名称标识（小写）"""
        raise NotImplementedError
    
    @classmethod
    def get_supported_types(cls) -> List[str]:
        """支持的数据类型列表"""
        raise NotImplementedError
    
    def fetch(self, data_type: str, symbols: List[str], **kwargs) -> List[Any]:
        """
        统一入口方法
        
        Args:
            data_type: 数据类型
            symbols: 标的代码列表
            **kwargs: 其他参数
        
        Returns:
            List[FinanceData]
        """
        raise NotImplementedError
    
    def health_check(self) -> bool:
        """健康检查"""
        raise NotImplementedError
    
    def get_priority(self) -> int:
        """优先级（越小越高），默认根据支持类型数量计算"""
        return len(self.get_supported_types())
    
    def get_capabilities(self) -> Dict[str, Any]:
        """获取数据源能力描述"""
        return {
            'source': self.get_source_name(),
            'supported_types': self.get_supported_types(),
            'priority': self.get_priority(),
        }


class CircuitBreaker:
    """熔断器实现"""
    
    def __init__(self, source: str, failure_threshold: int = 5, reset_timeout: float = 60.0):
        self.source = source
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.failure_count = 0
        self.last_failure_time: Optional[float] = None
        self.state = 'closed'  # closed | open | half_open
    
    def _on_failure(self):
        """记录失败"""
        self.failure_count += 1
        self.last_failure_time = __import__('datetime').datetime.now().timestamp()
        if self.failure_count >= self.failure_threshold:
            self.state = 'open'
            logger.warning(f"[{self.source}] 熔断器打开 (失败{self.failure_count}次)")
    
    def _on_success(self):
        """记录成功"""
        self.failure_count = max(0, self.failure_count - 1)
        if self.state == 'half_open':
            self.state = 'closed'
            logger.info(f"[{self.source}] 熔断器关闭 (恢复)")
    
    def should_request(self) -> bool:
        """检查是否允许请求"""
        if self.state == 'closed':
            return True
        
        if self.state == 'open' and self.last_failure_time:
            elapsed = __import__('datetime').datetime.now().timestamp() - self.last_failure_time
            if elapsed > self.reset_timeout:
                self.state = 'half_open'
                logger.info(f"[{self.source}] 熔断器半开，尝试恢复")
                return True
        
        return False
    
    def reset(self):
        """手动重置"""
        self.failure_count = 0
        self.state = 'closed'
        self.last_failure_time = None


class MultiSourceFetcher(BaseFetcher):
    """
    多数据源聚合 Fetcher - 自动按优先级降级
    
    降级逻辑:
    1. 检查指定数据源是否存在且未熔断
    2. 按 SOURCE_PRIORITY 配置遍历数据源
    3. 首次成功即返回
    4. 全部失败则抛出 FallbackError
    """
    
    # 数据源优先级配置
    SOURCE_PRIORITY: Dict[str, List[str]] = {
        'stock_quote': ['akshare', 'tencent', 'sina', 'eastmoney', 'netease'],
        'stock_kline': ['akshare', 'sina'],
        'stock_financial': ['akshare', 'tushare'],
        'bond_yield': ['akshare', 'eastmoney'],
        'bond_convertible': ['akshare', 'eastmoney'],
        'fund_etf_quote': ['akshare', 'eastmoney'],
        'fund_open_nav': ['akshare'],
        'crypto_quote': ['akshare', 'coingecko', 'binance'],
        'crypto_kline': ['akshare', 'binance', 'coingecko'],
        'crypto_rank': ['coingecko', 'binance', 'akshare'],
        'forex_quote': ['akshare', 'sina'],
        'forex_cny': ['akshare'],
        'future_quote': ['akshare', 'eastmoney'],
        'future_kline': ['akshare'],
        'macro_gdp': ['akshare', 'eastmoney'],
        'macro_cpi': ['akshare'],
    }
    
    def __init__(self, default_source: str = 'akshare', 
                 failure_threshold: int = 5,
                 reset_timeout: float = 60.0):
        self.default_source = default_source
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self._circuit_breakers: Dict[str, CircuitBreaker] = {}
        self._registered_fetchers: Dict[str, BaseFetcher] = {}
    
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
    
    @classmethod
    def get_source_name(cls) -> str:
        return 'multi_source_router'
    
    @classmethod
    def get_supported_types(cls) -> List[str]:
        all_types = set()
        for fb in cls._registered_fetchers.values():
            all_types.update(fb.get_supported_types())
        return list(all_types)
    
    def fetch(self, data_type: str, symbols: List[str], **kwargs) -> List[Any]:
        """
        统一 fetch 入口 - 自动按优先级降级
        """
        # 1. 检查是否指定了源
        source = kwargs.pop('source', None)
        if source:
            return self._fetch_from_source(source, data_type, symbols, **kwargs)
        
        # 2. 按优先级尝试各数据源
        priority_list = self.SOURCE_PRIORITY.get(data_type, [])
        
        # 3. 追加 default_source
        if self.default_source not in priority_list:
            priority_list.append(self.default_source)
        
        errors = []
        results = []
        
        for src in priority_list:
            try:
                fetcher = self._registered_fetchers.get(src)
                if not fetcher:
                    logger.debug(f"[{src}] 未注册，跳过")
                    continue
                
                # 检查熔断器
                cb = self._circuit_breakers.get(src)
                if cb and not cb.should_request():
                    logger.warning(f"[{src}] 熔断器开启，跳过")
                    continue
                
                # 执行抓取
                data = fetcher.fetch(data_type, symbols, **kwargs)
                
                if data:
                    if cb:
                        cb._on_success()
                    results.extend(data)
                    logger.info(f"[{src}] {data_type} 获取成功: {len(data)} 条")
                    return results
                
            except Exception as e:
                err_msg = f"[{src}] {type(e).__name__}: {e}"
                errors.append(err_msg)
                logger.warning(f"数据源 {src} 失败: {e}")
                if src in self._circuit_breakers:
                    self._circuit_breakers[src]._on_failure()
        
        if not results:
            raise FallbackError(
                f"所有数据源均失败 [{data_type}]: {'; '.join(errors[:3])}"
            )
        return results
    
    def _fetch_from_source(
        self,
        source: str,
        data_type: str,
        symbols: List[str],
        **kwargs
    ) -> List[Any]:
        """从指定源获取数据"""
        fetcher = self._registered_fetchers.get(source)
        if not fetcher:
            raise SourceUnavailableError(
                source=source,
                message=f"数据源 '{source}' 未注册",
            )
        
        cb = self._circuit_breakers.get(source)
        if cb and not cb.should_request():
            raise SourceUnavailableError(
                source=source,
                message="熔断器开启，等待恢复",
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
        """聚合健康检查"""
        if not self._registered_fetchers:
            return False
        healthy_count = sum(
            1 for fb in self._registered_fetchers.values()
            if fb.health_check()
        )
        logger.info(f"多源路由健康检查: {healthy_count}/{len(self._registered_fetchers)} 正常")
        return healthy_count > 0
    
    def get_health_report(self) -> Dict[str, Dict[str, Any]]:
        """获取所有数据源健康报告"""
        report = {}
        for name, cb in self._circuit_breakers.items():
            report[name] = {
                'state': cb.state,
                'failure_count': cb.failure_count,
            }
        return report
    
    def reset_circuit_breaker(self, source: str):
        """手动重置指定数据源的熔断器"""
        if source in self._circuit_breakers:
            self._circuit_breakers[source].reset()
            logger.info(f"熔断器 [{source}] 已手动重置")


class FallbackError(Exception):
    """所有数据源均失败"""
    pass


class SourceUnavailableError(Exception):
    """数据源不可用"""
    pass


def create_default_router() -> MultiSourceFetcher:
    """创建默认路由实例（一次性调用）"""
    router = MultiSourceFetcher(default_source='akshare')
    
    # 使用适配器注册所有数据源
    try:
        from .adapters import register_all_adapters
        register_all_adapters(router)
    except ImportError as e:
        logger.warning(f"适配器注册失败: {e}")
    
    return router


# 全局单例路由
_global_router: Optional[MultiSourceFetcher] = None


def get_router() -> MultiSourceFetcher:
    """获取全局路由单例（懒加载）"""
    global _global_router
    if _global_router is None:
        _global_router = create_default_router()
    return _global_router


def fetch_by_type(data_type: str, symbols: List[str], **kwargs) -> List[Any]:
    """
    便捷函数：按数据类型获取数据（自动降级）
    
    Args:
        data_type: 数据类型
        symbols: 标的代码列表
        **kwargs: 其他参数
    
    Returns:
        List[FinanceData]
    
    Examples:
        >>> fetch_by_type('crypto_quote', ['BTC', 'ETH'])
        >>> fetch_by_type('bond_yield', [])
        >>> fetch_by_type('fund_open_nav', ['000001'])
    """
    return get_router().fetch(data_type, symbols, **kwargs)


def fetch_crypto_quote(symbols: List[str] = None, **kwargs) -> List[Any]:
    """获取加密货币行情"""
    return fetch_by_type('crypto_quote', symbols or [])


def fetch_bond_yield(**kwargs) -> List[Any]:
    """获取债券收益率"""
    return fetch_by_type('bond_yield', [])


def fetch_fund_nav(symbols: List[str] = None, **kwargs) -> List[Any]:
    """获取基金净值"""
    return fetch_by_type('fund_open_nav', symbols or [])


def fetch_forex_quote(symbols: List[str] = None, **kwargs) -> List[Any]:
    """获取外汇行情"""
    return fetch_by_type('forex_quote', symbols or [])


def fetch_futures_quote(symbols: List[str] = None, **kwargs) -> List[Any]:
    """获取期货行情"""
    return fetch_by_type('future_quote', symbols or [])


def get_source_status() -> Dict[str, Dict[str, Any]]:
    """获取所有数据源状态"""
    return get_router().get_health_report()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    
    print("=== 统一数据源抽象接口测试 ===\n")
    
    # 测试路由
    router = get_router()
    print(f"已注册数据源: {list(router._registered_fetchers.keys())}\n")
    
    # 测试加密货币行情
    print("测试 crypto_quote:")
    try:
        result = fetch_crypto_quote(['BTC', 'ETH'])
        print(f"  获取 {len(result)} 条结果")
        if result:
            print(f"  示例: {result[0]}")
    except Exception as e:
        print(f"  失败: {e}")
    
    # 测试债券收益率
    print("\n测试 bond_yield:")
    try:
        result = fetch_bond_yield()
        print(f"  获取 {len(result)} 条结果")
    except Exception as e:
        print(f"  失败: {e}")
    
    # 打印数据源状态
    print("\n数据源状态:")
    status = get_source_status()
    for source, info in status.items():
        print(f"  {source}: {info}")
