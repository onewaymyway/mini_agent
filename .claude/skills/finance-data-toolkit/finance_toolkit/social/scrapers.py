"""
社交媒体抓取器实现
覆盖：微博热搜、雪球讨论、同花顺问财
使用公开 HTTP API，无需 CDP/浏览器
"""

import asyncio
import hashlib
import json
import re
from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional, Dict
from urllib.parse import quote

import httpx

from .models import SocialPost, SocialSource, SocialCategory


class BaseSocialScraper(ABC):
    """社交媒体抓取器基类"""
    
    def __init__(self, proxy: str = None, timeout: int = 20):
        self.proxy = proxy
        self.timeout = timeout
        self.client = httpx.AsyncClient(
            timeout=timeout,
            proxy=proxy,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            }
        )
    
    @property
    @abstractmethod
    def source(self) -> SocialSource:
        """数据源标识"""
        pass
    
    @abstractmethod
    async def get_hot_list(self, limit: int = 50) -> List[SocialPost]:
        """获取热门列表 (热搜/热门讨论/热门问答)"""
        pass
    
    @abstractmethod
    async def search_posts(self, keyword: str, limit: int = 20) -> List[SocialPost]:
        """关键词搜索帖子"""
        pass
    
    async def get_post_detail(self, post_id: str) -> Optional[SocialPost]:
        """获取帖子详情 (可选实现)"""
        return None
    
    async def close(self):
        """关闭客户端"""
        await self.client.aclose()
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, *args):
        await self.close()
    
    def _extract_symbols(self, text: str) -> List[str]:
        """从文本提取股票代码"""
        patterns = [
            r'(?:^|[^\w.])([036]\d{5})(?=[^\w.]|$)',              # 纯数字 6 位
            r'(?:^|[^\w.])([SHSZ]\d{6})(?=[^\w.]|$)',             # SH/SZ + 6位
            r'(?:^|[^\w.])(\d{6}\.(?:SH|SZ))(?=[^\w.]|$)',       # 6位.SH/.SZ
        ]
        symbols = []
        for pat in patterns:
            symbols.extend(re.findall(pat, text, re.IGNORECASE))
        return list(set(symbols))
    
    def _simple_sentiment(self, text: str) -> tuple:
        """简单情感分析 (基于关键词)"""
        positive_words = ['涨', '买入', '看好', '利好', '超预期', '突破', '强势', '大涨', '暴涨', '推荐', '买', '牛', '红', '盈利', '增长', '业绩好']
        negative_words = ['跌', '卖出', '看空', '利空', '不及预期', '破位', '弱势', '大跌', '暴跌', '风险', '卖', '熊', '绿', '亏损', '下降', '业绩差']
        
        pos_count = sum(1 for w in positive_words if w in text)
        neg_count = sum(1 for w in negative_words if w in text)
        
        if pos_count + neg_count == 0:
            return 0.0, 'neutral'
        
        score = (pos_count - neg_count) / (pos_count + neg_count)
        label = 'positive' if score > 0.1 else ('negative' if score < -0.1 else 'neutral')
        return score, label


