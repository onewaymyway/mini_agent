# -*- coding: utf-8 -*-
"""
行情解析器
支持: tencent/sina/eastmoney/akshare/stock_quote/etf_quote 等多种格式
"""

import re
import logging
from typing import Any, Dict, List
from .abstract import DataParser, register_parser, _parse_float, _parse_int, _parse_date, _now_iso

logger = logging.getLogger(__name__)


@register_parser
class QuoteParser(DataParser):
    """行情数据解析器"""

    @property
    def source_name(self) -> str:
        return 'quote'

    @property
    def supported_data_types(self) -> List[str]:
        return ['quote', 'tencent_quote', 'sina_quote', 'eastmoney_quote', 'akshare_quote', 'stock_quote', 'etf_quote']

    def parse(self, raw_data: Any, data_type: str, **kwargs) -> List[Dict[str, Any]]:
        if data_type in ('tencent_quote', 'sina_quote'):
            return self._parse_text_format(raw_data, data_type)
        elif data_type == 'eastmoney_quote':
            return self._parse_eastmoney(raw_data)
        elif data_type in ('akshare_quote', 'stock_quote', 'etf_quote', 'quote'):
            return self._parse_dict_format(raw_data, data_type)
        return []

    def _parse_text_format(self, text: str, fmt: str) -> List[Dict[str, Any]]:
        if not isinstance(text, str) or not text.strip():
            return []
        results = []
        timestamp = _now_iso()
        try:
            if fmt == 'tencent_quote':
                for line in text.strip().split(';'):
                    if '=' not in line:
                        continue
                    var_part, data_part = line.split('=', 1)
                    code = re.sub(r'var\s+hq_str_\s*', '', var_part).strip().replace('"', '')
                    data_str = data_part.strip().strip('"')
                    if not data_str:
                        continue
                    fields = data_str.split('~')
                    if len(fields) < 32:
                        continue
                    try:
                        results.append({
                            'code': fields[2] if len(fields) > 2 else code,
                            'name': fields[1] if len(fields) > 1 else '',
                            'price': _parse_float(fields[3]),
                            'pre_close': _parse_float(fields[4]),
                            'open': _parse_float(fields[5]),
                            'high': _parse_float(fields[6]),
                            'low': _parse_float(fields[7]),
                            'volume': _parse_int(fields[8]),
                            'amount': _parse_float(fields[9]),
                            'change_pct': _parse_float(fields[30]) if len(fields) > 30 else 0.0,
                            'change_amt': _parse_float(fields[31]) if len(fields) > 31 else 0.0,
                            'timestamp': timestamp,
                        })
                    except (ValueError, IndexError):
                        continue
            elif fmt == 'sina_quote':
                for line in text.strip().split('\n'):
                    if '=' not in line:
                        continue
                    var_part, data_part = line.split('=', 1)
                    code = re.sub(r'var\s+hq_str_\s*', '', var_part).strip().replace('"', '')
                    data_str = data_part.strip().strip('"')
                    if not data_str:
                        continue
                    parts = data_str.split(',')
                    if len(parts) < 32:
                        continue
                    try:
                        results.append({
                            'code': code.replace('sh', '').replace('sz', ''),
                            'name': parts[0] if parts else '',
                            'price': _parse_float(parts[3]),
                            'pre_close': _parse_float(parts[2]),
                            'open': _parse_float(parts[1]),
                            'high': _parse_float(parts[4]),
                            'low': _parse_float(parts[5]),
                            'volume': _parse_int(parts[8]),
                            'amount': _parse_float(parts[9]),
                            'change_pct': _parse_float(parts[30]) if len(parts) > 30 else 0.0,
                            'change_amt': _parse_float(parts[31]) if len(parts) > 31 else 0.0,
                            'timestamp': timestamp,
                        })
                    except (ValueError, IndexError):
                        continue
        except Exception as e:
            logger.error(f"{fmt} 解析异常: {e}")
        return results

    def _parse_eastmoney(self, data: Any) -> List[Dict[str, Any]]:
        if not isinstance(data, dict) or 'data' not in data:
            return []
        d = data['data']
        if isinstance(d, dict):
            return [{
                'code': str(d.get('f57', '')),
                'name': str(d.get('f169', '')),
                'price': _parse_float(d.get('f43')),
                'pre_close': _parse_float(d.get('f47')),
                'open': _parse_float(d.get('f46')),
                'high': _parse_float(d.get('f44')),
                'low': _parse_float(d.get('f45')),
                'volume': _parse_int(d.get('f57')),
                'amount': _parse_float(d.get('f58')),
                'change_pct': _parse_float(d.get('f170')),
                'change_amt': _parse_float(d.get('f171')),
                'pe': _parse_float(d.get('f49')),
                'pb': _parse_float(d.get('f50')),
                'total_mv': _parse_float(d.get('f60')),
                'float_mv': _parse_float(d.get('f61')),
                'timestamp': _now_iso(),
            }]
        return []

    def _parse_dict_format(self, raw_data: Any, data_type: str) -> List[Dict[str, Any]]:
        records = []
        timestamp = _now_iso()
        if raw_data is None:
            return []
        # pandas DataFrame
        if hasattr(raw_data, 'to_dict'):
            try:
                for _, row in raw_data.iterrows():
                    r = dict(row)
                    records.append({
                        'symbol': r.get('symbol', r.get('代码', '')),
                        'name': r.get('名称', r.get('name', '')),
                        'price': _parse_float(r.get('最新价', r.get('price', 0))),
                        'pre_close': _parse_float(r.get('昨收', r.get('pre_close', 0))),
                        'open': _parse_float(r.get('今开', r.get('open', 0))),
                        'high': _parse_float(r.get('最高', r.get('high', 0))),
                        'low': _parse_float(r.get('最低', r.get('low', 0))),
                        'volume': _parse_int(r.get('成交量', r.get('volume', 0))),
                        'amount': _parse_float(r.get('成交额', r.get('amount', 0))),
                        'change_pct': _parse_float(r.get('涨跌幅', r.get('change_pct', 0))),
                        'change_amt': _parse_float(r.get('涨跌额', r.get('change_amt', 0))),
                        'turnover': _parse_float(r.get('换手率', r.get('turnover', 0))),
                        'pe': _parse_float(r.get('市盈率-动态', r.get('pe', 0))),
                        'pb': _parse_float(r.get('市净率', r.get('pb', 0))),
                        'total_mv': _parse_float(r.get('总市值', r.get('total_mv', 0))),
                        'circ_mv': _parse_float(r.get('流通市值', r.get('circ_mv', 0))),
                        'data_type': data_type,
                        'timestamp': timestamp,
                    })
                return records
            except Exception as e:
                logger.warning(f"DataFrame 解析失败: {e}")
                return []
        # dict with nested lists
        if isinstance(raw_data, dict):
            items = raw_data.get('quotes', raw_data.get('records', raw_data.get('data', [])))
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        records.append({
                            'symbol': item.get('symbol', item.get('code', '')),
                            'name': item.get('name', item.get('名称', '')),
                            'price': _parse_float(item.get('price', item.get('最新价', 0))),
                            'pre_close': _parse_float(item.get('pre_close', item.get('昨收', 0))),
                            'open': _parse_float(item.get('open', item.get('今开', 0))),
                            'high': _parse_float(item.get('high', item.get('最高', 0))),
                            'low': _parse_float(item.get('low', item.get('最低', 0))),
                            'volume': _parse_int(item.get('volume', item.get('成交量', 0))),
                            'amount': _parse_float(item.get('amount', item.get('成交额', 0))),
                            'change_pct': _parse_float(item.get('change_pct', item.get('涨跌幅', 0))),
                            'change_amt': _parse_float(item.get('change_amt', item.get('涨跌额', 0))),
                            'turnover': _parse_float(item.get('turnover', item.get('换手率', 0))),
                            'pe': _parse_float(item.get('pe', item.get('市盈率', 0))),
                            'pb': _parse_float(item.get('pb', item.get('市净率', 0))),
                            'total_mv': _parse_float(item.get('total_mv', item.get('总市值', 0))),
                            'circ_mv': _parse_float(item.get('circ_mv', item.get('流通市值', 0))),
                            'data_type': data_type,
                            'timestamp': timestamp,
                        })
        # plain list of dicts
        elif isinstance(raw_data, list):
            for item in raw_data:
                if isinstance(item, dict):
                    records.append({
                        'symbol': item.get('symbol', item.get('code', '')),
                        'name': item.get('name', item.get('名称', '')),
                        'price': _parse_float(item.get('price', item.get('最新价', 0))),
                        'pre_close': _parse_float(item.get('pre_close', item.get('昨收', 0))),
                        'open': _parse_float(item.get('open', item.get('今开', 0))),
                        'high': _parse_float(item.get('high', item.get('最高', 0))),
                        'low': _parse_float(item.get('low', item.get('最低', 0))),
                        'volume': _parse_int(item.get('volume', item.get('成交量', 0))),
                        'amount': _parse_float(item.get('amount', item.get('成交额', 0))),
                        'change_pct': _parse_float(item.get('change_pct', item.get('涨跌幅', 0))),
                        'change_amt': _parse_float(item.get('change_amt', item.get('涨跌额', 0))),
                        'turnover': _parse_float(item.get('turnover', item.get('换手率', 0))),
                        'pe': _parse_float(item.get('pe', item.get('市盈率', 0))),
                        'pb': _parse_float(item.get('pb', item.get('市净率', 0))),
                        'total_mv': _parse_float(item.get('total_mv', item.get('总市值', 0))),
                        'circ_mv': _parse_float(item.get('circ_mv', item.get('流通市值', 0))),
                        'data_type': data_type,
                        'timestamp': timestamp,
                    })
        return records
