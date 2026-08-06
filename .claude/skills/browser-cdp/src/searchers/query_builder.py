#!/usr/bin/env python
"""
query_builder.py - 智能查询构造器

支持：
- 查询词规范化（去重、截断、编码）
- 多语言查询支持
- 查询词扩展（同义词、相关词）
- 查询词拆分与组合
- 搜索参数构造（排序、过滤、时间范围）
"""
from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple
from urllib.parse import quote

logger = logging.getLogger(__name__)


@dataclass
class QueryParams:
    """查询参数"""
    original: str = ""
    normalized: str = ""
    encoded: str = ""
    language: str = "zh"
    sort_by: str = "relevance"  # relevance/time/popularity
    sort_order: str = "desc"  # asc/desc
    time_range: Optional[str] = None  # day/week/month/year/all
    filters: Dict[str, Any] = field(default_factory=dict)
    page: int = 1
    per_page: int = 10
    
    def to_dict(self) -> dict:
        return {
            "original": self.original,
            "normalized": self.normalized,
            "encoded": self.encoded,
            "language": self.language,
            "sort_by": self.sort_by,
            "sort_order": self.sort_order,
            "time_range": self.time_range,
            "filters": self.filters,
            "page": self.page,
            "per_page": self.per_page,
        }


class QueryBuilder:
    """
    智能查询构造器
    
    提供查询词规范化、扩展、参数构造等功能。
    """
    
    # 常见停用词
    STOP_WORDS = {
        "zh": {"的", "了", "是", "在", "我", "有", "和", "就", "不", "人", "都", "一", "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有", "看", "好", "自己", "这"},
        "en": {"the", "a", "an", "is", "are", "was", "were", "be", "been", "being", "have", "has", "had", "do", "does", "did", "will", "would", "shall", "should", "may", "might", "must", "can", "could", "of", "at", "by", "for", "with", "about", "against", "between", "through", "during", "before", "after", "above", "below", "to", "from", "up", "down", "in", "out", "on", "off", "over", "under", "again", "further", "then", "once", "here", "there", "when", "where", "why", "how", "all", "both", "each", "few", "more", "most", "other", "some", "such", "no", "nor", "not", "only", "own", "same", "so", "than", "too", "very", "s", "t", "just", "don", "now", "i", "me", "my", "myself", "we", "our", "ours", "ourselves", "you", "your", "yours", "yourself", "yourselves", "he", "him", "his", "himself", "she", "her", "hers", "herself", "it", "its", "itself", "they", "them", "their", "theirs", "themselves", "what", "which", "who", "whom", "this", "that", "these", "those", "am", "if", "and", "but", "or", "as", "until", "while", "into"},
    }
    
    # 同义词映射（可扩展）
    SYNONYMS: Dict[str, Dict[str, List[str]]] = {
        "zh": {
            "手机": ["移动电话", "智能手机", "mobile phone", "smartphone"],
            "电脑": ["计算机", "笔记本", "PC", "computer", "laptop"],
            "电影": ["影视", "影片", "movie", "film"],
            "招聘": ["求职", "工作", "job", "career", "hire"],
            "房价": ["房产", "二手房", "房价查询", "house price", "real estate"],
        },
        "en": {
            "phone": ["mobile", "cell phone", "smartphone"],
            "computer": ["laptop", "PC", "notebook"],
            "movie": ["film", "movie"],
        },
    }
    
    def __init__(self, language: str = "zh"):
        self.language = language
        self._stop_words = self.STOP_WORDS.get(language, set())
    
    def normalize(self, query: str) -> str:
        """
        规范化查询词
        
        - 去除多余空白
        - 去除停用词
        - 统一大小写（英文）
        - 去除特殊字符
        """
        if not query:
            return ""
        
        # 去除多余空白
        text = re.sub(r'\s+', ' ', query.strip())
        
        # 去除特殊字符（保留中文、英文、数字、常见标点）
        text = re.sub(r'[^\w\s\u4e00-\u9fff，、。！？：；"\'（）【】《》]', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        
        # 英文转小写
        if self.language == "en":
            text = text.lower()
        
        # 去除停用词（中文按字符，英文按单词）
        if self.language == "zh":
            # 中文：逐字符过滤停用词
            filtered = [c for c in text if c not in self._stop_words]
            text = ''.join(filtered)
        else:
            words = text.split()
            filtered = [w for w in words if w not in self._stop_words]
            text = ' '.join(filtered)
        
        return text
    
    def expand(self, query: str) -> List[str]:
        """
        扩展查询词（同义词）
        
        Returns:
            扩展后的查询词列表（包含原始查询）
        """
        normalized = self.normalize(query)
        expanded = [normalized]
        
        # 检查同义词
        for key, synonyms in self.SYNONYMS.get(self.language, {}).items():
            if key in normalized:
                for syn in synonyms:
                    expanded.append(normalized.replace(key, syn))
        
        # 保持原始查询在第一位，其余去重
        seen = {normalized}
        result = [normalized]
        for item in expanded[1:]:
            if item not in seen:
                seen.add(item)
                result.append(item)
        return result
    
    def split(self, query: str, max_parts: int = 3) -> List[str]:
        """
        拆分查询词为多个子查询
        
        用于复杂查询的拆分搜索
        """
        normalized = self.normalize(query)
        if not normalized:
            return [query]
        
        # 按空格拆分
        parts = normalized.split()
        
        if len(parts) <= max_parts:
            return [normalized]
        
        # 按 max_parts 拆分
        chunk_size = len(parts) // max_parts
        chunks = []
        for i in range(0, len(parts), chunk_size):
            chunk = ' '.join(parts[i:i + chunk_size])
            if chunk:
                chunks.append(chunk)
        
        return chunks if chunks else [normalized]
    
    def build_params(self, query: str, **kwargs) -> QueryParams:
        """
        构建查询参数
        
        Args:
            query: 原始查询词
            **kwargs: 额外参数
                - sort_by: 排序方式 (relevance/time/popularity)
                - sort_order: 排序顺序 (asc/desc)
                - time_range: 时间范围 (day/week/month/year/all)
                - filters: 过滤条件字典
                - page: 页码
                - per_page: 每页数量
        """
        normalized = self.normalize(query)
        encoded = quote(normalized)
        
        params = QueryParams(
            original=query,
            normalized=normalized,
            encoded=encoded,
            language=kwargs.get("language", self.language),
            sort_by=kwargs.get("sort_by", "relevance"),
            sort_order=kwargs.get("sort_order", "desc"),
            time_range=kwargs.get("time_range"),
            filters=kwargs.get("filters", {}),
            page=kwargs.get("page", 1),
            per_page=kwargs.get("per_page", 10),
        )
        
        return params
    
    def build_url(self, base_url: str, params: QueryParams) -> str:
        """
        构建搜索 URL
        
        支持常见搜索引擎的 URL 格式
        """
        from urllib.parse import urlencode, urlparse, parse_qs, urlunparse
        
        parsed = urlparse(base_url)
        query_params = parse_qs(parsed.query)
        
        # 添加搜索参数
        query_params['q'] = [params.encoded]
        
        # 排序参数
        if params.sort_by == "time":
            query_params['sort'] = ['date']
        elif params.sort_by == "popularity":
            query_params['sort'] = ['relevance']
        
        # 时间范围
        if params.time_range and params.time_range != "all":
            query_params['tbs'] = [f"qdr:{params.time_range[0]}"]  # d/w/m/y
        
        # 分页
        if params.page > 1:
            query_params['start'] = [(params.page - 1) * params.per_page]
        
        # 构建新 URL
        new_query = urlencode(query_params, doseq=True)
        new_url = urlunparse((
            parsed.scheme, parsed.netloc, parsed.path,
            parsed.params, new_query, parsed.fragment
        ))
        
        return new_url
    
    def build_api_params(self, params: QueryParams) -> Dict[str, Any]:
        """
        构建 API 请求参数
        """
        api_params = {
            "query": params.normalized,
            "page": params.page,
            "per_page": params.per_page,
        }
        
        if params.sort_by != "relevance":
            api_params["sort"] = params.sort_by
        if params.sort_order:
            api_params["order"] = params.sort_order
        if params.time_range and params.time_range != "all":
            api_params["time_range"] = params.time_range
        if params.filters:
            api_params["filters"] = params.filters
        
        return api_params


# 便捷函数
def build_query(query: str, language: str = "zh", **kwargs) -> QueryParams:
    """构建查询参数"""
    builder = QueryBuilder(language)
    return builder.build_params(query, **kwargs)


def expand_query(query: str, language: str = "zh") -> List[str]:
    """扩展查询词"""
    builder = QueryBuilder(language)
    return builder.expand(query)


def split_query(query: str, max_parts: int = 3, language: str = "zh") -> List[str]:
    """拆分查询词"""
    builder = QueryBuilder(language)
    return builder.split(query, max_parts)