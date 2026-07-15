"""
多源财经新闻抓取器实现
覆盖：新浪财经、财联社、华尔街见闻、同花顺、雪球、微信公众号、arXiv、监管公告
"""

import asyncio
import json
import re
import hashlib
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import List, Dict, Optional, AsyncIterator, Callable
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .models import FinanceNews, NewsSource, NewsCategory


class BaseNewsScraper(ABC):
    """新闻抓取器基类"""
    
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
    def source(self) -> NewsSource:
        """数据源标识"""
        pass
    
    @abstractmethod
    async def get_latest_list(self, page: int = 1, page_size: int = 50) -> List[FinanceNews]:
        """获取最新新闻列表"""
        pass
    
    @abstractmethod
    async def get_detail(self, url: str) -> FinanceNews:
        """抓取详情页全文"""
        pass
    
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
            r'\b([036]\d{5})\b',           # 纯数字 6 位
            r'\b([SHSZ]\d{6})\b',          # SH/SZ + 6位
            r'\b(\d{6}\.[SZSH])\b',       # 6位.SZ/SH
        ]
        symbols = []
        for pat in patterns:
            symbols.extend(re.findall(pat, text, re.IGNORECASE))
        return list(set(symbols))
    
    def _map_category(self, channel: str) -> NewsCategory:
        """频道映射到分类"""
        mapping = {
            'stock': NewsCategory.STOCK,
            'finance': NewsCategory.MARKET,
            'macro': NewsCategory.MACRO,
            'industry': NewsCategory.INDUSTRY,
            'company': NewsCategory.STOCK,
            'fund': NewsCategory.FINANCIAL,
            'bond': NewsCategory.MACRO,
            'forex': NewsCategory.MACRO,
            'futures': NewsCategory.MACRO,
        }
        return mapping.get(channel.lower(), NewsCategory.MARKET)


class SinaNewsScraper(BaseNewsScraper):
    """新浪财经新闻抓取器"""
    
    API_LIST = 'https://feed.mix.sina.com.cn/api/roll/get'
    API_DETAIL = 'https://interface.sina.cn/wap_api/layout_col.d.json'
    
    @property
    def source(self) -> NewsSource:
        return NewsSource.SINA
    
    async def get_latest_list(self, page: int = 1, page_size: int = 50) -> List[FinanceNews]:
        """获取最新财经新闻列表"""
        params = {
            'pageid': 153,           # 财经频道 ID
            'lid': 2509,             # 综合财经
            'num': page_size,
            'page': page,
            'r': 0.123456789,
            'callback': 'jQuery11120',
        }
        try:
            resp = await self.client.get(self.API_LIST, params=params)
            # 响应是 JSONP，需提取 JSON
            json_str = re.search(r'\((.*)\)', resp.text, re.DOTALL)
            if not json_str:
                return []
            data = json.loads(json_str.group(1))
            
            news_list = []
            for item in data.get('result', {}).get('data', []):
                try:
                    news = FinanceNews(
                        news_id=f"sina_{item.get('docid', '')}",
                        source=NewsSource.SINA,
                        category=self._map_category(item.get('channel', '')),
                        title=item.get('title', ''),
                        summary=item.get('intro', ''),
                        content='',  # 需详情页获取
                        url=item.get('url', ''),
                        author=item.get('source', ''),
                        publish_time=self._parse_time(item.get('ctime', '')),
                        symbols=self._extract_symbols(item.get('title', '') + item.get('intro', '')),
                        keywords=item.get('keywords', '').split(',') if item.get('keywords') else [],
                        raw=item,
                    )
                    news_list.append(news)
                except Exception as e:
                    continue
            return news_list
        except Exception as e:
            return []
    
    async def get_detail(self, url: str) -> FinanceNews:
        """抓取详情页全文"""
        try:
            resp = await self.client.get(url)
            soup = BeautifulSoup(resp.text, 'lxml')
            
            # 多种页面结构兼容
            content_elem = (soup.select_one('.article-body') or 
                           soup.select_one('#artibody') or
                           soup.select_one('.content') or
                           soup.select_one('article'))
            content = content_elem.get_text('\n', strip=True) if content_elem else ''
            
            # 发布时间
            time_elem = soup.select_one('.date') or soup.select_one('.time-source') or soup.select_one('time')
            publish_time = self._parse_time(time_elem.get_text() if time_elem else '')
            
            # 标题
            title_elem = soup.select_one('h1') or soup.select_one('.main-title')
            title = title_elem.get_text(strip=True) if title_elem else ''
            
            return FinanceNews(
                news_id=f"sina_{url.split('/')[-1].replace('.shtml', '')}",
                source=NewsSource.SINA,
                category=NewsCategory.MARKET,
                title=title,
                summary='',
                content=content,
                url=url,
                publish_time=publish_time,
                raw={'html': resp.text[:2000]},
            )
        except Exception as e:
            return FinanceNews(
                news_id=f"sina_{hashlib.md5(url.encode()).hexdigest()[:8]}",
                source=NewsSource.SINA,
                category=NewsCategory.MARKET,
                title='',
                summary='',
                content='',
                url=url,
                raw={'error': str(e)},
            )
    
    def _parse_time(self, time_str: str) -> Optional[datetime]:
        """解析新浪时间格式"""
        if not time_str:
            return None
        # 格式: 2024-01-15 10:30:00 或 01月15日 10:30
        patterns = [
            r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})',
            r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2})',
            r'(\d{2}月\d{2}日 \d{2}:\d{2})',
        ]
        for pat in patterns:
            match = re.search(pat, time_str)
            if match:
                try:
                    dt_str = match.group(1)
                    if '月' in dt_str:
                        year = datetime.now().year
                        dt_str = f"{year}-{dt_str.replace('月', '-').replace('日', '')}:00"
                    return datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S')
                except:
                    continue
        return None


