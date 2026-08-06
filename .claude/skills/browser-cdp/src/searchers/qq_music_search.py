#!/usr/bin/env python
"""
qq_music_search.py - QQ音乐搜索器

使用 browser-cdp skill 搜索QQ音乐，获取歌曲、歌手、歌单等音乐数据。

用法:
    python qq_music_search.py "晴天"
    python qq_music_search.py "周杰伦" --type artist --output-dir ./qqmusic_results
"""

import argparse
import json
import random
import sys
import time
from pathlib import Path
from typing import List, Dict, Optional
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.searchers.base import SearcherConfig, SearchResult, BaseSearcher
from src.searchers.utils import random_delay, save_results
from src.searchers.browser_utils import ensure_browser, run_cmd, PYTHON_CMD, SKILL_DIR


# ========== QQ音乐专用配置 ==========
QQ_MUSIC_BASE = "https://y.qq.com"
QQ_MUSIC_SEARCH_URL = f"{QQ_MUSIC_BASE}/search/"
QQ_MUSIC_SONG_URL = f"{QQ_MUSIC_BASE}/song/"
QQ_MUSIC_ARTIST_URL = f"{QQ_MUSIC_BASE}/singer/"

# 默认输出目录
QQ_MUSIC_OUTPUT_DIR = SKILL_DIR.parent / "search_results" / "qq_music"


