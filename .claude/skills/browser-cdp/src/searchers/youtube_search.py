#!/usr/bin/env python
"""
youtube_search.py - YouTube 视频搜索器

使用 browser-cdp skill 搜索 YouTube 视频，支持关键词搜索、频道搜索、播放列表搜索。

用法:
    python youtube_search.py --query "Python tutorial" --max-results 10
    python youtube_search.py --channel "MrBeast" --max-results 20
    python youtube_search.py --playlist "PLxyz" --output-dir ./results
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


# ========== YouTube 专用配置 ==========
YT_BASE = "https://www.youtube.com"
YT_SEARCH_URL = "https://www.youtube.com/results?search_query={query}"
YT_CHANNEL_URL = "https://www.youtube.com/@{channel}/videos"
YT_API_SEARCH = "https://www.youtube.com/oembed?url={url}&format=json"


class YouTubeSearcher(BaseSearcher):
    """YouTube 视频搜索器"""
    
    def __init__(self, config: Optional[SearcherConfig] = None):
        super().__init__(config)
        self._search_type = "query"  # query/channel/playlist
        self._extra_param = ""
    
    @property
    def source_name(self) -> str:
        return "youtube"
    
    @property
    def supported_types(self) -> List[str]:
        return ["query", "channel", "playlist"]
    
    def search(
        self,
        query: str = "",
        search_type: str = "query",
        max_results: int = 10,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30,
        session_name: Optional[str] = "youtube_session",
    ) -> List[Dict]:
        """搜索 YouTube 视频
        
        Args:
            query: 搜索关键词或频道名
            search_type: 搜索类型（query/channel/playlist）
            max_results: 最大结果数
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            output_dir: 输出目录
            wait_timeout: 等待超时时间
            session_name: 浏览器会话名称
            
        Returns:
            视频数据列表
        """
        self._search_type = search_type
        self._extra_param = query
        
        print(f"[YouTube搜索] 类型: {search_type}, 关键词: {query}")
        
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
        
        # 根据类型执行搜索
        if search_type == "query":
            results = self._search_videos(port, tab_id, query, max_results)
        elif search_type == "channel":
            results = self._search_channel(port, tab_id, query, max_results)
        elif search_type == "playlist":
            results = self._search_playlist(port, tab_id, query, max_results)
        else:
            results = self._search_videos(port, tab_id, query, max_results)
        
        # 保存结果
        if output_dir:
            save_results(results, output_dir, f"youtube_{search_type}_{query[:20]}", "json")
            save_results(results, output_dir, f"youtube_{search_type}_{query[:20]}", "csv")
        
        print(f"[完成] 共抓取 {len(results)} 条视频")
        return results
    
    def _search_videos(self, port: int, tab_id: str, query: str, limit: int) -> List[Dict]:
        """搜索视频"""
        encoded_query = quote(query)
        url = YT_SEARCH_URL.format(query=encoded_query)
        
        # 导航到搜索结果页
        run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", str(tab_id),
            "--goto", url,
            "--wait-for", "networkidle",
            "--timeout", str(wait_timeout),
        ])
        
        # 提取视频信息
        js_code = '''
        (function() {
            var results = [];
            var videoItems = document.querySelectorAll('ytd-video-renderer, ytd-grid-video-renderer');
            
            videoItems.forEach(function(item, index) {
                if (index >= ''' + str(limit) + ''') return;
                
                var titleEl = item.querySelector('#video-title');
                var metaEl = item.querySelector('#metadata-line');
                var channelEl = item.querySelector('#channel-name a, #text a');
                var thumbEl = item.querySelector('img');
                
                if (!titleEl) return;
                
                var title = titleEl.textContent.trim();
                var url = titleEl.href || '';
                var meta = metaEl ? metaEl.textContent.trim() : '';
                var channel = channelEl ? channelEl.textContent.trim() : '';
                var thumbnail = thumbEl ? thumbEl.src : '';
                
                // 解析元数据
                var views = '';
                var duration = '';
                var parts = meta.split('•');
                if (parts.length >= 2) {
                    views = parts[parts.length - 2].trim();
                    duration = parts[parts.length - 1].trim();
                }
                
                results.push({
                    title: title,
                    url: url,
                    channel: channel,
                    views: views,
                    duration: duration,
                    thumbnail: thumbnail,
                    source: 'youtube',
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
    
    def _search_channel(self, port: int, tab_id: str, channel: str, limit: int) -> List[Dict]:
        """搜索频道视频"""
        url = YT_CHANNEL_URL.format(channel=channel)
        
        run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", str(tab_id),
            "--goto", url,
            "--wait-for", "networkidle",
            "--timeout", str(wait_timeout),
        ])
        
        # 提取频道视频
        js_code = '''
        (function() {
            var results = [];
            var videoItems = document.querySelectorAll('ytd-grid-video-renderer, ytd-rich-item-renderer');
            
            videoItems.forEach(function(item, index) {
                if (index >= ''' + str(limit) + ''') return;
                
                var titleEl = item.querySelector('#video-title');
                var metaEl = item.querySelector('#metadata-line');
                var thumbEl = item.querySelector('img');
                
                if (!titleEl) return;
                
                var title = titleEl.textContent.trim();
                var url = titleEl.href || '';
                var meta = metaEl ? metaEl.textContent.trim() : '';
                var thumbnail = thumbEl ? thumbEl.src : '';
                
                var views = '';
                var duration = '';
                var parts = meta.split('•');
                if (parts.length >= 2) {
                    views = parts[parts.length - 2].trim();
                    duration = parts[parts.length - 1].trim();
                }
                
                results.push({
                    title: title,
                    url: url,
                    channel: ''' + json.dumps(channel) + ''',
                    views: views,
                    duration: duration,
                    thumbnail: thumbnail,
                    source: 'youtube',
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
    
    def _search_playlist(self, port: int, tab_id: str, playlist_id: str, limit: int) -> List[Dict]:
        """搜索播放列表视频"""
        url = f"https://www.youtube.com/playlist?list={playlist_id}"
        
        run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", str(tab_id),
            "--goto", url,
            "--wait-for", "networkidle",
            "--timeout", str(wait_timeout),
        ])
        
        # 提取播放列表视频
        js_code = '''
        (function() {
            var results = [];
            var videoItems = document.querySelectorAll('ytd-playlist-video-renderer');
            
            videoItems.forEach(function(item, index) {
                if (index >= ''' + str(limit) + ''') return;
                
                var titleEl = item.querySelector('#video-title');
                var metaEl = item.querySelector('#index');
                
                if (!titleEl) return;
                
                var title = titleEl.textContent.trim();
                var url = titleEl.href || '';
                var index = metaEl ? metaEl.textContent.trim() : '';
                
                results.push({
                    title: title,
                    url: url,
                    index: index,
                    source: 'youtube',
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
    
    def get_video_info(self, video_url: str, port: int, tab_id: str) -> Dict:
        """获取单个视频详细信息"""
        run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", str(tab_id),
            "--goto", video_url,
            "--wait-for", "networkidle",
            "--timeout", str(wait_timeout),
        ])
        
        js_code = '''
        (function() {
            var info = {};
            
            // 标题
            var titleEl = document.querySelector('h1.title, ytd-video-primary-info-renderer #title');
            info.title = titleEl ? titleEl.textContent.trim() : '';
            
            // 频道
            var channelEl = document.querySelector('#channel-name a, #owner-name a');
            info.channel = channelEl ? channelEl.textContent.trim() : '';
            info.channel_url = channelEl ? channelEl.href : '';
            
            // 描述
            var descEl = document.querySelector('#description yt-formatted-string');
            info.description = descEl ? descEl.textContent.trim() : '';
            
            // 统计数据
            var statsEl = document.querySelector('#metadata-line span');
            if (statsEl) {
                var stats = statsEl.textContent;
                var parts = stats.split('•');
                info.views = parts[0] ? parts[0].trim() : '';
                info.upload_date = parts[1] ? parts[1].trim() : '';
            }
            
            // 点赞数
            var likeEl = document.querySelector('#like-button button span');
            info.likes = likeEl ? likeEl.textContent.trim() : '';
            
            // 订阅数
            var subEl = document.querySelector('#subscriber-count');
            info.subscribers = subEl ? subEl.textContent.trim() : '';
            
            // 标签
            var tags = [];
            document.querySelectorAll('meta[name="keywords"]').forEach(function(meta) {
                tags = meta.content.split(',');
            });
            info.tags = tags;
            
            info.source = 'youtube';
            info.scraped_at = new Date().toISOString();
            
            return info;
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
        """获取视频详情"""
        raise NotImplementedError("请使用 get_video_info 方法")
    
    async def health_check(self) -> bool:
        """健康检查"""
        try:
            result = run_cmd([
                PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
                "--goto", YT_BASE,
                "--wait-for", "stable",
                "--timeout", 10,
            ])
            return "error" not in result
        except Exception:
            return False


def main():
    parser = argparse.ArgumentParser(description="YouTube 视频搜索器")
    parser.add_argument("query", help="搜索关键词或频道名")
    parser.add_argument("--type", default="query", choices=["query", "channel", "playlist"])
    parser.add_argument("--max-results", type=int, default=10)
    parser.add_argument("--port", type=int, default=9333)
    parser.add_argument("--stealth", action="store_true", default=True)
    parser.add_argument("--no-stealth", dest="stealth", action="store_false")
    parser.add_argument("--output-dir", help="输出目录")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--session", default="youtube_session", help="浏览器会话名称")
    
    args = parser.parse_args()
    
    searcher = YouTubeSearcher()
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
