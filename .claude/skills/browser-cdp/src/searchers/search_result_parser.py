"""
search_result_parser.py - 搜索结果解析器

支持：
- 通用搜索结果解析
- 多种网站格式适配
- 结果去重与排序
- 结果质量评估
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class ParsedResult:
    """解析后的搜索结果"""
    title: str = ""
    url: str = ""
    snippet: str = ""
    author: str = ""
    published_time: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "author": self.author,
            "published_time": self.published_time,
            "metadata": self.metadata,
        }
    
    def quality_score(self) -> float:
        """计算结果质量分数"""
        score = 0.0
        
        # 标题长度评分
        if len(self.title) > 10:
            score += 0.3
        if len(self.title) > 30:
            score += 0.2
        
        # 摘要长度评分
        if len(self.snippet) > 50:
            score += 0.3
        if len(self.snippet) > 100:
            score += 0.2
        
        # 有发布时间评分
        if self.published_time:
            score += 0.1
        
        return min(1.0, score)


class ResultParser:
    """
    搜索结果解析器
    
    支持多种网站格式的结果解析。
    """
    
    # 常见结果容器选择器
    RESULT_SELECTORS = [
        # 通用
        ".result", ".search-result", ".search-result-item",
        ".item", ".product", ".article", ".post",
        # 百度
        ".result-op", ".c-container",
        # 谷歌
        "div[data-hveid]", "g", ".g",
        # 必应
        ".b_algo", ".b_algoContainer",
        # 知乎
        ".List-item", ".RichContent",
        # 豆瓣
        ".item", ".rate",
        # 京东
        ".gl-item", ".sku-item",
        # 淘宝
        ".item", ".card",
    ]
    
    # 标题选择器
    TITLE_SELECTORS = [
        "h3", "h2", ".title", ".result-title",
        "a[href]", "[class*='title']",
    ]
    
    # 链接选择器
    LINK_SELECTORS = [
        "a[href]", "[href]",
    ]
    
    # 摘要选择器
    SNIPPET_SELECTORS = [
        ".snippet", ".abstract", ".description",
        ".text", ".content", ".summary",
        "p", ".c-abstract",
    ]
    
    # 时间选择器
    TIME_SELECTORS = [
        "time", ".time", ".date", ".publish-time",
        "[class*='time']", "[class*='date']",
    ]
    
    # 作者选择器
    AUTHOR_SELECTORS = [
        ".author", ".by", ".writer",
        "[class*='author']", "[class*='by']",
    ]
    
    def __init__(self, session):
        self.session = session
        self._custom_parsers: Dict[str, Callable] = {}
    
    def parse(self, html: str = None, url: str = None) -> List[ParsedResult]:
        """
        解析搜索结果
        
        Args:
            html: HTML 内容（可选，如果提供则直接解析）
            url: 页面 URL（可选，用于选择解析器）
        
        Returns:
            解析后的结果列表
        """
        if html:
            return self._parse_html(html)
        
        # 从当前页面解析
        return self._parse_current_page()
    
    def register_parser(self, domain: str, parser: Callable):
        """
        注册自定义解析器
        
        Args:
            domain: 域名
            parser: 解析函数，接收 (session, html) 返回 List[ParsedResult]
        """
        self._custom_parsers[domain] = parser
    
    def _parse_current_page(self) -> List[ParsedResult]:
        """解析当前页面"""
        url = self._get_current_url()
        
        # 检查是否有自定义解析器
        for domain, parser in self._custom_parsers.items():
            if domain in url:
                logger.info(f"使用自定义解析器: {domain}")
                return parser(self.session, url)
        
        # 使用通用解析器
        return self._parse_generic()
    
    def _parse_html(self, html: str) -> List[ParsedResult]:
        """解析 HTML 内容"""
        js = f'''
        (function() {{
            var parser = new DOMParser();
            var doc = parser.parseFromString({html!r}, 'text/html');
            var results = [];
            
            // 查找结果容器
            var containers = [];
            var selectors = {self.RESULT_SELECTORS!r};
            for (var sel of selectors) {{
                var elements = doc.querySelectorAll(sel);
                for (var el of elements) {{
                    if (el.children.length > 0) {{
                        containers.push(el);
                    }}
                }}
            }}
            
            // 去重
            var seen = new Set();
            containers = containers.filter(function(el) {{
                var key = el.outerHTML.substring(0, 100);
                if (seen.has(key)) return false;
                seen.add(key);
                return true;
            }});
            
            // 解析每个结果
            for (var container of containers) {{
                var result = {{
                    title: '',
                    url: '',
                    snippet: '',
                    author: '',
                    published_time: ''
                }};
                
                // 提取标题
                var titleEl = container.querySelector('h3, h2, .title, .result-title, a[href]');
                if (titleEl) {{
                    result.title = titleEl.textContent.trim().substring(0, 200);
                }}
                
                // 提取链接
                var linkEl = container.querySelector('a[href]');
                if (linkEl) {{
                    result.url = linkEl.href;
                }}
                
                // 提取摘要
                var snippetEl = container.querySelector('.snippet, .abstract, .description, p');
                if (snippetEl) {{
                    result.snippet = snippetEl.textContent.trim().substring(0, 500);
                }}
                
                // 提取时间
                var timeEl = container.querySelector('time, .time, .date');
                if (timeEl) {{
                    result.published_time = timeEl.textContent.trim();
                }}
                
                // 提取作者
                var authorEl = container.querySelector('.author, [class*="author"]');
                if (authorEl) {{
                    result.author = authorEl.textContent.trim();
                }}
                
                if (result.title || result.url) {{
                    results.push(result);
                }}
            }}
            
            return results;
        }})()
        '''
        
        try:
            results = self.session.eval_js(js)
            return [ParsedResult(**r) for r in results] if results else []
        except Exception as e:
            logger.error(f"解析 HTML 失败: {e}")
            return []
    
    def _parse_generic(self) -> List[ParsedResult]:
        """通用解析"""
        js = '''
        (function() {
            var results = [];
            
            // 查找结果容器
            var selectors = ''' + str(self.RESULT_SELECTORS) + ''';
            var containers = [];
            for (var sel of selectors) {
                var elements = document.querySelectorAll(sel);
                for (var el of elements) {
                    if (el.children.length > 0 && el.getBoundingClientRect().width > 0) {
                        containers.push(el);
                    }
                }
            }
            
            // 去重
            var seen = new Set();
            containers = containers.filter(function(el) {
                var key = el.outerHTML.substring(0, 100);
                if (seen.has(key)) return false;
                seen.add(key);
                return true;
            });
            
            // 解析每个结果
            for (var container of containers) {
                var result = {
                    title: '',
                    url: '',
                    snippet: '',
                    author: '',
                    published_time: ''
                };
                
                // 提取标题
                var titleEl = container.querySelector('h3, h2, .title, .result-title, a[href]');
                if (titleEl) {
                    result.title = titleEl.textContent.trim().substring(0, 200);
                }
                
                // 提取链接
                var linkEl = container.querySelector('a[href]');
                if (linkEl) {
                    result.url = linkEl.href;
                }
                
                // 提取摘要
                var snippetEl = container.querySelector('.snippet, .abstract, .description, p');
                if (snippetEl) {
                    result.snippet = snippetEl.textContent.trim().substring(0, 500);
                }
                
                // 提取时间
                var timeEl = container.querySelector('time, .time, .date');
                if (timeEl) {
                    result.published_time = timeEl.textContent.trim();
                }
                
                // 提取作者
                var authorEl = container.querySelector('.author, [class*="author"]');
                if (authorEl) {
                    result.author = authorEl.textContent.trim();
                }
                
                if (result.title || result.url) {
                    results.push(result);
                }
            }
            
            return results;
        })()
        '''
        
        try:
            results = self.session.eval_js(js)
            return [ParsedResult(**r) for r in results] if results else []
        except Exception as e:
            logger.error(f"通用解析失败: {e}")
            return []
    
    def _get_current_url(self) -> str:
        """获取当前 URL"""
        try:
            result = self.session.send("Runtime.evaluate", {"expression": "location.href"})
            return result.get("result", {}).get("value", "")
        except Exception:
            return ""
    
    def deduplicate(self, results: List[ParsedResult], by: str = "url", threshold: float = 0.9) -> List[ParsedResult]:
        """
        结果去重
        
        Args:
            results: 结果列表
            by: 去重依据 (url/title/simhash)
            threshold: 相似度阈值
        
        Returns:
            去重后的结果列表
        """
        if by == "url":
            seen = set()
            unique = []
            for r in results:
                if r.url and r.url not in seen:
                    seen.add(r.url)
                    unique.append(r)
            return unique
        
        # 其他去重策略（简化实现）
        return results[:len(set(r.url for r in results if r.url))]
    
    def sort_results(self, results: List[ParsedResult], by: str = "relevance") -> List[ParsedResult]:
        """
        结果排序
        
        Args:
            results: 结果列表
            by: 排序依据 (relevance/time/quality)
        
        Returns:
            排序后的结果列表
        """
        if by == "quality":
            return sorted(results, key=lambda r: r.quality_score(), reverse=True)
        
        if by == "time":
            # 按发布时间排序（简化实现）
            return results
        
        # 默认按相关性（保持原顺序）
        return results
    
    def filter_results(self, results: List[ParsedResult], min_quality: float = 0.3) -> List[ParsedResult]:
        """
        过滤低质量结果
        
        Args:
            results: 结果列表
            min_quality: 最低质量分数
        
        Returns:
            过滤后的结果列表
        """
        return [r for r in results if r.quality_score() >= min_quality]


# 便捷函数
def parse_search_results(session, html: str = None) -> List[ParsedResult]:
    """解析搜索结果"""
    parser = ResultParser(session)
    return parser.parse(html)


def deduplicate_results(results: List[ParsedResult], by: str = "url") -> List[ParsedResult]:
    """去重结果"""
    parser = ResultParser(None)
    return parser.deduplicate(results, by)