class CLSNewsScraper(BaseNewsScraper):
    """财联社实时电报流 (WebSocket + REST API)"""
    
    WS_URL = 'wss://www.cls.cn/v1/websocket'
    API_TELEGRAPH = 'https://www.cls.cn/api/telegraph/list'
    
    def __init__(self, proxy: str = None, timeout: int = 20):
        super().__init__(proxy, timeout)
        self.ws = None
        self.callbacks: List[Callable] = []
        self._running = False
    
    @property
    def source(self) -> NewsSource:
        return NewsSource.CLS
    
    async def get_latest_list(self, page: int = 1, page_size: int = 50) -> List[FinanceNews]:
        """获取最新电报列表 (REST API 备选)"""
        params = {
            'app': 'CailianpressWeb',
            'os': 'web',
            'sv': '7.7.5',
            'page': page,
            'size': page_size,
        }
        try:
            resp = await self.client.get(self.API_TELEGRAPH, params=params)
            data = resp.json()
            
            news_list = []
            for item in data.get('data', []):
                news = FinanceNews(
                    news_id=f"cls_{item.get('id', '')}",
                    source=NewsSource.CLS,
                    category=NewsCategory.MARKET,
                    title=item.get('title', ''),
                    summary=item.get('descr', ''),
                    content=item.get('content', ''),
                    url=f"https://www.cls.cn/detail/{item.get('id', '')}",
                    author='财联社',
                    publish_time=datetime.fromtimestamp(item.get('time', 0)) if item.get('time') else None,
                    symbols=self._extract_symbols(item.get('title', '') + item.get('descr', '')),
                    importance=item.get('important', 1),
                    raw=item,
                )
                news_list.append(news)
            return news_list
        except Exception as e:
            return []
    
    async def get_detail(self, url: str) -> FinanceNews:
        """抓取详情页 (电报通常已含全文)"""
        # 财联社电报列表已含完整内容，直接返回
        telegraph_id = url.split('/')[-1]
        return await self.get_latest_list(page=1, page_size=1)
    
    async def connect_ws(self):
        """连接 WebSocket 实时流"""
        import websockets
        try:
            self.ws = await websockets.connect(self.WS_URL)
            await self.ws.send(json.dumps({
                'action': 'subscribe',
                'channels': ['telegraph', 'depth', 'subject']
            }))
            self._running = True
            asyncio.create_task(self._listen_ws())
        except Exception as e:
            pass
    
    async def _listen_ws(self):
        """监听 WebSocket 消息"""
        try:
            async for message in self.ws:
                if not self._running:
                    break
                data = json.loads(message)
                if data.get('type') == 'telegraph':
                    news = self._parse_telegraph(data['data'])
                    for cb in self.callbacks:
                        try:
                            await cb(news)
                        except:
                            pass
        except:
            pass
    
    def _parse_telegraph(self, item: dict) -> FinanceNews:
        return FinanceNews(
            news_id=f"cls_{item.get('id', '')}",
            source=NewsSource.CLS,
            category=NewsCategory.MARKET,
            title=item.get('title', ''),
            summary=item.get('descr', ''),
            content=item.get('content', ''),
            url=f"https://www.cls.cn/detail/{item.get('id', '')}",
            author='财联社',
            publish_time=datetime.fromtimestamp(item.get('time', 0)) if item.get('time') else None,
            symbols=self._extract_symbols(item.get('title', '') + item.get('descr', '')),
            importance=item.get('important', 1),
            raw=item,
        )
    
    def on_news(self, callback: Callable):
        """注册实时新闻回调"""
        self.callbacks.append(callback)
    
    async def close(self):
        self._running = False
        if self.ws:
            await self.ws.close()
        await super().close()


