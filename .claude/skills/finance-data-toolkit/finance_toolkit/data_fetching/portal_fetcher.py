# -*- coding: utf-8 -*-
"""
财经门户数据抓取器
整合多个财经门户网站的公开数据接口
"""

import json
import logging
import re
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

from ..core import FinanceData

logger = logging.getLogger(__name__)


class PortalFetcher:
    """财经门户数据整合抓取器"""
    
    def __init__(self):
        self.timeout = 20
    
    async def _fetch_json(self, url: str, params: dict = None, headers: dict = None) -> dict:
        """通用JSON请求方法"""
        if not HAS_HTTPX:
            raise ImportError("httpx未安装")
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, params=params, headers=headers or {}, timeout=self.timeout)
            return resp.json()
    
    def fetch_market_summary(self) -> List[FinanceData]:
        """获取市场概况（上证指数、深证成指、创业板指）"""
        results = []
        
        if not HAS_HTTPX:
            return results
        
        try:
            async def _fetch():
                async with httpx.AsyncClient(timeout=20) as client:
                    # 获取主要指数行情
                    indices = ['sh000001', 'sz399001', 'sz399006']
                    url = f"https://qt.gtimg.cn/q={','.join(indices)}"
                    
                    resp = await client.get(url, timeout=20)
                    resp.encoding = 'gbk'
                    
                    for line in resp.text.strip().split(';'):
                        if '=' not in line:
                            continue
                        
                        var_part, data_part = line.split('=', 1)
                        code = var_part.strip().replace('var hq_str_', '').replace('"', '')
                        data_str = data_part.strip().strip('"')
                        
                        if not data_str:
                            continue
                        
                        fields = data_str.split('~')
                        if len(fields) < 10:
                            continue
                        
                        try:
                            # 指数字段映射
                            payload = {
                                'name': fields[1] if len(fields) > 1 else '',
                                'code': code,
                                'price': float(fields[3]) if fields[3] else 0.0,
                                'pre_close': float(fields[4]) if len(fields) > 4 and fields[4] else 0.0,
                                'open': float(fields[5]) if len(fields) > 5 and fields[5] else 0.0,
                                'high': float(fields[6]) if len(fields) > 6 and fields[6] else 0.0,
                                'low': float(fields[7]) if len(fields) > 7 and fields[7] else 0.0,
                                'volume': int(float(fields[8])) if len(fields) > 8 and fields[8] else 0,
                                'change_pct': float(fields[30]) if len(fields) > 30 and fields[30] else 0.0,
                                'index_type': 'market'
                            }
                            
                            results.append(FinanceData(
                                source='portal',
                                data_type='index_quote',
                                symbol=code,
                                timestamp=datetime.utcnow().isoformat(),
                                payload=payload
                            ))
                        except (ValueError, IndexError) as e:
                            logger.warning(f"指数数据解析失败 {code}: {e}")
                            continue
                
                return results
            
            import asyncio
            results = asyncio.run(_fetch())
            
        except Exception as e:
            logger.error(f"市场概况获取失败: {e}")
        
        return results
    
    def fetch_limit_up_stocks(self) -> List[FinanceData]:
        """获取涨停板股票列表"""
        results = []
        
        if not HAS_HTTPX:
            return results
        
        try:
            async def _fetch():
                async with httpx.AsyncClient(timeout=20) as client:
                    # 东方财富涨停板数据
                    url = "https://push2.eastmoney.com/api/qt/clist/get"
                    params = {
                        'pn': '1',
                        'pz': '100',
                        'po': '1',
                        'np': '1',
                        'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
                        'fltt': '2',
                        'invt': '2',
                        'fid': 'f3',
                        'fs': 'm:0+t:6,m:0+t:13,m:1+t:2,m:1+t:23',
                        'fields': 'f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18'
                    }
                    
                    resp = await client.get(url, params=params, timeout=20)
                    data = resp.json()
                    
                    if data.get('data') and data['data'].get('diff'):
                        for item in data['data']['diff']:
                            if item.get('f2') is not None:
                                payload = {
                                    'name': item.get('f14', ''),
                                    'code': item.get('f12', ''),
                                    'price': item.get('f2'),
                                    'change_pct': item.get('f3'),
                                    'high': item.get('f4'),
                                    'low': item.get('f5'),
                                    'open': item.get('f6'),
                                    'pre_close': item.get('f17'),
                                    'volume': item.get('f7'),
                                    'amount': item.get('f8'),
                                    'limit_type': 'up'
                                }
                                
                                results.append(FinanceData(
                                    source='portal',
                                    data_type='limit_up',
                                    symbol=item.get('f12', ''),
                                    timestamp=datetime.utcnow().isoformat(),
                                    payload=payload
                                ))
                    
                    return results
            
            import asyncio
            results = asyncio.run(_fetch())
            
        except Exception as e:
            logger.error(f"涨停板数据获取失败: {e}")
        
        return results
    
    def fetch_limit_down_stocks(self) -> List[FinanceData]:
        """获取跌停板股票列表"""
        results = []
        
        if not HAS_HTTPX:
            return results
        
        try:
            async def _fetch():
                async with httpx.AsyncClient(timeout=20) as client:
                    # 东方财富跌停板数据
                    url = "https://push2.eastmoney.com/api/qt/clist/get"
                    params = {
                        'pn': '1',
                        'pz': '100',
                        'po': '1',
                        'np': '1',
                        'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
                        'fltt': '2',
                        'invt': '2',
                        'fid': 'f3',
                        'fs': 'm:0+t:6,m:0+t:13,m:1+t:2,m:1+t:23',
                        'fields': 'f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18'
                    }
                    
                    resp = await client.get(url, params=params, timeout=20)
                    data = resp.json()
                    
                    if data.get('data') and data['data'].get('diff'):
                        for item in data['data']['diff']:
                            if item.get('f2') is not None and item.get('f3', 0) < -9:
                                payload = {
                                    'name': item.get('f14', ''),
                                    'code': item.get('f12', ''),
                                    'price': item.get('f2'),
                                    'change_pct': item.get('f3'),
                                    'high': item.get('f4'),
                                    'low': item.get('f5'),
                                    'open': item.get('f6'),
                                    'pre_close': item.get('f17'),
                                    'volume': item.get('f7'),
                                    'amount': item.get('f8'),
                                    'limit_type': 'down'
                                }
                                
                                results.append(FinanceData(
                                    source='portal',
                                    data_type='limit_down',
                                    symbol=item.get('f12', ''),
                                    timestamp=datetime.utcnow().isoformat(),
                                    payload=payload
                                ))
                    
                    return results
            
            import asyncio
            results = asyncio.run(_fetch())
            
        except Exception as e:
            logger.error(f"跌停板数据获取失败: {e}")
        
        return results
    
    def fetch_sector_performance(self) -> List[FinanceData]:
        """获取板块涨跌幅排行"""
        results = []
        
        if not HAS_HTTPX:
            return results
        
        try:
            async def _fetch():
                async with httpx.AsyncClient(timeout=20) as client:
                    # 东方财富板块数据
                    url = "https://push2.eastmoney.com/api/qt/clist/get"
                    params = {
                        'pn': '1',
                        'pz': '100',
                        'po': '1',
                        'np': '1',
                        'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
                        'fltt': '2',
                        'invt': '2',
                        'fid': 'f3',
                        'fs': 'm:90+t:2+m:90+t:3+m:90+t:4+m:90+t:1',
                        'fields': 'f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18'
                    }
                    
                    resp = await client.get(url, params=params, timeout=20)
                    data = resp.json()
                    
                    if data.get('data') and data['data'].get('diff'):
                        for item in data['data']['diff']:
                            if item.get('f2') is not None:
                                payload = {
                                    'name': item.get('f14', ''),
                                    'code': item.get('f12', ''),
                                    'price': item.get('f2'),
                                    'change_pct': item.get('f3'),
                                    'high': item.get('f4'),
                                    'low': item.get('f5'),
                                    'open': item.get('f6'),
                                    'pre_close': item.get('f17'),
                                    'volume': item.get('f7'),
                                    'amount': item.get('f8'),
                                    'sector_type': 'industry'
                                }
                                
                                results.append(FinanceData(
                                    source='portal',
                                    data_type='sector_performance',
                                    symbol=item.get('f12', ''),
                                    timestamp=datetime.utcnow().isoformat(),
                                    payload=payload
                                ))
                    
                    return results
            
            import asyncio
            results = asyncio.run(_fetch())
            
        except Exception as e:
            logger.error(f"板块数据获取失败: {e}")
        
        return results
    
    def fetch_all(self) -> Dict[str, List[FinanceData]]:
        """获取所有门户数据"""
        return {
            'market_summary': self.fetch_market_summary(),
            'limit_up': self.fetch_limit_up_stocks(),
            'limit_down': self.fetch_limit_down_stocks(),
            'sector_performance': self.fetch_sector_performance(),
        }


# 便捷实例
portal_fetcher = PortalFetcher()


if __name__ == '__main__':
    logger.info("测试门户数据抓取...")
    
    logger.info("\n1. 市场概况...")
    summary = portal_fetcher.fetch_market_summary()
    for s in summary:
        logger.info(f"{s.payload.get('name')}: {s.payload.get('price')} ({s.payload.get('change_pct')}%)")
    
    logger.info("\n2. 涨停板...")
    limit_up = portal_fetcher.fetch_limit_up_stocks()
    logger.info(f"涨停股数量: {len(limit_up)}")
    
    logger.info("\n3. 跌停板...")
    limit_down = portal_fetcher.fetch_limit_down_stocks()
    logger.info(f"跌停股数量: {len(limit_down)}")
    
    logger.info("\n4. 板块表现...")
    sectors = portal_fetcher.fetch_sector_performance()
    for s in sectors[:5]:
        logger.info(f"{s.payload.get('name')}: {s.payload.get('change_pct')}%")
