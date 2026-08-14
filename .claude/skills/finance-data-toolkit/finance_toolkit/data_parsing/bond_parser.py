# -*- coding: utf-8 -*-
"""
债券解析器
"""
import logging
from typing import Any, Dict, List
from .abstract import DataParser, register_parser, _parse_float, _parse_date, _now_iso

logger = logging.getLogger(__name__)

@register_parser
class BondParser(DataParser):
    @property
    def source_name(self) -> str:
        return 'bond'

    @property
    def supported_data_types(self) -> List[str]:
        return ['bond_yield', 'bond_quote', 'convertible', 'bond_info']

    def parse(self, raw_data: Any, data_type: str, **kwargs) -> List[Dict[str, Any]]:
        records = []
        timestamp = _now_iso()
        items = []
        if isinstance(raw_data, dict):
            items = raw_data.get('quotes', raw_data.get('records', raw_data.get('data', [])))
        elif isinstance(raw_data, list):
            items = raw_data
        if not isinstance(items, list):
            return []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                if data_type == 'bond_yield':
                    records.append({
                        'date': _parse_date(item.get('date', '')),
                        '1y': _parse_float(item.get('1y', 0)),
                        '2y': _parse_float(item.get('2y', 0)),
                        '3y': _parse_float(item.get('3y', 0)),
                        '5y': _parse_float(item.get('5y', 0)),
                        '10y': _parse_float(item.get('10y', 0)),
                        'data_type': data_type, 'timestamp': timestamp,
                    })
                elif data_type == 'bond_quote':
                    records.append({
                        'name': str(item.get('name', '')),
                        'code': str(item.get('code', '')),
                        'price': _parse_float(item.get('price', 0)),
                        'yield_rate': _parse_float(item.get('yield_rate', 0)),
                        'change': _parse_float(item.get('change', 0)),
                        'change_pct': _parse_float(item.get('change_pct', 0)),
                        'data_type': data_type, 'timestamp': timestamp,
                    })
                elif data_type == 'convertible':
                    records.append({
                        'name': str(item.get('name', '')),
                        'code': str(item.get('code', '')),
                        'price': _parse_float(item.get('price', 0)),
                        'stock_price': _parse_float(item.get('stock_price', 0)),
                        'change_pct': _parse_float(item.get('change_pct', 0)),
                        'premium': _parse_float(item.get('premium', 0)),
                        'data_type': data_type, 'timestamp': timestamp,
                    })
                else:
                    records.append({k: v for k, v in item.items() if v is not None})
            except (ValueError, TypeError):
                continue
        return records
