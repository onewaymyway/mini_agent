# -*- coding: utf-8 -*-
"""
东方财富股吧帖子抓取模块

支持：
- Web API 模式（高频列表页，需逆向签名）
- CDP 浏览器模式（详情页、评论树、登录态操作）
- 用户画像抓取
- 关键词过滤与增量同步
"""

import sys
import os
import json
import time
import hashlib
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, AsyncIterator
from dataclasses import dataclass, asdict
from pathlib import Path

# 尝试导入可选依赖
try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False


@dataclass
class GubaPost:
    """股吧帖子数据结构"""
    post_id: str              # 帖子 ID
    title: str                # 标题
    author: str               # 作者昵称
    author_id: str            # 作者 ID
    author_followers: int     # 粉丝数
    author_influence: int     # 影响力指数
    read_count: int           # 阅读数
    comment_count: int        # 评论数
    like_count: int           # 点赞数
    publish_time: str         # 发布时间 (ISO 格式)
    update_time: str          # 最后更新时间 (ISO 格式)
    content: str              # 正文内容（HTML 或纯文本）
    stock_codes: List[str]    # 涉及股票代码列表
    board: str                # 所属板块
    sentiment: Optional[float] = None  # 情感得分 [-1, 1]
    keywords: List[str] = None         # 提取关键词
    raw: Optional[Dict] = None         # 原始响应数据

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GubaComment:
    """股吧评论数据结构"""
    comment_id: str
    post_id: str
    parent_id: Optional[str]  # 父评论 ID，None 为一级评论
    author: str
    author_id: str
    content: str
    publish_time: str
    like_count: int
    replies: List['GubaComment'] = None  # 子评论树

    def __post_init__(self):
        if self.replies is None:
            self.replies = []

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GubaUserProfile:
    """股吧用户画像"""
    user_id: str
    nickname: str
    followers: int
    following: int
    influence: int
    total_posts: int
    verified: bool
    tags: List[str]
    recent_stocks: List[str]
    raw: Optional[Dict] = None

    def to_dict(self) -> dict:
        return asdict(self)


class EastmoneyGubaAPI:
    """东方财富股吧 Web API 客户端（列表页高频抓取）"""
    
    BASE_URL = 'https://guba.eastmoney.com/interface/GetData.aspx'
    
    def __init__(self, proxy: str = None, timeout: int = 30):
        if not HAS_HTTPX:
            raise ImportError("httpx is required for EastmoneyGubaAPI. Install with: pip install httpx")
        
        self.client = httpx.AsyncClient(
            timeout=timeout,
            proxy=proxy,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Referer': 'https://guba.eastmoney.com/',
                'Origin': 'https://guba.eastmoney.com',
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            }
        )
    
    def _generate_sign(self, params: dict) -> str:
        """生成签名（逆向算法，需定期维护盐值）"""
        # 核心参数排序拼接
        sorted_params = '&'.join(f'{k}={v}' for k, v in sorted(params.items()))
        # 固定盐值（需从 JS 逆向获取，示例值，实际使用时需更新）
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
        获取帖子列表
        
        Args:
            stock_code: 股票代码，如 '000001' / '600000' (不带后缀)
            board_id: 板块 ID，如 'gn_001' (概念)、'hy_001' (行业)
            page: 页码
            page_size: 每页数量
            sort: hot(热度) / time(最新)
            start_time: 开始时间 YYYY-MM-DD
            end_time: 结束时间 YYYY-MM-DD
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
        
        try:
            resp = await self.client.get(self.BASE_URL, params=params)
            data = resp.json()
            
            for item in data.get('data', {}).get('list', []):
                # 解析时间
                pub_time = item.get('post_publish_time', '')
                upd_time = item.get('post_update_time', '')
                
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
                    publish_time=pub_time,
                    update_time=upd_time,
                    content=item.get('post_content', ''),
                    stock_codes=[stock_code] if stock_code else [],
                    board=board_id or 'stock',
                    raw=item
                )
        except Exception as e:
            print(f"东方财富股吧 API 请求失败: {e}", file=sys.stderr)
    
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
    
    async def close(self):
        await self.client.aclose()
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, *args):
        await self.close()


