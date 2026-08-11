# -*- coding: utf-8 -*-
"""
数据解析器
提供统一的数据解析和转换功能
"""

import logging
from datetime import datetime
from typing import List, Dict, Any, Optional, Callable

logger = logging.getLogger(__name__)


class DataParser:
    """数据解析器基类"""
    
    def __init__(self):
        self.parsers: Dict[str, Callable] = {}
    
    def register_parser(self, data_type: str, parser_func: Callable):
        """注册数据解析函数"""
        self.parsers[data_type] = parser_func
    
    def parse(self, data_type: str, raw_data: Any) -> Optional[Dict[str, Any]]:
        """解析数据"""
        parser = self.parsers.get(data_type)
        if parser:
            return parser(raw_data)
        logger.warning(f"未找到 {data_type} 的解析器")
        return None


def parse_tencent_quote(text: str) -> List[Dict[str, Any]]:
    """解析腾讯行情数据"""
    results = []
    
    for line in text.strip().split(';'):
        if '=' not in line:
            continue
        
        var_part, data_part = line.split('=', 1)
        code = var_part.strip().replace('var hq_str_', '').replace('"', '')
        data_str = data_part.strip().strip('"')
        
        if not data_str:
            continue
        
        fields = data_str.split('~')
        if len(fields) < 35:
            continue
        
        try:
            results.append({
                'code': fields[2] if len(fields) > 2 else code,
                'name': fields[1] if len(fields) > 1 else '',
                'price': float(fields[3]) if fields[3] else 0.0,
                'pre_close': float(fields[4]) if len(fields) > 4 and fields[4] else 0.0,
                'open': float(fields[5]) if len(fields) > 5 and fields[5] else 0.0,
                'high': float(fields[6]) if len(fields) > 6 and fields[6] else 0.0,
                'low': float(fields[7]) if len(fields) > 7 and fields[7] else 0.0,
                'volume': int(float(fields[8])) if len(fields) > 8 and fields[8] else 0,
                'amount': float(fields[9]) if len(fields) > 9 and fields[9] else 0.0,
                'change_pct': float(fields[30]) if len(fields) > 30 and fields[30] else 0.0,
                'change_amt': float(fields[31]) if len(fields) > 31 and fields[31] else 0.0,
                'timestamp': datetime.utcnow().isoformat(),
            })
        except (ValueError, IndexError) as e:
            logger.warning(f"解析腾讯行情数据失败 {code}: {e}")
            continue
    
    return results


def parse_sina_quote(text: str) -> List[Dict[str, Any]]:
    """解析新浪行情数据"""
    results = []
    
    for line in text.strip().split('\n'):
        if '=' not in line:
            continue
        
        var_part, data_part = line.split('=', 1)
        code = var_part.strip().replace('var hq_str_', '').replace('"', '')
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
                'price': float(parts[3]) if parts[3] else 0.0,
                'pre_close': float(parts[2]) if parts[2] else 0.0,
                'open': float(parts[1]) if parts[1] else 0.0,
                'high': float(parts[4]) if parts[4] else 0.0,
                'low': float(parts[5]) if parts[5] else 0.0,
                'volume': int(float(parts[8])) if len(parts) > 8 and parts[8] else 0,
                'amount': float(parts[9]) if len(parts) > 9 and parts[9] else 0.0,
                'change_pct': float(parts[30]) if len(parts) > 30 and parts[30] else 0.0,
                'change_amt': float(parts[31]) if len(parts) > 31 and parts[31] else 0.0,
                'timestamp': datetime.utcnow().isoformat(),
            })
        except (ValueError, IndexError) as e:
            logger.warning(f"解析新浪行情数据失败 {code}: {e}")
            continue
    
    return results


