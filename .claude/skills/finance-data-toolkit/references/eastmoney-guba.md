# 东方财富股吧帖子抓取模块

覆盖：指定股票/板块帖子列表、热度排序、时间范围筛选、评论树抓取、用户画像、关键词过滤、增量同步。

## 1. 数据源与接口分析

| 接口类型 | URL 模式 | 优点 | 缺点 | 适用场景 |
|----------|----------|------|------|----------|
| **Web API (JSON)** | `guba.eastmoney.com/interface/GetData.aspx` | 结构化、无需渲染、速度快 | 参数加密、签名复杂、易失效 | 高频抓取、列表页 |
| **网页解析 (HTML)** | `guba.eastmoney.com/list,<code>.html` | 稳定、字段全、含评论 | 需浏览器渲染、反爬强 | 详情页、评论树 |
| **CDP 浏览器控制** | 真实 Chrome + browser-cdp | 绕过所有反爬、支持交互 | 资源占用高、速度慢 | 登录后操作、复杂筛选 |

> **推荐策略**：列表页用 Web API（逆向签名算法），详情页+评论用 CDP 浏览器

## 2. 核心数据结构

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List

@dataclass
class GubaPost:
    post_id: str              # 帖子 ID
    title: str                # 标题
    author: str               # 作者昵称
    author_id: str            # 作者 ID
    author_followers: int     # 粉丝数
    author_influence: int     # 影响力指数
    read_count: int           # 阅读数
    comment_count: int        # 评论数
    like_count: int           # 点赞数
    publish_time: datetime    # 发布时间
    update_time: datetime     # 最后更新时间
    content: str              # 正文内容（HTML 或纯文本）
    stock_codes: List[str]    # 涉及股票代码列表
    board: str                # 所属板块
    sentiment: Optional[float] = None  # 情感得分 [-1, 1]
    keywords: List[str] = None         # 提取关键词

@dataclass
class GubaComment:
    comment_id: str
    post_id: str
    parent_id: Optional[str]  # 父评论 ID，None 为一级评论
    author: str
    author_id: str
    content: str
    publish_time: datetime
    like_count: int
    replies: List['GubaComment'] = None  # 子评论树
```

## 3. Web API 逆向签名实现（列表页高频抓取）

```python
import httpx
import hashlib
import time
import json
from typing import AsyncIterator

class EastmoneyGubaAPI:
    """东方财富股吧 Web API 客户端"""
    
    BASE_URL = 'https://guba.eastmoney.com/interface/GetData.aspx'
    
    def __init__(self, proxy: str = None):
        self.client = httpx.AsyncClient(
            timeout=30,
            proxy=proxy,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': 'https://guba.eastmoney.com/',
                'Origin': 'https://guba.eastmoney.com',
            }
        )
    
    def _generate_sign(self, params: dict) -> str:
        """生成签名（逆向算法，需定期维护）"""
        # 核心参数排序拼接
        sorted_params = '&'.join(f'{k}={v}' for k, v in sorted(params.items()))
        # 固定盐值（需从 JS 逆向获取，示例值）
        salt = 'em_guba_2024_secret_key'
        sign_str = f'{sorted_params}&{salt}'
        return hashlib.md5(sign_str.encode()).hexdigest().upper()
    
    async def get_post_list(self,
        stock_code: str = None,
        board_id: str = None,
        page: int = 1,
        page_size: int = 50,
        sort: str = 'hot',  # hot/time
        start_time: str = None,
        end_time: str = None
    ) -> AsyncIterator[GubaPost]:
        """
        stock_code: 000001 / 600000 (不带后缀)
        board_id: 板块 ID，如 'gn_001' (概念)、'hy_001' (行业)
        sort: hot(热度) / time(最新)
        """
        params = {
            'path': 'reply/api/Reply/ArticleList',
            'param': json.dumps({
                'stockCode': stock_code,
                'boardId': board_id,
                'pageIndex': page,
                'pageSize': page_size,
                'sortType': 1 if sort == 'hot' else 0,
                'startTime': start_time,
                'endTime': end_time,
            }, separators=(',', ':')),
            'env': '2',
            'type': 'web',
            'v': '2.0',
            'timestamp': str(int(time.time() * 1000)),
        }
        params['sign'] = self._generate_sign(params)
        
        resp = await self.client.get(self.BASE_URL, params=params)
        data = resp.json()
        
        for item in data.get('data', {}).get('list', []):
            yield GubaPost(
                post_id=item['post_id'],
                title=item['post_title'],
                author=item['user_nickname'],
                author_id=item['user_id'],
                author_followers=item.get('fans_count', 0),
                author_influence=item.get('influence', 0),
                read_count=item['read_count'],
                comment_count=item['reply_count'],
                like_count=item.get('like_count', 0),
                publish_time=pd.to_datetime(item['post_publish_time']),
                update_time=pd.to_datetime(item['post_update_time']),
                content=item.get('post_content', ''),
                stock_codes=[stock_code] if stock_code else [],
                board=board_id or 'stock',
            )
    
    async def get_hot_posts(self, board: str = 'concept', top_n: int = 20) -> List[GubaPost]:
        """获取板块热帖 Top N"""
        board_map = {
            'concept': 'gn_001',  # 概念板块
            'industry': 'hy_001', # 行业板块
            'region': 'dy_001',   # 地域板块
        }
        board_id = board_map.get(board, board)
        posts = []
        async for post in self.get_post_list(board_id=board_id, page=1, page_size=top_n, sort='hot'):
            posts.append(post)
        return posts