class GubaCDPScraper:
    """基于 CDP 浏览器的股吧详情页/评论/用户画像抓取器"""
    
    def __init__(self, cdp_endpoint: str = 'http://127.0.0.1:9222'):
        self.cdp_endpoint = cdp_endpoint
        self._browser = None
    
    async def _get_browser(self):
        """懒加载浏览器连接"""
        if self._browser is None:
            # 这里需要 browser-cdp skill 的支持
            # 实际使用时请确保已启动 CDP 浏览器
            try:
                import websocket
                import uuid
                self._browser = {
                    'endpoint': self.cdp_endpoint,
                    'ws': None,
                    'msg_id': 0
                }
            except ImportError:
                raise ImportError("websocket-client is required for CDP. Install with: pip install websocket-client")
        return self._browser
    
    async def _cdp_send(self, method: str, params: dict = None) -> dict:
        """发送 CDP 命令"""
        browser = await self._get_browser()
        if browser['ws'] is None:
            browser['ws'] = websocket.create_connection(browser['endpoint'])
        
        browser['msg_id'] += 1
        msg = {
            'id': browser['msg_id'],
            'method': method,
            'params': params or {}
        }
        browser['ws'].send(json.dumps(msg))
        
        # 等待响应
        while True:
            resp = json.loads(browser['ws'].recv())
            if resp.get('id') == browser['msg_id']:
                return resp
    
    async def _cdp_navigate(self, url: str):
        """导航到 URL"""
        # 创建新标签页
        resp = await self._cdp_send('Target.createTarget', {'url': url})
        target_id = resp['result']['targetId']
        
        # 附加到目标
        resp = await self._cdp_send('Target.attachToTarget', {'targetId': target_id, 'flatten': True})
        session_id = resp['result']['sessionId']
        
        # 启用必要域
        await self._cdp_send('Page.enable', {}, session_id)
        await self._cdp_send('Runtime.enable', {}, session_id)
        
        # 导航
        await self._cdp_send('Page.navigate', {'url': url}, session_id)
        
        # 等待加载完成
        await asyncio.sleep(3)
        
        return session_id, target_id
    
    async def _cdp_evaluate(self, session_id: str, expression: str) -> Any:
        """在页面上下文执行 JS"""
        resp = await self._cdp_send('Runtime.evaluate', {
            'expression': expression,
            'returnByValue': True,
            'awaitPromise': True
        }, session_id)
        return resp.get('result', {}).get('result', {}).get('value')
    
    async def get_post_detail(self, post_url: str) -> GubaPost:
        """抓取帖子详情页（含完整正文、图片、@用户）"""
        session_id, target_id = await self._cdp_navigate(post_url)
        
        try:
            # 等待内容加载
            await asyncio.sleep(2)
            
            # 提取字段
            title = await self._cdp_evaluate(session_id, "document.querySelector('.article-title')?.textContent || ''")
            author = await self._cdp_evaluate(session_id, "document.querySelector('.author-name')?.textContent || ''")
            author_id = await self._cdp_evaluate(session_id, "document.querySelector('.author-name')?.getAttribute('data-uid') || ''")
            content_html = await self._cdp_evaluate(session_id, "document.querySelector('.article-content')?.innerHTML || ''")
            publish_time_str = await self._cdp_evaluate(session_id, "document.querySelector('.publish-time')?.textContent || ''")
            read_count = await self._cdp_evaluate(session_id, "parseInt((document.querySelector('.read-count')?.textContent || '0').replace(/,/g, '')) || 0")
            comment_count = await self._cdp_evaluate(session_id, "parseInt((document.querySelector('.comment-count')?.textContent || '0').replace(/,/g, '')) || 0")
            
            # 股票代码标签
            stock_tags = await self._cdp_evaluate(session_id, 
                "Array.from(document.querySelectorAll('.stock-tag')).map(t => t.textContent.trim())")
            stock_codes = stock_tags if isinstance(stock_tags, list) else []
            
            # 解析 post_id
            post_id = post_url.split('/')[-1].replace('.html', '').replace('news,', '')
            
            return GubaPost(
                post_id=post_id,
                title=title,
                author=author,
                author_id=author_id,
                author_followers=0,  # 需单独请求用户主页
                author_influence=0,
                read_count=read_count,
                comment_count=comment_count,
                like_count=0,
                publish_time=publish_time_str,
                update_time=publish_time_str,
                content=content_html,
                stock_codes=stock_codes,
                board='',
            )
        finally:
            # 关闭标签页
            await self._cdp_send('Target.closeTarget', {'targetId': target_id})
    
    async def get_comment_tree(self, post_id: str, max_depth: int = 3) -> List[GubaComment]:
        """抓取评论树（支持展开更多、分页）"""
        url = f'https://guba.eastmoney.com/news,{post_id}.html'
        session_id, target_id = await self._cdp_navigate(url)
        
        try:
            # 点击「查看更多评论」直到加载完或达 max_depth
            for _ in range(max_depth):
                more_btn = await self._cdp_evaluate(session_id,
                    "document.querySelector('.comment-load-more:not(.hidden)')")
                if not more_btn:
                    break
                await self._cdp_evaluate(session_id,
                    "document.querySelector('.comment-load-more:not(.hidden)').click()")
                await asyncio.sleep(1)
            
            # 解析评论树
            comment_elements = await self._cdp_evaluate(session_id, """
                Array.from(document.querySelectorAll('.comment-item')).map(elem => ({
                    comment_id: elem.getAttribute('data-cid'),
                    parent_id: elem.getAttribute('data-pid') || null,
                    author: elem.querySelector('.comment-author')?.textContent || '',
                    author_id: elem.querySelector('.comment-author')?.getAttribute('data-uid') || '',
                    content: elem.querySelector('.comment-content')?.innerHTML || '',
                    publish_time: elem.querySelector('.comment-time')?.textContent || '',
                    like_count: parseInt((elem.querySelector('.comment-like')?.textContent || '0').replace(/,/g, '')) || 0,
                }))
            """)
            
            comments = comment_elements if isinstance(comment_elements, list) else []
            
            # 构建树结构
            comment_map = {c['comment_id']: GubaComment(
                comment_id=c['comment_id'],
                post_id=post_id,
                parent_id=c['parent_id'],
                author=c['author'],
                author_id=c['author_id'],
                content=c['content'],
                publish_time=c['publish_time'],
                like_count=c['like_count'],
                replies=[]
            ) for c in comments if c['comment_id']}
            
            roots = []
            for c in comment_map.values():
                if c.parent_id and c.parent_id in comment_map:
                    comment_map[c.parent_id].replies.append(c)
                else:
                    roots.append(c)
            
            return roots
        finally:
            await self._cdp_send('Target.closeTarget', {'targetId': target_id})
    
    async def get_user_profile(self, user_id: str) -> GubaUserProfile:
        """抓取用户主页：粉丝数、影响力、历史发帖、持仓透露"""
        url = f'https://guba.eastmoney.com/u/{user_id}'
        session_id, target_id = await self._cdp_navigate(url)
        
        try:
            await asyncio.sleep(2)
            
            nickname = await self._cdp_evaluate(session_id, "document.querySelector('.user-nickname')?.textContent || ''")
            followers = await self._cdp_evaluate(session_id, "parseInt((document.querySelector('.fans-count')?.textContent || '0').replace(/,/g, '')) || 0")
            following = await self._cdp_evaluate(session_id, "parseInt((document.querySelector('.follow-count')?.textContent || '0').replace(/,/g, '')) || 0")
            influence = await self._cdp_evaluate(session_id, "parseInt((document.querySelector('.influence-score')?.textContent || '0').replace(/,/g, '')) || 0")
            total_posts = await self._cdp_evaluate(session_id, "parseInt((document.querySelector('.post-count')?.textContent || '0').replace(/,/g, '')) || 0")
            verified = await self._cdp_evaluate(session_id, "document.querySelector('.verified-badge') !== null")
            
            tags = await self._cdp_evaluate(session_id,
                "Array.from(document.querySelectorAll('.user-tag')).map(t => t.textContent.trim())")
            tags = tags if isinstance(tags, list) else []
            
            # 解析最近 20 条帖子提取涉及股票
            recent_stocks = []
            post_links = await self._cdp_evaluate(session_id,
                "Array.from(document.querySelectorAll('.user-post-list a')).slice(0, 20).map(a => a.href)")
            
            if isinstance(post_links, list):
                for href in post_links:
                    if 'news,' in href:
                        pid = href.split('news,')[-1].replace('.html', '')
                        try:
                            post = await self.get_post_detail(f'https://guba.eastmoney.com{href}')
                            recent_stocks.extend(post.stock_codes)
                        except:
                            pass
            
            recent_stocks = list(set(recent_stocks))
            
            return GubaUserProfile(
                user_id=user_id,
                nickname=nickname,
                followers=followers,
                following=following,
                influence=influence,
                total_posts=total_posts,
                verified=verified,
                tags=tags,
                recent_stocks=recent_stocks,
            )
        finally:
            await self._cdp_send('Target.closeTarget', {'targetId': target_id})
    
    async def close(self):
        if self._browser and self._browser['ws']:
            self._browser['ws'].close()
            self._browser['ws'] = None
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, *args):
        await self.close()


