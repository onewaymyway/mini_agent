"""
search_pagination.py - 搜索结果分页处理模块

支持：
- 自动翻页
- 分页导航检测
- 分页参数构造
- 翻页状态追踪
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class PaginationInfo:
    """分页信息"""
    current_page: int = 1
    total_pages: int = 0
    total_results: int = 0
    page_size: int = 20
    has_next: bool = False
    has_prev: bool = False
    next_page_url: Optional[str] = None
    prev_page_url: Optional[str] = None
    
    def to_dict(self) -> dict:
        return {
            "current_page": self.current_page,
            "total_pages": self.total_pages,
            "total_results": self.total_results,
            "page_size": self.page_size,
            "has_next": self.has_next,
            "has_prev": self.has_prev,
            "next_page_url": self.next_page_url,
            "prev_page_url": self.prev_page_url,
        }


class PaginationDetector:
    """
    分页检测器
    
    自动检测页面的分页结构，返回分页信息。
    """
    
    # 分页元素选择器
    PAGINATION_SELECTORS = [
        # 通用分页容器
        ".pagination", ".pager", ".page-nav", ".page_navigation",
        "nav[role='navigation']", "[class*='pagination']",
        # 页码链接
        "a[href*='page']", "a[href*='p=']", "a[href*='start=']",
        "a[class*='page']", "a[class*='next']", "a[class*='prev']",
        # 按钮式分页
        "button[class*='page']", "button[class*='next']", "button[class*='prev']",
        # 数字分页
        "[class*='page-num']", ".page-numbers", "li[class*='page']",
    ]
    
    # 下一页选择器
    NEXT_PAGE_SELECTORS = [
        "a[rel='next']",
        "a[class*='next']",
        "a[href*='page=2']",
        "a[href*='start=20']",
        "button[class*='next']",
        "[aria-label*='next']",
        "[data-page='2']",
    ]
    
    # 上一页选择器
    PREV_PAGE_SELECTORS = [
        "a[rel='prev']",
        "a[class*='prev']",
        "a[class*='previous']",
        "button[class*='prev']",
        "[aria-label*='prev']",
        "[aria-label*='previous']",
    ]
    
    def __init__(self, session):
        self.session = session
    
    def detect(self, url: str = None) -> PaginationInfo:
        """
        检测当前页面的分页信息
        
        Args:
            url: 可选，当前页面 URL
        
        Returns:
            PaginationInfo 对象
        """
        info = PaginationInfo()
        
        try:
            # 检测总页数
            total_pages = self._detect_total_pages()
            info.total_pages = total_pages
            
            # 检测总结果数
            total_results = self._detect_total_results()
            info.total_results = total_results
            
            # 检测当前页码
            current_page = self._detect_current_page()
            info.current_page = current_page
            
            # 检测下一页
            next_url = self._detect_next_page_url()
            info.next_page_url = next_url
            info.has_next = next_url is not None
            
            # 检测上一页
            prev_url = self._detect_prev_page_url()
            info.prev_page_url = prev_url
            info.has_prev = prev_url is not None
            
            logger.debug(f"分页检测完成: 第{info.current_page}页/共{info.total_pages}页")
        except Exception as e:
            logger.error(f"分页检测失败: {e}")
        
        return info
    
    def _detect_total_pages(self) -> int:
        """检测总页数"""
        js = """
        (function() {
            var totalPages = 0;
            
            // 方法1: 查找页码链接中的最大数字
            var pageLinks = document.querySelectorAll('a[href*="page"], a[href*="p="], a[class*="page"]');
            pageLinks.forEach(function(link) {
                var text = link.textContent.trim();
                var num = parseInt(text);
                if (!isNaN(num) && num > totalPages) {
                    totalPages = num;
                }
            });
            
            // 方法2: 查找分页容器中的页码
            var pagination = document.querySelector('.pagination, .pager, [class*="pagination"]');
            if (pagination) {
                var links = pagination.querySelectorAll('a');
                links.forEach(function(link) {
                    var text = link.textContent.trim();
                    var num = parseInt(text);
                    if (!isNaN(num) && num > totalPages) {
                        totalPages = num;
                    }
                });
            }
            
            // 方法3: 从 URL 参数推断
            var urlParams = new URLSearchParams(window.location.search);
            var pageParam = urlParams.get('page') || urlParams.get('p') || urlParams.get('start');
            if (pageParam) {
                var pageNum = parseInt(pageParam);
                if (!isNaN(pageNum)) {
                    // 尝试从 URL 推断总页数
                    var lastPageMatch = window.location.href.match(/[?&]page=(\\d+)/);
                    if (lastPageMatch) {
                        totalPages = Math.max(totalPages, parseInt(lastPageMatch[1]));
                    }
                }
            }
            
            return totalPages || 1;
        })()
        """
        try:
            result = self.session.eval_js(js)
            return max(1, int(result) if result else 1)
        except Exception:
            return 1
    
    def _detect_total_results(self) -> int:
        """检测总结果数"""
        js = """
        (function() {
            var total = 0;
            
            // 方法1: 查找结果总数文本
            var textPatterns = [
                /共\\s*(\\d+)\\s*条/, 
                /found\\s*(\\d+)/i,
                /results?\\s*:\\s*(\\d+)/i,
                /\\d+,?\\d+\\s*results?/i,
                /约\\s*(\\d+)/,
            ];
            
            var bodyText = document.body ? document.body.innerText : '';
            for (var pattern of textPatterns) {
                var match = bodyText.match(pattern);
                if (match) {
                    total = parseInt(match[1].replace(/,/g, ''));
                    if (!isNaN(total) && total > 0) {
                        return total;
                    }
                }
            }
            
            // 方法2: 查找特定元素
            var resultCountEl = document.querySelector('[class*="result-count"], [class*="total"]');
            if (resultCountEl) {
                var text = resultCountEl.textContent.trim();
                var match = text.match(/\\d+/);
                if (match) {
                    total = parseInt(match[0]);
                }
            }
            
            return total || 0;
        })()
        """
        try:
            result = self.session.eval_js(js)
            return int(result) if result else 0
        except Exception:
            return 0
    
    def _detect_current_page(self) -> int:
        """检测当前页码"""
        js = """
        (function() {
            // 从 URL 参数获取
            var urlParams = new URLSearchParams(window.location.search);
            var pageParam = urlParams.get('page') || urlParams.get('p') || urlParams.get('start');
            if (pageParam) {
                var pageNum = parseInt(pageParam);
                if (!isNaN(pageNum)) {
                    return pageNum;
                }
            }
            
            // 从分页元素获取
            var activePage = document.querySelector('.active, [class*="active"], [aria-current="page"]');
            if (activePage) {
                var text = activePage.textContent.trim();
                var num = parseInt(text);
                if (!isNaN(num)) {
                    return num;
                }
            }
            
            return 1;
        })()
        """
        try:
            result = self.session.eval_js(js)
            return max(1, int(result) if result else 1)
        except Exception:
            return 1
    
    def _detect_next_page_url(self) -> Optional[str]:
        """检测下一页 URL"""
        js = """
        (function() {
            // 方法1: 查找 rel="next" 链接
            var nextLink = document.querySelector("a[rel='next']");
            if (nextLink && nextLink.href) {
                return nextLink.href;
            }
            
            // 方法2: 查找 class 包含 next 的链接
            var nextButtons = document.querySelectorAll("a[class*='next'], button[class*='next']");
            for (var btn of nextButtons) {
                var href = btn.href || btn.getAttribute('data-href');
                if (href && !href.includes('javascript')) {
                    return href;
                }
            }
            
            // 方法3: 查找 aria-label 包含 next 的链接
            var nextAria = document.querySelectorAll("[aria-label*='next'], [aria-label*='下一页']");
            for (var el of nextAria) {
                var href = el.href || el.getAttribute('href');
                if (href && !href.includes('javascript')) {
                    return href;
                }
            }
            
            // 方法4: 从当前 URL 构造下一页 URL
            var url = new URL(window.location.href);
            var pageParam = url.searchParams.get('page') || url.searchParams.get('p');
            if (pageParam) {
                var nextPage = parseInt(pageParam) + 1;
                url.searchParams.set('page', nextPage);
                url.searchParams.set('p', nextPage);
                return url.toString();
            }
            
            return null;
        })()
        """
        try:
            result = self.session.eval_js(js)
            return result if result and result.startswith(('http://', 'https://')) else None
        except Exception:
            return None
    
    def _detect_prev_page_url(self) -> Optional[str]:
        """检测上一页 URL"""
        js = """
        (function() {
            // 方法1: 查找 rel="prev" 链接
            var prevLink = document.querySelector("a[rel='prev']");
            if (prevLink && prevLink.href) {
                return prevLink.href;
            }
            
            // 方法2: 查找 class 包含 prev 的链接
            var prevButtons = document.querySelectorAll("a[class*='prev'], button[class*='prev']");
            for (var btn of prevButtons) {
                var href = btn.href || btn.getAttribute('data-href');
                if (href && !href.includes('javascript')) {
                    return href;
                }
            }
            
            // 方法3: 从当前 URL 构造上一页 URL
            var url = new URL(window.location.href);
            var pageParam = url.searchParams.get('page') || url.searchParams.get('p');
            if (pageParam) {
                var prevPage = parseInt(pageParam) - 1;
                if (prevPage >= 1) {
                    url.searchParams.set('page', prevPage);
                    url.searchParams.set('p', prevPage);
                    return url.toString();
                }
            }
            
            return null;
        })()
        """
        try:
            result = self.session.eval_js(js)
            return result if result and result.startswith(('http://', 'https://')) else None
        except Exception:
            return None


class PaginationNavigator:
    """
    分页导航器
    
    支持自动翻页和分页状态追踪。
    """
    
    def __init__(self, session, delay_range: tuple = (1, 3)):
        self.session = session
        self.delay_range = delay_range
        self._page_history: List[Dict[str, Any]] = []
    
    def go_to_page(self, page: int, base_url: str = None) -> bool:
        """
        导航到指定页
        
        Args:
            page: 目标页码
            base_url: 基础 URL（可选）
        
        Returns:
            是否成功
        """
        try:
            if base_url:
                # 构造分页 URL
                from urllib.parse import urlparse, urlencode, parse_qs, urlunparse
                parsed = urlparse(base_url)
                query_params = parse_qs(parsed.query)
                query_params['page'] = [str(page)]
                new_query = urlencode(query_params, doseq=True)
                new_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, 
                                     parsed.params, new_query, parsed.fragment))
            else:
                # 从当前 URL 构造
                current_url = self._get_current_url()
                from urllib.parse import urlparse, urlencode, parse_qs, urlunparse
                parsed = urlparse(current_url)
                query_params = parse_qs(parsed.query)
                query_params['page'] = [str(page)]
                new_query = urlencode(query_params, doseq=True)
                new_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path,
                                     parsed.params, new_query, parsed.fragment))
            
            # 导航到新页面
            self.session.send("Page.navigate", {"url": new_url})
            
            # 记录历史
            self._page_history.append({
                "page": page,
                "url": new_url,
                "timestamp": time.time(),
            })
            
            logger.info(f"已导航到第 {page} 页")
            return True
        except Exception as e:
            logger.error(f"导航到第 {page} 页失败: {e}")
            return False
    
    def go_next(self) -> bool:
        """
        导航到下一页
        
        Returns:
            是否成功
        """
        current_page = self._get_current_page()
        return self.go_to_page(current_page + 1)
    
    def go_prev(self) -> bool:
        """
        导航到上一页
        
        Returns:
            是否成功
        """
        current_page = self._get_current_page()
        return self.go_to_page(max(1, current_page - 1))
    
    def wait_for_page_load(self, timeout: float = 30.0) -> bool:
        """
        等待页面加载完成
        
        Args:
            timeout: 超时时间（秒）
        
        Returns:
            是否成功
        """
        import random
        delay = random.uniform(*self.delay_range)
        time.sleep(delay)
        
        # 等待网络空闲
        try:
            self.session.send("Network.enable")
            # 等待一段时间让网络请求完成
            time.sleep(2)
            return True
        except Exception as e:
            logger.warning(f"等待页面加载失败: {e}")
            return False
    
    def _get_current_url(self) -> str:
        """获取当前 URL"""
        try:
            result = self.session.send("Runtime.evaluate", {"expression": "location.href"})
            return result.get("result", {}).get("value", "")
        except Exception:
            return ""
    
    def _get_current_page(self) -> int:
        """获取当前页码"""
        url = self._get_current_url()
        try:
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            page = params.get('page', ['1'])[0]
            return max(1, int(page))
        except (ValueError, IndexError):
            return 1
    
    def get_page_history(self) -> List[Dict[str, Any]]:
        """获取分页历史"""
        return self._page_history.copy()
    
    def clear_history(self):
        """清空分页历史"""
        self._page_history.clear()


class AutoPagination:
    """
    自动分页器
    
    支持自动遍历所有分页，收集结果。
    """
    
    def __init__(self, session, max_pages: int = 10, delay_range: tuple = (1, 3)):
        self.session = session
        self.max_pages = max_pages
        self.delay_range = delay_range
        self.detector = PaginationDetector(session)
        self.navigator = PaginationNavigator(session, delay_range)
        self._collected_results: List[Dict[str, Any]] = []
    
    def paginate(self, base_url: str = None, callback: Callable = None) -> List[Dict[str, Any]]:
        """
        自动分页遍历
        
        Args:
            base_url: 基础 URL
            callback: 每页处理回调函数，接收 (page_info, results) 参数
        
        Returns:
            所有收集的结果
        """
        self._collected_results.clear()
        self.navigator.clear_history()
        
        current_page = 1
        
        while current_page <= self.max_pages:
            logger.info(f"正在处理第 {current_page} 页...")
            
            # 检测分页信息
            page_info = self.detector.detect(base_url)
            page_info.current_page = current_page
            
            # 收集当前页结果（通过回调）
            page_results = []
            if callback:
                page_results = callback(page_info, self.session)
            
            self._collected_results.extend(page_results)
            
            logger.info(f"第 {current_page} 页收集到 {len(page_results)} 条结果")
            
            # 检查是否还有下一页
            if not page_info.has_next or current_page >= page_info.total_pages:
                break
            
            # 导航到下一页
            if not self.navigator.go_next():
                logger.warning(f"无法导航到第 {current_page + 1} 页，停止分页")
                break
            
            # 等待页面加载
            self.navigator.wait_for_page_load()
            
            current_page += 1
        
        logger.info(f"分页遍历完成，共收集 {len(self._collected_results)} 条结果")
        return self._collected_results
    
    def get_results(self) -> List[Dict[str, Any]]:
        """获取已收集的结果"""
        return self._collected_results.copy()
    
    def reset(self):
        """重置分页器"""
        self._collected_results.clear()
        self.navigator.clear_history()


# 便捷函数
def detect_pagination(session) -> PaginationInfo:
    """检测分页信息"""
    detector = PaginationDetector(session)
    return detector.detect()


def create_auto_pagination(session, max_pages: int = 10) -> AutoPagination:
    """创建自动分页器"""
    return AutoPagination(session, max_pages)
