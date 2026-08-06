#!/usr/bin/env python
"""
result_parser.py - 搜索结果页解析器

支持：
- 通用搜索结果解析
- 多种网站布局适配
- 结构化数据提取（JSON-LD/微数据）
- 搜索结果去重
- 结果质量评估
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable
from urllib.parse import urljoin

logger = logging.getLogger(__name__)


@dataclass
class ParsedResult:
    """解析后的搜索结果"""
    title: str = ""
    url: str = ""
    snippet: str = ""
    source: str = ""
    published_time: Optional[str] = None
    author: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "source": self.source,
            "published_time": self.published_time,
            "author": self.author,
            "metadata": self.metadata,
        }


class ResultParser:
    """
    搜索结果解析器

    支持多种网站布局的搜索结果解析。
    """

    # 通用结果项选择器
    RESULT_SELECTORS = [
        "article",
        ".result",
        ".search-result",
        ".sr-result",
        ".result-item",
        ".search-result-item",
        "[class*='result']",
        "[class*='search-result']",
        ".card",
        ".item",
        ".list-item",
        ".search-item",
        "li.g",
        "div.yuRUbf",
        "div.tF2C8d",
        "div[data-sokoban]",
    ]

    # 标题选择器
    TITLE_SELECTORS = [
        "h3 a",
        "h3 a[href]",
        "a[href] h3",
        ".r a",
        ".yuRUbf a",
        "[class*='title'] a",
        "a[title]",
        "h2 a",
        "h3 a",
        ".result-title a",
        "a.CurTitleType_1-1-1",
    ]

    # 摘要选择器
    SNIPPET_SELECTORS = [
        ".st",
        ".[class*='snippet']",
        ".[class*='desc']",
        ".[class*='abstract']",
        "[class*='snippet']",
        "[class*='description']",
        "[class*='abstract']",
        "span[class*='text']",
        ".[class*='summary']",
    ]

    # URL 选择器
    URL_SELECTORS = [
        "a[href]",
        ".r a[href]",
        "a[href*='/url?q=']",
        "a[href*='/link?q=']",
        "a[href*='?url=']",
    ]

    def __init__(self, session, source: str = "generic"):
        self.session = session
        self.source = source

    def parse(self, html: str = None) -> List[ParsedResult]:
        """
        解析搜索结果

        Args:
            html: HTML 内容，None 则从当前页面获取

        Returns:
            ParsedResult 列表
        """
        if html is None:
            html = self._get_page_html()

        results = []

        # 尝试多种解析策略
        results.extend(self._parse_by_selectors(html))
        results.extend(self._parse_json_ld(html))
        results.extend(self._parse_microdata(html))

        # 去重
        results = self._deduplicate(results)

        logger.info(f"解析完成，共 {len(results)} 条结果")
        return results

    def _get_page_html(self) -> str:
        """获取当前页面 HTML"""
        try:
            result = self.session.send("Page.getLayoutMetrics", {})
            return result.get("content", "")
        except Exception as e:
            logger.error(f"获取页面 HTML 失败: {e}")
            return ""

    def _parse_by_selectors(self, html: str) -> List[ParsedResult]:
        """通过选择器解析"""
        results = []

        # 提取所有链接
        js = '''
        (function() {
            var results = [];
            var links = document.querySelectorAll('a[href]');
            links.forEach(function(link) {
                var href = link.href || '';
                var text = link.textContent.trim();
                var title = link.getAttribute('title') || text;
                if (href && text) {
                    results.push({
                        title: title,
                        url: href,
                        snippet: ''
                    });
                }
            });
            return results;
        })()
        '''
        try:
            items = self.session.eval_js(js)
            if items:
                for item in items[:20]:  # 限制数量
                    results.append(ParsedResult(
                        title=item.get("title", ""),
                        url=item.get("url", ""),
                        snippet=item.get("snippet", ""),
                        source=self.source,
                    ))
        except Exception as e:
            logger.error(f"选择器解析失败: {e}")

        return results

    def _parse_json_ld(self, html: str) -> List[ParsedResult]:
        """解析 JSON-LD 结构化数据"""
        results = []
        try:
            js = '''
            (function() {
                var scripts = document.querySelectorAll('script[type="application/ld+json"]');
                var results = [];
                scripts.forEach(function(script) {
                    try {
                        var data = JSON.parse(script.textContent);
                        if (data['@graph']) {
                            data['@graph'].forEach(function(item) {
                                if (item.headline || item.name) {
                                    results.push({
                                        title: item.headline || item.name,
                                        url: item.url || '',
                                        snippet: item.description || '',
                                        date: item.datePublished || item.dateCreated
                                    });
                                }
                            });
                        } else if (data.headline || data.name) {
                            results.push({
                                title: data.headline || data.name,
                                url: data.url || '',
                                snippet: data.description || '',
                                date: data.datePublished || data.dateCreated
                            });
                        }
                    } catch(e) {}
                });
                return results;
            })()
            '''
            items = self.session.eval_js(js)
            if items:
                for item in items:
                    results.append(ParsedResult(
                        title=item.get("title", ""),
                        url=item.get("url", ""),
                        snippet=item.get("snippet", ""),
                        published_time=item.get("date", ""),
                        source=self.source,
                    ))
        except Exception as e:
            logger.debug(f"JSON-LD 解析失败: {e}")

        return results

    def _parse_microdata(self, html: str) -> List[ParsedResult]:
        """解析微数据"""
        results = []
        try:
            js = '''
            (function() {
                var items = document.querySelectorAll('[itemtype*="http://schema.org"]');
                var results = [];
                items.forEach(function(item) {
                    var titleEl = item.querySelector('[itemprop="name"], [itemprop="headline"]');
                    var urlEl = item.querySelector('[itemprop="url"]');
                    var descEl = item.querySelector('[itemprop="description"]');
                    if (titleEl) {
                        results.push({
                            title: titleEl.textContent.trim(),
                            url: urlEl ? urlEl.href : '',
                            snippet: descEl ? descEl.textContent.trim() : ''
                        });
                    }
                });
                return results;
            })()
            '''
            items = self.session.eval_js(js)
            if items:
                for item in items:
                    results.append(ParsedResult(
                        title=item.get("title", ""),
                        url=item.get("url", ""),
                        snippet=item.get("snippet", ""),
                        source=self.source,
                    ))
        except Exception as e:
            logger.debug(f"微数据解析失败: {e}")

        return results

    def _deduplicate(self, results: List[ParsedResult]) -> List[ParsedResult]:
        """去重"""
        seen_urls = set()
        unique = []
        for r in results:
            if r.url and r.url not in seen_urls:
                seen_urls.add(r.url)
                unique.append(r)
        return unique

    def extract_metadata(self, html: str = None) -> Dict[str, Any]:
        """提取页面元数据"""
        if html is None:
            html = self._get_page_html()

        js = '''
        (function() {
            var meta = {};
            
            // 标题
            meta.title = document.title;
            
            // Meta 标签
            var metas = document.querySelectorAll('meta[name], meta[property]');
            metas.forEach(function(m) {
                var name = m.getAttribute('name') || m.getAttribute('property');
                var content = m.getAttribute('content');
                if (name && content) {
                    meta[name] = content;
                }
            });
            
            // Open Graph
            var ogTags = document.querySelectorAll('meta[property^="og:"]');
            ogTags.forEach(function(tag) {
                var prop = tag.getAttribute('property');
                var content = tag.getAttribute('content');
                if (prop && content) {
                    meta[prop] = content;
                }
            });
            
            return meta;
        })()
        '''
        try:
            return self.session.eval_js(js) or {}
        except Exception:
            return {}


# 便捷函数
def parse_search_results(session, source: str = "generic") -> List[ParsedResult]:
    """解析搜索结果"""
    parser = ResultParser(session, source)
    return parser.parse()


def extract_page_metadata(session) -> Dict[str, Any]:
    """提取页面元数据"""
    parser = ResultParser(session)
    return parser.extract_metadata()