class QQMusicSearcher(BaseSearcher):
    """QQ音乐搜索器"""

    @property
    def source_name(self) -> str:
        return "qq_music"

    @property
    def supported_types(self) -> List[str]:
        return ["song_search", "artist_search", "playlist_search", "album_search"]

    def search(
        self,
        query: str,
        search_type: str = "all",
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30,
        max_results: int = 20,
    ) -> List[Dict]:
        """搜索QQ音乐内容"""
        print(f"[QQ音乐] 正在搜索: {query}")
        results = []
        browser = None
        try:
            browser = ensure_browser(port=port, stealth=stealth)
            if search_type in ["song", "all"]:
                results.extend(self._search_songs(browser, query, max_results, wait_timeout))
            if search_type in ["artist", "all"]:
                results.extend(self._search_artists(browser, query, max_results, wait_timeout))
            if search_type in ["playlist", "all"]:
                results.extend(self._search_playlists(browser, query, max_results, wait_timeout))
            seen_urls = set()
            unique_results = []
            for r in results:
                if r.get("url") and r["url"] not in seen_urls:
                    seen_urls.add(r["url"])
                    unique_results.append(r)
            results = unique_results[:max_results]
            output_path = output_dir or str(QQ_MUSIC_OUTPUT_DIR)
            save_results(results, output_path)
            print(f"[QQ音乐] 搜索完成，共获取 {len(results)} 条结果")
            return results
        except Exception as e:
            print(f"[QQ音乐] 搜索失败: {e}")
            return []
        finally:
            if browser:
                browser.close()

    def _search_songs(self, browser, query: str, max_results: int, wait_timeout: int) -> List[Dict]:
        results = []
        try:
            url = f"{QQ_MUSIC_SEARCH_URL}?searchid=0&inputtype=0&keyword={quote(query)}&t=song"
            browser.get(url, timeout=wait_timeout)
            time.sleep(random.uniform(2, 4))
            song_items = browser.query_selector_all(".song-item, .list-item, .song-list li")
            for item in song_items[:max_results]:
                try:
                    title_elem = item.query_selector(".songname, .title, h3, h4, a")
                    link_elem = item.query_selector("a[href]")
                    if title_elem and link_elem:
                        title = title_elem.text.strip()
                        link = link_elem.get_attribute("href")
                        if title and link:
                            results.append({
                                "title": title,
                                "url": link if link.startswith("http") else f"{QQ_MUSIC_BASE}{link}",
                                "source": self.source_name,
                                "type": "song",
                                "snippet": title[:100],
                                "timestamp": int(time.time()),
                            })
                except Exception:
                    continue
        except Exception as e:
            print(f"[QQ音乐歌曲搜索] 错误: {e}")
        return results

    def _search_artists(self, browser, query: str, max_results: int, wait_timeout: int) -> List[Dict]:
        results = []
        try:
            url = f"{QQ_MUSIC_SEARCH_URL}?searchid=0&inputtype=0&keyword={quote(query)}&t=singer"
            browser.get(url, timeout=wait_timeout)
            time.sleep(random.uniform(2, 4))
            artist_items = browser.query_selector_all(".singer-item, .artist-item, .list-item")
            for item in artist_items[:max_results]:
                try:
                    title_elem = item.query_selector(".singername, .name, h3, h4, a")
                    link_elem = item.query_selector("a[href]")
                    if title_elem and link_elem:
                        title = title_elem.text.strip()
                        link = link_elem.get_attribute("href")
                        if title and link:
                            results.append({
                                "title": title,
                                "url": link if link.startswith("http") else f"{QQ_MUSIC_BASE}{link}",
                                "source": self.source_name,
                                "type": "artist",
                                "snippet": title[:100],
                                "timestamp": int(time.time()),
                            })
                except Exception:
                    continue
        except Exception as e:
            print(f"[QQ音乐歌手搜索] 错误: {e}")
        return results

    def _search_playlists(self, browser, query: str, max_results: int, wait_timeout: int) -> List[Dict]:
        results = []
        try:
            url = f"{QQ_MUSIC_SEARCH_URL}?searchid=0&inputtype=0&keyword={quote(query)}&t=playlist"
            browser.get(url, timeout=wait_timeout)
            time.sleep(random.uniform(2, 4))
            playlist_items = browser.query_selector_all(".playlist-item, .list-item")
            for item in playlist_items[:max_results]:
                try:
                    title_elem = item.query_selector(".playlistname, .name, h3, h4, a")
                    link_elem = item.query_selector("a[href]")
                    if title_elem and link_elem:
                        title = title_elem.text.strip()
                        link = link_elem.get_attribute("href")
                        if title and link:
                            results.append({
                                "title": title,
                                "url": link if link.startswith("http") else f"{QQ_MUSIC_BASE}{link}",
                                "source": self.source_name,
                                "type": "playlist",
                                "snippet": title[:100],
                                "timestamp": int(time.time()),
                            })
                except Exception:
                    continue
        except Exception as e:
            print(f"[QQ音乐歌单搜索] 错误: {e}")
        return results

    def health_check(self, port: int = 9333) -> Dict:
        return {
            "source": self.source_name,
            "status": "healthy",
            "supported_types": self.supported_types,
            "base_url": QQ_MUSIC_BASE,
        }

    def get_detail(self, url: str, config: Optional[SearcherConfig] = None) -> Dict:
        """获取详情页内容"""
        return {"url": url, "title": "", "content": "", "source": self.source_name}

    def close(self):
        pass


def main():
    parser = argparse.ArgumentParser(description="QQ音乐搜索器")
    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--type", default="all", choices=["song", "artist", "playlist", "all"])
    parser.add_argument("--port", type=int, default=9333)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--max-results", type=int, default=20)
    parser.add_argument("--no-stealth", action="store_true")
    args = parser.parse_args()
    searcher = QQMusicSearcher()
    results = searcher.search(
        query=args.query,
        search_type=args.type,
        port=args.port,
        stealth=not args.no_stealth,
        output_dir=args.output_dir,
        max_results=args.max_results,
    )
    if results:
        print(f"\n找到 {len(results)} 条结果:")
        for i, r in enumerate(results[:10], 1):
            print(f"{i}. {r.get('title', 'N/A')}")
            print(f"   URL: {r.get('url', 'N/A')}")
            print()


if __name__ == "__main__":
    main()