class GubaIncrementalSync:
    """增量同步：基于 post_id 去重 + 时间游标"""
    
    def __init__(self, storage=None, proxy: str = None):
        self.storage = storage  # 可选：Redis / PostgreSQL / SQLite 等存储后端
        self.api = EastmoneyGubaAPI(proxy=proxy)
        self.cdp = GubaCDPScraper()
    
    async def sync_stock(self, stock_code: str, keywords: List[str] = None,
                         since_hours: int = 24, max_pages: int = 10,
                         detail_threshold_read: int = 1000,
                         detail_threshold_comment: int = 50) -> List[GubaPost]:
        """
        同步某股票最新帖子，支持关键词过滤
        
        Args:
            stock_code: 股票代码 (不带后缀，如 '000001')
            keywords: 关键词列表，仅保留包含关键词的帖子
            since_hours: 同步最近多少小时内的帖子
            max_pages: 最大翻页数
            detail_threshold_read: 阅读数超过此值则抓取详情页
            detail_threshold_comment: 评论数超过此值则抓取详情页
        """
        cutoff_time = datetime.now() - timedelta(hours=since_hours)
        new_posts = []
        
        for page in range(1, max_pages + 1):
            posts = []
            async for post in self.api.get_post_list(stock_code=stock_code, page=page, sort='time'):
                # 时间倒序，遇到旧帖停止
                try:
                    pub_time = datetime.fromisoformat(post.publish_time.replace('Z', '+00:00'))
                    if pub_time < cutoff_time:
                        await self.api.close()
                        return new_posts
                except:
                    pass
                
                # 去重检查
                if self.storage and hasattr(self.storage, 'exists'):
                    if await self.storage.exists(f'guba:post:{post.post_id}'):
                        continue
                
                # 关键词过滤
                if keywords:
                    content_lower = (post.title + post.content).lower()
                    if not any(kw.lower() in content_lower for kw in keywords):
                        continue
                
                # 详情页补全（仅热帖）
                if post.read_count > detail_threshold_read or post.comment_count > detail_threshold_comment:
                    try:
                        detail = await self.cdp.get_post_detail(
                            f'https://guba.eastmoney.com/news,{post.post_id}.html'
                        )
                        post.content = detail.content
                    except Exception as e:
                        print(f"详情页抓取失败 {post.post_id}: {e}", file=sys.stderr)
                
                # 存储
                if self.storage and hasattr(self.storage, 'set'):
                    await self.storage.set(f'guba:post:{post.post_id}', post.to_dict())
                
                new_posts.append(post)
                posts.append(post)
            
            if len(posts) == 0:
                break
            
            await asyncio.sleep(1)  # 礼貌延迟
        
        await self.api.close()
        return new_posts
    
    async def close(self):
        await self.api.close()
        await self.cdp.close()
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, *args):
        await self.close()


