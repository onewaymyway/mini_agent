#!/usr/bin/env python
"""
sports_search.py - 体育平台搜索器

使用 browser-cdp skill 搜索体育平台，获取体育新闻、赛事数据、球队信息等。

用法:
    python sports_search.py "NBA" --type news
    python sports_search.py "足球" --type match --output-dir ./sports_results
    python sports_search.py "篮球" --type team --port 9333

示例:
    python sports_search.py "NBA" --type news
    python sports_search.py "足球" --type match --output-dir ./sports_results
"""

import argparse
import json
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
from src.searchers.utils import random_delay, save_results
from src.searchers.browser_utils import ensure_browser, run_cmd, PYTHON_CMD, SKILL_DIR


# ========== 体育平台专用配置 ==========
HUPU_BASE = "https://www.hupu.com"
DONGQIU_DI_BASE = "https://www.dongqiudi.com"
ZHIBO8_BASE = "https://www.zhibo8.cc"

# 默认输出目录
SPORTS_OUTPUT_DIR = SKILL_DIR.parent / "search_results" / "sports"


class SportsSearcher(BaseSearcher):
    """体育平台搜索器"""

    @property
    def source_name(self) -> str:
        return "sports_platform"

    @property
    def supported_types(self) -> List[str]:
        return ["sports_news", "match_data", "team_info", "player_info"]

    def search(
        self,
        query: str,
        search_type: str = "news",
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30,
        max_results: int = 20,
    ) -> List[Dict]:
        """搜索体育信息

        Args:
            query: 搜索关键词
            search_type: 搜索类型 (news/match/team/player)
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            output_dir: 输出目录
            wait_timeout: 等待超时时间
            max_results: 最大结果数

        Returns:
            搜索结果列表
        """
        print(f"[体育平台] 正在搜索: {query}")

        # 确保浏览器连接
        if tab_id is None:
            result = ensure_browser(port=port, stealth=stealth)
            if result.get("error"):
                print(f"[错误] 浏览器启动失败: {result['error']}")
                return []
            tab_id = result.get("tab_id")
            port = result.get("port", port)
            print(f"[浏览器] 端口: {port}, Tab: {tab_id}")

        # 随机延迟
        delay = random_delay(1.0, 2.0)
        print(f"  [延迟] 请求前等待 {delay:.1f} 秒")

        results = []

        # 根据搜索类型执行不同搜索
        if search_type == "news":
            print(f"  [搜索] 体育新闻...")
            news_results = self._search_news(query, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(news_results)

        elif search_type == "match":
            print(f"  [搜索] 赛事数据...")
            match_results = self._search_match(query, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(match_results)

        elif search_type == "team":
            print(f"  [搜索] 球队信息...")
            team_results = self._search_team(query, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(team_results)

        elif search_type == "player":
            print(f"  [搜索] 球员信息...")
            player_results = self._search_player(query, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(player_results)

        # 保存结果
        if results and output_dir:
            path = save_results(
                results,
                output_dir or str(SPORTS_OUTPUT_DIR),
                f"sports_{query}_{int(time.time())}.json"
            )
            print(f"  [保存] {path}")

        return results

    def _search_news(
        self,
        query: str,
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索体育新闻"""
        search_url = f"{HUPU_BASE}/search?keyword={quote(query)}"
        print(f"    [URL] 新闻搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".news-list, .result-list, .article-item, .post-item",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 新闻搜索导航失败")
            return []

        time.sleep(2.0)

        # 提取新闻信息
        js_news = r"""
(() => {
  const results = [];
  const selectors = [
    '.news-list .item',
    '.result-list .item',
    '.article-item',
    '.post-item',
    '.news-item'
  ];
  
  let items = [];
  for (const selector of selectors) {
    items = document.querySelectorAll(selector);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    if (i >= max_results) return;
    
    const titleEl = item.querySelector('.title, .headline, h3, h4, a');
    const linkEl = item.querySelector('a');
    const dateEl = item.querySelector('.date, .time, .publish-date');
    const sourceEl = item.querySelector('.source, .author');
    
    const title = titleEl ? titleEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const date = dateEl ? dateEl.innerText.trim() : '';
    const source = sourceEl ? sourceEl.innerText.trim() : '';
    
    if (title && title.length > 5) {
      results.push({
        title: title,
        url: url,
        publish_date: date,
        source: source,
        type: 'news',
        source_site: 'sports_platform',
      });
    }
  });
  
  return results;
})()
"""
        
        js_news = js_news.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_news,
        ])

        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass

        return []

    def _search_match(
        self,
        query: str,
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索赛事数据"""
        search_url = f"{DONGQIU_DI_BASE}/search?keyword={quote(query)}"
        print(f"    [URL] 赛事搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".match-list, .result-list, .match-item",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 赛事搜索导航失败")
            return []

        time.sleep(2.0)

        # 提取赛事信息
        js_match = r"""
(() => {
  const results = [];
  const selectors = [
    '.match-list .item',
    '.result-list .item',
    '.match-item'
  ];
  
  let items = [];
  for (const selector of selectors) {
    items = document.querySelectorAll(selector);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    if (i >= max_results) return;
    
    const homeEl = item.querySelector('.home-team, .home, .team-home');
    const awayEl = item.querySelector('.away-team, .away, .team-away');
    const scoreEl = item.querySelector('.score, .result');
    const timeEl = item.querySelector('.time, .date');
    const linkEl = item.querySelector('a');
    
    const home = homeEl ? homeEl.innerText.trim() : '';
    const away = awayEl ? awayEl.innerText.trim() : '';
    const score = scoreEl ? scoreEl.innerText.trim() : '';
    const time = timeEl ? timeEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    
    if (home && away) {
      results.push({
        home_team: home,
        away_team: away,
        score: score,
        match_time: time,
        url: url,
        type: 'match',
        source_site: 'sports_platform',
      });
    }
  });
  
  return results;
})()
"""
        
        js_match = js_match.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_match,
        ])

        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass

        return []

    def _search_team(
        self,
        query: str,
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索球队信息"""
        search_url = f"{HUPU_BASE}/team/search?keyword={quote(query)}"
        print(f"    [URL] 球队搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".team-list, .result-list, .team-item",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 球队搜索导航失败")
            return []

        time.sleep(2.0)

        # 提取球队信息
        js_team = r"""
(() => {
  const results = [];
  const selectors = [
    '.team-list .item',
    '.result-list .item',
    '.team-item'
  ];
  
  let items = [];
  for (const selector of selectors) {
    items = document.querySelectorAll(selector);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    if (i >= max_results) return;
    
    const nameEl = item.querySelector('.name, .team-name, h3, h4');
    const linkEl = item.querySelector('a');
    const leagueEl = item.querySelector('.league, .division');
    const countryEl = item.querySelector('.country, .nation');
    
    const name = nameEl ? nameEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const league = leagueEl ? leagueEl.innerText.trim() : '';
    const country = countryEl ? countryEl.innerText.trim() : '';
    
    if (name) {
      results.push({
        name: name,
        url: url,
        league: league,
        country: country,
        type: 'team',
        source_site: 'sports_platform',
      });
    }
  });
  
  return results;
})()
"""
        
        js_team = js_team.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_team,
        ])

        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass

        return []

    async def get_detail(self, url: str, config=None) -> Dict:
        """获取详情页内容（抽象方法实现）"""
        return {"url": url, "title": "", "content": ""}

    def _search_player(
        self,
        query: str,
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索球员信息"""
        search_url = f"{HUPU_BASE}/player/search?keyword={quote(query)}"
        print(f"    [URL] 球员搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".player-list, .result-list, .player-item",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 球员搜索导航失败")
            return []

        time.sleep(2.0)

        # 提取球员信息
        js_player = r"""
(() => {
  const results = [];
  const selectors = [
    '.player-list .item',
    '.result-list .item',
    '.player-item'
  ];
  
  let items = [];
  for (const selector of selectors) {
    items = document.querySelectorAll(selector);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    if (i >= max_results) return;
    
    const nameEl = item.querySelector('.name, .player-name, h3, h4');
    const linkEl = item.querySelector('a');
    const teamEl = item.querySelector('.team, .club');
    const positionEl = item.querySelector('.position, .role');
    
    const name = nameEl ? nameEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const team = teamEl ? teamEl.innerText.trim() : '';
    const position = positionEl ? positionEl.innerText.trim() : '';
    
    if (name) {
      results.push({
        name: name,
        url: url,
        team: team,
        position: position,
        type: 'player',
        source_site: 'sports_platform',
      });
    }
  });
  
  return results;
})()
"""
        
        js_player = js_player.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_player,
        ])

        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass

        return []


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="体育平台搜索器 - 获取体育新闻、赛事数据、球队球员信息",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python sports_search.py "NBA" --type news
    python sports_search.py "足球" --type match --output-dir ./sports_results
    python sports_search.py "篮球" --type team --port 9333