class WeiboHotScraper(BaseSocialScraper):
    """微博热搜抓取器 (使用公开 API)"""
    
    HOT_API = 'https://weibo.com/ajax/side/hotSearch'
    TOPIC_API = 'https://weibo.com/ajax/statuses/topic_band'
    
    @property
    def source(self) -> SocialSource:
        return SocialSource.WEIBO_HOT
    
    async def get_hot_list(self, limit: int = 50) -> List[SocialPost]:
        """获取微博热搜榜"""
        try:
            resp = await self.client.get(self.HOT_API)
            resp.raise_for_status()
            data = resp.json()
            
            posts = []
            # 实时热搜榜
            for item in data.get('data', {}).get('realtime', [])[:limit]:
                title = item.get('word', '')
                heat = item.get('num', 0)  # 热度值
                url = f"https://s.weibo.com/weibo?q={quote(title)}"
                
                sentiment_score, sentiment_label = self._simple_sentiment(title)
                symbols = self._extract_symbols(title)
                
                post = SocialPost(
                    post_id=f"weibo_hot_{hashlib.md5(title.encode()).hexdigest()[:8]}",
                    source=SocialSource.WEIBO_HOT,
                    category=SocialCategory.HOT_TOPIC,
                    title=title,
                    content=title,
                    url=url,
                    topic_heat=heat,
                    sentiment_score=sentiment_score,
                    sentiment_label=sentiment_label,
                    symbols=symbols,
                    keywords=[title],
                    raw=item,
                    meta={'rank': item.get('rank'), 'category': item.get('category')}
                )
                posts.append(post)
            
            return posts
        except Exception as e:
            print(f"Weibo hot search error: {e}")
            return []
    
    async def search_posts(self, keyword: str, limit: int = 20) -> List[SocialPost]:
        """搜索微博话题 (简化版，返回热搜中匹配的)"""
        hot_list = await self.get_hot_list(limit=100)
        matched = [p for p in hot_list if keyword in p.title]
        return matched[:limit]
    
    async def get_topic_detail(self, topic_name: str) -> List[SocialPost]:
        """获取话题下的微博 (需要登录，简化返回空)"""
        return []


