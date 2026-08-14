# -*- coding: utf-8 -*-
"""
大宗商品解析器
"""
import logging
from typing import Any, Dict, List
from .abstract import DataParser, register_parser, _parse_float, _parse_date, _now_iso

logger = logging.getLogger(__name__)

@register_parser
class CommodityParser(DataParser):
    @property
    def source_name(self) -> str:
        return 'commodity'

    @property
    def supported_data_types(self) -> List[str]:
        return ['commodity_quote', 'gold_quote', 'oil_quote', 'metal_quote', 'dxy']

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
                records.append({
                    'symbol': str(item.get('symbol', item.get('code', item.get('商品', '')))),
                    'name': str(item.get('name', item.get('名称', ''))),
                    'price': _parse_float(item.get('price', item.get('最新价', item.get('price', 0)))),
                    'change_pct': _parse_float(item.get('change_pct', item.get('涨跌幅', 0))),
                    'open': _parse_float(item.get('open', 0)),
                    'high': _parse_float(item.get('high', 0)),
                    'low': _parse_float(item.get('low', 0)),
                    'pre_close': _parse_float(item.get('pre_close', 0)),
                    'volume': _parse_float(item.get('volume', 0)),
                    'date': _parse_date(item.get('date', item.get('日期', ''))),
                    'data_type': data_type,
                    'timestamp': timestamp,
                })
            except (ValueError, TypeError):
                continue
        return records
