# -*- coding: utf-8 -*-
"""
基金数据抓取器实现
数据源: 东方财富、天天基金网 (免费、无需 token)
支持: 基金净值、历史净值、持仓、分类、基金经理等
"""

import json
import re
from datetime import datetime, timedelta
from typing import List, Optional, AsyncIterator

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False
    httpx = None

from ..core import BaseScraper, FinanceData, register_scraper


@register_scraper
class FundScraper(BaseScraper):
    """基金数据抓取器"""

    @property
    def source_name(self) -> str:
        return 'fund'

    @property
    def supported_types(self) -> List[str]:
        return ['fund_nav', 'fund_holdings', 'fund_rank', 'fund_info', 'fund_history']

    async def health_check(self) -> bool:
        if not HAS_HTTPX:
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    'https://fundgz.eastmoney.com/js/fundcode_search.js',
                    timeout=10
                )
                return resp.status_code == 200
        except Exception:
            return False

    async def fetch(
        self,
        symbols: List[str],
        data_type: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        **kwargs
    ) -> AsyncIterator[FinanceData]:
        """获取数据主入口"""
        if not HAS_HTTPX:
            raise ImportError("httpx 未安装，请运行：pip install httpx")

        if data_type == 'fund_nav':
            for symbol in symbols:
                async for data in self._fetch_nav(symbol, **kwargs):
                    yield data
        elif data_type == 'fund_holdings':
            for symbol in symbols:
                async for data in self._fetch_holdings(symbol, **kwargs):
                    yield data
        elif data_type == 'fund_rank':
            async for data in self._fetch_rank(**kwargs):
                yield data
        elif data_type == 'fund_info':
            for symbol in symbols:
                async for data in self._fetch_info(symbol):
                    yield data
        elif data_type == 'fund_history':
            for symbol in symbols:
                async for data in self._fetch_history(symbol, start, end):
                    yield data
        else:
            raise ValueError(f"不支持的数据类型：{data_type}")

    async def _fetch_nav(self, symbol: str, **kwargs) -> AsyncIterator[FinanceData]:
        """获取基金最新净值 - 使用 JS 文件方式"""
        async with httpx.AsyncClient(timeout=30) as client:
            # 获取基金 JS 数据文件
            resp = await client.get(
                f'https://fund.eastmoney.com/pingzhongdata/{symbol}.js',
                timeout=30
            )

            if resp.status_code != 200:
                return

            text = resp.text
            
            # 提取基本信息
            info = {}
            
            # 基金名称
            name_match = re.search(r'fS_name\s*=\s*["\']([^"\']+)["\']', text)
            if name_match:
                info['name'] = name_match.group(1)
            
            # 基金代码
            code_match = re.search(r'fS_code\s*=\s*["\']([^"\']+)["\']', text)
            if code_match:
                info['code'] = code_match.group(1)
            
            # 最新净值 (Data_netWorthTrend 最后一个元素的 y 值)
            nav_match = re.search(r'Data_netWorthTrend\s*=\s*\[(.+?)\];', text, re.DOTALL)
            if nav_match:
                nav_str = nav_match.group(1)
                # 提取最后一个净值记录
                nav_pattern = r'\{"x":"([^"]+)","y":"([^"]+)"'
                nav_matches = re.findall(nav_pattern, nav_str)
                if nav_matches:
                    last_nav = nav_matches[-1]
                    info['nav_date'] = last_nav[0]
                    info['nav'] = float(last_nav[1])
            
            # 累计净值 (Data_ACWorthTrend)
            acc_nav_match = re.search(r'Data_ACWorthTrend\s*=\s*\[(.+?)\];', text, re.DOTALL)
            if acc_nav_match:
                acc_str = acc_nav_match.group(1)
                acc_pattern = r'\["([^"]+)",([\d.]+)'
                acc_matches = re.findall(acc_pattern, acc_str)
                if acc_matches:
                    info['acc_nav'] = float(acc_matches[-1][1])
            
            if info:
                yield FinanceData(
                    source='fund',
                    data_type='fund_nav',
                    symbol=symbol,
                    timestamp=datetime.utcnow(),
                    payload=info
                )

    async def _fetch_holdings(self, symbol: str, **kwargs) -> AsyncIterator[FinanceData]:
        """获取基金持仓 - 使用 JS 文件方式"""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f'https://fund.eastmoney.com/pingzhongdata/{symbol}.js',
                timeout=30
            )

            if resp.status_code != 200:
                return

            text = resp.text
            holdings = []
            
            # 股票持仓代码
            stock_match = re.search(r'stockCodes\s*=\s*\[(.+?)\];', text, re.DOTALL)
            if stock_match:
                stock_str = stock_match.group(1)
                stock_codes = re.findall(r'["\']([0-9a-f]{6,})["\']', stock_str)
                for code in stock_codes[:20]:  # 最多20只
                    holdings.append({
                        'type': 'stock',
                        'code': code,
                        'name': '未知'
                    })
            
            # 债券持仓代码
            bond_match = re.search(r'zqCodesNew\s*=\s*\[(.+?)\];', text, re.DOTALL)
            if bond_match:
                bond_str = bond_match.group(1)
                bond_codes = re.findall(r'["\']([0-9.]+)["\']', bond_str)
                for code in bond_codes[:10]:  # 最多10只
                    holdings.append({
                        'type': 'bond',
                        'code': code,
                        'name': '未知'
                    })
            
            if holdings:
                yield FinanceData(
                    source='fund',
                    data_type='fund_holdings',
                    symbol=symbol,
                    timestamp=datetime.utcnow(),
                    payload={'holdings': holdings, 'count': len(holdings)}
                )

    async def _fetch_rank(self, **kwargs) -> AsyncIterator[FinanceData]:
        """获取基金排行榜"""
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://fund.eastmoney.com/',
        }
        async with httpx.AsyncClient(timeout=30, headers=headers) as client:
            fund_type = kwargs.get('fund_type', 'gp')  # gp=股票型, hh=混合型, zq=债券型
            
            resp = await client.get(
                'https://fund.eastmoney.com/data/rankhandler.aspx',
                params={
                    'op': 'ph',
                    'dt': 'kf',
                    'ft': fund_type,
                    'rs': '',
                    'gs': '',
                    'sc': 'zjfz',
                    'st': 'desc',
                    'pi': '1',
                    'pn': '50',
                    'dx': '1',
                    'v': '0.123456789'
                },
                timeout=30
            )

            if resp.status_code != 200:
                return

            text = resp.text
            
            # 解析 JSONP: var rankData = {datas:[...], allRecords:...}
            match = re.search(r'var rankData\s*=\s*(.+?);\s*$', text, re.DOTALL)
            if not match:
                return
            
            json_str = match.group(1).strip()
            # 修复 JSON key 引号
            json_str = re.sub(r'([{,])\s*(\w+)\s*:', r'\1"\2":', json_str)
            
            try:
                data = json.loads(json_str)
            except json.JSONDecodeError:
                return
            
            funds = []
            if data.get('datas'):
                for item in data['datas'][:50]:
                    parts = item.split(',')
                    if len(parts) >= 12:
                        try:
                            funds.append({
                                'code': parts[0],
                                'name': parts[1],
                                'pinyin': parts[2],
                                'date': parts[3],
                                'nav': parts[4],
                                'acc_nav': parts[5],
                                'daily_return': parts[6],
                                'return_1m': parts[7],
                                'return_3m': parts[8],
                                'return_6m': parts[9],
                                'return_1y': parts[10],
                                'return_3y': parts[11],
                                'return_5y': parts[12] if len(parts) > 12 else None,
                                'fund_type': parts[13] if len(parts) > 13 else None
                            })
                        except (ValueError, IndexError):
                            continue
            
            if funds:
                yield FinanceData(
                    source='fund',
                    data_type='fund_rank',
                    symbol='*',
                    timestamp=datetime.utcnow(),
                    payload={'funds': funds, 'count': len(funds), 'total': data.get('allRecords', 0)}
                )

    async def _fetch_info(self, symbol: str) -> AsyncIterator[FinanceData]:
        """获取基金基本信息 - 使用 JS 文件方式"""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f'https://fund.eastmoney.com/pingzhongdata/{symbol}.js',
                timeout=30
            )

            if resp.status_code != 200:
                return

            text = resp.text
            info = {}
            
            # 基金名称
            name_match = re.search(r'fS_name\s*=\s*["\']([^"\']+)["\']', text)
            if name_match:
                info['name'] = name_match.group(1)
            
            # 基金代码
            code_match = re.search(r'fS_code\s*=\s*["\']([^"\']+)["\']', text)
            if code_match:
                info['code'] = code_match.group(1)
            
            # 基金类型
            type_match = re.search(r'fund_Type\s*=\s*["\']([^"\']+)["\']', text)
            if type_match:
                info['type'] = type_match.group(1)
            
            # 基金公司
            company_match = re.search(r'fund_Company\s*=\s*["\']([^"\']+)["\']', text)
            if company_match:
                info['company'] = company_match.group(1)
            
            # 成立日期
            date_match = re.search(r'fund_EstablishDate\s*=\s*["\']([^"\']+)["\']', text)
            if date_match:
                info['establish_date'] = date_match.group(1)
            
            # 基金规模
            size_match = re.search(r'fund_Scale\s*=\s*["\']([^"\']+)["\']', text)
            if size_match:
                info['size'] = size_match.group(1)
            
            # 基金经理
            manager_match = re.search(r'fund_Manager\s*=\s*["\']([^"\']+)["\']', text)
            if manager_match:
                info['manager'] = manager_match.group(1)
            
            # 费率信息
            rate_match = re.search(r'fund_Rate\s*=\s*\{([^}]+)\}', text)
            if rate_match:
                info['rate'] = rate_match.group(1)
            
            # 最小申购金额
            min_match = re.search(r'fund_minsg\s*=\s*["\']([^"\']+)["\']', text)
            if min_match:
                info['min_purchase'] = min_match.group(1)
            
            if info:
                yield FinanceData(
                    source='fund',
                    data_type='fund_info',
                    symbol=symbol,
                    timestamp=datetime.utcnow(),
                    payload=info
                )

    async def _fetch_history(self, symbol: str, start: Optional[datetime], end: Optional[datetime], **kwargs) -> AsyncIterator[FinanceData]:
        """获取基金历史净值 - 使用 JS 文件方式"""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f'https://fund.eastmoney.com/pingzhongdata/{symbol}.js',
                timeout=30
            )

            if resp.status_code != 200:
                return

            text = resp.text
            records = []
            
            # 提取净值历史数据
            nav_match = re.search(r'Data_netWorthTrend\s*=\s*\[(.+?)\];', text, re.DOTALL)
            if nav_match:
                nav_str = nav_match.group(1)
                # 解析为 JSON 数组
                try:
                    nav_data = json.loads(f'[{nav_str}]')
                    for item in nav_data:
                        x = item.get('x', 0)
                        # 转换时间戳为日期
                        if isinstance(x, (int, float)) and x > 1000000000000:
                            date_str = datetime.fromtimestamp(x / 1000).strftime('%Y-%m-%d')
                        else:
                            date_str = str(x)
                        records.append({
                            'date': date_str,
                            'nav': float(item.get('y', 0)),
                            'equity_return': float(item.get('equityReturn', 0)) if item.get('equityReturn') else None
                        })
                except json.JSONDecodeError:
                    pass
            
            # 提取累计净值历史
            acc_nav_records = []
            acc_match = re.search(r'Data_ACWorthTrend\s*=\s*\[(.+?)\];', text, re.DOTALL)
            if acc_match:
                acc_str = acc_match.group(1)
                try:
                    acc_data = json.loads(f'[{acc_str}]')
                    for item in acc_data:
                        if len(item) >= 2:
                            acc_nav_records.append({
                                'date': str(item[0]),
                                'acc_nav': float(item[1])
                            })
                except json.JSONDecodeError:
                    pass
            
            # 合并数据
            if records:
                # 按日期排序（最新的在前）
                records.sort(key=lambda x: x['date'], reverse=True)
                
                # 添加累计净值
                acc_dict = {r['date']: r['acc_nav'] for r in acc_nav_records}
                for record in records:
                    if record['date'] in acc_dict:
                        record['acc_nav'] = acc_dict[record['date']]
                
                yield FinanceData(
                    source='fund',
                    data_type='fund_history',
                    symbol=symbol,
                    timestamp=datetime.utcnow(),
                    payload={
                        'records': records[:100],  # 最多100条
                        'count': len(records),
                        'start': records[-1]['date'] if records else '',
                        'end': records[0]['date'] if records else ''
                    }
                )

    async def close(self):
        """关闭资源"""
        pass


# 便捷函数
async def create_scraper(source: str = 'fund') -> BaseScraper:
    """创建抓取器实例"""
    if source == 'fund':
        return FundScraper()
    raise ValueError(f"Unknown source: {source}")