class XueqiuDiscussionScraper(BaseSocialScraper):
    """雪球讨论抓取器 (使用公开 API，无需登录可获取部分公开数据)"""
    
    HOT_STOCKS_API = 'https://xueqiu.com/service/v5/stock/hot_stock/list.json'
    DISCUSSION_API = 'https://xueqiu.com/statuses/hot/listV2.json'
    STOCK_DISCUSSION_API = 'https://xueqiu.com/statuses/search.json'
    
    @property
    def source(self) -> SocialSource:
        return SocialSource.XUEQIU_DISCUSSION
    
    async def get_hot_list(self, limit: int = 50) -> List[SocialPost]:
        """获取雪球热门讨论/大V观点"""
        try:
            params = {'count': limit, 'type': '11'}
            resp = await self.client.get(self.DISCUSSION_API, params=params)
            resp.raise_for_status()
            data = resp.json()
            
            posts = []
            for item in data.get('list', [])[:limit]:
                # 解析雪球动态数据
                status = item.get('data', {})
                if isinstance(status, str):
                    try:
                        status = json.loads(status)
                    except (json.JSONDecodeError, TypeError):
                        continue
                
                title = status.get('title', '') or status.get('description', '')[:100]
                content = status.get('description', '') or status.get('text', '')
                user = status.get('user', {})
                author = user.get('screen_name', '')
                
                # 热度指标
                heat = status.get('retweet_count', 0) + status.get('reply_count', 0) + status.get('like_count', 0)
                
                url = f"https://xueqiu.com/{user.get('id', '')}/{status.get('id', '')}"
                
                sentiment_score, sentiment_label = self._simple_sentiment(title + content)
                symbols = self._extract_symbols(title + content)
                
                # 从股票标签提取
                for stock in status.get('stocks', []):
                    code = stock.get('code', '')
                    if code:
                        symbols.append(f"{code}.SH" if code.startswith('6') else f"{code}.SZ")
                
                post = SocialPost(
                    post_id=f"xueqiu_{status.get('id', hashlib.md5(title.encode()).hexdigest()[:8])}",
                    source=SocialSource.XUEQIU_DISCUSSION,
                    category=SocialCategory.STOCK_DISCUSSION,
                    title=title,
                    content=content,
                    url=url,
                    author=author,
                    publish_time=datetime.fromtimestamp(status.get('created_at', 0) / 1000) if status.get('created_at') else datetime.utcnow(),
                    topic_heat=heat,
                    like_count=status.get('like_count', 0),
                    comment_count=status.get('reply_count', 0),
                    repost_count=status.get('retweet_count', 0),
                    sentiment_score=sentiment_score,
                    sentiment_label=sentiment_label,
                    symbols=list(set(symbols)),
                    raw=status,
                )
                posts.append(post)
            
            return posts
        except Exception as e:
            print(f"Xueqiu discussion error: {e}")
            return []
    
    async def search_posts(self, keyword: str, limit: int = 20) -> List[SocialPost]:
        """搜索雪球讨论"""
        try:
            params = {
                'q': keyword,
                'count': limit,
                'type': '11',
            }
            resp = await self.client.get(self.STOCK_DISCUSSION_API, params=params)
            resp.raise_for_status()
            data = resp.json()
            
            posts = []
            for item in data.get('list', [])[:limit]:
                status = item.get('data', {})
                if isinstance(status, str):
                    try:
                        status = json.loads(status)
                    except (json.JSONDecodeError, TypeError):
                        continue
                
                title = status.get('title', '') or status.get('description', '')[:100]
                content = status.get('description', '') or status.get('text', '')
                user = status.get('user', {})
                author = user.get('screen_name', '')
                
                heat = status.get('retweet_count', 0) + status.get('reply_count', 0) + status.get('like_count', 0)
                url = f"https://xueqiu.com/{user.get('id', '')}/{status.get('id', '')}"
                
                sentiment_score, sentiment_label = self._simple_sentiment(title + content)
                symbols = self._extract_symbols(title + content)
                for stock in status.get('stocks', []):
                    code = stock.get('code', '')
                    if code:
                        symbols.append(f"{code}.SH" if code.startswith('6') else f"{code}.SZ")
                
                post = SocialPost(
                    post_id=f"xueqiu_{status.get('id', hashlib.md5(title.encode()).hexdigest()[:8])}",
                    source=SocialSource.XUEQIU_DISCUSSION,
                    category=SocialCategory.STOCK_DISCUSSION,
                    title=title,
                    content=content,
                    url=url,
                    author=author,
                    publish_time=datetime.fromtimestamp(status.get('created_at', 0) / 1000) if status.get('created_at') else datetime.utcnow(),
                    topic_heat=heat,
                    like_count=status.get('like_count', 0),
                    comment_count=status.get('reply_count', 0),
                    repost_count=status.get('retweet_count', 0),
                    sentiment_score=sentiment_score,
                    sentiment_label=sentiment_label,
                    symbols=list(set(symbols)),
                    raw=status,
                )
                posts.append(post)
            
            return posts
        except Exception as e:
            print(f"Xueqiu search error: {e}")
            return []
    
    async def get_stock_discussions(self, symbol: str, limit: int = 20) -> List[SocialPost]:
        """获取特定股票的讨论"""
        # 转换代码格式: 600000.SH -> SH600000
        code = symbol.split('.')[0]
        market = 'SH' if symbol.endswith('.SH') else 'SZ'
        xueqiu_symbol = f"{market}{code}"
        
        return await self.search_posts(xueqiu_symbol, limit)