"""
    )

    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--type", type=str, default="news",
                        choices=["news", "match", "team", "player"],
                        help="搜索类型 (默认: news)")
    parser.add_argument("--output-dir", type=str, default=None, help="输出目录")
    parser.add_argument("--port", type=int, default=9333, help="浏览器调试端口 (默认: 9333)")
    parser.add_argument("--tab", type=str, default=None, help="Tab ID")
    parser.add_argument("--stealth", action="store_true", default=True, help="启用反检测模式")
    parser.add_argument("--no-stealth", action="store_false", dest="stealth", help="禁用反检测模式")
    parser.add_argument("--wait-timeout", type=int, default=30, help="等待超时时间 (默认: 30秒)")
    parser.add_argument("--max-results", type=int, default=20, help="最大结果数 (默认: 20)")

    args = parser.parse_args()

    # 创建搜索器
    searcher = SportsSearcher()

    # 执行搜索
    results = searcher.search(
        query=args.query,
        search_type=args.type,
        port=args.port,
        tab_id=args.tab,
        stealth=args.stealth,
        output_dir=args.output_dir,
        wait_timeout=args.wait_timeout,
        max_results=args.max_results,
    )

    if results:
        print(f"\n[结果] 共获取 {len(results)} 条体育信息")
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("[结果] 未获取到体育信息")


if __name__ == "__main__":
    main()
