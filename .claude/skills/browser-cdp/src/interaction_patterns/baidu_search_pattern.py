"""
baidu_search_pattern.py - 百度搜索示例实现

基于 SearchPattern 实现百度搜索引擎的完整搜索流程。
用于验证新架构的可运行性。
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

from ._base import SearchResults
from .search_pattern import SearchPattern

logger = logging.getLogger(__name__)


class BaiduSearchPattern(SearchPattern):
    """百度搜索模式"""
    
    def __init__(self, session, domain: str = "baidu.com", config: Optional[Dict] = None):
        # 百度特有配置
        baidu_config = {
            "search_url": "https://www.baidu.com/s?wd={query}",
            "result_item": ".result.c-container",
            "result_title": ".t a",
            "result_url": ".t a[href]",
            "result_snippet": ".content-2C2Si",
            "next_page": 'a.prev,a.next,'
        }
        if config:
            baidu_config.update(config)
        super().__init__(session, domain, baidu_config)
        self._site_name = "baidu"
        
    async def execute(self, query: str, max_pages: int = 1, **kwargs) -> SearchResults:
        """执行百度搜索"""
        from datetime import datetime
        self._record_start()
        
        try:
            # 1. 导航到百度首页
            await self._session.navigate("https://www.baidu.com")
            await self._wait.wait_for_selector("#kw", timeout=10.0)
            
            # 2. 输入关键词
            search_input = self._get_selector("search_input") or self._get_selector("kw")
            if search_input:
                await self._session.click(search_input.value)
                await self._session.type_text(query)
            
            # 3. 提交搜索
            search_btn = self._get_selector("search_button") or self._get_selector("sd")
            if search_btn:
                await self._session.click(search_btn.value)
            else:
                await self._session.press_key("Enter")
            
            # 4. 等待结果加载
            await self._wait.wait_for_selector(".result", timeout=15.0)
            await self._wait.wait_for_network_idle(timeout=10.0)
            
            # 5. 解析结果
            results = await self._parse_results(query)
            
            # 6. 翻页
            if max_pages > 1:
                results = await self._paginate(results, max_pages)
            
            results.pattern_used = f"BaiduSearchPattern({self._site_name})"
            return self._record_latency(results.to_dict())
            
        except Exception as e:
            logger.error(f"BaiduSearchPattern failed: {e}")
            return SearchResults(
                success=False,
                query=query,
                error_message=str(e),
                pattern_used="BaiduSearchPattern"
            )
        
    async def _parse_results(self, query: str) -> SearchResults:
        """解析百度搜索结果"""
        from ..core.selector_manager import SelectorType
        
        # 注册百度专用选择器
        baidu_selectors = {
            "search_input": Selector(type=SelectorType.CSS, value="#kw", description="百度输入框"),
            "search_button": Selector(type=SelectorType.CSS, value="#su", description="百度按钮"),
            "result_item": Selector(type=SelectorType.CSS, value=".result.c-container", description="结果容器"),
            "result_title": Selector(type=SelectorType.CSS, value=".t a", description="结果标题"),
            "result_url": Selector(type=SelectorType.ATTRIBUTE, value=".t a[href]", description="结果URL"),
            "result_snippet": Selector(type=SelectorType.CSS, value=".content-2C2Si", description="结果摘要"),
            "next_page": Selector(type=SelectorType.CSS, value="a.pagination-next", description="下一页"),
        }
        for name, sel in baidu_selectors.items():
            self._selectors.register(self._domain, name, sel)
        
        items = await self._session.query_selector_all(".result.c-container")
        results = []
        
        for item in items[:20]:  # 限制最多20条
            try:
                title_el = await item.query_selector(".t a")
                url = await title_el.get_attribute("href") if title_el else ""
                title = await title_el.get_text() if title_el else ""
                
                snippet_el = await item.query_selector(".content-2C2Si")
                snippet = await snippet_el.get_text() if snippet_el else ""
                
                # 提取来源域名
                source_domain = ""
                if url:
                    from urllib.parse import urlparse
                    parsed = urlparse(url)
                    source_domain = parsed.netloc
                
                results.append(SearchResultItem(
                    title=title[:100],
                    url=url,
                    snippet=snippet[:300],
                    source_domain=source_domain,
                    metadata={"source": "baidu"}
                ))
            except Exception as e:
                logger.warning(f"Parse result item failed: {e}")
                continue
        
        return SearchResults(success=True, query=query, results=results)
