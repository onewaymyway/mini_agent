# 多源财经新闻抓取模块

覆盖：新浪财经、同花顺、雪球、华尔街见闻、财联社、英文财经媒体、微信公众号、arXiv 学术论文、监管公告。

## 1. 数据源矩阵

| 数据源 | 类型 | 覆盖范围 | 更新频率 | 反爬等级 | 推荐方式 | 字段完整度 |
|--------|------|----------|----------|----------|----------|------------|
| **新浪财经** | 门户 | A股/港股/美股/期货/外汇/宏观 | 分钟级 | ⭐⭐ | API + 网页解析 | ⭐⭐⭐⭐ |
| **同花顺** | 终端/门户 | 深度研报/公告/资讯/直播 | 分钟级 | ⭐⭐⭐ | CDP + API | ⭐⭐⭐⭐⭐ |
| **雪球** | 社区 | 大V观点/组合调仓/长文分析 | 实时 | ⭐⭐⭐⭐ | CDP (需登录) | ⭐⭐⭐ |
| **华尔街见闻** | 专业媒体 | 全球宏观/美股/加密/大宗商品 | 分钟级 | ⭐⭐ | API + RSS | ⭐⭐⭐⭐ |
| **财联社** | 专业媒体 | A股快讯/深度/电报/题材挖掘 | 秒级 | ⭐⭐ | WebSocket + API | ⭐⭐⭐⭐⭐ |
| **英文媒体** | 境外媒体 | Bloomberg/Reuters/FT/WSJ/CNBC | 实时 | ⭐⭐⭐⭐ | RSS + 付费API | ⭐⭐⭐⭐ |
| **微信公众号** | 自媒体 | 机构研报/行业深度/内幕消息 | 不定期 | ⭐⭐⭐⭐⭐ | CDP + 搜狗微信搜索 | ⭐⭐⭐ |
| **arXiv** | 学术预印本 | 量化金融/机器学习/风控模型 | 日级 | ⭐ | 官方 API | ⭐⭐⭐⭐⭐ |
| **监管公告** | 官方 | 证监会/交易所/央行/银保监 | 日级 | ⭐ | 官网定时抓取 | ⭐⭐⭐⭐⭐ |

## 2. 统一新闻数据结构

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List
from enum import Enum

class NewsSource(Enum):
    SINA = "sina"
    THS = "ths"
    XUEQIU = "xueqiu"
    WALLSTREETCN = "wallstreetcn"
    CLS = "cls"
    BLOOMBERG = "bloomberg"
    REUTERS = "reuters"
    WECHAT = "wechat"
    ARXIV = "arxiv"
    REGULATOR = "regulator"

class NewsCategory(Enum):
    MARKET = "market"           # 大盘行情
    STOCK = "stock"             # 个股消息
    INDUSTRY = "industry"       # 行业动态
    MACRO = "macro"             # 宏观政策
    FINANCIAL = "financial"     # 财报业绩
    RESEARCH = "research"       # 研报观点
    BLOCKCHAIN = "blockchain"   # 加密货币
    ACADEMIC = "academic"       # 学术前沿

@dataclass
class FinanceNews:
    news_id: str                    # 唯一ID (source + 原始ID)
    source: NewsSource
    category: NewsCategory
    title: str
    summary: str                    # 摘要/导语
    content: str                    # 正文 (HTML/Markdown/纯文本)
    url: str                        # 原文链接
    author: Optional[str] = None
    publish_time: datetime = None
    crawl_time: datetime = field(default_factory=datetime.utcnow)
    
    # 结构化标签
    symbols: List[str] = field(default_factory=list)      # 涉及标的: ['000001.SZ', 'BTC-USDT']
    keywords: List[str] = field(default_factory=list)     # 关键词
    entities: List[dict] = field(default_factory=list)    # 实体: [{type: 'company', name: '平安银行', code: '000001.SZ'}]
    
    # 质量指标
    sentiment: Optional[float] = None     # 情感得分 [-1, 1]
    importance: Optional[int] = None      # 重要性 1-5
    credibility: Optional[float] = None   # 可信度 0-1
    
    # 多媒体
    images: List[str] = field(default_factory=list)
    videos: List[str] = field(default_factory=list)
    
    # 原始数据 (调试用)
    raw: Optional[dict] = None