class WallstreetcnScraper(BaseNewsScraper):
    """华尔街见闻 API (需申请 token)"""
    
    API_BASE = 'https://api-one.wallstcn.com/apiv1'
    
    def __init__(self, token: str = None, proxy: str = None, timeout: int = 20):
        super().__init__(proxy, timeout)
        self.token = token
        if token:
            self.client.headers['Authorization'] = f'Bearer {token}'
    
    @property
    def source(self) -> NewsSource:
        return NewsSource.WALLSTREETCN
    
    async def get_latest_list(self, page: int = 1, page_size: int = 50) -> List[FinanceNews]:
        """获取最新资讯列表"""
        params = {
            'page': page,
            'limit': page_size,
            'channel': 'global',  # global, a-stock, us-stock, crypto, etc.
        }
        try:
            resp = await self.client.get(f'{self.API_BASE}/content/articles', params=params)
            data = resp.json()
            
            news_list = []
            for item in data.get('data', {}).get('items', []):
                news = FinanceNews(
                    news_id=f"wscn_{item.get('id', '')}",
                    source=NewsSource.WALLSTREETCN,
                    category=self._map_wscn_category(item.get('channel', '')),
                    title=item.get('title', ''),
                    summary=item.get('summary', ''),
                    content=item.get('content', ''),
                    url=item.get('uri', ''),
                    author=item.get('author', {}).get('name', ''),
                    publish_time=datetime.fromtimestamp(item.get('display_time', 0)) if item.get('display_time') else None,
                    symbols=self._extract_symbols(item.get('title', '') + item.get('summary', '')),
                    keywords=[tag.get('name', '') for tag in item.get('tags', [])],
                    raw=item,
                )
                news_list.append(news)
            return news_list
        except Exception as e:
            return []
    
    async def get_detail(self, url: str) -> FinanceNews:
        """抓取详情页"""
        # 华尔街见闻列表已含完整内容
        return await self.get_latest_list(page=1, page_size=1)
    
    def _map_wscn_category(self, channel: str) -> NewsCategory:
        mapping = {
            'global': NewsCategory.MACRO,
            'a-stock': NewsCategory.STOCK,
            'us-stock': NewsCategory.STOCK,
            'crypto': NewsCategory.BLOCKCHAIN,
            'commodity': NewsCategory.MACRO,
            'fund': NewsCategory.FINANCIAL,
        }
        return mapping.get(channel, NewsCategory.MARKET)

class XueqiuScraper(BaseNewsScraper):
    """雪球社区观点抓取 (需登录、CDP 模式)"""
    
    def __init__(self, cdp_endpoint: str = 'http://127.0.0.1:9222', proxy: str = None, timeout: int = 20):
        super().__init__(proxy, timeout)
        self.cdp_endpoint = cdp_endpoint
        self.browser = None
    
    @property
    def source(self) -> NewsSource:
        return NewsSource.XUEQIU
    
    async def get_latest_list(self, page: int = 1, page_size: int = 50) -> List[FinanceNews]:
        """获取大V时间线 (需 CDP 浏览器)"""
        # 这里需要 browser-cdp skill 支持
        # 简化实现：返回空列表，实际使用需配合 CDP
        return []
    
    async def get_user_timeline(self, user_id: str, max_count: int = 50) -> List[FinanceNews]:
        """抓取指定用户时间线 (如 @林园、@但斌)"""
        # 需要 CDP 浏览器实现
        return []
    
    async def get_detail(self, url: str) -> FinanceNews:
        """抓取详情页"""
        return FinanceNews(
            news_id=f"xueqiu_{hashlib.md5(url.encode()).hexdigest()[:8]}",
            source=NewsSource.XUEQIU,
            category=NewsCategory.RESEARCH,
            title='',
            summary='',
            content='',
            url=url,
            raw={'note': 'Requires CDP browser'},
        )
    
    async def close(self):
        if self.browser:
            await self.browser.close()
        await super().close()


