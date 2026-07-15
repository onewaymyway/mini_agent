"""
研报生成模块
提供统一的研报生成接口
"""

from .report_generator import (
    generate_comprehensive_report,
    generate_html_report,
    generate_markdown_report,
    generate_json_report,
    load_latest_basic_data,
    load_latest_kline_data,
)

__all__ = [
    'generate_comprehensive_report',
    'generate_html_report',
    'generate_markdown_report',
    'generate_json_report',
    'load_latest_basic_data',
    'load_latest_kline_data',
]