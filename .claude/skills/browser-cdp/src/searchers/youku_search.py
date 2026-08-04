#!/usr/bin/env python
"""
youku_search.py - 优酷视频搜索器

支持：
- 视频搜索（标题、UP主、标签）
- 视频详情抓取（播放量、弹幕数、时长）
- 剧集/综艺搜索
- 输出 JSON 格式结果

用法:
    python youku_search.py "庆余年" --max-results 10
    python youku_search.py "甄嬛传" --type drama --output-dir ./youku_results
    python youku_search.py "Python教程" --port 9333
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Dict, Optional
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.searchers.base import BaseSearcher, SearcherConfig, SearchResult, SearchResults
from src.searchers.utils import random_delay, save_results, dedup_results


SKILL_DIR = Path(__file__).parent
PYTHON_CMD = sys.executable

# ========== 优酷专用配置 ==========
YOUKU_BASE = "https://so.youku.com"
YOUKU_VIDEO_BASE = "https://v.youku.com"
YOUKU_OUTPUT_DIR = SKILL_DIR.parent / "search_results" / "youku"

# 搜索类型映射
SEARCH_TYPES = {
    "video": "video",
    "drama": "drama",      # 电视剧
    "variety": "variety",   # 综艺
    "animation": "animation",  # 动漫
}


class YoukuConfig(SearcherConfig):
    """优酷搜索器专用配置"""
    search_type: str = "video"  # video/drama/variety/animation
    fetch_details: bool = True
    max_scroll_pages: int = 3

    def __post_init__(self):
        if self.session_name is None:
            self.session_name = "youku_session"


class YoukuSearcher(BaseSearcher):
    """
    优酷视频搜索器

    特性：
    - 视频搜索（支持多种类型筛选）
    - 视频详情抓取（播放量、弹幕数、时长、标签）
    - 剧集/综艺搜索
    - 反检测模式
    """

    @property
    def source_name(self) -> str:
        return "youku"

    @property
    def supported_types(self) -> List[str]:
        return ["video_search", "video_detail", "drama_search", "variety_search"]

    def __init__(self, config: YoukuConfig = None):
        super().__init__(config or YoukuConfig())
        self._port = None
        self._tab_id = None

    # =========================================================================
    # 内部方法
    # =========================================================================

    def _build_search_url(self, query: str, search_type: str = "video", page: int = 1) -> str:
        """构建搜索 URL"""
        encoded = quote(query)
        if search_type == "video":
            return f"{YOUKU_BASE}/search_video/q_{encoded}"
        elif search_type == "drama":
            return f"{YOUKU_BASE}/search_video/q_{encoded}?searchType=drama"
        elif search_type == "variety":
            return f"{YOUKU_BASE}/search_video/q_{encoded}?searchType=variety"
        elif search_type == "animation":
            return f"{YOUKU_BASE}/search_video/q_{encoded}?searchType=animation"
        return f"{YOUKU_BASE}/search_video/q_{encoded}"

    def _ensure_browser(self, port: int = 9333, headless: bool = False) -> Dict:
        """确保浏览器连接"""
        if self._tab_id and self._port == port:
            return {"port": port, "tab_id": self._tab_id}

        print(f"[浏览器] 启动/连接优酷专用浏览器实例 (端口: {port})")
        cmd = [
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_launch.py"),
            "--dedicated",
            "--name", self.config.session_name,
            "--port", str(port),
            "--start-url", YOUKU_BASE,
        ]
        if headless:
            cmd.append("--headless")

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            return {"error": f"浏览器启动失败: {result.stderr[:200]}"}

        # 解析 tab_id
        tab_id = None
        for line in result.stdout.split('\n'):
            line = line.strip()
            if '首个 tab id=' in line:
                tab_id = line.split('首个 tab id=')[1].split('\r')[0].strip()
                break
            elif 'tab id=' in line and '首个' not in line:
                tab_id = line.split('tab id=')[1].split('\r')[0].strip()
                break

        # 备用：列出 tabs
        if not tab_id:
            list_result = subprocess.run([
                PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_launch.py"),
                "--port", str(port), "--list"
            ], capture_output=True, text=True, timeout=10)
            if list_result.returncode == 0:
                try:
                    tabs = json.loads(list_result.stdout.strip())
                    if tabs:
                        tab_id = tabs[0].get('id')
                except Exception:
                    pass

        if not tab_id:
            return {"error": "无法获取 tab_id"}

        self._port = port
        self._tab_id = tab_id
        return {"port": port, "tab_id": tab_id}

    def _navigate(self, url: str, wait_selector: str = ".search-result", timeout: int = 30) -> bool:
        """导航到指定 URL"""
        if self._tab_id is None:
            return False
        cmd = [
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(self._port),
            "--tab", self._tab_id,
            "--goto", url,
            "--timeout", str(timeout),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return result.returncode == 0

    def _eval_js(self, js_code: str) -> Optional[Dict]:
        """执行 JS 并返回结果"""
        if self._tab_id is None:
            return None
        cmd = [
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(self._port),
            "--tab", self._tab_id,
            "--eval", js_code,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return None
        try:
            stdout = result.stdout.strip()
            json_start = stdout.find('{')
            if json_start >= 0:
                return json.loads(stdout[json_start:])
        except Exception:
            pass
        return None

    def _scroll_page(self, pages: int = 3, delay: float = 1.0):
        """滚动页面加载更多结果"""
        if self._tab_id is None:
            return
        js_scroll = f"""
