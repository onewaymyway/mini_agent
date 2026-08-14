# -*- coding: utf-8 -*-
"""
宏观经济解析器
"""
import logging
from typing import Any, Dict, List
from .abstract import DataParser, register_parser, _parse_float, _parse_date, _now_iso

logger = logging.getLogger(__name__)

@register_parser
class MacroParser(DataParser):
    @property
    def source_name(self) -> str:
        return 'macro'

    @property
    def supported_data_types(self) -> List[str]:
        return ['macro', 'gdp', 'cpi', 'pmi', 'interest_rate', 'exchange_rate', 'money_supply', 'unemployment', 'trade']

    def parse(self, raw_data: Any, data_type: str, **kwargs) -> List[Dict[str, Any]]:
        records = []
        timestamp = _now_iso()
        items = []
        if isinstance(raw_data, dict):
            items = raw_data.get('records', raw_data.get('data', raw_data.get('items', [])))
            # 扁平 dict（单条记录）也支持
            if not isinstance(items, list) and 'date' in raw_data:
                items = [raw_data]
        elif isinstance(raw_data, list):
            items = raw_data
        if not isinstance(items, list):
            return []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                rec = {'data_type': data_type, 'timestamp': timestamp}
                if data_type in ('gdp',):
                    rec.update({
                        'quarter': str(item.get('quarter', item.get('quarter_str', ''))),
                        'gdp': _parse_float(item.get('gdp', 0)),
                        'growth_rate': _parse_float(item.get('growth_rate', item.get('增长率', 0))),
                        'per_capita': _parse_float(item.get('per_capita', 0)),
                    })
                elif data_type in ('cpi',):
                    rec.update({
                        'date': _parse_date(item.get('date', item.get('月份', ''))),
                        'cpi': _parse_float(item.get('cpi', 0)),
                        'yoy': _parse_float(item.get('yoy', item.get('同比', 0))),
                        'food': _parse_float(item.get('food', 0)),
                    })
                elif data_type in ('pmi',):
                    rec.update({
                        'date': _parse_date(item.get('date', item.get('月份', ''))),
                        'manufacturing_pmi': _parse_float(item.get('manufacturing_pmi', item.get('制造业PMI', 0))),
                        'non_manufacturing_pmi': _parse_float(item.get('non_manufacturing_pmi', item.get('非制造业PMI', 0))),
                        'new_order_pmi': _parse_float(item.get('new_order_pmi', 0)),
                    })
                elif data_type in ('interest_rate',):
                    rec.update({
                        'date': _parse_date(item.get('date', item.get('日期', ''))),
                        'deposit_rate': _parse_float(item.get('deposit_rate', item.get('存款利率', 0))),
                        'loan_rate': _parse_float(item.get('loan_rate', item.get('贷款利率', 0))),
                        'mlf_rate': _parse_float(item.get('mlf_rate', 0)),
                    })
                elif data_type in ('exchange_rate',):
                    rec.update({
                        'date': _parse_date(item.get('date', item.get('日期', ''))),
                        'usd_cny': _parse_float(item.get('usd_cny', item.get('美元', 0))),
                        'eur_cny': _parse_float(item.get('eur_cny', item.get('欧元', 0))),
                        'jpy_cny': _parse_float(item.get('jpy_cny', item.get('日元', 0))),
                    })
                elif data_type in ('money_supply',):
                    rec.update({
                        'date': _parse_date(item.get('date', item.get('月份', ''))),
                        'm0': _parse_float(item.get('m0', 0)),
                        'm1': _parse_float(item.get('m1', 0)),
                        'm2': _parse_float(item.get('m2', 0)),
                    })
                else:
                    # 通用字段
                    for k, v in item.items():
                        if k not in ('data_type', 'timestamp'):
                            rec[k] = v
                records.append(rec)
            except (ValueError, TypeError):
                continue
        return records
