#!/usr/bin/env python3
"""
realtime_searcher.py - 实时数据搜索器

支持实时数据网站的搜索，包括：
- 股票行情（东方财富、同花顺）
- 加密货币（CoinMarketCap、币安）
- 实时新闻
- 天气数据
"""
import asyncio
import json
import logging
import random
import time
from typing import List, Dict, Optional, Any
from datetime import datetime
from urllib.parse import quote

from src.searchers.base import BaseSearcher, SearcherConfig, SearchResult
from src.searchers.utils import random_delay

logger = logging.getLogger(__name__)


class RealtimeDataConfig:
    """实时数据配置"""
    refresh_interval: int = 60  # 数据刷新间隔（秒）
    cache_enabled: bool = True
    cache_ttl: int = 300  # 缓存过期时间（秒）
    max_retries: int = 3


class StockSearcher(BaseSearcher):
    """股票行情搜索器"""
    
    def __init__(self, config: Optional[SearcherConfig] = None):
        super().__init__(config)
        self._cache: Dict[str, Dict] = {}
        self._cache_time: Dict[str, float] = {}
    
    @property
    def source_name(self) -> str:
        return "stock"
    
    @property
    def supported_types(self) -> List[str]:
        return ["stock_quote", "stock_search", "fund_search"]
    
    async def search(self, query: str, config: Optional[SearcherConfig] = None) -> List[SearchResult]:
        """搜索股票"""
        cfg = config or self.config
        results = []
        
        try:
            # 检查缓存
            cache_key = f"stock_{query}"
            if cfg.cache_enabled and cache_key in self._cache:
                if time.time() - self._cache_time.get(cache_key, 0) < cfg.refresh_interval:
                    logger.debug(f"使用缓存数据：{query}")
                    return self._cache[cache_key]
            
            # 搜索股票
            results = await self._search_stock(query, cfg)
            
            # 更新缓存
            if cfg.cache_enabled:
                self._cache[cache_key] = results
                self._cache_time[cache_key] = time.time()
            
        except Exception as e:
            logger.error(f"股票搜索失败: {e}")
        
        return results
    
    async def _search_stock(self, query: str, config: SearcherConfig) -> List[SearchResult]:
        """搜索股票信息"""
        results = []
        
        # 东方财富股票搜索 API
        search_url = f"https://searchapi.eastmoney.com/bussiness/web/StockSearch"
        params = {
            "q": query,
            "type": 14,
            "token": "D33BF32E0A9303E7E6F5C2E4E6F5C2E4",
            "count": config.max_results
        }
        
        try:
            # 使用 CDP 获取数据
            import aiohttp
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://quote.eastmoney.com/"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(search_url, params=params, headers=headers, timeout=config.wait_timeout) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        
                        # 解析结果
                        if data.get("Success"):
                            for item in data.get("Data", {}).get("QuotationTable", {}).get("List", [])[:config.max_results]:
                                results.append(SearchResult(
                                    source=self.source_name,
                                    title=f"{item.get('Name', '')} ({item.get('Code', '')})",
                                    url=f"https://quote.eastmoney.com/{item.get('Code', '')}.html",
                                    snippet=f"现价：{item.get('NowPrice', '')} 涨跌幅：{item.get('ChangeRatio', '')}%",
                                    metadata={
                                        "code": item.get("Code"),
                                        "name": item.get("Name"),
                                        "price": item.get("NowPrice"),
                                        "change": item.get("ChangeRatio"),
                                        "volume": item.get("Volume")
                                    }
                                ))
        except Exception as e:
            logger.error(f"获取股票数据失败: {e}")
        
        return results
    
    async def get_quote(self, code: str, config: Optional[SearcherConfig] = None) -> Dict:
        """获取股票实时行情"""
        cfg = config or self.config
        
        # 检查缓存
        cache_key = f"quote_{code}"
        if cfg.cache_enabled and cache_key in self._cache:
            if time.time() - self._cache_time.get(cache_key, 0) < cfg.refresh_interval:
                return self._cache[cache_key]
        
        try:
            # 东方财富实时行情 API
            url = f"https://push2.eastmoney.com/api/qt/stock/get"
            params = {
                "secid": f"1.{code}" if code.startswith(("6", "9")) else f"0.{code}",
                "fields": "f43,f44,f45,f46,f47,f48,f57,f58,f60,f170",
                "ut": "fa5fd1943c7b386f172d6893dbbd1d0c"
            }
            
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=cfg.wait_timeout) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        result = data.get("data", {})
                        
                        # 缓存结果
                        if cfg.cache_enabled:
                            self._cache[cache_key] = result
                            self._cache_time[cache_key] = time.time()
                        
                        return result
        except Exception as e:
            logger.error(f"获取行情失败: {e}")
        
        return {}

    async def get_detail(self, url: str, config: Optional[SearcherConfig] = None) -> Dict:
        """获取股票详情"""
        cfg = config or self.config
        try:
            # 从 URL 提取股票代码
            import re
            match = re.search(r'/(\d+)\.html', url)
            if match:
                code = match.group(1)
                return await self.get_quote(code, cfg)
        except Exception as e:
            logger.error(f"获取详情失败: {e}")
        return {}