```

## 3. 各数据源实战代码

### 3.1 新浪财经 (免费、稳定、字段全)

```python
import httpx
import re
from bs4 import BeautifulSoup

class SinaNewsScraper:
    """新浪财经新闻抓取器"""
    
    API_LIST = 'https://feed.mix.sina.com.cn/api/roll/get'
    API_DETAIL = 'https://interface.sina.cn/wap_api/layout_col.d.json'
    
    def __init__(self, proxy: str = None):
        self.client = httpx.AsyncClient(timeout=20, proxy=proxy)
    
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
        resp = await self.client.get(self.API_LIST, params=params)
        # 响应是 JSONP，需提取 JSON
        json_str = re.search(r'\((.*)\)', resp.text).group(1)
        data = json.loads(json_str)
        
        news_list = []
        for item in data.get('result', {}).get('data', []):
            news_list.append(FinanceNews(
                news_id=f"sina_{item['docid']}",
                source=NewsSource.SINA,
                category=self._map_category(item.get('channel', '')),
                title=item['title'],
                summary=item.get('intro', ''),
                content='',  # 需详情页获取
                url=item['url'],
                author=item.get('source', ''),
                publish_time=pd.to_datetime(item['ctime']),
                symbols=self._extract_symbols(item['title'] + item.get('intro', '')),
                keywords=item.get('keywords', '').split(',') if item.get('keywords') else [],
            ))
        return news_list
    
    async def get_detail(self, url: str) -> FinanceNews:
        """抓取详情页全文"""
        resp = await self.client.get(url)
        soup = BeautifulSoup(resp.text, 'lxml')
        
        # 多种页面结构兼容
        content_elem = (soup.select_one('.article-body') or 
                       soup.select_one('#artibody') or
                       soup.select_one('.content'))
        content = content_elem.get_text('\n', strip=True) if content_elem else ''
        
        # 发布时间
        time_elem = soup.select_one('.date') or soup.select_one('.time-source')
        publish_time = parse_sina_time(time_elem.get_text() if time_elem else '')
        
        return FinanceNews(
            news_id=f"sina_{url.split('/')[-1].replace('.shtml', '')}",
            source=NewsSource.SINA,
            category=NewsCategory.MARKET,
            title=soup.select_one('h1').get_text(strip=True) if soup.select_one('h1') else '',
            summary='',
            content=content,
            url=url,
            publish_time=publish_time,
        )
    
    def _map_category(self, channel: str) -> NewsCategory:
        mapping = {
            'stock': NewsCategory.STOCK,
            'finance': NewsCategory.MARKET,
            'macro': NewsCategory.MACRO,
            'industry': NewsCategory.INDUSTRY,
        }
        return mapping.get(channel, NewsCategory.MARKET)
    
    def _extract_symbols(self, text: str) -> List[str]:
        """从文本提取股票代码"""
        # 匹配 000001、600000、SH600000、SZ000001、000001.SZ 等格式
        patterns = [
            r'\b([036]\d{5})\b',           # 纯数字 6 位
            r'\b([SHSZ]\d{6})\b',          # SH/SZ + 6位
            r'\b(\d{6}\.[SZSH])\b',       # 6位.SZ/SH
        ]
        symbols = []
        for pat in patterns:
            symbols.extend(re.findall(pat, text, re.IGNORECASE))
        return list(set(symbols))
```

### 3.2 财联社 (电报/快讯/深度、WebSocket 实时流)

```python
import websockets
import json

class CLSNewsScraper:
    """财联社实时电报流 (WebSocket)"""
    
    WS_URL = 'wss://www.cls.cn/v1/websocket'
    
    def __init__(self):
        self.ws = None
        self.callbacks = []
    
    async def connect(self):
        self.ws = await websockets.connect(self.WS_URL)
        # 订阅频道
        await self.ws.send(json.dumps({
            'action': 'subscribe',
            'channels': ['telegraph', 'depth', 'subject']
        }))
        
        asyncio.create_task(self._listen())
    
    async def _listen(self):
        async for message in self.ws:
            data = json.loads(message)
            if data.get('type') == 'telegraph':
                news = self._parse_telegraph(data['data'])
                for cb in self.callbacks:
                    await cb(news)
    
    def _parse_telegraph(self, item: dict) -> FinanceNews:
        return FinanceNews(
            news_id=f"cls_{item['id']}",
            source=NewsSource.CLS,
            category=NewsCategory.MARKET,
            title=item.get('title', ''),
            summary=item.get('descr', ''),
            content=item.get('content', ''),
            url=f"https://www.cls.cn/detail/{item['id']}",
            author='财联社',
            publish_time=pd.to_datetime(item['time'], unit='s'),
            symbols=self._extract_symbols(item.get('title', '') + item.get('descr', '')),
            importance=item.get('important', 1),
        )
    
    def on_news(self, callback):
        self.callbacks.append(callback)
