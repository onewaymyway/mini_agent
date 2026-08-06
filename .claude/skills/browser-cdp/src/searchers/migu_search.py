#!/usr/bin/env python
"""
migu_search.py - 咪咕音乐搜索器

使用 browser-cdp skill 搜索咪咕音乐，获取音乐信息、歌单、歌手等。

用法:
    python migu_search.py "周杰伦"
    python migu_search.py "流行" --type playlist --output-dir ./migu_results
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
from src.searchers.baidu_search import ensure_browser, run_cmd, PYTHON_CMD, SKILL_DIR


# ========== 咪咕音乐专用配置 ==========
MIGU_BASE = "https://music.migu.cn"
MIGU_SEARCH_URL = f"{MIGU_BASE}/v3/music/search"
MIGU_SONG_URL = f"{MIGU_BASE}/v3/music/song"
MIGU_ARTIST_URL = f"{MIGU_BASE}/v3/music/artist"

# 默认输出目录
MIGU_OUTPUT_DIR = SKILL_DIR.parent / "search_results" / "migu"


class MiguSearcher(BaseSearcher):
    """咪咕音乐搜索器"""

    @property
    def source_name(self) -> str:
        return "migu"

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
        """搜索咪咕音乐内容"""
        print(f"[咪咕音乐] 正在搜索: {query}")
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
            output_path = output_dir or str(MIGU_OUTPUT_DIR)
            save_results(results, output_path, source=self.source_name)
            print(f"[咪咕音乐] 搜索完成，共获取 {len(results)} 条结果")
            return results
        except Exception as e:
            print(f"[咪咕音乐] 搜索失败: {e}")
            return []
        finally:
            if browser:
                browser.close()

    def _search_songs(self, browser, query: str, max_results: int, wait_timeout: int) -> List[Dict]:
        results = []
        try:
            url = f"{MIGU_SEARCH_URL}?keyword={quote(query)}&type=song"
            browser.get(url, timeout=wait_timeout)
            time.sleep(random.uniform(2, 4))
            song_items = browser.query_selector_all(".song-item, .music-item, [class*='song']")
            for item in song_items[:max_results]:
                try:
                    title_elem = item.query_selector(".title, .song-name, h3, h4, a")
                    link_elem = item.query_selector("a[href]")
                    if title_elem and link_elem:
                        title = title_elem.text.strip()
                        link = link_elem.get_attribute("href")
                        if title and link:
                            results.append({
                                "title": title,
                                "url": link if link.startswith("http") else f"{MIGU_BASE}{link}",
                                "source": self.source_name,
                                "type": "song",
                                "snippet": title[:100],
                                "timestamp": int(time.time()),
                            })
                except Exception:
                    continue
        except Exception as e:
            print(f"[咪咕歌曲搜索] 错误: {e}")
        return results

    def _search_artists(self, browser, query: str, max_results: int, wait_timeout: int) -> List[Dict]:
        results = []
        try:
            url = f"{MIGU_SEARCH_URL}?keyword={quote(query)}&type=artist"
            browser.get(url, timeout=wait_timeout)
            time.sleep(random.uniform(2, 4))
            artist_items = browser.query_selector_all(".artist-item, .singer-item")
            for item in artist_items[:max_results]:
                try:
                    title_elem = item.query_selector(".name, .artist-name, h4")
                    link_elem = item.query_selector("a[href]")
                    if title_elem and link_elem:
                        title = title_elem.text.strip()
                        link = link_elem.get_attribute("href")
                        if title and link:
                            results.append({
                                "title": title,
                                "url": link if link.startswith("http") else f"{MIGU_BASE}{link}",
                                "source": self.source_name,
                                "type": "artist",
                                "snippet": title[:100],
                                "timestamp": int(time.time()),
                            })
                except Exception:
                    continue
        except Exception as e:
            print(f"[咪咕歌手搜索] 错误: {e}")
        return results

    def _search_playlists(self, browser, query: str, max_results: int, wait_timeout: int) -> List[Dict]:
        results = []
        try:
            url = f"{MIGU_SEARCH_URL}?keyword={quote(query)}&type=playlist"
            browser.get(url, timeout=wait_timeout)
            time.sleep(random.uniform(2, 4))
            playlist_items = browser.query_selector_all(".playlist-item, .list-item")
            for item in playlist_items[:max_results]:
                try:
                    title_elem = item.query_selector(".title, .playlist-name, h4")
                    link_elem = item.query_selector("a[href]")
                    if title_elem and link_elem:
                        title = title_elem.text.strip()
                        link = link_elem.get_attribute("href")
                        if title and link:
                            results.append({
                                "title": title,
                                "url": link if link.startswith("http") else f"{MIGU_BASE}{link}",
                                "source": self.source_name,
                                "type": "playlist",
                                "snippet": title[:100],
                                "timestamp": int(time.time()),
                            })
                except Exception:
                    continue
        except Exception as e:
            print(f"[咪咕歌单搜索] 错误: {e}")
        return results

    def get_detail(self, url: str, config: Optional[SearcherConfig] = None) -> Dict:
        """获取详情页内容"""
        return {"url": url, "title": "", "content": "", "source": self.source_name}

    def health_check(self, port: int = 9333) -> Dict:
        return {
            "source": self.source_name,
            "status": "healthy",
            "supported_types": self.supported_types,
            "base_url": MIGU_BASE,
        }

    def close(self):
        pass


def main():
    parser = argparse.ArgumentParser(description="咪咕音乐搜索器")
    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--type", default="all", choices=["song", "artist", "playlist", "all"])
    parser.add_argument("--port", type=int, default=9333)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--max-results", type=int, default=20)
    parser.add_argument("--no-stealth", action="store_true")
    args = parser.parse_args()
    searcher = MiguSearcher()
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
