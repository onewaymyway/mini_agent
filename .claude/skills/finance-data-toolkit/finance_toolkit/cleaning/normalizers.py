"""
L1 结构标准化清洗器
包括：字段命名规范化、类型强制转换、时间标准化
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from decimal import Decimal, InvalidOperation

from .pipeline import BaseCleaner, CleanLevel, CleanResult


class StructureNormalizer(BaseCleaner):
    """L1: 结构标准化 - 字段命名规范 (snake_case)"""
    
    level = CleanLevel.L1_STRUCTURE
    source_types = ['quote', 'kline', 'financial', 'news', 'guba', 'report']
    
    # 统一字段映射表：标准字段名 -> [可能的源字段名]
    FIELD_MAPPING = {
        # 通用字段
        'symbol': ['symbol', 'code', 'ts_code', 'secid', 'stock_code', 'ticker', 'scode', 'zqdm'],
        'name': ['name', 'stock_name', 'sec_name', 'title', 'sname', 'zqmc'],
        'timestamp': ['timestamp', 'time', 'datetime', 'date', 'trade_date', 'publish_time', 'ctime', 'utime'],
        
        # 行情字段
        'open': ['open', 'open_price', 'o', 'kline_open', 'openprice'],
        'high': ['high', 'high_price', 'h', 'kline_high', 'highprice'],
        'low': ['low', 'low_price', 'l', 'kline_low', 'lowprice'],
        'close': ['close', 'close_price', 'c', 'kline_close', 'price', 'closeprice', 'newprice'],
        'pre_close': ['pre_close', 'prev_close', 'preclose', 'yclose', 'y_close'],
        'volume': ['volume', 'vol', 'v', 'turnover_volume', 'cjl'],
        'amount': ['amount', 'turnover', 'turnover_amount', 'cje'],
        'pct_chg': ['pct_chg', 'change_pct', 'pct_change', 'zdf', 'zdp'],
        'change': ['change', 'price_change', 'zd', 'zde'],
        'turnover_rate': ['turnover_rate', 'turnover', 'hs', 'turn_rate'],
        'amplitude': ['amplitude', 'amp', 'zf'],
        
        # 财务字段
        'report_date': ['report_date', 'rpt_date', 'end_date', 'reportdate'],
        'report_type': ['report_type', 'rpt_type', 'type'],
        'revenue': ['revenue', 'total_revenue', 'income', 'yysr'],
        'net_profit': ['net_profit', 'net_income', 'profit', 'jlr'],
        'eps': ['eps', 'basic_eps', 'diluted_eps', 'mgjzc'],
        'roe': ['roe', 'return_on_equity', 'roe_waa'],
        'pe': ['pe', 'pe_ttm', 'pe_lyr'],
        'pb': ['pb', 'pb_mrq'],
        
        # 新闻字段
        'news_id': ['news_id', 'docid', 'id', 'article_id'],
        'url': ['url', 'link', 'article_url'],
        'author': ['author', 'source', 'writer'],
        'summary': ['summary', 'intro', 'digest', 'abstract'],
        'content': ['content', 'body', 'text', 'article_content'],
        'category': ['category', 'channel', 'classify'],
        'keywords': ['keywords', 'tags', 'key_words'],
        
        # 股吧字段
        'post_id': ['post_id', 'id', 'article_id'],
        'read_count': ['read_count', 'read', 'views', 'click'],
        'comment_count': ['comment_count', 'reply', 'comments', 'pl'],
        'user_id': ['user_id', 'uid', 'author_id'],
        'user_name': ['user_name', 'username', 'author', 'nickname'],
    }
    
    def clean(self, raw_data: Dict) -> CleanResult:
        payload = raw_data.get('payload', {})
        if not payload:
            return CleanResult(data=raw_data, level=self.level, passed=True)
        
        normalized = {}
        issues = []
        
        # 遍历标准字段，尝试从 payload 找到对应值
        for std_field, possible_names in self.FIELD_MAPPING.items():
            value = None
            for name in possible_names:
                if name in payload:
                    value = payload[name]
                    break
            
            if value is not None:
                normalized[std_field] = value
            # 保留原始字段用于追溯
            for name in possible_names:
                if name in payload and name != std_field:
                    normalized[f'_raw_{name}'] = payload[name]
        
        # 复制未映射的字段
        for k, v in payload.items():
            if k not in normalized and not k.startswith('_'):
                normalized[k] = v
        
        raw_data['payload'] = normalized
        return CleanResult(data=raw_data, level=self.level, passed=True, issues=issues)


class TypeCoercer(BaseCleaner):
    """L1: 类型强制转换"""
    
    level = CleanLevel.L1_STRUCTURE
    source_types = ['quote', 'kline', 'financial']
    
    # 字段类型定义
    TYPE_SPEC = {
        'quote': {
            'float': ['open', 'high', 'low', 'close', 'pre_close', 'price',
                       'pct_chg', 'change', 'turnover_rate', 'amplitude',
                       'amount', 'volume', 'bid1', 'ask1', 'bid1_vol', 'ask1_vol'],
            'int': ['volume', 'amount', 'turnover_volume', 'turnover_amount'],
            'str': ['symbol', 'name'],
        },
        'kline': {
            'float': ['open', 'high', 'low', 'close', 'pre_close',
                       'pct_chg', 'change', 'turnover_rate', 'amplitude',
                       'ma5', 'ma10', 'ma20', 'ma60', 'vol_ma5', 'vol_ma10'],
            'int': ['volume', 'amount', 'date'],
            'str': ['symbol', 'period'],
        },
        'financial': {
            'float': ['revenue', 'net_profit', 'eps', 'roe', 'pe', 'pb',
                       'total_assets', 'total_liab', 'equity', 'cash_flow'],
            'int': ['report_date'],
            'str': ['symbol', 'report_type'],
        },
    }
    
    def clean(self, raw_data: Dict) -> CleanResult:
        payload = raw_data.get('payload', {})
        data_type = raw_data.get('data_type', 'quote')
        spec = self.TYPE_SPEC.get(data_type, {})
        
        issues = []
        
        for type_name, fields in spec.items():
            for field in fields:
                if field in payload and payload[field] is not None:
                    try:
                        if type_name == 'float':
                            payload[field] = self._to_float(payload[field])
                        elif type_name == 'int':
                            payload[field] = self._to_int(payload[field])
                        elif type_name == 'str':
                            payload[field] = self._to_str(payload[field])
                    except (ValueError, InvalidOperation) as e:
                        issues.append(f"字段 {field} 类型转换失败: {e}")
                        payload[field] = None
        
        raw_data['payload'] = payload
        return CleanResult(data=raw_data, level=self.level, passed=len(issues)==0, issues=issues)
    
    def _to_float(self, value: Any) -> float:
        """转换为浮点数"""
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            # 处理中文数字单位
            value = value.strip()
            if value.endswith('亿'):
                return float(value[:-1]) * 1e8
            if value.endswith('万'):
                return float(value[:-1]) * 1e4
            if value.endswith('%'):
                return float(value[:-1]) / 100
            # 移除逗号
            value = value.replace(',', '')
            return float(value)
        if isinstance(value, Decimal):
            return float(value)
        raise ValueError(f"无法转换为 float: {value}")
    
    def _to_int(self, value: Any) -> int:
        """转换为整数"""
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            value = value.strip().replace(',', '')
            if value.endswith('亿'):
                return int(float(value[:-1]) * 1e8)
            if value.endswith('万'):
                return int(float(value[:-1]) * 1e4)
            return int(float(value))
        raise ValueError(f"无法转换为 int: {value}")
    
    def _to_str(self, value: Any) -> str:
        """转换为字符串"""
        if value is None:
            return ''
        return str(value).strip()


class TimeNormalizer(BaseCleaner):
    """L1: 时间标准化 - 统一转为 UTC datetime"""
    
    level = CleanLevel.L1_STRUCTURE
    source_types = ['quote', 'kline', 'financial', 'news', 'guba', 'report']
    
    # 支持的时间格式
    TIME_FORMATS = [
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d %H:%M',
        '%Y-%m-%d',
        '%Y/%m/%d %H:%M:%S',
        '%Y/%m/%d',
        '%Y%m%d',
        '%Y%m%d%H%M%S',
        '%Y年%m月%d日 %H:%M',
        '%Y年%m月%d日',
        '%m-%d %H:%M',
        '%m月%d日 %H:%M',
        '%H:%M:%S',
        '%H:%M',
    ]
    
    # 字段名 -> 是否为时间字段
    TIME_FIELDS = {
        'timestamp', 'time', 'datetime', 'date', 'trade_date', 'publish_time',
        'ctime', 'utime', 'report_date', 'end_date', 'ann_date',
        'created_at', 'updated_at', 'crawl_time',
    }
    
    def clean(self, raw_data: Dict) -> CleanResult:
        payload = raw_data.get('payload', {})
        issues = []
        
        # 处理 payload 中的时间字段
        for field, value in payload.items():
            if field in self.TIME_FIELDS and value is not None:
                dt = self._parse_time(value)
                if dt:
                    payload[field] = dt
                else:
                    issues.append(f"时间字段 {field} 解析失败: {value}")
        
        # 处理顶层 crawl_time
        if 'crawl_time' in raw_data and raw_data['crawl_time']:
            dt = self._parse_time(raw_data['crawl_time'])
            if dt:
                raw_data['crawl_time'] = dt
            else:
                issues.append(f"crawl_time 解析失败: {raw_data['crawl_time']}")
        
        raw_data['payload'] = payload
        return CleanResult(data=raw_data, level=self.level, passed=len(issues)==0, issues=issues)
    
    def _parse_time(self, value: Any) -> Optional[datetime]:
        """解析时间值为 UTC datetime"""
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        
        if isinstance(value, (int, float)):
            # 时间戳 (秒或毫秒)
            if value > 1e12:  # 毫秒
                value = value / 1000
            try:
                return datetime.fromtimestamp(value, tz=timezone.utc)
            except (ValueError, OSError):
                return None
        
        if isinstance(value, str):
            value = value.strip()
            # 尝试各种格式
            for fmt in self.TIME_FORMATS:
                try:
                    dt = datetime.strptime(value, fmt)
                    return dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
            
            # 尝试 ISO 格式
            try:
                dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except ValueError:
                pass
        
        return None