```

### 3.3 华尔街见闻 (全球宏观、付费 API)

```python
class WallstreetcnScraper:
    """华尔街见闻 API (需申请 token)"""
    
    BASE = 'https://api-one.wallstcn.com/apiv1'
    
    def __init__(self, token: str):
        self.token = token
        self.client = httpx.AsyncClient(
            headers={'Authorization': f'Bearer {token}'},
            timeout=20
        )
    
    async def get_live_feed(self, limit: int = 20) -> List[FinanceNews]:
        """实时快讯流"""
        resp = await self.client.get(f'{self.BASE}/content/lives', params={'limit': limit})
        data = resp.json()
        
        news_list = []
        for item in data.get('data', {}).get('items', []):
            news_list.append(FinanceNews(
                news_id=f"wallstreetcn_{item['id']}",
                source=NewsSource.WALLSTREETCN,
                category=self._map_category(item.get('category', '')),
                title=item.get('title', ''),
                summary=item.get('summary', ''),
                content=item.get('content', ''),
                url=item.get('uri', ''),
                author=item.get('author', {}).get('name', ''),
                publish_time=pd.to_datetime(item['display_time'], unit='s'),
                symbols=self._extract_symbols(item.get('title', '')),
                keywords=item.get('tags', []),
            ))
        return news_list
    
    async def get_articles(self, category: str = 'global', limit: int = 20) -> List[FinanceNews]:
        """深度文章 (global/china/tech/crypto)"""
        resp = await self.client.get(f'{self.BASE}/content/articles', 
                                     params={'category': category, 'limit': limit})
        # 解析逻辑类似
        ...
```

### 3.4 雪球 (社区观点、需登录、CDP 模式)

```python
# 使用 browser-cdp skill
from browser_cdp import CDPBrowser

class XueqiuScraper:
    def __init__(self, cdp_endpoint: str = 'http://127.0.0.1:9222'):
        self.browser = CDPBrowser(cdp_endpoint)
    
    async def get_user_timeline(self, user_id: str, max_count: int = 50) -> List[FinanceNews]:
        """抓取大V时间线 (如 @林园、@但斌)"""
        url = f'https://xueqiu.com/{user_id}'
        page = await self.browser.new_page(url)
        
        # 等待加载、滚动加载更多
        await page.wait_for_selector('.status-list', timeout=10000)
        for _ in range(3):  # 滚动 3 次
            await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
            await page.wait_for_timeout(2000)
        
        items = await page.query_selector_all('.status-item')
        news_list = []
        for item in items[:max_count]:
            news_list.append(await self._parse_status_item(item))
        
        await page.close()
        return news_list
    
    async def _parse_status_item(self, item) -> FinanceNews:
        content = await item.text_content('.status-content')
        time_str = await item.text_content('.status-time')
        symbols = await item.query_selector_all('.stock-symbol')
        symbol_list = [await s.text_content() for s in symbols]
        
        return FinanceNews(
            news_id=f"xueqiu_{await item.get_attribute('data-id')}",
            source=NewsSource.XUEQIU,
            category=NewsCategory.RESEARCH,
            title=content[:100],  # 雪球无标题，用内容前100字
            summary=content[:200],
            content=content,
            url=f"https://xueqiu.com/status/{await item.get_attribute('data-id')}",
            author=await item.text_content('.user-name'),
            publish_time=parse_xueqiu_time(time_str),
            symbols=symbol_list,
        )
