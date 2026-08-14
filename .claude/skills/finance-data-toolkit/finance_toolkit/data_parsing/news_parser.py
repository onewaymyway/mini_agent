# -*- coding: utf-8 -*-
"""
新闻解析器
"""
import logging
from typing import Any, Dict, List
from .abstract import DataParser, register_parser, _parse_date, _now_iso

logger = logging.getLogger(__name__)

@register_parser
class NewsParser(DataParser):
    @property
    def source_name(self) -> str:
        return 'news'

    @property
    def supported_data_types(self) -> List[str]:
        return ['news', 'stock_news', 'macro_news', 'hot_news']

    def parse(self, raw_data: Any, data_type: str, **kwargs) -> List[Dict[str, Any]]:
        records = []
        timestamp = _now_iso()
        items = []
        if isinstance(raw_data, dict):
            items = raw_data.get('list', raw_data.get('data', {}))
            if isinstance(items, dict):
                items = items.get('list', items.get('data', []))
        elif isinstance(raw_data, list):
            items = raw_data
        if not isinstance(items, list):
            return []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                records.append({
                    'title': str(item.get('title', '')),
                    'url': str(item.get('url', item.get('link', ''))),
                    'source': str(item.get('source', item.get('来源', kwargs.get('source', 'unknown')))),
                    'publish_time': _parse_date(item.get('publish_time', item.get('time', item.get('ctime', item.get('datetime', ''))))),
                    'content': str(item.get('content', item.get('digest', item.get('intro', '')))),
                    'author': str(item.get('author', item.get('作者', ''))),
                    'tags': str(item.get('tags', item.get('tag', ''))),
                    'view_count': int(item.get('view_count', item.get('阅读量', 0))),
                    'comment_count': int(item.get('comment_count', item.get('评论数', 0))),
                    'data_type': data_type,
                    'timestamp': timestamp,
                })
            except (ValueError, TypeError):
                continue
        return records