```

## 4. CDP 浏览器模式（详情页 + 评论树 + 登录态）

```python
# 使用本项目的 browser-cdp skill
from browser_cdp import CDPBrowser, ElementLocator

class GubaCDPScraper:
    def __init__(self, cdp_endpoint: str = 'http://127.0.0.1:9222'):
        self.browser = CDPBrowser(cdp_endpoint)
    
    async def get_post_detail(self, post_url: str) -> GubaPost:
        """抓取帖子详情页（含完整正文、图片、@用户）"""
        page = await self.browser.new_page(post_url)
        
        # 等待内容加载
        await page.wait_for_selector('.article-content', timeout=10000)
        
        # 提取字段
        title = await page.text_content('.article-title')
        author = await page.text_content('.author-name')
        author_id = await page.get_attribute('.author-name', 'data-uid')
        content_html = await page.inner_html('.article-content')
        publish_time_str = await page.text_content('.publish-time')
        read_count = int(await page.text_content('.read-count').replace(',', ''))
        comment_count = int(await page.text_content('.comment-count').replace(',', ''))
        
        # 股票代码标签
        stock_tags = await page.query_selector_all('.stock-tag')
        stock_codes = [await tag.text_content() for tag in stock_tags]
        
        await page.close()
        
        return GubaPost(
            post_id=post_url.split('/')[-1].replace('.html', ''),
            title=title,
            author=author,
            author_id=author_id,
            author_followers=0,  # 需单独请求用户主页
            author_influence=0,
            read_count=read_count,
            comment_count=comment_count,
            like_count=0,
            publish_time=parse_guba_time(publish_time_str),
            update_time=parse_guba_time(publish_time_str),
            content=content_html,
            stock_codes=stock_codes,
            board='',
        )
    
    async def get_comment_tree(self, post_id: str, max_depth: int = 3) -> List[GubaComment]:
        """抓取评论树（支持展开更多、分页）"""
        url = f'https://guba.eastmoney.com/news,{post_id}.html'
        page = await self.browser.new_page(url)
        
        # 点击「查看更多评论」直到加载完或达 max_depth
        for _ in range(max_depth):
            more_btn = await page.query_selector('.comment-load-more:not(.hidden)')
            if not more_btn:
                break
            await more_btn.click()
            await page.wait_for_timeout(1000)
        
        # 解析评论树
        comment_elements = await page.query_selector_all('.comment-item')
        comments = []
        for elem in comment_elements:
            comment = await self._parse_comment_element(elem)
            comments.append(comment)
        
        await page.close()
        return self._build_comment_tree(comments)
    
    async def _parse_comment_element(self, elem) -> GubaComment:
        return GubaComment(
            comment_id=await elem.get_attribute('data-cid'),
            post_id='',  # 后续填充
            parent_id=await elem.get_attribute('data-pid') or None,
            author=await elem.text_content('.comment-author'),
            author_id=await elem.get_attribute('.comment-author', 'data-uid'),
            content=await elem.inner_html('.comment-content'),
            publish_time=parse_guba_time(await elem.text_content('.comment-time')),
            like_count=int(await elem.text_content('.comment-like').replace(',', '') or 0),
            replies=[],
        )
    
    def _build_comment_tree(self, comments: List[GubaComment]) -> List[GubaComment]:
        """扁平列表转树结构"""
        comment_map = {c.comment_id: c for c in comments}
        roots = []
        for c in comments:
            if c.parent_id and c.parent_id in comment_map:
                comment_map[c.parent_id].replies.append(c)
            else:
                roots.append(c)
        return roots