class ThsWencaiScraper(BaseSocialScraper):
    """同花顺问财抓取器 (使用公开问答接口)"""
    
    SEARCH_API = 'https://www.iwencai.com/unifiedwap/result'
    QUESTION_API = 'https://www.iwencai.com/unifiedwap/question/answer'
    
    @property
    def source(self) -> SocialSource:
        return SocialSource.THS_WENCAI
    
    async def get_hot_list(self, limit: int = 50) -> List[SocialPost]:
        """获取问财热门问题/热门概念"""
        try:
            # 问财热门问题通常通过搜索热门关键词获取
            hot_keywords = ['今日热股', '涨停', '龙头', '概念股', '北向资金', '主力资金', '融资余额']
            all_posts = []
            
            for kw in hot_keywords[:5]:  # 限制请求数
                posts = await self.search_posts(kw, limit=10)
                all_posts.extend(posts)
                await asyncio.sleep(0.5)  # 避免频率限制
            
            # 去重并按热度排序
            seen = set()
            unique_posts = []
            for p in all_posts:
                if p.post_id not in seen:
                    seen.add(p.post_id)
                    unique_posts.append(p)
            
            unique_posts.sort(key=lambda x: x.topic_heat or 0, reverse=True)
            return unique_posts[:limit]
        except Exception as e:
            print(f"Ths wencai hot list error: {e}")
            return []
    
    async def search_posts(self, keyword: str, limit: int = 20) -> List[SocialPost]:
        """搜索问财问答"""
        try:
            params = {
                'question': keyword,
                'perpage': limit,
                'page': 1,
                'source': 'Ths_iwencai_Xuangu',
            }
            resp = await self.client.get(self.SEARCH_API, params=params)
            resp.raise_for_status()
            data = resp.json()
            
            posts = []
            for item in data.get('data', {}).get('result', [])[:limit]:
                # 问财返回结构较复杂，简化处理
                title = item.get('question', '') or item.get('title', '')
                content = item.get('answer', '') or item.get('content', '')
                
                if not title and not content:
                    continue
                
                heat = item.get('heat', 0) or item.get('view_count', 0)
                url = item.get('url', f"https://www.iwencai.com/unifiedwap/result?w={quote(keyword)}")
                
                sentiment_score, sentiment_label = self._simple_sentiment(title + content)
                symbols = self._extract_symbols(title + content)
                
                post = SocialPost(
                    post_id=f"ths_wencai_{hashlib.md5((title+content).encode()).hexdigest()[:8]}",
                    source=SocialSource.THS_WENCAI,
                    category=SocialCategory.QA,
                    title=title,
                    content=content,
                    url=url,
                    topic_heat=heat,
                    sentiment_score=sentiment_score,
                    sentiment_label=sentiment_label,
                    symbols=symbols,
                    keywords=[keyword],
                    raw=item,
                )
                posts.append(post)
            
            return posts
        except Exception as e:
            print(f"Ths wencai search error: {e}")
            return []
    
    async def get_stock_qa(self, symbol: str, limit: int = 10) -> List[SocialPost]:
        """获取特定股票的问财问答"""
        code = symbol.split('.')[0]
        name_map = {
            '600000': '浦发银行',
            '000001': '平安银行',
            '600519': '贵州茅台',
        }
        name = name_map.get(code, code)
        return await self.search_posts(f"{name} {code}", limit)


# 便捷函数
async def fetch_weibo_hot(limit: int = 50) -> List[SocialPost]:
    """获取微博热搜"""
    async with WeiboHotScraper() as scraper:
        return await scraper.get_hot_list(limit)


async def fetch_xueqiu_hot(limit: int = 50) -> List[SocialPost]:
    """获取雪球热门讨论"""
    async with XueqiuDiscussionScraper() as scraper:
        return await scraper.get_hot_list(limit)


async def fetch_ths_wencai_hot(limit: int = 50) -> List[SocialPost]:
    """获取同花顺问财热门"""
    async with ThsWencaiScraper() as scraper:
        return await scraper.get_hot_list(limit)


async def fetch_all_social_hot(limit: int = 50) -> Dict[SocialSource, List[SocialPost]]:
    """一键获取所有社交媒体热门内容"""
    results = {}
    
    async with WeiboHotScraper() as weibo:
        results[SocialSource.WEIBO_HOT] = await weibo.get_hot_list(limit)
    
    async with XueqiuDiscussionScraper() as xueqiu:
        results[SocialSource.XUEQIU_DISCUSSION] = await xueqiu.get_hot_list(limit)
    
    async with ThsWencaiScraper() as ths:
        results[SocialSource.THS_WENCAI] = await ths.get_hot_list(limit)
    
    return results
