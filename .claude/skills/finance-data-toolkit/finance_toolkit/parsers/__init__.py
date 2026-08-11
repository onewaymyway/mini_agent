# -*- coding: utf-8 -*-
"""
通用数据解析模块
提供统一的HTML/JSON/文本解析工具函数
"""

from .html_parser import (
    parse_html_table,
    parse_html_text,
    parse_html_jsonp,
    extract_field,
    safe_float,
    safe_int,
)
from .json_parser import (
    parse_json_response,
    parse_eastmoney_json,
    parse_sina_jsonp,
)
from .text_parser import (
    parse_sina_text,
    parse_eastmoney_text,
    parse_jsonp_text,
)

__all__ = [
    # HTML 解析
    'parse_html_table',
    'parse_html_text',
    'parse_html_jsonp',
    'extract_field',
    'safe_float',
    'safe_int',
    # JSON 解析
    'parse_json_response',
    'parse_eastmoney_json',
    'parse_sina_jsonp',
    # 文本解析
    'parse_sina_text',
    'parse_eastmoney_text',
    'parse_jsonp_text',
]
