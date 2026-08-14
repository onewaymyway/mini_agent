# -*- coding: utf-8 -*-
"""
龙虎榜解析器
"""
import logging
from typing import Any, Dict, List
from .abstract import DataParser, register_parser, _parse_float, _parse_int, _parse_date, _now_iso

logger = logging.getLogger(__name__)

@register_parser
class LHBParser(DataParser):
    @property
    def source_name(self) -> str:
        return 'lhb'

    @property
    def supported_data_types(self) -> List[str]:
        return ['lhb', 'lhb_detail']

    def parse(self, raw_data: Any, data_type: str, **kwargs) -> List[Dict[str, Any]]:
        records = []
        timestamp = _now_iso()
        items = []
        if isinstance(raw_data, dict):
            items = raw_data.get('records', raw_data.get('data', raw_data.get('items', [])))
        elif isinstance(raw_data, list):
            items = raw_data
        if not isinstance(items, list):
            return []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                code = str(item.get('code', item.get('代码', '')))
                records.append({
                    'symbol': f"{code}.SH" if code.startswith(('60', '68', '90')) else f"{code}.SZ",
                    'code': code,
                    'name': str(item.get('name', item.get('名称', ''))),
                    'date': _parse_date(item.get('date', item.get('龙虎榜日期', ''))),
                    'explain': str(item.get('explain', item.get('解释说明', ''))),
                    'buy_amount': _parse_float(item.get('buy_amount', item.get('买入金额', 0))),
                    'sell_amount': _parse_float(item.get('sell_amount', item.get('卖出金额', 0))),
                    'net_amount': _parse_float(item.get('net_amount', item.get('净买入', 0))),
                    'buy_seat': str(item.get('buy_seat', item.get('买入营业部', ''))),
                    'sell_seat': str(item.get('sell_seat', item.get('卖出营业部', ''))),
                    'reason': str(item.get('reason', item.get('原因', ''))),
                    'data_type': data_type,
                    'timestamp': timestamp,
                })
            except (ValueError, TypeError):
                continue
        return records
