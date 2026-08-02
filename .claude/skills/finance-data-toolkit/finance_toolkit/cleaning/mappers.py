"""
L2 字段映射清洗器
多数据源字段对齐到统一标准
"""

import re
from typing import Dict

from .pipeline import BaseCleaner, CleanLevel, CleanResult


class FieldMapper(BaseCleaner):
    """L2: 字段映射 - 多数据源字段对齐"""
    
    level = CleanLevel.L2_MAPPING
    source_types = ['quote', 'kline', 'financial', 'news']
    
    # 多数据源 -> 统一字段映射配置
    SOURCE_MAPPING = {
        'quote': {
            'akshare': {
                '代码': 'symbol',
                '名称': 'name',
                '最新价': 'price',
                '涨跌幅': 'pct_chg',
                '涨跌额': 'change',
                '成交量': 'volume',
                '成交额': 'amount',
                '换手率': 'turnover_rate',
                '振幅': 'amplitude',
                '最高': 'high',
                '最低': 'low',
                '今开': 'open',
                '昨收': 'pre_close',
                '量比': 'volume_ratio',
                '市盈率-动态': 'pe_ttm',
                '市净率': 'pb',
                '总市值': 'total_mv',
                '流通市值': 'circ_mv',
            },
            'tushare': {
                'ts_code': 'symbol',
                'trade_date': 'date',
                'open': 'open',
                'high': 'high',
                'low': 'low',
                'close': 'close',
                'pre_close': 'pre_close',
                'vol': 'volume',
                'amount': 'amount',
                'pct_chg': 'pct_chg',
                'change': 'change',
            },
            'eastmoney': {
                'f12': 'symbol',
                'f14': 'name',
                'f43': 'price',  # 需 /100
                'f170': 'pct_chg',  # 需 /100
                'f171': 'change',  # 需 /100
                'f47': 'volume',
                'f48': 'amount',
                'f168': 'turnover_rate',
                'f169': 'amplitude',
                'f15': 'high',  # 需 /100
                'f16': 'low',  # 需 /100
                'f17': 'open',  # 需 /100
                'f60': 'pre_close',  # 需 /100
                'f184': 'pe_ttm',
                'f185': 'pb',
                'f116': 'total_mv',
                'f117': 'circ_mv',
            },
            'sina': {
                'symbol': 'symbol',
                'name': 'name',
                'price': 'price',
                'changepercent': 'pct_chg',
                'change': 'change',
                'volume': 'volume',
                'amount': 'amount',
                'turnover': 'turnover_rate',
                'high': 'high',
                'low': 'low',
                'open': 'open',
                'pre_close': 'pre_close',
            },
        },
        'kline': {
            'akshare': {
                '日期': 'date',
                '开盘': 'open',
                '收盘': 'close',
                '最高': 'high',
                '最低': 'low',
                '成交量': 'volume',
                '成交额': 'amount',
                '振幅': 'amplitude',
                '涨跌幅': 'pct_chg',
                '涨跌额': 'change',
                '换手率': 'turnover_rate',
            },
            'tushare': {
                'ts_code': 'symbol',
                'trade_date': 'date',
                'open': 'open',
                'high': 'high',
                'low': 'low',
                'close': 'close',
                'pre_close': 'pre_close',
                'vol': 'volume',
                'amount': 'amount',
                'pct_chg': 'pct_chg',
                'change': 'change',
            },
            'sina': {
                'day': 'date',
                'open': 'open',
                'high': 'high',
                'low': 'low',
                'close': 'close',
                'volume': 'volume',
                'amount': 'amount',
            },
        },
        'financial': {
            'akshare': {
                '报告期': 'report_date',
                '报告类型': 'report_type',
                '营业收入': 'revenue',
                '净利润': 'net_profit',
                '基本每股收益': 'eps',
                '净资产收益率': 'roe',
                '毛利率': 'gross_margin',
                '营业收入同比': 'revenue_yoy',
                '净利润同比': 'net_profit_yoy',
                '总资产': 'total_assets',
                '总负债': 'total_liab',
                '净资产': 'equity',
            },
            'tushare': {
                'end_date': 'report_date',
                'report_type': 'report_type',
                'revenue': 'revenue',
                'n_income': 'net_profit',
                'basic_eps': 'eps',
                'roe': 'roe',
                'grossprofit_margin': 'gross_margin',
                'total_assets': 'total_assets',
                'total_liab': 'total_liab',
                'total_hldr_eqy': 'equity',
            },
        },
        'news': {
            'sina': {
                'docid': 'news_id',
                'title': 'title',
                'intro': 'summary',
                'url': 'url',
                'source': 'author',
                'channel': 'category',
                'ctime': 'publish_time',
                'keywords': 'keywords',
            },
            'cls': {
                'id': 'news_id',
                'title': 'title',
                'descr': 'summary',
                'content': 'content',
                'time': 'publish_time',
                'important': 'importance',
            },
            'wallstreetcn': {
                'id': 'news_id',
                'title': 'title',
                'summary': 'summary',
                'content': 'content',
                'uri': 'url',
                'display_time': 'publish_time',
                'author': 'author',
                'tags': 'keywords',
            },
        },
    }
    
    # 特殊转换规则：字段名 -> 转换函数
    TRANSFORM_RULES = {
        'eastmoney': {
            'f43': lambda v: v / 100 if v else None,  # 价格
            'f170': lambda v: v / 100 if v else None,  # 涨跌幅
            'f171': lambda v: v / 100 if v else None,  # 涨跌额
            'f15': lambda v: v / 100 if v else None,  # 最高
            'f16': lambda v: v / 100 if v else None,  # 最低
            'f17': lambda v: v / 100 if v else None,  # 开盘
            'f60': lambda v: v / 100 if v else None,  # 昨收
        },
    }
    
    def clean(self, raw_data: Dict) -> CleanResult:
        source = raw_data.get('source')
        data_type = raw_data.get('data_type')
        payload = raw_data.get('payload', {})
        
        if not source or not data_type:
            return CleanResult(data=raw_data, level=self.level, passed=True)
        
        mapping = self.SOURCE_MAPPING.get(data_type, {}).get(source, {})
        if not mapping:
            return CleanResult(data=raw_data, level=self.level, passed=True)
        
        transform_rules = self.TRANSFORM_RULES.get(source, {})
        
        mapped = {}
        for src_field, std_field in mapping.items():
            if src_field in payload:
                val = payload[src_field]
                # 应用特殊转换
                if src_field in transform_rules:
                    try:
                        val = transform_rules[src_field](val)
                    except Exception:
                        pass
                mapped[std_field] = val
        
        # 合并：映射字段优先，保留原有标准字段
        payload = {**payload, **mapped}
        raw_data['payload'] = payload
        
        return CleanResult(data=raw_data, level=self.level, passed=True)


