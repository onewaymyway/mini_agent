# -*- coding: utf-8 -*-
"""
加密货币解析器
"""
import logging
from typing import Any, Dict, List
from .abstract import DataParser, register_parser, _parse_float, _parse_date, _now_iso

logger = logging.getLogger(__name__)

@register_parser
class CryptoParser(DataParser):
    @property
    def source_name(self) -> str:
        return 'crypto'

    @property
    def supported_data_types(self) -> List[str]:
        return ['crypto_quote', 'crypto_kline', 'crypto_rank', 'crypto_trending']

    def parse(self, raw_data: Any, data_type: str, **kwargs) -> List[Dict[str, Any]]:
        records = []
        timestamp = _now_iso()
        items = []
        if isinstance(raw_data, dict):
            items = raw_data.get('quotes', raw_data.get('coins', raw_data.get('records', raw_data.get('data', []))))
        elif isinstance(raw_data, list):
            items = raw_data
        if not isinstance(items, list):
            return []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                rec = {
                    'symbol': str(item.get('symbol', item.get('coin', item.get('code', '')))),
                    'name': str(item.get('name', item.get('币种', ''))),
                    'price': _parse_float(item.get('price', item.get('最新价', item.get('current', 0)))),
                    'change_pct': _parse_float(item.get('change_pct', item.get('24h_change', item.get('涨跌幅', 0)))),
                    'volume_24h': _parse_float(item.get('volume_24h', item.get('24h_volume', 0))),
                    'market_cap': _parse_float(item.get('market_cap', item.get('市值', 0))),
                    'high_24h': _parse_float(item.get('high_24h', item.get('24h_high', 0))),
                    'low_24h': _parse_float(item.get('low_24h', item.get('24h_low', 0))),
                    'data_type': data_type,
                    'timestamp': timestamp,
                }
                records.append(rec)
            except (ValueError, TypeError):
                continue
        return records