```

### 3.5 微信公众号 (搜狗微信搜索 + CDP)

```python
class WechatScraper:
    """微信公众号文章抓取 (通过搜狗微信搜索)"""
    
    SEARCH_URL = 'https://weixin.sogou.com/weixin'
    
    def __init__(self, cdp_endpoint: str = 'http://127.0.0.1:9222'):
        self.browser = CDPBrowser(cdp_endpoint)
    
    async def search_articles(self, query: str, page: int = 1) -> List[FinanceNews]:
        """搜索公众号文章"""
        params = {'query': query, 'type': 2, 'page': page}  # type=2 搜文章
        url = f'{self.SEARCH_URL}?{urlencode(params)}'
        
        page = await self.browser.new_page(url)
        await page.wait_for_selector('.news-list', timeout=15000)
        
        items = await page.query_selector_all('.news-list li')
        results = []
        for item in items:
            title_elem = await item.query_selector('.txt-box h3 a')
            if not title_elem:
                continue
            
            title = await title_elem.text_content()
            url = await title_elem.get_attribute('href')
            source = await item.text_content('.txt-box .account')
            time_str = await item.text_content('.txt-box .s-p')
            
            results.append(FinanceNews(
                news_id=f"wechat_{hash(url)}",
                source=NewsSource.WECHAT,
                category=NewsCategory.RESEARCH,
                title=title,
                summary='',
                content='',  # 需进详情页
                url=url,
                author=source,
                publish_time=parse_wechat_time(time_str),
            ))
        
        await page.close()
        return results
    
    async def get_article_detail(self, url: str) -> FinanceNews:
        """抓取文章全文 (需处理版权保护、图片防盗链)"""
        page = await self.browser.new_page(url)
        await page.wait_for_selector('#js_content', timeout=15000)
        
        title = await page.text_content('#activity-name')
        content_html = await page.inner_html('#js_content')
        author = await page.text_content('#js_name')
        publish_time_str = await page.text_content('#publish_time')
        
        # 处理图片：下载并上传到自己的存储
        images = await self._download_images(page, '#js_content img')
        
        await page.close()
        
        return FinanceNews(
            news_id=f"wechat_{hash(url)}",
            source=NewsSource.WECHAT,
            category=NewsCategory.RESEARCH,
            title=title,
            content=content_html,
            url=url,
            author=author,
            publish_time=parse_wechat_time(publish_time_str),
            images=images,
        )
```

### 3.6 arXiv 学术论文 (官方 API、无反爬)

```python
import arxiv

class ArxivScraper:
    """arXiv 量化金融/ML 论文抓取"""
    
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
    
    def __init__(self):
        self.client = arxiv.Client()
    
    def search_papers(self, 
        query: str = None,
        categories: List[str] = None,
        max_results: int = 100,
        date_from: datetime = None
    ) -> List[FinanceNews]:
        """搜索论文"""
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
        for paper in self.client.results(search):
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
            ))
        return papers
