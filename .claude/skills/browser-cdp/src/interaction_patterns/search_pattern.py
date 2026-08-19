"""
search_pattern.py - 通用搜索交互模式

流程：打开搜索页 → 输入关键词 → 触发搜索 → 等待加载 → 解析结果 → （可选）翻页
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from ._base import InteractionPattern, SearchResults, SearchResultItem

logger = logging.getLogger(__name__)


class SearchPattern(InteractionPattern):
    """通用搜索模式"""
    
    def __init__(self, session, domain: str, config: Optional[Dict] = None):
        super().__init__(session, domain, config)
        self._max_pages: int = config.get("max_pages", 1) if config else 1
    
    async def execute(self, query: str, max_pages: int = 1, **kwargs) -> SearchResults:
        """执行搜索流程"""
        self._record_start()
        
        try:
            # 1. 导航到搜索页
            await self._navigate_to_search_page(query)
            
            # 2. 输入关键词并触发搜索
            await self._input_query(query)
            
            # 3. 等待搜索结果加载
            await self._wait.wait_for_network_idle(timeout=15.0)
            
            # 4. 解析结果
            results = await self._parse_results(query)
            
            # 5. 翻页（如需要）
            if max_pages > 1 and results.results:
                results = await self._paginate(results, max_pages)
            
            results.pattern_used = "SearchPattern"
            return self._record_latency(results.to_dict())
            
        except Exception as e:
            logger.error(f"SearchPattern failed for {self._domain}: {e}")
            return SearchResults(
                success=False,
                query=query,
                error_message=str(e),
                pattern_used="SearchPattern"
            )
    
    async def _navigate_to_search_page(self, query: str):
        """导航到搜索页"""
        search_url = self._config.get("search_url", f"https://{self._domain}/search?q={query}")
        await self._session.navigate(search_url)
    
    async def _input_query(self, query: str):
        """输入关键词并触发搜索"""
        search_input_sel = self._get_selector("search_input")
        if search_input_sel:
            await self._session.click(search_input_sel.value)
            await self._session.type_text(query)
        
        # 尝试点击搜索按钮或按 Enter
        search_btn_sel = self._get_selector("search_button")
        if search_btn_sel:
            await self._session.click(search_btn_sel.value)
        else:
            await self._session.press_key("Enter")
    
    async def _parse_results(self, query: str) -> SearchResults:
        """解析搜索结果"""
        result_item_sel = self._get_selector("result_item")
        if not result_item_sel:
            return SearchResults(success=True, query=query, results=[])
        
        items = await self._session.query_selector_all(result_item_sel.value)
        results = []
        
        for item in items:
            try:
                title_sel = self._get_selector("result_title")
                url_sel = self._get_selector("result_url")
                snippet_sel = self._get_selector("result_snippet")
                
                title = await item.get_text(title_sel.value) if title_sel else ""
                url = await item.get_attribute(url_sel.value, "href") if url_sel else ""
                snippet = await item.get_text(snippet_sel.value) if snippet_sel else ""
                
                results.append(SearchResultItem(
                    title=title[:200],
                    url=url,
                    snippet=snippet[:500],
                    source_domain=self._domain
                ))
            except Exception as e:
                logger.warning(f"Failed to parse result item: {e}")
                continue
        
        return SearchResults(success=True, query=query, results=results)
    
    async def _paginate(self, results: SearchResults, max_pages: int) -> SearchResults:
        """翻页获取更多结果"""
        all_results = results.results[:]
        
        for page in range(2, max_pages + 1):
            next_page_sel = self._get_selector("next_page")
            if not next_page_sel:
                break
            
            try:
                await self._session.click(next_page_sel.value)
                await self._wait.wait_for_network_idle(timeout=10.0)
                
                page_results = await self._parse_results(results.query)
                all_results.extend(page_results.results)
                
                if page_results.is_empty:
                    break
                    
            except Exception as e:
                logger.warning(f"Pagination failed on page {page}: {e}")
                break
        
        results.results = all_results
        return results