class SymbolNormalizer(BaseCleaner):
    """L2: 股票代码标准化 (统一为 000001.SZ 格式)"""
    
    level = CleanLevel.L2_MAPPING
    source_types = ['quote', 'kline', 'financial', 'news', 'guba']
    
    def clean(self, raw_data: Dict) -> CleanResult:
        payload = raw_data.get('payload', {})
        
        # 处理 symbol 字段
        if 'symbol' in payload:
            payload['symbol'] = self._normalize_symbol(payload['symbol'])
        
        # 处理 symbols 列表 (新闻/股吧)
        if 'symbols' in payload and isinstance(payload['symbols'], list):
            payload['symbols'] = [self._normalize_symbol(s) for s in payload['symbols']]
        
        raw_data['payload'] = payload
        return CleanResult(data=raw_data, level=self.level, passed=True)
    
    def _normalize_symbol(self, symbol: str) -> str:
        """标准化股票代码为 000001.SZ 格式"""
        if not symbol:
            return ''
        
        symbol = str(symbol).strip().upper()
        
        # 已是标准格式
        if re.match(r'^\d{6}\.(SZ|SH|BJ)$', symbol):
            return symbol
        
        # 纯 6 位数字
        if re.match(r'^\d{6}$', symbol):
            code = symbol
            if code.startswith(('6', '9')):
                return f"{code}.SH"
            elif code.startswith(('0', '3', '2')):
                return f"{code}.SZ"
            elif code.startswith(('4', '8')):
                return f"{code}.BJ"
            return f"{code}.SZ"  # 默认深市
        
        # SH/SZ + 6位
        if re.match(r'^(SH|SZ|BJ)\d{6}$', symbol):
            return f"{symbol[2:]}.{symbol[:2]}"
        
        # 返回原值
        return symbol