class WechatScraper(BaseNewsScraper):
    """微信公众号文章抓取 (通过搜狗微信搜索 + CDP)"""
    
    SEARCH_URL = 'https://weixin.sogou.com/weixin'
    
    def __init__(self, cdp_endpoint: str = 'http://127.0.0.1:9222', proxy: str = None, timeout: int = 20):
        super().__init__(proxy, timeout)
        self.cdp_endpoint = cdp_endpoint
        self.browser = None
    
    @property
    def source(self) -> NewsSource:
        return NewsSource.WECHAT
    
    async def get_latest_list(self, page: int = 1, page_size: int = 50) -> List[FinanceNews]:
        """获取最新列表 (需关键词搜索)"""
        return []
    
    async def search_articles(self, query: str, page: int = 1) -> List[FinanceNews]:
        """搜索公众号文章"""
        # 需要 CDP 浏览器实现
        return []
    
    async def get_article_detail(self, url: str) -> FinanceNews:
        """抓取文章全文 (需处理版权保护、图片防盗链)"""
        return FinanceNews(
            news_id=f"wechat_{hashlib.md5(url.encode()).hexdigest()[:8]}",
            source=NewsSource.WECHAT,
            category=NewsCategory.RESEARCH,
            title='',
            summary='',
            content='',
            url=url,
            raw={'note': 'Requires CDP browser'},
        )
    
    async def get_detail(self, url: str) -> FinanceNews:
        return await self.get_article_detail(url)
    
    async def close(self):
        if self.browser:
            await self.browser.close()
        await super().close()


class ArxivScraper(BaseNewsScraper):
    """arXiv 量化金融/ML 论文抓取 (官方 API、无反爬)"""
    
    CATEGORIES = [
        'q-fin',      # 量化金融
        'q-fin.CP',   # 计算金融
        'q-fin.EC',   # 计量经济学
        'q-fin.GN',   # 一般金融
        'q-fin.MF',   # 数学金融
        'q-fin.PM',   # 投资组合管理
        'q-fin.PR',   # 定价
        'q-fin.RM',   # 风险管理
        'q-fin.ST',   # 统计金融
        'q-fin.TR',   # 交易
        'cs.LG',      # 机器学习
        'stat.ML',    # 统计学习
    ]
    
    def __init__(self, proxy: str = None, timeout: int = 30):
        super().__init__(proxy, timeout)
        self._client = None
    
    @property
    def source(self) -> NewsSource:
        return NewsSource.ARXIV
    
    def _get_arxiv_client(self):
        """延迟导入 arxiv 库"""
        if self._client is None:
            try:
                import arxiv
                self._client = arxiv.Client(delay_seconds=3, page_size=100)
            except ImportError:
                raise ImportError("请安装 arxiv: pip install arxiv")
        return self._client
    
    async def get_latest_list(self, page: int = 1, page_size: int = 50) -> List[FinanceNews]:
        """获取最新论文列表"""
        return self.search_papers(max_results=page_size)
    
    def search_papers(self, 
        query: str = None,
        categories: List[str] = None,
        max_results: int = 100,
        date_from: datetime = None
    ) -> List[FinanceNews]:
        """搜索论文 (同步方法，内部使用 arxiv 库)"""
        import arxiv
        
        search_query = query or ''
        if categories:
            search_query += ' ' + ' OR '.join(f'cat:{c}' for c in categories)
        if date_from:
            search_query += f' submittedDate:[{date_from.strftime("%Y%m%d")} TO *]'
        
        search = arxiv.Search(
            query=search_query,
            max_results=max_results,
            sort_by=arxiv.SortCriterion.SubmittedDate,
            sort_order=arxiv.SortOrder.Descending,
        )
        
        papers = []
        try:
            client = self._get_arxiv_client()
            for paper in client.results(search):
                papers.append(FinanceNews(
                    news_id=f"arxiv_{paper.entry_id.split('/')[-1]}",
                    source=NewsSource.ARXIV,
                    category=NewsCategory.ACADEMIC,
                    title=paper.title,
                    summary=paper.summary,
                    content=paper.summary,  # 摘要即正文，全文需下载 PDF
                    url=paper.entry_id,
                    author=', '.join(a.name for a in paper.authors),
                    publish_time=paper.published,
                    keywords=paper.categories,
                    credibility=0.95,  # 学术论文高可信度
                    raw={
                        'entry_id': paper.entry_id,
                        'pdf_url': paper.pdf_url,
                        'primary_category': paper.primary_category,
                        'comment': paper.comment,
                    },
                ))
        except Exception as e:
            pass
        return papers
    
    async def get_detail(self, url: str) -> FinanceNews:
        """抓取论文详情 (已含摘要)"""
        # arxiv 列表已含完整摘要
        return await self.get_latest_list(page=1, page_size=1)
    
    async def close(self):
        await super().close()


