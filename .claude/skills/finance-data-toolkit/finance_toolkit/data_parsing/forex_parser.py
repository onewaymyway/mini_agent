# -*- coding: utf-8 -*-
"""
外汇解析器
"""
import logging
from typing import Any, Dict, List
from .abstract import DataParser, register_parser, _parse_float, _parse_date, _now_iso

logger = logging.getLogger(__name__)

@register_parser
class ForexParser(DataParser):
    @property
    def source_name(self) -> str:
        return 'forex'

    @property
    def supported_data_types(self) -> List[str]:
        return ['forex_quote', 'forex_kline', 'forex_cny', 'forex_cross']

    def parse(self, raw_data: Any, data_type: str, **kwargs) -> List[Dict[str, Any]]:
        records = []
        timestamp = _now_iso()
        if data_type in ('forex_quote', 'forex_cny', 'forex_cross'):
            items = []
            if isinstance(raw_data, dict):
                items = raw_data.get('quotes', raw_data.get('records', raw_data.get('data', [])))
            elif isinstance(raw_data, list):
                items = raw_data
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    try:
                        records.append({
                            'code': str(item.get('code', item.get('pair', ''))),
                            'name': str(item.get('name', item.get('名称', ''))),
                            'price': _parse_float(item.get('price', item.get('latest', item.get('汇率', 0)))),
                            'open': _parse_float(item.get('open', 0)),
                            'high': _parse_float(item.get('high', 0)),
                            'low': _parse_float(item.get('low', 0)),
                            'pre_close': _parse_float(item.get('pre_close', item.get('昨收', 0))),
                            'volume': _parse_float(item.get('volume', item.get('成交量', 0))),
                            'date': _parse_date(item.get('date', item.get('date_str', ''))),
                            'time': str(item.get('time', '')),
                            'change_pct': _parse_float(item.get('change_pct', 0)),
                            'data_type': data_type,
                            'timestamp': timestamp,
                        })
                    except (ValueError, TypeError):
                        continue
        elif data_type == 'forex_kline':
            items = []
            if isinstance(raw_data, dict):
                items = raw_data.get('records', raw_data.get('data', []))
            elif isinstance(raw_data, list):
                items = raw_data
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    try:
                        records.append({
                            'date': _parse_date(item.get('date', item.get('day', ''))),
                            'open': _parse_float(item.get('open', 0)),
                            'close': _parse_float(item.get('close', 0)),
                            'high': _parse_float(item.get('high', 0)),
                            'low': _parse_float(item.get('low', 0)),
                            'volume': _parse_float(item.get('volume', 0)),
                            'data_type': data_type,
                            'timestamp': timestamp,
                        })
                    except (ValueError, TypeError):
                        continue
        return records
