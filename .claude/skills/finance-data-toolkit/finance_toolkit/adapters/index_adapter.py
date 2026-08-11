# -*- coding: utf-8 -*-
"""
指数数据适配器

基于 IndexScraper 实现 Adapter 接口，支持：
- 指数行情 (index_quote)
- 指数成分股 (index_constituents)
- 指数历史数据 (index_history)
- 指数估值 (index_valuation)
"""

import logging
from datetime import datetime
from typing import List, Optional, AsyncIterator, Any

from ..adapter import Adapter
from ..scrapers.index_scraper import IndexScraper

logger = logging.getLogger(__name__)


class IndexAdapter(Adapter):
    """
    指数数据适配器
    
    封装 IndexScraper，提供统一的 Adapter 接口。
    """
    
    def __init__(self):
        self._scraper = IndexScraper()
        self._initialized = False
        self._request_count = 0
        self._error_count = 0
    
    @property
    def source_name(self) -> str:
        return 'index'
    
    @property
    def supported_types(self) -> List[str]:
        return ['index_quote', 'index_constituents', 'index_history', 'index_valuation']
    
    @property
    def requires_auth(self) -> bool:
        return False
    
    @property
    def rate_limit(self) -> int:
        return 30
    
    @property
    def description(self) -> str:
        return 'A股指数数据适配器（东方财富）'
    
    async def initialize(self):
        """初始化适配器"""
        self._initialized = True
        logger.info(f"指数适配器初始化完成: {self.source_name}")
    
    async def fetch(
        self,
        symbols: List[str],
        data_type: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        **kwargs
    ) -> AsyncIterator[Any]:
        """
        异步获取指数数据
        
        Args:
            symbols: 指数代码列表
            data_type: 数据类型
            start: 开始时间
            end: 结束时间
            **kwargs: 其他参数
        
        Yields:
            FinanceData 对象
        """
        if data_type not in self.supported_types:
            from ..core import FinanceData
            yield FinanceData(
                source=self.source_name,
                data_type=data_type,
                symbol='*',
                timestamp=datetime.utcnow().isoformat(),
                payload={'error': f'不支持的数据类型: {data_type}'}
            )
            return
        
        try:
            async for data in self._scraper.fetch(symbols, data_type, start, end, **kwargs):
                self._request_count += 1
                yield data
        except Exception as e:
            self._error_count += 1
            logger.error(f"指数数据获取失败: {e}")
            from ..core import FinanceData
            yield FinanceData(
                source=self.source_name,
                data_type=data_type,
                symbol=symbols[0] if symbols else '*',
                timestamp=datetime.utcnow().isoformat(),
                payload={'error': str(e)}
            )
    
    async def health_check(self) -> bool:
        """
        健康检查
        
        Returns:
            True 表示健康
        """
        try:
            return await self._scraper.health_check()
        except Exception as e:
            logger.warning(f"指数适配器健康检查失败: {e}")
            return False
    
    async def close(self):
        """关闭资源"""
        self._initialized = False
        await self._scraper.close()
        logger.info(f"指数适配器已关闭: {self.source_name}")
    
    def record_success(self):
        """记录成功请求"""
        self._request_count += 1
    
    def record_error(self, error: str):
        """记录错误"""
        self._error_count += 1
    
    def get_stats(self) -> dict:
        """获取统计信息"""
        total = self._request_count + self._error_count
        return {
            'source_name': self.source_name,
            'request_count': self._request_count,
            'error_count': self._error_count,
            'success_rate': round(
                self._request_count / total * 100, 2
            ) if total > 0 else 0.0,
            'initialized': self._initialized,
        }
    
    def get_adapter_info(self) -> dict:
        """获取适配器信息"""
        return {
            'source_name': self.source_name,
            'supported_types': self.supported_types,
            'requires_auth': self.requires_auth,
            'rate_limit': self.rate_limit,
            'description': self.description,
            'stats': self.get_stats(),
        }
    
    def __repr__(self) -> str:
        return f"IndexAdapter(source={self.source_name})"


# 便捷函数
async def create_index_adapter() -> IndexAdapter:
    """创建指数适配器实例"""
    adapter = IndexAdapter()
    await adapter.initialize()
    return adapter


if __name__ == '__main__':
    # 测试
    import asyncio
    
    async def main():
        adapter = IndexAdapter()
        await adapter.initialize()
        
        # 健康检查
        healthy = await adapter.health_check()
        print(f"健康状态: {healthy}")
        
        # 获取指数行情
        async for data in adapter.fetch(['000001'], 'index_quote'):
            print(f"指数行情: {data.payload}")
        
        # 统计
        print(f"统计: {adapter.get_stats()}")
        
        await adapter.close()
    
    asyncio.run(main())