(() => {{
  let scrolled = 0;
  const interval = setInterval(() => {{
    window.scrollBy(0, 800);
    scrolled++;
    if (scrolled >= {pages}) {{
      clearInterval(interval);
    }}
  }}, {delay * 1000});
  return 'scrolled ' + scrolled + ' pages';
}})()
"""
        self._eval_js(js_scroll)
        time.sleep(pages * delay + 1)

    # =========================================================================
    # 公开方法
    # =========================================================================

    def search(
        self,
        query: str,
        search_type: str = None,
        max_results: int = None,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30,
        headless: bool = False,
        enable_scroll: bool = False,
    ) -> SearchResults:
        """
        搜索优酷视频

        Args:
            query: 搜索关键词
            search_type: 搜索类型 (video/drama/variety/animation)
            max_results: 最大结果数
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            output_dir: 输出目录
            wait_timeout: 等待超时时间
            headless: 是否无头模式
            enable_scroll: 是否启用滚动加载更多

        Returns:
            SearchResults: 搜索结果
        """
        self.config.query = query
        if search_type:
            self.config.search_type = search_type
        if max_results:
            self.config.max_results = max_results

        print(f"[优酷搜索] 正在搜索: {query}, 类型: {self.config.search_type}")

        results = SearchResults(
            source="youku",
            query=query,
        )
        results.metadata['search_type'] = self.config.search_type

        # 确保浏览器连接
        browser_info = self._ensure_browser(port=port, headless=headless)
        if browser_info.get("error"):
            results.error = browser_info["error"]
            print(f"[错误] {results.error}")
            return results

        self._port = browser_info["port"]
        self._tab_id = browser_info["tab_id"]
        print(f"[浏览器] 端口: {self._port}, Tab: {self._tab_id}")

        # 随机延迟
        delay = random_delay(1.0, 2.0)
        print(f"  [延迟] 请求前等待 {delay:.1f} 秒")

        # 构建搜索 URL
        search_url = self._build_search_url(query, self.config.search_type)
        print(f"  [URL] {search_url}")

        # 导航到搜索结果页
        if not self._navigate(search_url, wait_timeout=wait_timeout):
            results.error = "导航失败"
            print(f"[错误] 导航失败")
            return results

        time.sleep(2.0)

        # 滚动加载更多（可选）
        if enable_scroll:
            print("  [滚动] 正在加载更多结果...")
            self._scroll_page(pages=self.config.max_scroll_pages)

        # 提取搜索结果
        raw_results = self._extract_search_results()

        # 去重和限制数量
        unique_results = dedup_results(raw_results, by="url")[:self.config.max_results]

        for r in unique_results:
            result = SearchResult(
                source="youku",
                title=r.get('title', ''),
                url=r.get('url', ''),
                snippet=r.get('info', ''),
                author=r.get('author', ''),
                published_time=r.get('publish_time', ''),
                metadata={
                    'play_count': r.get('play_count', ''),
                    'duration': r.get('duration', ''),
                    'type': r.get('type', ''),
                    'score': r.get('score', ''),
                },
                scraped_at=time.strftime('%Y-%m-%d %H:%M:%S'),
            )
            results.results.append(result)

        # 抓取详情（可选）
        if self.config.fetch_details and len(results.results) < self.config.max_results:
            self._fetch_details(results)

        print(f"  [结果] 共提取 {len(results.results)} 条结果")

        # 保存结果
        if output_dir:
            path = save_results(
                [r.to_dict() for r in results.results],
                output_dir,
                f"youku_{self.config.search_type}_{query.replace(' ', '_')}.json",
            )
            print(f"  [保存] {path}")

        return results

    def _extract_search_results(self) -> List[Dict]:
        """从搜索结果页提取数据"""
        js_code = r"""