class RegulatorScraper(BaseNewsScraper):
    """监管公告定时抓取 (证监会/交易所/央行官网)"""
    
    SOURCES = {
        'csrc': 'https://www.csrc.gov.cn/csrc/xwfbh/',           # 证监会新闻发布
        'sse': 'https://www.sse.com.cn/assortment/stock/list/info/announcement/',  # 上交所公告
        'szse': 'https://www.szse.cn/disclosure/listed/announcement/',  # 深交所公告
        'pbc': 'https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/index.html',  # 央行货币政策
    }
    
    @property
    def source(self) -> NewsSource:
        return NewsSource.REGULATOR
    
    async def get_latest_list(self, page: int = 1, page_size: int = 50) -> List[FinanceNews]:
        """获取最新监管公告"""
        return await self.fetch_all()
    
    async def fetch_all(self) -> List[FinanceNews]:
        """并发抓取所有监管源"""
        tasks = [self._fetch_source(name, url) for name, url in self.SOURCES.items()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        all_news = []
        for name, result in zip(self.SOURCES.keys(), results):
            if isinstance(result, Exception):
                continue
            all_news.extend(result)
        return all_news
    
    async def _fetch_source(self, name: str, url: str) -> List[FinanceNews]:
        """抓取单个监管源"""
        try:
            resp = await self.client.get(url)
            soup = BeautifulSoup(resp.text, 'lxml')
            
            news_list = []
            # 通用列表选择器
            items = soup.select('ul li, .list li, .news-list li, table tr')
            for item in items[:20]:  # 限制数量
                link = item.select_one('a')
                if not link:
                    continue
                title = link.get_text(strip=True)
                if not title or len(title) < 5:
                    continue
                
                href = link.get('href', '')
                if href and not href.startswith('http'):
                    href = urljoin(url, href)
                
                # 尝试获取时间
                time_elem = item.select_one('span, .date, .time')
                time_str = time_elem.get_text(strip=True) if time_elem else ''
                
                news_list.append(FinanceNews(
                    news_id=f"reg_{name}_{hashlib.md5(href.encode()).hexdigest()[:8]}",
                    source=NewsSource.REGULATOR,
                    category=NewsCategory.MACRO,
                    title=title,
                    summary=title,
                    content='',
                    url=href,
                    author=name.upper(),
                    publish_time=self._parse_regulator_time(time_str),
                    raw={'source': name, 'html': str(item)[:500]},
                ))
            return news_list
        except Exception as e:
            return []
    
    def _parse_regulator_time(self, time_str: str) -> Optional[datetime]:
        """解析监管网站时间格式"""
        if not time_str:
            return None
        patterns = [
            r'(\d{4}-\d{2}-\d{2})',
            r'(\d{4}/\d{2}/\d{2})',
            r'(\d{2}-\d{2})',
        ]
        for pat in patterns:
            match = re.search(pat, time_str)
            if match:
                try:
                    dt_str = match.group(1)
                    if len(dt_str) == 5:  # MM-DD
                        dt_str = f"{datetime.now().year}-{dt_str}"
                    return datetime.strptime(dt_str, '%Y-%m-%d')
                except:
                    continue
        return None
    
    async def get_detail(self, url: str) -> FinanceNews:
        """抓取公告详情"""
        try:
            resp = await self.client.get(url)
            soup = BeautifulSoup(resp.text, 'lxml')
            
            content_elem = soup.select_one('.content, .article, .detail, #main')
            content = content_elem.get_text('\n', strip=True) if content_elem else ''
            
            title_elem = soup.select_one('h1, h2, .title')
            title = title_elem.get_text(strip=True) if title_elem else ''
            
            return FinanceNews(
                news_id=f"reg_{hashlib.md5(url.encode()).hexdigest()[:8]}",
                source=NewsSource.REGULATOR,
                category=NewsCategory.MACRO,
                title=title,
                summary=title,
                content=content,
                url=url,
                raw={'html': resp.text[:2000]},
            )
        except Exception as e:
            return FinanceNews(
                news_id=f"reg_{hashlib.md5(url.encode()).hexdigest()[:8]}",
                source=NewsSource.REGULATOR,
                category=NewsCategory.MACRO,
                title='',
                summary='',
                content='',
                url=url,
                raw={'error': str(e)},
            )
    
    async def close(self):
        await super().close()