```

### 3.7 监管公告 (证监会/交易所官网)

```python
class RegulatorScraper:
    """监管公告定时抓取"""
    
    SOURCES = {
        'csrc': 'https://www.csrc.gov.cn/csrc/xwfbh/',           # 证监会新闻发布
        'sse': 'https://www.sse.com.cn/assortment/stock/list/info/announcement/',  # 上交所公告
        'szse': 'https://www.szse.cn/disclosure/listed/announcement/',  # 深交所公告
        'pbc': 'https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/index.html',  # 央行货币政策
    }
    
    async def fetch_all(self) -> List[FinanceNews]:
        """并发抓取所有监管源"""
        tasks = [self._fetch_source(name, url) for name, url in self.SOURCES.items()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        all_news = []
        for name, result in zip(self.SOURCES.keys(), results):
            if isinstance(result, Exception):
                logger.error(f"{name} 抓取失败: {result}")
                continue
            all_news.extend(result)
        return all_news
```

## 4. 聚合器：多源融合去重

```python
class NewsAggregator:
    """多源新闻聚合、去重、排序、推送"""
    
    def __init__(self, scrapers: Dict[NewsSource, BaseScraper]):
        self.scrapers = scrapers
        self.dedup_cache = {}  # 内容指纹 -> news_id
    
    async def fetch_all(self, 
        sources: List[NewsSource] = None,
        keywords: List[str] = None,
        since_hours: int = 24
    ) -> List[FinanceNews]:
        """并发抓取所有源，去重、过滤、按时间倒序"""
        sources = sources or list(self.scrapers.keys())
        cutoff = datetime.utcnow() - timedelta(hours=since_hours)
        
        # 并发抓取
        tasks = [self.scrapers[s].get_latest() for s in sources]
        all_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 合并去重
        merged = []
        for source, result in zip(sources, all_results):
            if isinstance(result, Exception):
                continue
            for news in result:
                if news.publish_time < cutoff:
                    continue
                if keywords and not self._match_keywords(news, keywords):
                    continue
                if self._is_duplicate(news):
                    continue
                merged.append(news)
        
        # 按重要性、时间排序
        merged.sort(key=lambda x: (x.importance or 0, x.publish_time), reverse=True)
        return merged
    
    def _is_duplicate(self, news: FinanceNews) -> bool:
        """基于标题+内容指纹去重"""
        fingerprint = hashlib.md5(
            (news.title + news.content[:500]).encode()
        ).hexdigest()
        if fingerprint in self.dedup_cache:
            return True
        self.dedup_cache[fingerprint] = news.news_id
        return False
    
    def _match_keywords(self, news: FinanceNews, keywords: List[str]) -> bool:
        text = (news.title + ' ' + news.summary + ' ' + news.content).lower()
        return any(kw.lower() in text for kw in keywords)
```

## 5. 实时流式处理架构

```python
# 生产者-消费者模式
class NewsStreamProcessor:
    def __init__(self):
        self.aggregator = NewsAggregator(...)
        self.sentiment_analyzer = SentimentAnalyzer()  # 见 sentiment-analysis.md
        self.alert_webhooks = []
    
    async def start_stream(self, keywords: List[str], interval: int = 60):
        """每 interval 秒轮询一次"""
        while True:
            try:
                news_list = await self.aggregator.fetch_all(keywords=keywords, since_hours=1)
                for news in news_list:
                    # 情感分析
                    news.sentiment = self.sentiment_analyzer.analyze(news.content)
                    
                    # 实体识别
                    news.entities = self.sentiment_analyzer.extract_entities(news.content)
                    
                    # 触发告警
                    if news.sentiment < -0.6 and news.importance >= 3:
                        await self._trigger_alert(news)
                    
                    # 入库
                    await self._store(news)
            except Exception as e:
                logger.error(f"流式处理异常: {e}")
            
            await asyncio.sleep(interval)
```

## 6. 存储 Schema 建议

```sql
CREATE TABLE finance_news (
    news_id VARCHAR(64) PRIMARY KEY,
    source VARCHAR(32) NOT NULL,
    category VARCHAR(32),
    title TEXT NOT NULL,
    summary TEXT,
    content TEXT,
    url TEXT UNIQUE,
    author VARCHAR(100),
    publish_time TIMESTAMPTZ,
    crawl_time TIMESTAMPTZ DEFAULT NOW(),
    symbols TEXT[],
    keywords TEXT[],
    entities JSONB,
    sentiment FLOAT,
    importance INT,
    credibility FLOAT,
    images TEXT[],
    raw_json JSONB,
    
    -- 索引
    INDEX idx_news_source_time (source, publish_time DESC),
    INDEX idx_news_symbols (symbols),
    INDEX idx_news_sentiment (sentiment),
    INDEX idx_news_keywords (keywords)  -- GIN 索引
);
```

## 7. 常见问题

| 问题 | 解决方案 |
|------|----------|
| 新浪财经 JSONP 解析失败 | 正则提取 `\((.*)\)`，处理特殊字符转义 |
| 财联社 WebSocket 断连 | 自动重连指数退避，心跳保活 |
| 华尔街见闻 Token 过期 | 定时刷新 Token，监听 401 重新登录 |
| 雪球登录态失效 | CDP 复用已登录浏览器配置文件，定期检查 |
| 微信文章内容为空 | 版权保护，需模拟真人滚动触发懒加载 |
| arXiv 速率限制 | 官方建议 3 秒/请求，使用 `arxiv.Client(delay_seconds=3)` |
| 多源时间不一致 | 统一转 UTC，保留原时区信息在 meta 中 |
| 内容指纹冲突 | 使用 SimHash 而非 MD5，支持近似去重 |

## 8. 扩展：自定义数据源接入指南

1. 实现 `BaseScraper` 接口 (见主文件核心架构)
2. 定义 `source_name`、`supported_types`
3. 实现 `fetch()` 返回 `AsyncIterator[FinanceData]`
4. 在 `NewsAggregator` 中注册
5. 添加对应的单元测试和监控告警