class CryptoSearcher(BaseSearcher):
    """加密货币搜索器"""
    
    def __init__(self, config: Optional[SearcherConfig] = None):
        super().__init__(config)
        self._cache: Dict[str, Dict] = {}
        self._cache_time: Dict[str, float] = {}
    
    @property
    def source_name(self) -> str:
        return "crypto"
    
    @property
    def supported_types(self) -> List[str]:
        return ["crypto_search", "crypto_quote"]
    
    async def search(self, query: str, config: Optional[SearcherConfig] = None) -> List[SearchResult]:
        """搜索加密货币"""
        cfg = config or self.config
        results = []
        
        try:
            cache_key = f"crypto_{query}"
            if cfg.cache_enabled and cache_key in self._cache:
                if time.time() - self._cache_time.get(cache_key, 0) < cfg.refresh_interval:
                    return self._cache[cache_key]
            
            results = await self._search_crypto(query, cfg)
            
            if cfg.cache_enabled:
                self._cache[cache_key] = results
                self._cache_time[cache_key] = time.time()
                
        except Exception as e:
            logger.error(f"加密货币搜索失败: {e}")
        
        return results
    
    async def _search_crypto(self, query: str, config: SearcherConfig) -> List[SearchResult]:
        """搜索加密货币"""
        results = []
        
        # CoinMarketCap API
        search_url = "https://api.coinmarketcap.com/data-api/v3/core/search"
        params = {
            "keyword": query,
            "pageSize": config.max_results,
            "sortBy": "market_cap",
            "sortType": "desc"
        }
        
        try:
            import aiohttp
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(search_url, params=params, headers=headers, timeout=config.wait_timeout) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        
                        for item in data.get("data", {}).get("cryptoCurrencyList", [])[:config.max_results]:
                            results.append(SearchResult(
                                source=self.source_name,
                                title=f"{item.get('name', '')} ({item.get('symbol', '')})",
                                url=f"https://coinmarketcap.com/currencies/{item.get('slug', '')}/",
                                snippet=f"价格：${item.get('price', 0):.6f} 市值：${item.get('marketCap', 0):,.0f}",
                                metadata={
                                    "id": item.get("id"),
                                    "symbol": item.get("symbol"),
                                    "name": item.get("name"),
                                    "price": item.get("price"),
                                    "market_cap": item.get("marketCap"),
                                    "change_24h": item.get("changePercentage24Hr")
                                }
                            ))
        except Exception as e:
            logger.error(f"获取加密货币数据失败: {e}")
        
        return results
    
    async def get_trending(self, config: Optional[SearcherConfig] = None) -> List[SearchResult]:
        """获取热门加密货币"""
        cfg = config or self.config
        
        try:
            url = "https://api.coinmarketcap.com/data-api/v3/core/featured-trending-cryptocurrencies/list"
            params = {"pageSize": cfg.max_results}
            
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=cfg.wait_timeout) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        results = []
                        
                        for item in data.get("data", {}).get("cryptoCurrencyList", [])[:cfg.max_results]:
                            results.append(SearchResult(
                                source=self.source_name,
                                title=f"{item.get('name', '')} ({item.get('symbol', '')})",
                                url=f"https://coinmarketcap.com/currencies/{item.get('slug', '')}/",
                                metadata={"trending": True}
                            ))
                        
                        return results
        except Exception as e:
            logger.error(f"获取热门加密货币失败: {e}")
        
        return []

    async def get_detail(self, url: str, config: Optional[SearcherConfig] = None) -> Dict:
        """获取加密货币详情"""
        cfg = config or self.config
        try:
            import re
            match = re.search(r'/currencies/([^/]+)/', url)
            if match:
                slug = match.group(1)
                return {"slug": slug, "url": url}
        except Exception as e:
            logger.error(f"获取详情失败: {e}")
        return {}


