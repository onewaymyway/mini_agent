# -*- coding: utf-8 -*-
"""
基金净值解析器
支持: fund_nav / fund_holdings / fund_rank / fund_info / fund_history
"""

import re
import json
import logging
from typing import Any, Dict, List
from .abstract import DataParser, register_parser, _parse_float, _parse_int, _parse_date, _now_iso

logger = logging.getLogger(__name__)


@register_parser
class FundNavParser(DataParser):
    """基金净值/持仓/排名/信息/历史解析器"""

    @property
    def source_name(self) -> str:
        return 'fund'

    @property
    def supported_data_types(self) -> List[str]:
        return ['fund_nav', 'fund_holdings', 'fund_rank', 'fund_info', 'fund_history']

    def parse(self, raw_data: Any, data_type: str, **kwargs) -> List[Dict[str, Any]]:
        if data_type == 'fund_nav':
            return self._parse_nav(raw_data)
        elif data_type == 'fund_holdings':
            return self._parse_holdings(raw_data)
        elif data_type == 'fund_rank':
            return self._parse_rank(raw_data)
        elif data_type == 'fund_info':
            return self._parse_info(raw_data)
        elif data_type == 'fund_history':
            return self._parse_history(raw_data)
        return []

    def _parse_nav(self, raw_data: Any) -> List[Dict[str, Any]]:
        results = []
        if isinstance(raw_data, dict):
            results.append({
                'code': str(raw_data.get('code', '')),
                'name': str(raw_data.get('name', '')),
                'nav': _parse_float(raw_data.get('nav', 0)),
                'acc_nav': _parse_float(raw_data.get('acc_nav', 0)),
                'nav_date': str(raw_data.get('nav_date', raw_data.get('date', ''))),
                'data_type': 'fund_nav',
                'timestamp': _now_iso(),
            })
        elif isinstance(raw_data, str):
            # JS 文件字符串格式
            info = {}
            name_m = re.search(r'fS_name\s*=\s*["\']([^"\']+)["\']', raw_data)
            if name_m:
                info['name'] = name_m.group(1)
            code_m = re.search(r'fS_code\s*=\s*["\']([^"\']+)["\']', raw_data)
            if code_m:
                info['code'] = code_m.group(1)
            nav_m = re.search(r'Data_netWorthTrend\s*=\s*\[(.+?)\];', raw_data, re.DOTALL)
            if nav_m:
                nav_pat = r'\{"x":"([^"]+)","y":"([^"]+)"'
                nav_matches = re.findall(nav_pat, nav_m.group(1))
                if nav_matches:
                    info['nav_date'] = nav_matches[-1][0]
                    info['nav'] = _parse_float(nav_matches[-1][1])
            acc_m = re.search(r'Data_ACWorthTrend\s*=\s*\[(.+?)\];', raw_data, re.DOTALL)
            if acc_m:
                acc_pat = r'\["([^"]+)",([\d.]+)'
                acc_matches = re.findall(acc_pat, acc_m.group(1))
                if acc_matches:
                    info['acc_nav'] = _parse_float(acc_matches[-1][1])
            if info:
                results.append({**info, 'data_type': 'fund_nav', 'timestamp': _now_iso()})
        return results

    def _parse_holdings(self, raw_data: Any) -> List[Dict[str, Any]]:
        results = []
        if isinstance(raw_data, dict):
            holdings = raw_data.get('holdings', [])
            results.append({
                'symbol': raw_data.get('symbol', ''),
                'code': raw_data.get('code', ''),
                'holdings': holdings,
                'count': len(holdings),
                'data_type': 'fund_holdings',
                'timestamp': _now_iso(),
            })
        return results

    def _parse_rank(self, raw_data: Any) -> List[Dict[str, Any]]:
        results = []
        if isinstance(raw_data, dict):
            funds = raw_data.get('funds', [])
            total = raw_data.get('total', len(funds))
            for f in funds[:50]:
                if isinstance(f, dict):
                    results.append({
                        'code': str(f.get('code', '')),
                        'name': str(f.get('name', '')),
                        'nav': str(f.get('nav', '')),
                        'acc_nav': str(f.get('acc_nav', '')),
                        'daily_return': str(f.get('daily_return', '')),
                        'return_1m': str(f.get('return_1m', '')),
                        'return_3m': str(f.get('return_3m', '')),
                        'return_6m': str(f.get('return_6m', '')),
                        'return_1y': str(f.get('return_1y', '')),
                        'return_3y': str(f.get('return_3y', '')),
                        'fund_type': str(f.get('fund_type', '')),
                        'date': str(f.get('date', '')),
                        'data_type': 'fund_rank',
                        'timestamp': _now_iso(),
                    })
            results.append({
                'data_type': 'fund_rank',
                'count': len(results),
                'total': total,
                'records': results[:20],
                'timestamp': _now_iso(),
            })
        elif isinstance(raw_data, list):
            for item in raw_data[:50]:
                if isinstance(item, dict):
                    results.append({
                        'code': str(item.get('code', '')),
                        'name': str(item.get('name', '')),
                        'nav': str(item.get('nav', '')),
                        'daily_return': str(item.get('daily_return', '')),
                        'return_1y': str(item.get('return_1y', '')),
                        'data_type': 'fund_rank',
                        'timestamp': _now_iso(),
                    })
        return results

    def _parse_info(self, raw_data: Any) -> List[Dict[str, Any]]:
        results = []
        if isinstance(raw_data, dict):
            results.append({
                'code': str(raw_data.get('code', '')),
                'name': str(raw_data.get('name', '')),
                'type': str(raw_data.get('type', raw_data.get('fund_type', ''))),
                'company': str(raw_data.get('company', '')),
                'manager': str(raw_data.get('manager', '')),
                'establish_date': str(raw_data.get('establish_date', raw_data.get('成立日期', ''))),
                'size': str(raw_data.get('size', raw_data.get('基金规模', ''))),
                'min_purchase': str(raw_data.get('min_purchase', '')),
                'data_type': 'fund_info',
                'timestamp': _now_iso(),
            })
        elif isinstance(raw_data, str):
            info = {}
            name_m = re.search(r'fS_name\s*=\s*["\']([^"\']+)["\']', raw_data)
            if name_m:
                info['name'] = name_m.group(1)
            code_m = re.search(r'fS_code\s*=\s*["\']([^"\']+)["\']', raw_data)
            if code_m:
                info['code'] = code_m.group(1)
            type_m = re.search(r'fund_Type\s*=\s*["\']([^"\']+)["\']', raw_data)
            if type_m:
                info['type'] = type_m.group(1)
            company_m = re.search(r'fund_Company\s*=\s*["\']([^"\']+)["\']', raw_data)
            if company_m:
                info['company'] = company_m.group(1)
            manager_m = re.search(r'fund_Manager\s*=\s*["\']([^"\']+)["\']', raw_data)
            if manager_m:
                info['manager'] = manager_m.group(1)
            date_m = re.search(r'fund_EstablishDate\s*=\s*["\']([^"\']+)["\']', raw_data)
            if date_m:
                info['establish_date'] = date_m.group(1)
            size_m = re.search(r'fund_Scale\s*=\s*["\']([^"\']+)["\']', raw_data)
            if size_m:
                info['size'] = size_m.group(1)
            if info:
                results.append({**info, 'data_type': 'fund_info', 'timestamp': _now_iso()})
        return results

    def _parse_history(self, raw_data: Any) -> List[Dict[str, Any]]:
        results = []
        if isinstance(raw_data, dict):
            records = raw_data.get('records', [])
            if isinstance(records, list):
                # 取最近100条
                for rec in records[:100]:
                    if isinstance(rec, dict):
                        results.append({
                            'date': str(rec.get('date', '')),
                            'nav': _parse_float(rec.get('nav', 0)),
                            'acc_nav': _parse_float(rec.get('acc_nav', 0)),
                            'equity_return': _parse_float(rec.get('equity_return', rec.get('equityReturn', 0))),
                            'data_type': 'fund_history',
                            'timestamp': _now_iso(),
                        })
            return [{
                'symbol': raw_data.get('symbol', ''),
                'count': len(results),
                'start': results[-1]['date'] if results else '',
                'end': results[0]['date'] if results else '',
                'records': results,
                'data_type': 'fund_history',
                'timestamp': _now_iso(),
            }]
        return results