# ============== 便捷函数 ==============

async def fetch_guba_posts(stock_code: str, page: int = 1, page_size: int = 50,
                           sort: str = 'time', proxy: str = None) -> List[GubaPost]:
    """获取股吧帖子列表（便捷函数）"""
    async with EastmoneyGubaAPI(proxy=proxy) as api:
        posts = []
        async for post in api.get_post_list(stock_code=stock_code, page=page, page_size=page_size, sort=sort):
            posts.append(post)
        return posts


async def fetch_guba_hot_posts(board: str = 'concept', top_n: int = 20, proxy: str = None) -> List[GubaPost]:
    """获取板块热帖 Top N（便捷函数）"""
    async with EastmoneyGubaAPI(proxy=proxy) as api:
        return await api.get_hot_posts(board=board, top_n=top_n)


async def fetch_guba_post_detail(post_url: str, cdp_endpoint: str = 'http://127.0.0.1:9222') -> GubaPost:
    """抓取帖子详情页（便捷函数，需 CDP 浏览器）"""
    async with GubaCDPScraper(cdp_endpoint=cdp_endpoint) as scraper:
        return await scraper.get_post_detail(post_url)


async def fetch_guba_comments(post_id: str, cdp_endpoint: str = 'http://127.0.0.1:9222',
                              max_depth: int = 3) -> List[GubaComment]:
    """抓取评论树（便捷函数，需 CDP 浏览器）"""
    async with GubaCDPScraper(cdp_endpoint=cdp_endpoint) as scraper:
        return await scraper.get_comment_tree(post_id, max_depth=max_depth)