class NewsSearcher(BaseSearcher):
    """实时新闻搜索器"""
    
    def __init__(self, config: Optional[SearcherConfig] = None):
        super().__init__(config)
    
    @property
    def source_name(self) -> str:
        return "news"
    
    @property
    def supported_types(self) -> List[str]:
        return ["news_search", "breaking_news"]
    
    async def search(self, query: str, config: Optional[SearcherConfig] = None) -> List[SearchResult]:
        """搜索新闻"""
        cfg = config or self.config
        results = []
        
        try:
            # 百度新闻搜索
            url = f"https://www.baidu.com/s?wd={quote(query)}&tn=news"
            
            # 使用 CDP 访问
            from src.core.playwright_session import PlaywrightSession
            session = PlaywrightSession(port=cfg.port, tab_id=cfg.tab_id)
            
            await session.navigate(url, wait_strategy=cfg.wait_strategy, wait_timeout=cfg.wait_timeout)
            await random_delay(2, 4)
            
            # 提取新闻结果
            news_items = await session.query_selector_all(".result-op")
            
            for item in news_items[:cfg.max_results]:
                try:
                    title_elem = await item.query_selector("h3 a")
                    if title_elem:
                        title = await title_elem.inner_text()
                        href = await title_elem.get_attribute("href")
                        
                        # 获取摘要
                        snippet_elem = await item.query_selector(".c-author")
                        snippet = await snippet_elem.inner_text() if snippet_elem else ""
                        
                        results.append(SearchResult(
                            source=self.source_name,
                            title=title,
                            url=href or "",
                            snippet=snippet,
                            metadata={"source": "baidu_news"}
                        ))
                except Exception as e:
                    logger.debug(f"解析新闻项失败: {e}")
            
            await session.close()
            
        except Exception as e:
            logger.error(f"新闻搜索失败: {e}")
        
        return results

    async def get_detail(self, url: str, config: Optional[SearcherConfig] = None) -> Dict:
        """获取新闻详情"""
        cfg = config or self.config
        return {"url": url, "source": "news"}


class RealtimeSearcherFactory:
    """实时数据搜索器工厂"""
    
    _searchers = {
        "stock": StockSearcher,
        "crypto": CryptoSearcher,
        "news": NewsSearcher
    }
    
    @classmethod
    def create(cls, site: str) -> Optional[BaseSearcher]:
        """创建实时数据搜索器"""
        site_lower = site.lower()
        
        if any(kw in site_lower for kw in ["stock", "eastmoney", "同花顺", "行情"]):
            return cls._searchers["stock"]()
        elif any(kw in site_lower for kw in ["crypto", "coin", "bitcoin", "ethereum", "加密货币"]):
            return cls._searchers["crypto"]()
        elif any(kw in site_lower for kw in ["news", "新闻"]):
            return cls._searchers["news"]()
        
        return None
