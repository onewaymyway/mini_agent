# -*- coding: utf-8 -*-
"""
K线解析器
支持: kline / etf_kline / forex_kline / crypto_kline / commodity_kline
"""

import logging
from typing import Any, Dict, List
from .abstract import DataParser, register_parser, _parse_float, _parse_int, _parse_date, _now_iso

logger = logging.getLogger(__name__)


@register_parser
class KlineParser(DataParser):
    """K线数据解析器"""

    @property
    def source_name(self) -> str:
        return 'kline'

    @property
    def supported_data_types(self) -> List[str]:
        return ['kline', 'etf_kline', 'forex_kline', 'crypto_kline', 'commodity_kline', 'index_kline']

    def parse(self, raw_data: Any, data_type: str, **kwargs) -> List[Dict[str, Any]]:
        records = []
        timestamp = _now_iso()
        source = kwargs.get('source', data_type.split('_')[0])
        period = kwargs.get('period', 'daily')
        adjust = kwargs.get('adjust', 'qfq')

        # 尝试从不同嵌套结构中提取 records
        raw_records = None
        if isinstance(raw_data, list):
            raw_records = raw_data
        elif isinstance(raw_data, dict):
            raw_records = (
                raw_data.get('records') or
                raw_data.get('klines') or
                raw_data.get('data') or
                raw_data.get('items') or
                raw_data.get('list')
            )
            if isinstance(raw_records, list):
                pass  # 正常继续
            elif raw_records is not None:
                # 可能是单个记录对象
                raw_records = [raw_records]

        if raw_records is None:
            # pandas DataFrame 兼容
            if hasattr(raw_data, 'to_dict'):
                try:
                    raw_records = raw_data.to_dict('records')
                except Exception:
                    return []

        if not raw_records:
            return []

        for item in raw_records:
            if not isinstance(item, dict):
                continue
            try:
                d = {
                    'date': _parse_date(item.get('date', item.get('日期', item.get('time', '')))),
                    'open': _parse_float(item.get('open', item.get('开盘', 0))),
                    'close': _parse_float(item.get('close', item.get('收盘', 0))),
                    'high': _parse_float(item.get('high', item.get('最高', 0))),
                    'low': _parse_float(item.get('low', item.get('最低', 0))),
                    'volume': _parse_int(item.get('volume', item.get('成交量', 0))),
                    'amount': _parse_float(item.get('amount', item.get('成交额', 0))),
                    'amplitude': _parse_float(item.get('amplitude', item.get('振幅', 0))),
                    'change_pct': _parse_float(item.get('change_pct', item.get('涨跌幅', 0))),
                    'change_amt': _parse_float(item.get('change_amt', item.get('涨跌额', 0))),
                    'turnover': _parse_float(item.get('turnover', item.get('换手率', 0))),
                    'source': source,
                    'period': period,
                    'adjust': adjust,
                    'data_type': data_type,
                    'timestamp': timestamp,
                }
                # 保留额外字段
                extra_keys = {'symbol', 'code', 'name', 'pe', 'pb', 'total_mv', 'circ_mv'}
                for k in extra_keys:
                    if k in item:
                        d[k] = item[k]
                records.append(d)
            except (ValueError, TypeError) as e:
                logger.warning(f"K线记录解析失败: {e}")
                continue

        return records