def parse_eastmoney_quote(data: Dict[str, Any]) -> Dict[str, Any]:
    """解析东方财富行情数据"""
    if not data or 'data' not in data:
        return {}
    
    d = data['data']
    
    return {
        'code': d.get('f57', ''),
        'name': d.get('f169', ''),
        'price': float(d.get('f43', 0) or 0),
        'pre_close': float(d.get('f47', 0) or 0),
        'open': float(d.get('f46', 0) or 0),
        'high': float(d.get('f44', 0) or 0),
        'low': float(d.get('f45', 0) or 0),
        'volume': int(d.get('f57', 0) or 0),
        'amount': float(d.get('f58', 0) or 0),
        'change_pct': float(d.get('f170', 0) or 0),
        'change_amt': float(d.get('f171', 0) or 0),
        'pe': float(d.get('f49', 0) or 0),
        'pb': float(d.get('f50', 0) or 0),
        'total_mv': float(d.get('f60', 0) or 0),
        'float_mv': float(d.get('f61', 0) or 0),
        'timestamp': datetime.utcnow().isoformat(),
    }


def parse_kline_data(data: List[Dict[str, Any]], source: str = 'unknown') -> List[Dict[str, Any]]:
    """解析K线数据"""
    results = []
    
    for item in data:
        try:
            results.append({
                'date': item.get('date', ''),
                'open': float(item.get('open', 0) or 0),
                'close': float(item.get('close', 0) or 0),
                'high': float(item.get('high', 0) or 0),
                'low': float(item.get('low', 0) or 0),
                'volume': int(item.get('volume', 0) or 0),
                'amount': float(item.get('amount', 0) or 0),
                'source': source,
                'timestamp': datetime.utcnow().isoformat(),
            })
        except (ValueError, TypeError) as e:
            logger.warning(f"解析K线数据失败: {e}")
            continue
    
    return results


def parse_news_data(data: Dict[str, Any], source: str = 'unknown') -> List[Dict[str, Any]]:
    """解析新闻数据"""
    results = []
    
    news_list = data.get('list', [])
    if not news_list:
        news_list = data.get('data', {}).get('list', []) if isinstance(data.get('data'), dict) else []
    
    for item in news_list:
        try:
            results.append({
                'title': item.get('title', ''),
                'url': item.get('url', ''),
                'source': source,
                'publish_time': item.get('ctime', item.get('time', item.get('datetime', ''))),
                'content': item.get('digest', item.get('intro', '')),
                'timestamp': datetime.utcnow().isoformat(),
            })
        except Exception as e:
            logger.warning(f"解析新闻数据失败: {e}")
            continue
    
    return results


def parse_sector_data(data: Dict[str, Any], data_type: str = 'quote') -> List[Dict[str, Any]]:
    """解析板块数据"""
    results = []
    
    if data_type == 'quote':
        items = data.get('data', {}).get('diff', []) if 'data' in data else []
        for item in items:
            try:
                results.append({
                    'code': item.get('f12', ''),
                    'name': item.get('f14', ''),
                    'price': float(item.get('f2', 0) or 0),
                    'change_pct': float(item.get('f3', 0) or 0),
                    'volume': int(item.get('f5', 0) or 0),
                    'amount': float(item.get('f6', 0) or 0),
                    'timestamp': datetime.utcnow().isoformat(),
                })
            except (ValueError, TypeError) as e:
                logger.warning(f"解析板块数据失败: {e}")
                continue
    
    return results


# 创建解析器实例
parser = DataParser()
parser.register_parser('tencent_quote', parse_tencent_quote)
parser.register_parser('sina_quote', parse_sina_quote)
parser.register_parser('eastmoney_quote', parse_eastmoney_quote)
parser.register_parser('kline', parse_kline_data)
parser.register_parser('news', parse_news_data)
parser.register_parser('sector', parse_sector_data)


def parse_data(data_type: str, raw_data: Any, **kwargs) -> Any:
    """通用数据解析函数"""
    return parser.parse(data_type, raw_data)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    
    # 测试腾讯行情解析
    test_text = 'v_sh600000="1~浦发银行~600000~10.50~10.20~10.35~10.55~10.15~10.35~10.36~85643213~885643210~...";'
    result = parse_tencent_quote(test_text)
    print(f"腾讯行情解析结果: {result}")
    
    # 测试K线数据解析
    test_kline = [{'date': '2024-01-01', 'open': 10.0, 'close': 10.5, 'high': 10.8, 'low': 9.8, 'volume': 1000000}]
    result = parse_kline_data(test_kline, 'test')
    print(f"K线解析结果: {result}")