```

## 5. 用户画像抓取

```python
async def get_user_profile(self, user_id: str) -> dict:
    """抓取用户主页：粉丝数、影响力、历史发帖、持仓透露"""
    url = f'https://guba.eastmoney.com/u/{user_id}'
    page = await self.browser.new_page(url)
    
    profile = {
        'user_id': user_id,
        'nickname': await page.text_content('.user-nickname'),
        'followers': int(await page.text_content('.fans-count').replace(',', '') or 0),
        'following': int(await page.text_content('.follow-count').replace(',', '') or 0),
        'influence': int(await page.text_content('.influence-score').replace(',', '') or 0),
        'total_posts': int(await page.text_content('.post-count').replace(',', '') or 0),
        'verified': await page.query_selector('.verified-badge') is not None,
        'tags': [await t.text_content() for t in await page.query_selector_all('.user-tag')],
        'recent_stocks': [],  # 从最近帖子提取
    }
    
    # 解析最近 20 条帖子提取涉及股票
    post_links = await page.query_selector_all('.user-post-list a')
    for link in post_links[:20]:
        href = await link.get_attribute('href')
        if 'news,' in href:
            post_id = href.split('news,')[-1].replace('.html', '')
            post = await self.get_post_detail(f'https://guba.eastmoney.com{href}')
            profile['recent_stocks'].extend(post.stock_codes)
    
    profile['recent_stocks'] = list(set(profile['recent_stocks']))
    await page.close()
    return profile
```

## 6. 关键词过滤与增量同步

```python
class GubaIncrementalSync:
    """增量同步：基于 post_id 去重 + 时间游标"""
    
    def __init__(self, storage):
        self.storage = storage  # Redis / PostgreSQL / SQLite
        self.api = EastmoneyGubaAPI()
        self.cdp = GubaCDPScraper()
    
    async def sync_stock(self, stock_code: str, keywords: List[str] = None,
                         since_hours: int = 24) -> List[GubaPost]:
        """同步某股票最新帖子，支持关键词过滤"""
        cutoff_time = datetime.now() - timedelta(hours=since_hours)
        new_posts = []
        
        page = 1
        while True:
            posts = []
            async for post in self.api.get_post_list(stock_code=stock_code, page=page, sort='time'):
                if post.publish_time < cutoff_time:
                    return new_posts  # 时间倒序，遇到旧帖停止
                
                # 去重检查
                if await self.storage.exists(f'guba:post:{post.post_id}'):
                    continue
                
                # 关键词过滤
                if keywords:
                    content_lower = (post.title + post.content).lower()
                    if not any(kw.lower() in content_lower for kw in keywords):
                        continue
                
                # 详情页补全（可选：仅热帖补全）
                if post.read_count > 1000 or post.comment_count > 50:
                    detail = await self.cdp.get_post_detail(
                        f'https://guba.eastmoney.com/news,{post.post_id}.html'
                    )
                    post.content = detail.content
                
                await self.storage.set(f'guba:post:{post.post_id}', post)
                new_posts.append(post)
            
            if len(posts) == 0:
                break
            page += 1
            await asyncio.sleep(1)  # 礼貌延迟
        
        return new_posts
```

## 7. 反爬应对策略

| 反爬手段 | 识别特征 | 应对方案 |
|----------|----------|----------|
| **IP 频率限制** | 返回 429 / 空数据 / 验证码 | 代理池轮换、降低并发、指数退避 |
| **签名校验** | API 返回 `sign error` | 逆向最新签名算法、定期更新盐值 |
| **Cookie/登录态** | 返回「请登录」/ 重定向登录页 | CDP 复用已登录浏览器、Cookie 持久化 |
| **JS 混淆加密** | 参数加密、响应加密 | CDP 直接执行 JS、Hook 关键函数 |
| **行为分析** | 鼠标轨迹、点击间隔、停留时间 | CDP 模拟真人行为、随机延迟、滚动页面 |
| **设备指纹** | Canvas/WebGL/字体指纹 | 使用真实浏览器配置文件、禁用指纹保护 |

## 8. 性能与存储建议

```python
# 存储 Schema 示例 (PostgreSQL)
CREATE TABLE guba_posts (
    post_id VARCHAR(32) PRIMARY KEY,
    title TEXT NOT NULL,
    author VARCHAR(100),
    author_id VARCHAR(50),
    author_followers INT,
    author_influence INT,
    read_count BIGINT,
    comment_count INT,
    like_count INT,
    publish_time TIMESTAMPTZ,
    update_time TIMESTAMPTZ,
    content TEXT,
    stock_codes TEXT[],  -- 数组类型
    board VARCHAR(50),
    sentiment FLOAT,
    keywords TEXT[],
    raw_json JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_guba_stock_time ON guba_posts (stock_codes, publish_time DESC);
CREATE INDEX idx_guba_author ON guba_posts (author_id);
CREATE INDEX idx_guba_sentiment ON guba_posts (sentiment);
```

## 9. 常用板块 ID 速查

| 板块类型 | 板块名称 | board_id |
|----------|----------|----------|
| 概念板块 | 人工智能 | gn_001 |
| 概念板块 | 新能源 | gn_002 |
| 概念板块 | 半导体 | gn_003 |
| 行业板块 | 银行 | hy_001 |
| 行业板块 | 医药生物 | hy_002 |
| 地域板块 | 广东 | dy_001 |
| 个股吧 | 平安银行(000001) | stock_000001 |
| 个股吧 | 贵州茅台(600519) | stock_600519 |

> 完整板块 ID 列表请查看 `browse_paths` 下的 `full-api-docs/board-id-mapping.json`