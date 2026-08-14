# -*- coding: utf-8 -*-
"""
股吧社区解析器
"""
import logging
from typing import Any, Dict, List
from .abstract import DataParser, register_parser, _parse_float, _parse_date, _now_iso

logger = logging.getLogger(__name__)

@register_parser
class GubaParser(DataParser):
    @property
    def source_name(self) -> str:
        return 'guba'

    @property
    def supported_data_types(self) -> List[str]:
        return ['guba', 'guba_post', 'guba_comment', 'guba_sentiment']

    def parse(self, raw_data: Any, data_type: str, **kwargs) -> List[Dict[str, Any]]:
        records = []
        timestamp = _now_iso()
        items = []
        if isinstance(raw_data, dict):
            items = raw_data.get('posts', raw_data.get('data', raw_data.get('list', [])))
        elif isinstance(raw_data, list):
            items = raw_data
        if not isinstance(items, list):
            return []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                records.append({
                    'post_id': str(item.get('post_id', item.get('id', ''))),
                    'title': str(item.get('title', item.get('标题', ''))),
                    'author': str(item.get('author', item.get('作者', ''))),
                    'content': str(item.get('content', item.get('内容', ''))),
                    'publish_time': _parse_date(item.get('publish_time', item.get('时间', item.get('ctime', '')))),
                    'view_count': _parse_float(item.get('view_count', item.get('浏览量', 0))),
                    'comment_count': _parse_float(item.get('comment_count', item.get('评论数', 0))),
                    'like_count': _parse_float(item.get('like_count', item.get('点赞数', 0))),
                    'stock_code': str(item.get('stock_code', item.get('代码', ''))),
                    'sentiment_score': _parse_float(item.get('sentiment_score', item.get('情绪分', 0))),
                    'url': str(item.get('url', item.get('链接', ''))),
                    'data_type': data_type,
                    'timestamp': timestamp,
                })
            except (ValueError, TypeError):
                continue
        return records