async def fetch_guba_user_profile(user_id: str, cdp_endpoint: str = 'http://127.0.0.1:9222') -> GubaUserProfile:
    """抓取用户画像（便捷函数，需 CDP 浏览器）"""
    async with GubaCDPScraper(cdp_endpoint=cdp_endpoint) as scraper:
        return await scraper.get_user_profile(user_id)


# ============== 同步包装器（供同步代码调用） ==============

def _run_async(coro):
    """安全运行异步协程：检测是否在已有事件循环中"""
    try:
        # 检查是否已在事件循环中
        loop = asyncio.get_running_loop()
        # 已在事件循环中，无法使用 asyncio.run，抛出友好错误
        raise RuntimeError(
            "sync_fetch_guba_posts 不能在 async 事件循环内调用。"
            "请改用 async 版本：await fetch_guba_posts(...)"
            "或者安装 nest_asyncio 并调用 nest_asyncio.apply() 后再使用同步包装器。"
        )
    except RuntimeError as e:
        # asyncio.get_running_loop() 在没有事件循环时会抛出 "no running event loop"
        if "no running event loop" in str(e):
            # 没有事件循环，可以安全使用 asyncio.run
            return asyncio.run(coro)
        else:
            # 其他 RuntimeError（包括已在事件循环中的情况），重新抛出
            raise

def sync_fetch_guba_posts(stock_code: str, page: int = 1, page_size: int = 50,
                          sort: str = 'time', proxy: str = None) -> List[GubaPost]:
    """同步版本获取股吧帖子列表"""
    return _run_async(fetch_guba_posts(stock_code, page, page_size, sort, proxy))


def sync_fetch_guba_hot_posts(board: str = 'concept', top_n: int = 20, proxy: str = None) -> List[GubaPost]:
    """同步版本获取板块热帖"""
    return _run_async(fetch_guba_hot_posts(board, top_n, proxy))


if __name__ == '__main__':
    # 测试
    import sys
    
    print("测试股吧帖子列表抓取...")
    try:
        posts = sync_fetch_guba_posts('600519', page=1, page_size=5, sort='time')
        print(f"获取到 {len(posts)} 条帖子")
        for p in posts[:3]:
            print(f"  {p.post_id}: {p.title[:30]}... (阅读:{p.read_count}, 评论:{p.comment_count})")
    except Exception as e:
        print(f"测试失败: {e}", file=sys.stderr)
    
    print("\n测试板块热帖...")
    try:
        hot = sync_fetch_guba_hot_posts('concept', top_n=5)
        print(f"获取到 {len(hot)} 条热帖")
        for p in hot[:3]:
            print(f"  {p.post_id}: {p.title[:30]}... (阅读:{p.read_count})")
    except Exception as e:
        print(f"测试失败: {e}", file=sys.stderr)