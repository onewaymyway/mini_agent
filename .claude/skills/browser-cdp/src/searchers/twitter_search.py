#!/usr/bin/env python
"""
twitter_search.py - Twitter/X 推文搜索器

使用 browser-cdp skill 搜索 Twitter/X 推文，支持关键词搜索、用户主页、话题标签搜索。

注意：Twitter/X 需要登录态，首次使用需手动登录。

用法:
    python twitter_search.py --query "Python programming" --max-results 20
    python twitter_search.py --user "elonmusk" --max-results 50
    python twitter_search.py --hashtag "AI" --max-results 30
"""

import argparse
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Dict, Optional
from urllib.parse import quote

# 导入基础模块
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.searchers.base import SearcherConfig, SearchResult, BaseSearcher
from src.searchers.utils import (
    random_delay, get_random_ua, save_results, clean_text, truncate_text
)
from src.searchers.browser_utils import ensure_browser, run_cmd, PYTHON_CMD, SKILL_DIR


# ========== Twitter/X 专用配置 ==========
TW_BASE = "https://twitter.com"
TW_SEARCH_URL = "https://twitter.com/search?q={query}&f=live"
TW_USER_URL = "https://twitter.com/{username}/with_replies"
TW_HASHTAG_URL = "https://twitter.com/hashtag/{hashtag}"