(() => {
  const items = [];
  // 优酷搜索结果容器
  const selectors = [
    '.module-item',
    '.search-result-item',
    '[class*="video-item"]',
    '[class*="result-item"]',
    '.module-box .module-item',
    '.search-result .module-item',
  ];
  
  // 尝试多种选择器
  let elements = [];
  for (const sel of selectors) {
    const found = document.querySelectorAll(sel);
    if (found.length > 0) {
      elements = Array.from(found);
      break;
    }
  }
  
  // 兜底：查找所有包含视频链接的卡片
  if (elements.length === 0) {
    const allLinks = document.querySelectorAll('a[href*="youku.com/v_show"]');
    const seen = new Set();
    allLinks.forEach(a => {
      const parent = a.closest('.module-item, .item, [class*="item"]') || a.parentElement;
      if (parent && !seen.has(parent)) {
        seen.add(parent);
        elements.push(parent);
      }
    });
  }
  
  elements.forEach((el, i) => {
    if (i >= 50) return;
    
    // 标题
    const titleEl = el.querySelector('.title, .video-title, h3, h4, .item-title, a.title');
    const title = titleEl ? titleEl.textContent.trim() : '';
    
    // 链接
    const linkEl = el.querySelector('a[href*="youku.com"]');
    let url = linkEl ? linkEl.href : '';
    
    // 作者/UP主
    const authorEl = el.querySelector('.author, .up-name, .nick-name, .user-name, .item-author');
    const author = authorEl ? authorEl.textContent.trim() : '';
    
    // 播放量
    const playEl = el.querySelector('.play, .num, .video-num, [class*="play"]');
    const playCount = playEl ? playEl.textContent.trim() : '';
    
    // 时长
    const durationEl = el.querySelector('.duration, .time, [class*="duration"]');
    const duration = durationEl ? durationEl.textContent.trim() : '';
    
    // 简介/摘要
    const infoEl = el.querySelector('.desc, .intro, .summary, .video-desc');
    const info = infoEl ? infoEl.textContent.trim() : '';
    
    // 发布时间
    const timeEl = el.querySelector('.time, .date, .pub-time');
    const publishTime = timeEl ? timeEl.textContent.trim() : '';
    
    if (title && url) {
      items.push({
        title: title,
        url: url,
        author: author,
        play_count: playCount,
        duration: duration,
        info: info,
        publish_time: publishTime,
        type: 'video',
      });
    }
  });
  
  return items;
})()
"""
        result = self._eval_js(js_code)
        if result and 'result' in result:
            return result['result'] if isinstance(result['result'], list) else []
        return []

    def _fetch_details(self, results: SearchResults):
        """批量抓取视频详情"""
        for i, result in enumerate(results.results[:self.config.max_results]):
            if result.url:
                detail = self.get_detail(result.url)
                if detail:
                    result.metadata.update(detail)
            # 随机延迟
            time.sleep(random.uniform(1.0, 2.0))

    def get_detail(self, url: str, port: int = 9333, tab_id: Optional[str] = None, headless: bool = False) -> Dict:
        """
        获取视频详情

        Args:
            url: 视频 URL
            port: 浏览器调试端口
            tab_id: Tab ID
            headless: 是否无头模式

        Returns:
            Dict: 视频详情
        """
        print(f"[优酷详情] 正在获取: {url}")

        # 确保浏览器连接
        browser_info = self._ensure_browser(port=port, headless=headless)
        if browser_info.get("error"):
            print(f"[错误] {browser_info['error']}")
            return {}

        self._port = browser_info["port"]
        self._tab_id = browser_info["tab_id"]

        # 导航到详情页
        if not self._navigate(url, wait_selector=".video-content", timeout=30):
            print("[错误] 导航失败")
            return {}

        time.sleep(2.0)

        # 提取详情信息
        js_code = r"""
