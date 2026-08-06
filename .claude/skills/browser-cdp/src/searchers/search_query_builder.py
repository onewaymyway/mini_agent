"""
search_query_builder.py - 智能查询构造器

支持：
- 查询优化与扩展
- 同义词替换
- 查询建议
- 查询去重
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Set

logger = logging.getLogger(__name__)


@dataclass
class QueryExpansion:
    """查询扩展结果"""
    original: str
    expanded: List[str] = field(default_factory=list)
    synonyms: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {
            "original": self.original,
            "expanded": self.expanded,
            "synonyms": self.synonyms,
            "suggestions": self.suggestions,
        }


class QueryBuilder:
    """
    智能查询构造器
    
    提供查询优化、扩展、同义词替换等功能。
    """
    
    # 常见同义词映射
    SYNONYM_MAP = {
        # 中文同义词
        "手机": ["移动电话", "智能手机", "mobile phone", "smartphone"],
        "电脑": ["计算机", "计算机", "computer", "laptop"],
        "笔记本": ["笔记本电脑", "laptop", "notebook"],
        "电视": ["电视机", "television", "TV"],
        "冰箱": ["电冰箱", "refrigerator", "fridge"],
        "空调": ["空调器", "air conditioner", "AC"],
        "洗衣机": ["全自动洗衣机", "washing machine"],
        "汽车": ["轿车", "automobile", "car", "vehicle"],
        "房子": ["房屋", "房产", "住宅", "house", "property"],
        "工作": ["就业", "职位", "job", "career", "employment"],
        "招聘": ["求职", "招聘", "job", "hiring", "recruitment"],
        "电影": ["影片", "movie", "film"],
        "电视剧": ["剧集", "drama", "TV series"],
        "游戏": ["电子游戏", "game", "video game"],
        "音乐": ["歌曲", "music", "song"],
        "新闻": ["资讯", "news", "information"],
        "天气": ["气象", "weather", "forecast"],
        # 英文同义词
        "phone": ["mobile", "cell phone", "smartphone"],
        "computer": ["laptop", "desktop", "PC"],
        "laptop": ["notebook", "computer"],
        "TV": ["television", "tv set"],
        "car": ["automobile", "vehicle", "auto"],
        "house": ["home", "residence", "property"],
        "job": ["employment", "career", "position", "work"],
        "movie": ["film", "picture"],
        "game": ["video game", "gaming"],
        "music": ["song", "track", "audio"],
        "news": ["headline", "report", "article"],
    }
    
    # 查询修饰词
    MODIFIERS = {
        "最新": ["latest", "newest", "2024", "2025", "2026"],
        "价格": ["price", "cheap", "cost", "优惠"],
        "评测": ["review", "评测", "测评", "comparison"],
        "对比": ["compare", "对比", "比较"],
        "推荐": ["recommend", "推荐", "best"],
        "排行": ["rank", "ranking", "排行榜"],
        "教程": ["tutorial", "guide", "教程", "入门"],
        "下载": ["download", "下载", "free"],
        "免费": ["free", "免费", "gratis"],
    }
    
    def __init__(self):
        self._synonym_cache: Dict[str, List[str]] = {}
        self._query_history: List[str] = []
    
    def expand(self, query: str, max_expansions: int = 5) -> QueryExpansion:
        """
        扩展查询词
        
        Args:
            query: 原始查询词
            max_expansions: 最大扩展数量
        
        Returns:
            QueryExpansion 对象
        """
        query = query.strip()
        if not query:
            return QueryExpansion(original="")
        
        expanded = [query]
        synonyms = []
        suggestions = []
        
        # 1. 同义词扩展
        words = self._tokenize(query)
        for word in words:
            if word in self.SYNONYM_MAP:
                syns = self.SYNONYM_MAP[word]
                synonyms.extend(syns)
                # 构造扩展查询
                for syn in syns:
                    if syn not in expanded:
                        expanded.append(syn)
        
        # 2. 修饰词扩展
        for modifier, mods in self.MODIFIERS.items():
            if modifier in query:
                for mod in mods:
                    if mod not in expanded:
                        expanded.append(f"{query} {mod}")
        
        # 3. 生成建议
        suggestions = self._generate_suggestions(query)
        
        # 去重并限制数量
        expanded = list(dict.fromkeys(expanded))[:max_expansions]
        synonyms = list(dict.fromkeys(synonyms))[:max_expansions]
        suggestions = list(dict.fromkeys(suggestions))[:5]
        
        return QueryExpansion(
            original=query,
            expanded=expanded,
            synonyms=synonyms,
            suggestions=suggestions,
        )
    
    def optimize(self, query: str) -> str:
        """
        优化查询词
        
        Args:
            query: 原始查询词
        
        Returns:
            优化后的查询词
        """
        query = query.strip()
        
        # 1. 去除多余空格
        query = re.sub(r'\s+', ' ', query)
        
        # 2. 去除特殊字符（保留中文、英文、数字、常见标点）
        query = re.sub(r'[^一-龥a-zA-Z0-9\s\-\_\./]', '', query)
        
        # 3. 去除停用词
        stop_words = {'的', '了', '是', '在', '我', '你', '他', '她', '它', '们', '这', '那', '和', '与', '或'}
        words = query.split()
        words = [w for w in words if w not in stop_words]
        query = ' '.join(words)
        
        # 4. 标题化
        query = query.title()
        
        return query.strip()
    
    def suggest(self, query: str, limit: int = 5) -> List[str]:
        """
        生成查询建议
        
        Args:
            query: 原始查询词
            limit: 建议数量
        
        Returns:
            建议列表
        """
        suggestions = self._generate_suggestions(query)
        return suggestions[:limit]
    
    def deduplicate(self, queries: List[str]) -> List[str]:
        """
        查询去重
        
        Args:
            queries: 查询列表
        
        Returns:
            去重后的查询列表
        """
        seen: Set[str] = set()
        unique_queries = []
        
        for q in queries:
            normalized = self._normalize_query(q)
            if normalized not in seen:
                seen.add(normalized)
                unique_queries.append(q)
        
        return unique_queries
    
    def add_to_history(self, query: str):
        """添加查询到历史"""
        self._query_history.append(query)
        # 限制历史记录长度
        if len(self._query_history) > 100:
            self._query_history = self._query_history[-100:]
    
    def get_history(self) -> List[str]:
        """获取查询历史"""
        return self._query_history.copy()
    
    def clear_history(self):
        """清空查询历史"""
        self._query_history.clear()
    
    def _tokenize(self, text: str) -> List[str]:
        """分词（简单实现）"""
        # 中文分词：按字符分割
        # 英文分词：按空格分割
        words = []
        current_word = ""
        
        for char in text:
            if '\u4e00' <= char <= '\u9fa5':
                # 中文字符
                if current_word:
                    words.append(current_word)
                    current_word = ""
                words.append(char)
            elif char.isalnum() or char in '_-':
                current_word += char
            else:
                if current_word:
                    words.append(current_word)
                    current_word = ""
        
        if current_word:
            words.append(current_word)
        
        return words
    
    def _generate_suggestions(self, query: str) -> List[str]:
        """生成查询建议"""
        suggestions = []
        
        # 1. 基于同义词的建议
        words = self._tokenize(query)
        for word in words:
            if word in self.SYNONYM_MAP:
                for syn in self.SYNONYM_MAP[word]:
                    if syn != word:
                        suggestions.append(query.replace(word, syn))
        
        # 2. 基于修饰词的建议
        for modifier in self.MODIFIERS.keys():
            if modifier not in query:
                suggestions.append(f"{query} {modifier}")
        
        # 3. 基于历史的建议
        for history_query in self._query_history[-10:]:
            if history_query != query:
                suggestions.append(history_query)
        
        # 去重
        suggestions = list(dict.fromkeys(suggestions))
        
        return suggestions
    
    def _normalize_query(self, query: str) -> str:
        """标准化查询词（用于去重）"""
        query = query.lower().strip()
        query = re.sub(r'\s+', ' ', query)
        return query


# 便捷函数
def build_query(query: str, expand: bool = True) -> QueryExpansion:
    """构建查询"""
    builder = QueryBuilder()
    if expand:
        return builder.expand(query)
    return QueryExpansion(original=query)


def optimize_query(query: str) -> str:
    """优化查询词"""
    builder = QueryBuilder()
    return builder.optimize(query)


def suggest_queries(query: str, limit: int = 5) -> List[str]:
    """生成查询建议"""
    builder = QueryBuilder()
    return builder.suggest(query, limit)