class TwitterSearcher(BaseSearcher):
    """Twitter/X 推文搜索器"""
    
    def __init__(self, config: Optional[SearcherConfig] = None):
        super().__init__(config)
        self._search_type = "query"  # query/user/hashtag
        self._extra_param = ""
    
    @property
    def source_name(self) -> str:
        return "twitter"
    
    @property
    def supported_types(self) -> List[str]:
        return ["query", "user", "hashtag"]
    
    def search(
        self,
        query: str = "",
        search_type: str = "query",
        max_results: int = 20,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30,
        session_name: Optional[str] = "twitter_session",
    ) -> List[Dict]:
        """搜索 Twitter/X 推文
        
        Args:
            query: 搜索关键词、用户名或话题标签
            search_type: 搜索类型（query/user/hashtag）
            max_results: 最大结果数
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            output_dir: 输出目录
            wait_timeout: 等待超时时间
            session_name: 浏览器会话名称
            
        Returns:
            推文数据列表
        """
        self._search_type = search_type
        self._extra_param = query
        
        print(f"[Twitter搜索] 类型: {search_type}, 关键词: {query}")
        
        # 确保浏览器连接
        if tab_id is None:
            result = ensure_browser(
                port=port,
                stealth=stealth,
            )
            if result.get("error"):
                print(f"[错误] 浏览器启动失败: {result['error']}")
                return []
            tab_id = result.get("tab_id")
            port = result.get("port", port)
            print(f"[浏览器] 端口: {port}, Tab: {tab_id}")
        
        # 检查登录状态
        if not self._check_login_status(port, tab_id):
            print("[提示] 请先在浏览器中登录 Twitter/X，然后按回车继续...")
            input()
        
        # 根据类型执行搜索
        if search_type == "query":
            results = self._search_tweets(port, tab_id, query, max_results)
        elif search_type == "user":
            results = self._search_user(port, tab_id, query, max_results)
        elif search_type == "hashtag":
            results = self._search_hashtag(port, tab_id, query, max_results)
        else:
            results = self._search_tweets(port, tab_id, query, max_results)
        
        # 保存结果
        if output_dir:
            save_results(results, output_dir, f"twitter_{search_type}_{query[:20]}", "json")
            save_results(results, output_dir, f"twitter_{search_type}_{query[:20]}", "csv")
        
        print(f"[完成] 共抓取 {len(results)} 条推文")
        return results
    
    def _check_login_status(self, port: int, tab_id: str) -> bool:
        """检查登录状态"""
        js_code = '''
        (function() {
            // 检查是否有用户信息
            var userEl = document.querySelector('a[data-testid="AppTabBar_Profile_Link"]');
            var loginBtn = document.querySelector('[data-testid="loginBtn"]');
            
            // 如果存在登录按钮，说明未登录
            if (loginBtn) return false;
            
            // 如果存在用户信息，说明已登录
            return userEl !== null;
        })()
        '''
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", str(tab_id),
            "--eval", js_code,
        ])
        try:
            return json.loads(result.get("result", "false"))
        except:
            return False
    
    def _search_tweets(self, port: int, tab_id: str, query: str, limit: int) -> List[Dict]:
        """搜索推文"""
        encoded_query = quote(query)
        url = TW_SEARCH_URL.format(query=encoded_query)
        
        run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", str(tab_id),
            "--goto", url,
            "--wait-for", "networkidle",
            "--timeout", str(wait_timeout),
        ])
        
        # 滚动加载更多推文
        self._scroll_to_load(port, tab_id, limit)
        
        # 提取推文信息
        js_code = '''
        (function() {
            var results = [];
            var tweetElements = document.querySelectorAll('article[data-testid="tweet"]');
            
            tweetElements.forEach(function(article, index) {
                if (index >= ''' + str(limit) + ''') return;
                
                // 获取推文内容
                var textEl = article.querySelector('div[data-testid="tweetText"]');
                if (!textEl) return;
                
                var text = textEl.textContent.trim();
                if (!text || text.length < 5) return; // 过滤太短的内容
                
                // 获取用户信息
                var userEl = article.querySelector('a[href^="/"][data-testid="User-Name"]');
                var usernameEl = article.querySelector('a[href^="/"][data-testid="User-Name"] span');
                var username = userEl ? userEl.getAttribute('href').replace('/', '') : '';
                var displayName = usernameEl ? usernameEl.textContent.trim() : '';
                
                // 获取时间
                var timeEl = article.querySelector('time');
                var timestamp = timeEl ? timeEl.getAttribute('datetime') : '';
                
                // 获取互动数据
                var stats = [];
                article.querySelectorAll('[data-testid="tweet"] [role="link"]').forEach(function(link) {
                    var text = link.textContent.trim();
                    if (text.match(/^\d+[KkMm]?(\s*(retweets|replies|likes))?$/i)) {
                        stats.push(text);
                    }
                });
                
                // 获取链接
                var linkEl = article.querySelector('a[href^="/status/"]');
                var tweetUrl = linkEl ? 'https://twitter.com' + linkEl.getAttribute('href') : '';
                
                results.push({
                    text: text,
                    username: username,
                    display_name: displayName,
                    timestamp: timestamp,
                    url: tweetUrl,
                    retweets: stats[0] || '',
                    replies: stats[1] || '',
                    likes: stats[2] || '',
                    source: 'twitter',
                    scraped_at: new Date().toISOString()
                });
            });
            
            return results;
        })()
        '''
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", str(tab_id),
            "--eval", js_code,
        ])
        
        try:
            data = json.loads(result.get("result", "[]"))
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            print(f"[警告] JSON 解析失败: {result.get('result')}")
            return []
    
    def _search_user(self, port: int, tab_id: str, username: str, limit: int) -> List[Dict]:
        """搜索用户推文"""
        url = TW_USER_URL.format(username=username)
        
        run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", str(tab_id),
            "--goto", url,
            "--wait-for", "networkidle",
            "--timeout", str(wait_timeout),
        ])
        
        self._scroll_to_load(port, tab_id, limit)
        
        # 提取用户推文
        js_code = '''
        (function() {
            var results = [];
            var tweetElements = document.querySelectorAll('article[data-testid="tweet"]');
            
            tweetElements.forEach(function(article, index) {
                if (index >= ''' + str(limit) + ''') return;
                
                var textEl = article.querySelector('div[data-testid="tweetText"]');
                if (!textEl) return;
                
                var text = textEl.textContent.trim();
                if (!text || text.length < 5) return;
                
                var timeEl = article.querySelector('time');
                var timestamp = timeEl ? timeEl.getAttribute('datetime') : '';
                
                var linkEl = article.querySelector('a[href^="/status/"]');
                var tweetUrl = linkEl ? 'https://twitter.com' + linkEl.getAttribute('href') : '';
                
                results.push({
                    text: text,
                    username: ''' + json.dumps(username) + ''',
                    timestamp: timestamp,
                    url: tweetUrl,
                    source: 'twitter',
                    scraped_at: new Date().toISOString()
                });
            });
            
            return results;
        })()
        '''
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", str(tab_id),
            "--eval", js_code,
        ])
        
        try:
            data = json.loads(result.get("result", "[]"))
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            print(f"[警告] JSON 解析失败: {result.get('result')}")
            return []
    
    def _search_hashtag(self, port: int, tab_id: str, hashtag: str, limit: int) -> List[Dict]:
        """搜索话题标签推文"""
        url = TW_HASHTAG_URL.format(hashtag=hashtag)
        
        run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", str(tab_id),
            "--goto", url,
            "--wait-for", "networkidle",
            "--timeout", str(wait_timeout),
        ])
        
        self._scroll_to_load(port, tab_id, limit)
        
        # 提取话题推文
        js_code = '''
        (function() {
            var results = [];
            var tweetElements = document.querySelectorAll('article[data-testid="tweet"]');
            
            tweetElements.forEach(function(article, index) {
                if (index >= ''' + str(limit) + ''') return;
                
                var textEl = article.querySelector('div[data-testid="tweetText"]');
                if (!textEl) return;
                
                var text = textEl.textContent.trim();
                if (!text || text.length < 5) return;
                
                var userEl = article.querySelector('a[href^="/"][data-testid="User-Name"]');
                var username = userEl ? userEl.getAttribute('href').replace('/', '') : '';
                
                var timeEl = article.querySelector('time');
                var timestamp = timeEl ? timeEl.getAttribute('datetime') : '';
                
                var linkEl = article.querySelector('a[href^="/status/"]');
                var tweetUrl = linkEl ? 'https://twitter.com' + linkEl.getAttribute('href') : '';
                
                results.push({
                    text: text,
                    username: username,
                    hashtag: ''' + json.dumps(hashtag) + ''',
                    timestamp: timestamp,
                    url: tweetUrl,
                    source: 'twitter',
                    scraped_at: new Date().toISOString()
                });
            });
            
            return results;
        })()
        '''
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", str(tab_id),
            "--eval", js_code,
        ])
        
        try:
            data = json.loads(result.get("result", "[]"))
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            print(f"[警告] JSON 解析失败: {result.get('result')}")
            return []
    
    def _scroll_to_load(self, port: int, tab_id: str, target_count: int):
        """滚动页面加载更多推文"""
        scroll_js = '''
        (function() {
            var scrollCount = 0;
            var maxScrolls = 20;
            
            function scrollDown() {
                window.scrollBy(0, 800);
                scrollCount++;
                
                if (scrollCount < maxScrolls) {
                    setTimeout(scrollDown, 1500);
                }
            }
            
            scrollDown();
            return "开始滚动加载";
        })()
        '''
        
        run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", str(tab_id),
            "--eval", scroll_js,
        ])
        
        # 等待加载
        time.sleep(3)
    
    def get_user_profile(self, username: str, port: int, tab_id: str) -> Dict:
        """获取用户资料"""
        url = f"https://twitter.com/{username}"
        
        run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", str(tab_id),
            "--goto", url,
            "--wait-for", "networkidle",
            "--timeout", str(wait_timeout),
        ])
        
        js_code = '''
        (function() {
            var profile = {};
            
            // 用户名
            var titleEl = document.querySelector('h1[data-testid="UserTitle"]');
            if (titleEl) {
                var displayName = titleEl.querySelector('span');
                profile.display_name = displayName ? displayName.textContent.trim() : '';
                profile.username = ''' + json.dumps(username) + ''';
            }
            
            // 简介
            var bioEl = document.querySelector('div[data-testid="UserDescription"]');
            profile.bio = bioEl ? bioEl.textContent.trim() : '';
            
            // 统计数据
            var stats = {};
            document.querySelectorAll('[data-testid="UserProfileHeader_Items"] span').forEach(function(el) {
                var text = el.textContent.trim();
                if (text.includes('Following')) {
                    stats.following = text.replace('Following', '').trim();
                } else if (text.includes('Followers')) {
                    stats.followers = text.replace('Followers', '').trim();
                }
            });
            profile.stats = stats;
            
            // 关注/粉丝数
            var followCountEl = document.querySelector('[data-testid="followingCount"]');
            var followerCountEl = document.querySelector('[data-testid="followerCount"]');
            profile.following = followCountEl ? followCountEl.textContent.trim() : '';
            profile.followers = followerCountEl ? followerCountEl.textContent.trim() : '';
            
            profile.source = 'twitter';
            profile.scraped_at = new Date().toISOString();
            
            return profile;
        })()
        '''
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", str(tab_id),
            "--eval", js_code,
        ])
        
        try:
            return json.loads(result.get("result", "{}"))
        except json.JSONDecodeError:
            return {}
    
    async def get_detail(self, url: str, config: Optional[SearcherConfig] = None) -> Dict:
        """获取推文详情"""
        raise NotImplementedError("请使用 search 方法")
    
    async def health_check(self) -> bool:
        """健康检查"""
        try:
            result = run_cmd([
                PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
                "--goto", TW_BASE,
                "--wait-for", "stable",
                "--timeout", 10,
            ])
            return "error" not in result
        except Exception:
            return False


def main():
    parser = argparse.ArgumentParser(description="Twitter/X 推文搜索器")
    parser.add_argument("query", help="搜索关键词、用户名或话题标签")
    parser.add_argument("--type", default="query", choices=["query", "user", "hashtag"])
    parser.add_argument("--max-results", type=int, default=20)
    parser.add_argument("--port", type=int, default=9333)
    parser.add_argument("--stealth", action="store_true", default=True)
    parser.add_argument("--no-stealth", dest="stealth", action="store_false")
    parser.add_argument("--output-dir", help="输出目录")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--session", default="twitter_session", help="浏览器会话名称")
    
    args = parser.parse_args()
    
    searcher = TwitterSearcher()
    results = searcher.search(
        query=args.query,
        search_type=args.type,
        max_results=args.max_results,
        port=args.port,
        stealth=args.stealth,
        output_dir=args.output_dir,
        wait_timeout=args.timeout,
        session_name=args.session,
    )
    
    if results:
        print("\n" + json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
