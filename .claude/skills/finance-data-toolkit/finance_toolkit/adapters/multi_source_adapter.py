# -*- coding: utf-8 -*-
"""
多源数据适配器 - 步骤 3/9 核心模块

功能：
1. 包装现有 fetcher（腾讯/新浪/东财）为统一接口
2. 实现优先级降级路由
3. 支持熔断器和缓存

文件：finance_toolkit/adapters/multi_source_adapter.py
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional
from enum import Enum

logger = logging.getLogger(__name__)


class SourcePriority(int, Enum):
    """数据源优先级（数字越小优先级越高）"""
    TENCENT = 1      # 主数据源
    SINA = 2         # 备用
    EASTMONEY = 3    # 第三备选


class MultiSourceAdapter:
    """
    多源数据适配器
    
    按优先级依次尝试各数据源，失败时自动降级。
    """

    def __init__(self, priority: List[str] = None):
        """
        初始化适配器
        
        Args:
            priority: 数据源优先级列表，默认 [tencent, sina, eastmoney]
        """
        self._priority = priority or ['tencent', 'sina', 'eastmoney']
        self._sources: Dict[str, Any] = {}
        self._results_cache: Dict[str, Any] = {}
        self._circuit_breaker: Dict[str, int] = {}
        self._max_failures = 5  # 熔断阈值

    def register_source(self, name: str, fetcher_func, **kwargs):
        """注册数据源"""
        self._sources[name] = {
            'fetcher': fetcher_func,
            'kwargs': kwargs,
            'priority': len(self._sources),
        }
        logger.info(f"注册数据源: {name}")

    async def fetch_with_fallback(
        self,
        query: str,
        data_type: str = 'quote',
        timeout: float = 10.0,
        use_cache: bool = True
    ) -> Dict[str, Any]:
        """
        多源降级抓取
        
        Args:
            query: 查询参数（股票代码等）
            data_type: 数据类型
            timeout: 超时时间
            use_cache: 是否使用缓存
        
        Returns:
            {'success': bool, 'data': list, 'source': str, 'cache_hit': bool}
        """
        cache_key = f"{data_type}:{query}"

        # 检查缓存
        if use_cache and cache_key in self._results_cache:
            cached = self._results_cache[cache_key]
            if cached['ttl'] > asyncio.get_event_loop().time():
                logger.debug(f"缓存命中: {cache_key}")
                return {'success': True, 'data': cached['data'], 'source': 'cache', 'cache_hit': True}

        # 按优先级尝试各源
        for source_name in self._priority:
            if source_name not in self._sources:
                continue

            # 检查熔断器
            if self._circuit_breaker.get(source_name, 0) >= self._max_failures:
                logger.warning(f"{source_name} 已熔断，跳过")
                continue

            try:
                source = self._sources[source_name]
                result = await asyncio.wait_for(
                    source['fetcher'](query, data_type, **source['kwargs']),
                    timeout=timeout
                )

                if result and len(result) > 0:
                    logger.info(f"{source_name} 成功获取 {len(result)} 条数据")
                    # 写入缓存（TTL 5分钟）
                    if use_cache:
                        self._results_cache[cache_key] = {
                            'data': result,
                            'ttl': asyncio.get_event_loop().time() + 300
                        }
                    # 重置熔断计数
                    self._circuit_breaker[source_name] = 0
                    return {'success': True, 'data': result, 'source': source_name, 'cache_hit': False}
                else:
                    logger.warning(f"{source_name} 返回空数据")

            except asyncio.TimeoutError:
                logger.warning(f"{source_name} 请求超时")
                self._circuit_breaker[source_name] = self._circuit_breaker.get(source_name, 0) + 1
            except Exception as e:
                logger.warning(f"{source_name} 异常: {e}")
                self._circuit_breaker[source_name] = self._circuit_breaker.get(source_name, 0) + 1

        logger.error("所有数据源均失败")
        return {'success': False, 'data': [], 'source': 'none', 'cache_hit': False}

    def get_stats(self) -> Dict:
        """获取适配器统计信息"""
        return {
            'registered_sources': list(self._sources.keys()),
            'circuit_breaker_status': self._circuit_breaker.copy(),
            'cache_size': len(self._results_cache),
        }

    def clear_cache(self):
        """清除缓存"""
        self._results_cache.clear()
        logger.info("缓存已清除")

    def reset_circuit_breaker(self, source_name: str = None):
        """重置熔断器"""
        if source_name:
            self._circuit_breaker.pop(source_name, None)
        else:
            self._circuit_breaker.clear()
        logger.info(f"熔断器已{'重置' if not source_name else f'{source_name}重置'}")


# 预定义适配器工厂函数
def create_standard_adapter() -> MultiSourceAdapter:
    """创建标准多源适配器（已集成真实fetcher）"""
    from .async_fetcher_wrappers import create_async_wrappers
    
    adapter = MultiSourceAdapter()
    wrappers = create_async_wrappers()
    
    # 注册腾讯财经（主数据源）
    adapter.register_source('tencent', wrappers['tencent']['quote'])
    
    # 注册新浪财经（备用）
    adapter.register_source('sina', wrappers['sina']['quote'])
    
    # 注册东方财富（第三备选）
    adapter.register_source('eastmoney', wrappers['eastmoney']['quote'])
    
    logger.info("标准多源适配器已初始化，优先级: tencent > sina > eastmoney")
    return adapter


if __name__ == '__main__':
    # 测试示例
    import asyncio

    async def test():
        adapter = create_standard_adapter()
        stats = adapter.get_stats()
        print(f"适配器状态: {stats}")

    asyncio.run(test())