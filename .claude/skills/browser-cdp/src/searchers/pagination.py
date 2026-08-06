#!/usr/bin/env python
"""
pagination.py - 分页处理模块

支持：
- 自动检测分页元素
- 智能翻页（点击/URL构造）
- 分页状态追踪
- 多页结果合并
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable
from enum import Enum

logger = logging.getLogger(__name__)


class PaginationType(Enum):
    """分页类型"""
    URL_BASED = "url_based"      # URL 参数分页（?page=2）
    CLICK_NEXT = "click_next"    # 点击下一页按钮
    INFINITE_SCROLL = "infinite" # 无限滚动
    LOAD_MORE = "load_more"     # 加载更多按钮
    UNKNOWN = "unknown"


@dataclass
class PaginationInfo:
    """分页信息"""
    pagination_type: PaginationType = PaginationType.UNKNOWN
    current_page: int = 1
    total_pages: Optional[int] = None
    has_next: bool = False
    has_prev: bool = False
    next_selector: Optional[str] = None
    prev_selector: Optional[str] = None
    page_links: List[Dict[str, str]] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {
            "pagination_type": self.pagination_type.value,
            "current_page": self.current_page,
            "total_pages": self.total_pages,
            "has_next": self.has_next,
            "has_prev": self.has_prev,
            "next_selector": self.next_selector,
            "prev_selector": self.prev_selector,
            "page_links": self.page_links,
        }


@dataclass
class PageResult:
    """单页搜索结果"""
    page: int
    results: List[Dict[str, Any]] = field(default_factory=list)
    pagination: Optional[PaginationInfo] = None
    error: Optional[str] = None
    
    def to_dict(self) -> dict:
        return {
            "page": self.page,
            "results_count": len(self.results),
            "results": self.results,
            "pagination": self.pagination.to_dict() if self.pagination else None,
            "error": self.error,
        }


class PaginationDetector:
    """
    分页检测器
    
    自动检测页面的分页类型和元素。
    """
    
    # 下一页选择器模式
    NEXT_SELECTORS = [
        "a[rel='next']",
        "a[class*='next']",
        "a[class*='page-next']",
        "button[class*='next']",
        "a[href*='page=']",
        "a[href*='/page/']",
        "a[href*='?p=']",
        ".pagination a:last-child",
        "nav[aria-label*='pagination'] a:last-child",
        "[aria-label*='next']",
        "button[aria-label*='next']",
        ".next-page",
        ".pager-next",
        "a[title*='下一页']",
        "button[title*='下一页']",
        "a[text()='下一页']",
        "button[text()='下一页']",
        "a[text()='Next']",
        "button[text()='Next']",
    ]
    
    # 上一页选择器模式
    PREV_SELECTORS = [
        "a[rel='prev']",
        "a[class*='prev']",
        "a[class*='page-prev']",
        "button[class*='prev']",
        ".pagination a:first-child",
        "[aria-label*='prev']",
        "button[aria-label*='prev']",
        "a[title*='上一页']",
        "a[text()='上一页']",
        "a[text()='Prev']",
    ]
    
    # 分页容器选择器
    PAGINATION_CONTAINERS = [
        ".pagination",
        ".pager",
        ".page-nav",
        ".paging",
        ".pages",
        "nav[aria-label*='pagination']",
        "[class*='pagination']",
        "[class*='pager']",
        "[class*='page-nav']",
    ]
    
    # 加载更多选择器
    LOAD_MORE_SELECTORS = [
        "button[class*='load-more']",
        "a[class*='load-more']",
        ".load-more",
        ".show-more",
        "button[text()='加载更多']",
        "a[text()='加载更多']",
        "button[text()='Load More']",
    ]
    
    def __init__(self, session):
        self.session = session
    
    def detect(self) -> PaginationInfo:
        """
        检测分页类型和元素
        
        Returns:
            PaginationInfo 对象
        """
        info = PaginationInfo()
        
        # 1. 检测下一页按钮
        info.next_selector = self._find_selector(self.NEXT_SELECTORS)
        if info.next_selector:
            info.has_next = True
        
        # 2. 检测上一页按钮
        info.prev_selector = self._find_selector(self.PREV_SELECTORS)
        if info.prev_selector:
            info.has_prev = True
        
        # 3. 检测分页容器
        container = self._find_selector(self.PAGINATION_CONTAINERS)
        if container:
            info.page_links = self._extract_page_links(container)
            info.total_pages = self._count_pages(container)
        
        # 4. 检测分页类型
        info.pagination_type = self._determine_type(info)
        
        logger.debug(f"分页检测完成: type={info.pagination_type.value}, pages={info.total_pages}")
        return info
    
    def _find_selector(self, selectors: List[str]) -> Optional[str]:
        """查找第一个匹配的选择器"""
        js = f'''
        (function() {{
            var selectors = {selectors!r};
            for (var i = 0; i < selectors.length; i++) {{
                var el = document.querySelector(selectors[i]);
                if (el && el.offsetParent !== null) {{
                    return selectors[i];
                }}
            }}
            return null;
        }})()
        '''
        try:
            return self.session.eval_js(js)
        except Exception as e:
            logger.debug(f"查找选择器失败: {e}")
            return None
    
    def _extract_page_links(self, container_selector: str) -> List[Dict[str, str]]:
        """提取分页链接"""
        js = f'''
        (function() {{
            var container = document.querySelector("{container_selector}");
            if (!container) return [];
            
            var links = container.querySelectorAll('a[href]');
            var result = [];
            links.forEach(function(link) {{
                var href = link.href || link.getAttribute('href');
                var text = link.textContent.trim();
                var pageNum = parseInt(text);
                
                // 只保留数字页码链接
                if (!isNaN(pageNum) && pageNum > 0 && href) {{
                    result.push({{
                        page: pageNum,
                        url: href,
                        text: text
                    }});
                }}
            }});
            
            return result;
        }})()
        '''
        try:
            return self.session.eval_js(js) or []
        except Exception:
            return []
    
    def _count_pages(self, container_selector: str) -> Optional[int]:
        """计算总页数"""
        js = f'''
        (function() {{
            var container = document.querySelector("{container_selector}");
            if (!container) return null;
            
            var links = container.querySelectorAll('a[href]');
            var maxPage = 0;
            links.forEach(function(link) {{
                var text = link.textContent.trim();
                var pageNum = parseInt(text);
                if (!isNaN(pageNum) && pageNum > maxPage) {{
                    maxPage = pageNum;
                }}
            }});
            
            // 也检查 class 中包含总页数的元素
            var totalEl = container.querySelector('[class*="total"], [class*="page-count"]');
            if (totalEl) {{
                var totalText = totalEl.textContent;
                var match = totalText.match(/(\\d+)/);
                if (match) {{
                    var total = parseInt(match[1]);
                    if (total > maxPage) {{
                        maxPage = total;
                    }}
                }}
            }}
            
            return maxPage > 0 ? maxPage : null;
        }})()
        '''
        try:
            return self.session.eval_js(js)
        except Exception:
            return None
    
    def _determine_type(self, info: PaginationInfo) -> PaginationType:
        """确定分页类型"""
        if info.next_selector and 'href' in info.next_selector.lower():
            return PaginationType.URL_BASED
        elif info.next_selector:
            return PaginationType.CLICK_NEXT
        elif self._has_load_more():
            return PaginationType.LOAD_MORE
        elif self._has_infinite_scroll():
            return PaginationType.INFINITE_SCROLL
        return PaginationType.UNKNOWN
    
    def _has_load_more(self) -> bool:
        """检查是否有加载更多按钮"""
        for selector in self.LOAD_MORE_SELECTORS:
            try:
                result = self.session.eval_js(f"!!document.querySelector('{selector}')")
                if result:
                    return True
            except Exception:
                continue
        return False
    
    def _has_infinite_scroll(self) -> bool:
        """检查是否有无限滚动特征"""
        js = '''
        (function() {
            var scrollHeight = document.documentElement.scrollHeight;
            var windowHeight = window.innerHeight;
            var scrollTop = window.pageYOffset || document.documentElement.scrollTop;
            
            // 如果页面可以滚动且内容高度远大于窗口高度，可能是无限滚动
            return scrollHeight > windowHeight * 2 && (scrollHeight - scrollTop - windowHeight) < 500;
        })()
        '''
        try:
            return self.session.eval_js(js)
        except Exception:
            return False


class PaginationHandler:
    """
    分页处理器
    
    提供智能翻页、多页结果合并等功能。
    """
    
    def __init__(self, session, detector: Optional[PaginationDetector] = None):
        self.session = session
        self.detector = detector or PaginationDetector(session)
        self._page_results: List[PageResult] = []
    
    def get_pagination_info(self) -> PaginationInfo:
        """获取当前页面分页信息"""
        return self.detector.detect()
    
    def go_to_page(self, page: int) -> bool:
        """
        跳转到指定页面
        
        Args:
            page: 目标页码
        
        Returns:
            是否成功
        """
        info = self.get_pagination_info()
        
        if page <= 1:
            return True  # 当前就是第一页
        
        if info.pagination_type == PaginationType.URL_BASED:
            return self._navigate_url_based(page)
        elif info.pagination_type == PaginationType.CLICK_NEXT:
            return self._click_next(page)
        elif info.pagination_type == PaginationType.LOAD_MORE:
            return self._load_more(page)
        else:
            logger.warning(f"不支持的分页类型: {info.pagination_type}")
            return False
    
    def _navigate_url_based(self, page: int) -> bool:
        """URL 方式翻页"""
        # 查找页码链接
        js = f'''
        (function() {{
            var links = document.querySelectorAll('a[href]');
            for (var i = 0; i < links.length; i++) {{
                var text = links[i].textContent.trim();
                if (parseInt(text) === {page}) {{
                    return links[i].href;
                }}
            }}
            return null;
        }})()
        '''
        try:
            target_url = self.session.eval_js(js)
            if target_url:
                self.session.send("Page.navigate", {"url": target_url})
                return True
        except Exception as e:
            logger.error(f"URL 翻页失败: {e}")
        return False
    
    def _click_next(self, page: int) -> bool:
        """点击下一页按钮"""
        info = self.get_pagination_info()
        if not info.next_selector:
            return False
        
        for _ in range(page - 1):
            try:
                self.session.eval_js(f'''
                (function() {{
                    var el = document.querySelector("{info.next_selector}");
                    if (el && el.offsetParent !== null) {{
                        el.click();
                        return true;
                    }}
                    return false;
                }})()
                ''')
            except Exception as e:
                logger.error(f"点击下一页失败: {e}")
                return False
        
        return True
    
    def _load_more(self, page: int) -> bool:
        """加载更多"""
        js = '''
        (function() {
            var selectors = [
                "button[class*='load-more']",
                "a[class*='load-more']",
                ".load-more",
                "button[text()='加载更多']",
                "a[text()='加载更多']"
            ];
            for (var i = 0; i < selectors.length; i++) {
                var el = document.querySelector(selectors[i]);
                if (el && el.offsetParent !== null) {
                    el.click();
                    return true;
                }
            }
            return false;
        })()
        '''
        try:
            return self.session.eval_js(js)
        except Exception as e:
            logger.error(f"加载更多失败: {e}")
            return False
    
    def fetch_all_pages(self, max_pages: int = 5, result_extractor: Optional[Callable] = None) -> List[PageResult]:
        """
        获取所有页面的结果
        
        Args:
            max_pages: 最大页数
            result_extractor: 结果提取函数，接收 session 和 page 参数，返回结果列表
        
        Returns:
            所有页面的结果列表
        """
        self._page_results = []
        current_page = 1
        
        while current_page <= max_pages:
            # 提取当前页结果
            if result_extractor:
                results = result_extractor(self.session, current_page)
            else:
                results = []
            
            # 获取分页信息
            info = self.get_pagination_info()
            
            page_result = PageResult(
                page=current_page,
                results=results,
                pagination=info,
            )
            self._page_results.append(page_result)
            
            logger.info(f"已获取第 {current_page} 页，{len(results)} 条结果")
            
            # 检查是否还有下一页
            if not info.has_next or current_page >= (info.total_pages or max_pages):
                break
            
            # 翻到下一页
            if not self.go_to_page(current_page + 1):
                logger.warning(f"无法翻到第 {current_page + 1} 页，停止抓取")
                break
            
            current_page += 1
        
        return self._page_results
    
    def merge_results(self) -> List[Dict[str, Any]]:
        """合并所有页面结果"""
        merged = []
        for page_result in self._page_results:
            merged.extend(page_result.results)
        return merged
    
    def get_total_results(self) -> int:
        """获取总结果数"""
        total = 0
        for pr in self._page_results:
            total += len(pr.results)
        return total
    
    def get_summary(self) -> Dict[str, Any]:
        """获取分页抓取摘要"""
        return {
            "total_pages": len(self._page_results),
            "total_results": self.get_total_results(),
            "pages": [pr.to_dict() for pr in self._page_results],
        }


# 便捷函数
def detect_pagination(session) -> PaginationInfo:
    """检测分页"""
    detector = PaginationDetector(session)
    return detector.detect()


def create_pagination_handler(session) -> PaginationHandler:
    """创建分页处理器"""
    return PaginationHandler(session)
