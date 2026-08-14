# -*- coding: utf-8 -*-
"""
板块解析器
支持: sector_quote / sector_flow / sector_history
"""

import logging
from typing import Any, Dict, List
from .abstract import DataParser, register_parser, _parse_float, _parse_int, _now_iso

logger = logging.getLogger(__name__)


@register_parser
class SectorParser(DataParser):
    """板块数据解析器"""

    @property
    def source_name(self) -> str:
        return 'sector'

    @property
    def supported_data_types(self) -> List[str]:
        return ['sector_quote', 'sector_flow', 'sector_history']

    def parse(self, raw_data: Any, data_type: str, **kwargs) -> List[Dict[str, Any]]:
        if data_type == 'sector_quote':
            return self._parse_quote(raw_data, data_type=data_type, **kwargs)
        elif data_type == 'sector_flow':
            return self._parse_flow(raw_data, data_type)
        elif data_type == 'sector_history':
            return []
        return []

    def _parse_quote(self, raw_data: Any, data_type: str = 'sector_quote', **kwargs) -> List[Dict[str, Any]]:
        records = []
        timestamp = _now_iso()
        sector_type = kwargs.get('sector_type', 'industry')

        items = []
        if isinstance(raw_data, dict):
            items = raw_data.get('sectors', raw_data.get('data', raw_data.get('records', [])))
        elif isinstance(raw_data, list):
            items = raw_data

        if not isinstance(items, list):
            return []

        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                records.append({
                    'sector_code': str(item.get('sector_code', item.get('code', item.get('f12', '')))),
                    'sector_name': str(item.get('sector_name', item.get('name', item.get('f14', '')))),
                    'change_pct': _parse_float(item.get('change_pct', item.get('涨跌幅', item.get('f3', 0)))),
                    'change_amt': _parse_float(item.get('change_amt', item.get('涨跌额', item.get('f4', 0)))),
                    'price': _parse_float(item.get('price', item.get('f2', 0))),
                    'high': _parse_float(item.get('high', item.get('f4', 0))),
                    'low': _parse_float(item.get('low', item.get('f5', 0))),
                    'open': _parse_float(item.get('open', item.get('f6', 0))),
                    'pre_close': _parse_float(item.get('pre_close', item.get('f17', 0))),
                    'volume': _parse_int(item.get('volume', item.get('f7', 0))),
                    'amount': _parse_float(item.get('amount', item.get('f8', 0))),
                    'top_stock': str(item.get('top_stock', item.get('领涨股票', ''))),
                    'top_stock_change': _parse_float(item.get('top_stock_change', item.get('领涨股票-涨跌幅', 0))),
                    'avg_pe': _parse_float(item.get('avg_pe', item.get('市盈率', 0))),
                    'total_mv': _parse_float(item.get('total_mv', item.get('总市值', 0))),
                    'turnover': _parse_float(item.get('turnover', item.get('换手率', 0))),
                    'stock_count': _parse_int(item.get('stock_count', item.get('成分股数量', 0))),
                    'type': sector_type,
                    'data_type': data_type,
                    'timestamp': timestamp,
                })
            except (ValueError, TypeError):
                continue
        return records

    def _parse_flow(self, raw_data: Any, data_type: str = 'sector_flow') -> List[Dict[str, Any]]:
        records = []
        timestamp = _now_iso()

        items = []
        if isinstance(raw_data, dict):
            items = raw_data.get('sectors', raw_data.get('data', []))
        elif isinstance(raw_data, list):
            items = raw_data
        if not isinstance(items, list):
            return []

        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                records.append({
                    'sector_code': str(item.get('sector_code', item.get('code', ''))),
                    'sector_name': str(item.get('sector_name', item.get('name', item.get('行业', '')))),
                    'main_inflow': _parse_float(item.get('main_inflow', item.get('净额', 0))),
                    'main_inflow_ratio': _parse_float(item.get('main_inflow_ratio', 0)),
                    'retail_inflow': _parse_float(item.get('retail_inflow', 0)),
                    'change_pct': _parse_float(item.get('change_pct', item.get('涨跌幅', 0))),
                    'rank': _parse_int(item.get('rank', item.get('序号', 0))),
                    'data_type': data_type,
                    'timestamp': timestamp,
                })
            except (ValueError, TypeError):
                continue
        return records
