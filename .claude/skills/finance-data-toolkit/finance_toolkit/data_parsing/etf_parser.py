# -*- coding: utf-8 -*-
"""
ETF 解析器
支持: etf_quote / etf_kline / etf_nav / etf_holdings
"""
import logging
from typing import Any, Dict, List
from .abstract import DataParser, register_parser, _parse_float, _parse_int, _parse_date, _now_iso

logger = logging.getLogger(__name__)

@register_parser
class ETFParser(DataParser):
    @property
    def source_name(self) -> str:
        return 'etf'

    @property
    def supported_data_types(self) -> List[str]:
        return ['etf_quote', 'etf_kline', 'etf_nav', 'etf_holdings']

    def parse(self, raw_data: Any, data_type: str, **kwargs) -> List[Dict[str, Any]]:
        if data_type == 'etf_quote':
            return self._parse_quote(raw_data, data_type=data_type)
        elif data_type == 'etf_kline':
            return self._parse_kline(raw_data, data_type=data_type)
        elif data_type == 'etf_nav':
            return self._parse_nav(raw_data, data_type=data_type)
        elif data_type == 'etf_holdings':
            return self._parse_holdings(raw_data, data_type=data_type)
        return []

    def _parse_quote(self, raw_data: Any, data_type: str = 'etf_quote') -> List[Dict[str, Any]]:
        records = []
        timestamp = _now_iso()
        items = []
        if isinstance(raw_data, dict):
            items = raw_data.get('data', raw_data.get('records', raw_data.get('list', [])))
        elif isinstance(raw_data, list):
            items = raw_data
        if not isinstance(items, list):
            return []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                records.append({
                    'code': str(item.get('code', item.get('symbol', item.get('代码', '')))),
                    'name': str(item.get('name', item.get('名称', ''))),
                    'price': _parse_float(item.get('price', item.get('最新价', item.get('close', 0)))),
                    'pre_close': _parse_float(item.get('pre_close', item.get('昨收', 0))),
                    'open': _parse_float(item.get('open', item.get('今开', 0))),
                    'high': _parse_float(item.get('high', item.get('最高', 0))),
                    'low': _parse_float(item.get('low', item.get('最低', 0))),
                    'volume': _parse_int(item.get('volume', item.get('成交量', 0))),
                    'amount': _parse_float(item.get('amount', item.get('成交额', 0))),
                    'change_pct': _parse_float(item.get('change_pct', item.get('涨跌幅', 0))),
                    'premium': _parse_float(item.get('premium', item.get('溢价率', 0))),
                    'nav': _parse_float(item.get('nav', item.get('单位净值', 0))),
                    'total_mv': _parse_float(item.get('total_mv', item.get('总市值', 0))),
                    'data_type': data_type,
                    'timestamp': timestamp,
                })
            except (ValueError, TypeError):
                continue
        return records

    def _parse_kline(self, raw_data: Any, data_type: str = 'etf_kline') -> List[Dict[str, Any]]:
        records = []
        timestamp = _now_iso()
        raw_records = None
        if isinstance(raw_data, list):
            raw_records = raw_data
        elif isinstance(raw_data, dict):
            raw_records = raw_data.get('records', raw_data.get('klines', raw_data.get('data', [])))
        if not raw_records:
            return []
        for item in raw_records:
            if not isinstance(item, dict):
                continue
            try:
                records.append({
                    'date': _parse_date(item.get('date', item.get('日期', item.get('time', '')))),
                    'close': _parse_float(item.get('close', item.get('收盘', 0))),
                    'open': _parse_float(item.get('open', item.get('开盘', 0))),
                    'high': _parse_float(item.get('high', item.get('最高', 0))),
                    'low': _parse_float(item.get('low', item.get('最低', 0))),
                    'volume': _parse_int(item.get('volume', item.get('成交量', 0))),
                    'amount': _parse_float(item.get('amount', item.get('成交额', 0))),
                    'nav': _parse_float(item.get('nav', item.get('单位净值', 0))),
                    'data_type': data_type,
                    'timestamp': timestamp,
                })
            except (ValueError, TypeError):
                continue
        return records

    def _parse_nav(self, raw_data: Any, data_type: str = 'etf_nav') -> List[Dict[str, Any]]:
        records = []
        if isinstance(raw_data, dict):
            records.append({
                'code': str(raw_data.get('code', '')),
                'name': str(raw_data.get('name', '')),
                'nav': _parse_float(raw_data.get('nav', 0)),
                'acc_nav': _parse_float(raw_data.get('acc_nav', 0)),
                'nav_date': str(raw_data.get('nav_date', raw_data.get('date', ''))),
                'data_type': data_type,
                'timestamp': _now_iso(),
            })
        return records

    def _parse_holdings(self, raw_data: Any, data_type: str = 'etf_holdings') -> List[Dict[str, Any]]:
        records = []
        if isinstance(raw_data, dict):
            holdings = raw_data.get('holdings', [])
            records.append({
                'code': str(raw_data.get('code', '')),
                'name': str(raw_data.get('name', '')),
                'holdings': holdings,
                'count': len(holdings),
                'data_type': data_type,
                'timestamp': _now_iso(),
            })
        return records
