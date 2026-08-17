# -*- coding: utf-8 -*-
"""
AKShare 数据适配器

基于 AKShareScraper 实现 Adapter 接口，支持：
- 实时行情 (quote)
- K线数据 (kline)
- 财务数据 (financial)
- 分红数据 (dividend)
- 股东信息 (shareholder)
- 龙虎榜 (lhb)
- 北向资金 (northbound)

统一字段映射：将 akshare 返回的中文列名映射为标准字段名
"""

import logging
from datetime import datetime
from typing import List, Optional, AsyncIterator, Any

from .base_adapter import BaseAdapter, AdapterConfig
from ..core import FinanceData

logger = logging.getLogger(__name__)

# ============== 字段映射表 ==============

QUOTE_FIELD_MAP = {
    '代码': 'symbol', '名称': 'name', '最新价': 'close',
    '今开': 'open', '最高': 'high', '最低': 'low',
    '昨收': 'pre_close', '成交量': 'volume', '成交额': 'amount',
    '涨跌幅': 'change_pct', '涨跌额': 'change_amt', '换手率': 'turnover',
    '市盈率-动态': 'pe_ttm', '市净率': 'pb', '总市值': 'total_mv', '流通市值': 'circ_mv',
    '涨幅(%)': 'change_pct', '最新价(元)': 'close',
}

KLINE_FIELD_MAP = {
    '日期': 'date', '开盘': 'open', '收盘': 'close',
    '最高': 'high', '最低': 'low', '成交量': 'volume',
    '成交额': 'amount', '振幅': 'amplitude',
    '涨跌幅': 'change_pct', '涨跌额': 'change_amt', '换手率': 'turnover',
}

FINANCIAL_FIELD_MAP = {
    '指标名称': 'indicator_name', '2023年报': 'annual_2023',
    '2024一季报': 'q1_2024', '2024年报': 'annual_2024',
}

LHB_FIELD_MAP = {
    '代码': 'symbol', '名称': 'name', '上榜日期': 'date',
    '解读': 'reason', '收盘价': 'close', '涨跌幅': 'change_pct',
    '龙虎榜评分': 'score', '买入额': 'buy_amount', '卖出额': 'sell_amount',
}


def _map_fields(record: dict, field_map: dict) -> dict:
    """标准化字段名"""
    result = {}
    for src_key, val in record.items():
        mapped = field_map.get(src_key, src_key)
        result[mapped] = val
    return result


