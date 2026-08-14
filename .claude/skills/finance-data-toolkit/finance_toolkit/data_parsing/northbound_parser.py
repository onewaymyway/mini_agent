# -*- coding: utf-8 -*-
"""
北向资金解析器
"""
import logging
from typing import Any, Dict, List
from .abstract import DataParser, register_parser, _parse_float, _parse_date, _now_iso

logger = logging.getLogger(__name__)

@register_parser
class NorthboundParser(DataParser):
    @property
    def source_name(self) -> str:
        return 'northbound'

    @property
    def supported_data_types(self) -> List[str]:
        return ['northbound', 'northbound_summary', 'hsgt']

    def parse(self, raw_data: Any, data_type: str, **kwargs) -> List[Dict[str, Any]]:
        records = []
        timestamp = _now_iso()
        items = []
        if isinstance(raw_data, dict):
            items = raw_data.get('summary', raw_data.get('records', raw_data.get('data', [])))
        elif isinstance(raw_data, list):
            items = raw_data
        if not isinstance(items, list):
            return []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                records.append({
                    'date': _parse_date(item.get('date', item.get('日期', ''))),
                    'ggt_net': _parse_float(item.get('ggt_net', item.get('沪股通净额', item.get('北向资金', 0)))),
                    'szt_net': _parse_float(item.get('szt_net', item.get('深股通净额', 0))),
                    'total_net': _parse_float(item.get('total_net', item.get('合计净流入', 0))),
                    'buy_amount': _parse_float(item.get('buy_amount', item.get('买入金额', 0))),
                    'sell_amount': _parse_float(item.get('sell_amount', item.get('卖出金额', 0))),
                    'data_type': data_type,
                    'timestamp': timestamp,
                })
            except (ValueError, TypeError):
                continue
        return records
