"""
新闻聚合器：多源融合、去重、排序、推送
"""

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Set
from collections import defaultdict

from .models import FinanceNews, NewsSource, NewsCategory
from .scrapers import (
    SinaNewsScraper,
    CLSNewsScraper,
    WallstreetcnScraper,
    XueqiuScraper,
    WechatScraper,
    ArxivScraper,
    RegulatorScraper,
)

logger = logging.getLogger(__name__)


class NewsAggregator:
    """多源新闻聚合、去重、排序、推送"""
    
    def __init__(self, 
        scrapers: Dict[NewsSource, object] = None,
        dedup_cache_size: int = 10000,
        enable_simhash: bool = False):
        """
        初始化聚合器
        
        Args:
            scrapers: 数据源抓取器字典 {NewsSource: scraper_instance}
            dedup_cache_size: 去重缓存大小
            enable_simhash: 是否启用 SimHash 近似去重 (需安装 simhash 库)
        """
        self.scrapers = scrapers or {}
        self.dedup_cache: Dict[str, str] = {}  # fingerprint -> news_id
        self.dedup_cache_size = dedup_cache_size
        self.enable_simhash = enable_simhash
        self._simhash_index = None
        
        if enable_simhash:
            try:
                import simhash
                self._simhash_index = simhash.SimhashIndex([], k=3)
            except ImportError:
                logger.warning("simhash 未安装，降级使用 MD5 去重")
                self.enable_simhash = False
    
    def register_scraper(self, source: NewsSource, scraper):
        """注册抓取器"""
        self.scrapers[source] = scraper
    
    async def fetch_all(self,
        sources: List[NewsSource] = None,
        keywords: List[str] = None,
        since_hours: int = 24,
        categories: List[NewsCategory] = None,
        min_importance: int = 1,
        max_per_source: int = 50
    ) -> List[FinanceNews]:
        """
        并发抓取所有源，去重、过滤、按时间倒序
        
        Args:
            sources: 指定抓取的数据源，None 表示全部
            keywords: 关键词过滤
            since_hours: 只抓取最近 N 小时的新闻
            categories: 分类过滤
            min_importance: 最小重要性
            max_per_source: 每个源最大抓取数量
        
        Returns:
            去重、过滤、排序后的新闻列表
        """
        sources = sources or list(self.scrapers.keys())
        cutoff = datetime.utcnow() - timedelta(hours=since_hours)
        
        # 并发抓取
        tasks = []
        for source in sources:
            scraper = self.scrapers.get(source)
            if scraper and hasattr(scraper, 'get_latest_list'):
                tasks.append(self._safe_fetch(source, scraper, max_per_source))
        
        all_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 合并去重
        merged = []
        for source, result in zip(sources, all_results):
            if isinstance(result, Exception):
                logger.error(f"{source.value} 抓取失败: {result}")
                continue
            if not result:
                continue
            
            for news in result:
                # 时间过滤
                if news.publish_time and news.publish_time < cutoff:
                    continue
                # 关键词过滤
                if keywords and not self._match_keywords(news, keywords):
                    continue
                # 分类过滤
                if categories and news.category not in categories:
                    continue
                # 重要性过滤
                if news.importance and news.importance < min_importance:
                    continue
                # 去重
                if self._is_duplicate(news):
                    continue
                
                merged.append(news)
        
        # 按重要性、时间排序
        merged.sort(key=lambda x: (x.importance or 0, x.publish_time or datetime.min), reverse=True)
        return merged
    
    async def _safe_fetch(self, source: NewsSource, scraper, max_count: int) -> List[FinanceNews]:
        """安全抓取，捕获异常"""
        try:
            return await scraper.get_latest_list(page=1, page_size=max_count)
        except Exception as e:
            logger.error(f"{source.value} 抓取异常: {e}")
            return []
    
    def _is_duplicate(self, news: FinanceNews) -> bool:
        """基于标题+内容指纹去重"""
        if self.enable_simhash and self._simhash_index:
            # SimHash 近似去重
            simhash_val = self._compute_simhash(news.title + ' ' + news.content[:500])
            near_dups = self._simhash_index.get_near_dups(simhash_val)
            if near_dups:
                return True
            self._simhash_index.add(news.news_id, simhash_val)
            return False
        else:
            # MD5 精确去重
            fingerprint = hashlib.md5(
                (news.title + news.content[:500]).encode('utf-8')
            ).hexdigest()
            if fingerprint in self.dedup_cache:
                return True
            
            # 缓存大小控制
            if len(self.dedup_cache) >= self.dedup_cache_size:
                # 简单清理：删除一半旧缓存
                keys_to_remove = list(self.dedup_cache.keys())[:self.dedup_cache_size // 2]
                for k in keys_to_remove:
                    del self.dedup_cache[k]
            
            self.dedup_cache[fingerprint] = news.news_id
            return False
    
    def _compute_simhash(self, text: str) -> int:
        """计算文本 SimHash 值"""
        import simhash
        return simhash.Simhash(text).value
    
    def _match_keywords(self, news: FinanceNews, keywords: List[str]) -> bool:
        """关键词匹配 (标题+摘要+正文+关键词+实体)"""
        text = ' '.join([
            news.title,
            news.summary,
            news.content[:1000],
            ' '.join(news.keywords),
            ' '.join(e.get('name', '') for e in news.entities),
        ]).lower()
        return any(kw.lower() in text for kw in keywords)
    
    async def fetch_by_symbols(self,
        symbols: List[str],
        sources: List[NewsSource] = None,
        since_hours: int = 24
    ) -> List[FinanceNews]:
        """按股票代码筛选相关新闻"""
        all_news = await self.fetch_all(sources=sources, since_hours=since_hours)
        
        filtered = []
        for news in all_news:
            if any(sym in news.symbols for sym in symbols):
                filtered.append(news)
        return filtered
    
    async def fetch_hot_topics(self,
        sources: List[NewsSource] = None,
        since_hours: int = 6,
        top_n: int = 10
    ) -> List[Dict]:
        """提取热门话题 (基于关键词频次)"""
        from collections import Counter
        
        news_list = await self.fetch_all(sources=sources, since_hours=since_hours)
        
        # 统计关键词频次
        kw_counter = Counter()
        for news in news_list:
            for kw in news.keywords:
                kw_counter[kw] += 1
            for sym in news.symbols:
                kw_counter[sym] += 2  # 股票代码权重更高
        
        hot_topics = []
        for kw, count in kw_counter.most_common(top_n):
            # 找到相关新闻
            related = [n for n in news_list if kw in n.keywords or kw in n.symbols]
            hot_topics.append({
                'keyword': kw,
                'count': count,
                'related_news': related[:3],
                'avg_sentiment': sum(n.sentiment or 0 for n in related) / len(related) if related else 0,
            })
        return hot_topics
    
    def clear_dedup_cache(self):
        """清空去重缓存"""
        self.dedup_cache.clear()
        if self._simhash_index:
            self._simhash_index = None
            import simhash
            self._simhash_index = simhash.SimhashIndex([], k=3)


class NewsAggregatorBuilder:
    """聚合器构建器 - 便捷创建预配置的聚合器"""
    
    @staticmethod
    def create_default(proxy: str = None) -> NewsAggregator:
        """创建默认聚合器 (免费源)"""
        scrapers = {
            NewsSource.SINA: SinaNewsScraper(proxy=proxy),
            NewsSource.CLS: CLSNewsScraper(proxy=proxy),
            NewsSource.ARXIV: ArxivScraper(proxy=proxy),
            NewsSource.REGULATOR: RegulatorScraper(proxy=proxy),
        }
        return NewsAggregator(scrapers=scrapers)
    
    @staticmethod
    def create_full(proxy: str = None, 
                    wallstreetcn_token: str = None,
                    xueqiu_cookie: str = None,
                    cdp_endpoint: str = 'http://127.0.0.1:9222') -> NewsAggregator:
        """创建全功能聚合器 (含需认证的源)"""
        scrapers = {
            NewsSource.SINA: SinaNewsScraper(proxy=proxy),
            NewsSource.CLS: CLSNewsScraper(proxy=proxy),
            NewsSource.WALLSTREETCN: WallstreetcnScraper(token=wallstreetcn_token, proxy=proxy),
            NewsSource.XUEQIU: XueqiuScraper(cdp_endpoint=cdp_endpoint, proxy=proxy),
            NewsSource.WECHAT: WechatScraper(cdp_endpoint=cdp_endpoint, proxy=proxy),
            NewsSource.ARXIV: ArxivScraper(proxy=proxy),
            NewsSource.REGULATOR: RegulatorScraper(proxy=proxy),
        }
        return NewsAggregator(scrapers=scrapers, enable_simhash=True)
    
    @staticmethod
    async def close_all(aggregator: NewsAggregator):
        """关闭聚合器中所有抓取器"""
        for scraper in aggregator.scrapers.values():
            if hasattr(scraper, 'close'):
                await scraper.close()