class AKShareAdapter(BaseAdapter):
    """
    AKShare 数据适配器
    
    封装 AKShareScraper，提供统一接口和字段标准化。
    """
    
    def __init__(self, config: Optional[AdapterConfig] = None):
        super().__init__(config)
        self._scraper = None
        self._initialized = False
        self._request_count = 0
        self._error_count = 0
    
    @property
    def source_name(self) -> str:
        return 'akshare'
    
    @property
    def supported_types(self) -> List[str]:
        return ['quote', 'kline', 'financial', 'dividend', 'shareholder', 'lhb', 'northbound']
    
    @property
    def requires_auth(self) -> bool:
        return False
    
    @property
    def rate_limit(self) -> int:
        return 30  # AKShare 限制较低
    
    @property
    def description(self) -> str:
        return 'AKShare 免费财经数据适配器（无需Token）'
    
    async def initialize(self):
        """初始化适配器"""
        try:
            from ..scrapers.akshare_scraper import AKShareScraper
            self._scraper = AKShareScraper()
            self._initialized = True
            logger.info(f"AKShare适配器初始化完成: {self.source_name}")
        except ImportError as e:
            self._initialized = False
            logger.error(f"AKShare适配器初始化失败: {e}")
            raise
    
    async def fetch(
        self,
        symbols: List[str],
        data_type: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        **kwargs
    ) -> AsyncIterator[Any]:
        """获取数据，带字段标准化"""
        if data_type not in self.supported_types:
            yield FinanceData(
                source=self.source_name,
                data_type=data_type,
                symbol='*',
                timestamp=datetime.utcnow(),
                payload={'error': f'不支持的数据类型: {data_type}'}
            )
            return
        
        if not self._initialized:
            await self.initialize()
        
        try:
            async for data in self._scraper.fetch(symbols, data_type, start, end, **kwargs):
                self._request_count += 1
                # 标准化字段
                standardized = self._standardize(data)
                yield standardized
        except Exception as e:
            self._error_count += 1
            logger.error(f"AKShare数据获取失败: {e}")
            yield FinanceData(
                source=self.source_name,
                data_type=data_type,
                symbol=symbols[0] if symbols else '*',
                timestamp=datetime.utcnow(),
                payload={'error': str(e)[:200]}
            )
    
    def _standardize(self, data: FinanceData) -> FinanceData:
        """标准化数据字段"""
        payload = data.payload or {}
        
        if data.data_type == 'quote' and isinstance(payload, dict):
            payload = _map_fields(payload, QUOTE_FIELD_MAP)
        elif data.data_type == 'kline' and isinstance(payload, dict):
            if 'data' in payload and isinstance(payload['data'], list):
                payload['data'] = [
                    _map_fields(item, KLINE_FIELD_MAP) 
                    for item in payload['data']
                ]
        elif data.data_type == 'financial' and isinstance(payload, dict):
            if 'data' in payload and isinstance(payload['data'], list):
                payload['data'] = [
                    _map_fields(item, FINANCIAL_FIELD_MAP)
                    for item in payload['data']
                ]
        elif data.data_type == 'lhb' and isinstance(payload, dict):
            if 'data' in payload and isinstance(payload['data'], list):
                payload['data'] = [
                    _map_fields(item, LHB_FIELD_MAP)
                    for item in payload['data']
                ]
        
        return FinanceData(
            source=data.source,
            data_type=data.data_type,
            symbol=data.symbol,
            timestamp=data.timestamp,
            payload=payload,
            meta=data.meta
        )
    
    async def health_check(self) -> bool:
        """健康检查"""
        try:
            if not self._scraper:
                await self.initialize()
            return await self._scraper.health_check()
        except Exception as e:
            logger.warning(f"AKShare适配器健康检查失败: {e}")
            return False
    
    async def close(self):
        """关闭资源"""
        self._initialized = False
        if self._scraper:
            await self._scraper.close()
        logger.info(f"AKShare适配器已关闭: {self.source_name}")
    
    def get_stats(self) -> dict:
        total = self._request_count + self._error_count
        return {
            'source_name': self.source_name,
            'request_count': self._request_count,
            'error_count': self._error_count,
            'success_rate': round(self._request_count / total * 100, 2) if total > 0 else 0.0,
            'initialized': self._initialized,
        }
    
    def get_adapter_info(self) -> dict:
        return {
            'source_name': self.source_name,
            'supported_types': self.supported_types,
            'requires_auth': self.requires_auth,
            'rate_limit': self.rate_limit,
            'description': self.description,
            'stats': self.get_stats(),
            'field_mapping': {
                'quote': list(QUOTE_FIELD_MAP.items()),
                'kline': list(KLINE_FIELD_MAP.items()),
            }
        }
    
    def __repr__(self) -> str:
        return f"AKShareAdapter(source={self.source_name})"


async def create_akshare_adapter() -> AKShareAdapter:
    """创建AKShare适配器实例"""
    adapter = AKShareAdapter()
    await adapter.initialize()
    return adapter


if __name__ == '__main__':
    import asyncio
    
    async def main():
        adapter = AKShareAdapter()
        await adapter.initialize()
        
        print(f"健康状态: {await adapter.health_check()}")
        print(f"支持类型: {adapter.supported_types}")
        print(f"信息: {adapter.get_adapter_info()}")
        
        # 测试获取腾讯行情
        async for data in adapter.fetch(['000001.SZ'], 'quote'):
            print(f"数据: {data}")
        
        await adapter.close()
    
    asyncio.run(main())