(() => {
  const result = {};
  
  // 标题
  const titleEl = document.querySelector('.video-title, h1, .title, [class*="title"]');
  result.title = titleEl ? titleEl.textContent.trim() : '';
  
  // 简介
  const descEl = document.querySelector('.desc, .intro, .summary, [class*="desc"]');
  result.description = descEl ? descEl.textContent.trim() : '';
  
  // 播放量
  const playEl = document.querySelector('.play-count, .num, [class*="play"]');
  result.play_count = playEl ? playEl.textContent.trim() : '';
  
  // 弹幕数
  const danmuEl = document.querySelector('.danmu, .comment-count, [class*="danmu"]');
  result.danmu_count = danmuEl ? danmuEl.textContent.trim() : '';
  
  // 时长
  const durationEl = document.querySelector('.duration, .time, [class*="duration"]');
  result.duration = durationEl ? durationEl.textContent.trim() : '';
  
  // 作者/UP主
  const authorEl = document.querySelector('.author, .up-name, .nick-name, [class*="author"]');
  result.author = authorEl ? authorEl.textContent.trim() : '';
  
  // 发布时间
  const timeEl = document.querySelector('.publish-time, .date, [class*="time"]');
  result.publish_time = timeEl ? timeEl.textContent.trim() : '';
  
  // 标签
  const tags = [];
  document.querySelectorAll('.tag, .tags a, [class*="tag"]').forEach(tag => {
    const t = tag.textContent.trim();
    if (t) tags.push(t);
  });
  result.tags = tags;
  
  // 评分
  const scoreEl = document.querySelector('.score, .rating, [class*="score"]');
  result.score = scoreEl ? scoreEl.textContent.trim() : '';
  
  return result;
})()
"""
        result = self._eval_js(js_code)
        if result and 'result' in result:
            detail = result['result']
            detail['source'] = 'youku'
            detail['url'] = url
            detail['scraped_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
            return detail
        return {}

    def close(self):
        """关闭浏览器"""
        pass  # 浏览器实例由 session 管理，不在此处关闭


# =========================================================================
# 命令行入口
# =========================================================================

def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="优酷视频搜索器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python youku_search.py "庆余年" --max-results 10
    python youku_search.py "甄嬛传" --type drama --output-dir ./youku_results
    python youku_search.py "Python教程" --port 9333 --headless
"""
    )

    parser.add_argument("query", help="搜索关键词")
    parser.add_argument(
        "--type", "-t",
        type=str,
        default="video",
        choices=list(SEARCH_TYPES.keys()),
        help="搜索类型 (默认: video)",
    )
    parser.add_argument("--max-results", "-m", type=int, default=20, help="最大结果数 (默认: 20)")
    parser.add_argument("--output-dir", "-o", type=str, default=None, help="输出目录")
    parser.add_argument("--port", type=int, default=9333, help="浏览器调试端口 (默认: 9333)")
    parser.add_argument("--tab", type=str, default=None, help="Tab ID")
    parser.add_argument("--stealth", action="store_true", default=True, help="启用反检测模式")
    parser.add_argument("--no-stealth", action="store_false", dest="stealth", help="禁用反检测模式")
    parser.add_argument("--wait-timeout", type=int, default=30, help="等待超时时间 (默认: 30秒)")
    parser.add_argument("--headless", action="store_true", help="无头模式（服务器/纯抓取场景）")
    parser.add_argument("--no-details", action="store_true", help="不抓取视频详情")
    parser.add_argument("--scroll", action="store_true", help="启用滚动加载更多结果")

    args = parser.parse_args()

    # 创建搜索器
    config = YoukuConfig(
        query=args.query,
        search_type=args.type,
        max_results=args.max_results,
        fetch_details=not args.no_details,
        output_dir=args.output_dir,
    )
    searcher = YoukuSearcher(config)

    # 执行搜索
    results = searcher.search(
        query=args.query,
        search_type=args.type,
        max_results=args.max_results,
        port=args.port,
        tab_id=args.tab,
        stealth=args.stealth,
        output_dir=args.output_dir,
        wait_timeout=args.wait_timeout,
        headless=args.headless,
        enable_scroll=args.scroll,
    )

    # 输出结果
    print(f"\n[结果] 搜索关键词: {args.query}")
    print(f"[结果] 共找到 {len(results.results)} 条结果\n")
    print(results.to_json())


if __name__ == "__main__":
    main()