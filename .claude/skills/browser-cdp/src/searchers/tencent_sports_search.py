"""
腾讯体育 (sports.qq.com) 搜索器
支持：体育新闻、赛事直播、球队资讯搜索
"""

import sys
import os
import json
import time
from datetime import datetime
from typing import List, Dict, Any, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from src.searchers.base import BaseSearcher, SearchResult


class TencentSportsSearcher(BaseSearcher):
    """腾讯体育搜索器"""

    def __init__(self, **kwargs):
        super().__init__(
            name="tencent_sports",
            domain="sports.qq.com",
            description="腾讯体育 - 体育新闻、赛事直播、球队资讯",
            **kwargs
        )
        self.base_url = "https://sports.qq.com"
        self.search_url = "https://search.sports.qq.com/search"

    def search(self, query: str, max_results: int = 10, **kwargs) -> List[SearchResult]:
        """搜索体育新闻、赛事、球队"""
        results = []
        try:
            # 搜索体育新闻
            news_results = self._search_news(query, max_results)
            results.extend(news_results)

            # 搜索赛事
            match_results = self._search_matches(query, max_results)
            results.extend(match_results)

            # 搜索球队
            team_results = self._search_teams(query, max_results)
            results.extend(team_results)

        except Exception as e:
            self.logger.error(f"搜索失败: {e}")

        return self._deduplicate(results)[:max_results]

    def _search_news(self, query: str, max_results: int) -> List[SearchResult]:
        """搜索体育新闻"""
        results = []
        try:
            url = f"{self.base_url}/search?keyword={query}"
            self.nav.goto(url)
            time.sleep(2)

            items = self.nav.extract_elements('div.news-item, .news-list li, .article-item')
            for item in items[:max_results]:
                title_el = item.find('a')
                link_el = item.find('a', has_attribute='href')
                if title_el and link_el:
                    title = title_el.text.strip() if title_el.text else link_el.get('title', '')
                    link = link_el.get('href', '')
                    if link and not link.startswith('http'):
                        link = self.base_url + link
                    results.append(SearchResult(
                        title=title,
                        url=link,
                        snippet=f"体育新闻: {title}",
                        source="腾讯体育",
                        category="体育新闻"
                    ))
        except Exception as e:
            self.logger.warning(f"新闻搜索失败: {e}")
        return results

    def _search_matches(self, query: str, max_results: int) -> List[SearchResult]:
        """搜索赛事"""
        results = []
        try:
            url = f"{self.base_url}/match/{query}/"
            self.nav.goto(url)
            time.sleep(2)

            items = self.nav.extract_elements('div.match-item, .match-list li, .game-item')
            for item in items[:max_results]:
                title_el = item.find('a')
                link_el = item.find('a', has_attribute='href')
                if title_el and link_el:
                    title = title_el.text.strip() if title_el.text else link_el.get('title', '')
                    link = link_el.get('href', '')
                    if link and not link.startswith('http'):
                        link = self.base_url + link
                    results.append(SearchResult(
                        title=title,
                        url=link,
                        snippet=f"赛事: {title}",
                        source="腾讯体育",
                        category="赛事直播"
                    ))
        except Exception as e:
            self.logger.warning(f"赛事搜索失败: {e}")
        return results

    def _search_teams(self, query: str, max_results: int) -> List[SearchResult]:
        """搜索球队"""
        results = []
        try:
            url = f"{self.base_url}/team/{query}/"
            self.nav.goto(url)
            time.sleep(2)

            items = self.nav.extract_elements('div.team-item, .team-list li, .club-item')
            for item in items[:max_results]:
                title_el = item.find('a')
                link_el = item.find('a', has_attribute='href')
                if title_el and link_el:
                    title = title_el.text.strip() if title_el.text else link_el.get('title', '')
                    link = link_el.get('href', '')
                    if link and not link.startswith('http'):
                        link = self.base_url + link
                    results.append(SearchResult(
                        title=title,
                        url=link,
                        snippet=f"球队: {title}",
                        source="腾讯体育",
                        category="球队资讯"
                    ))
        except Exception as e:
            self.logger.warning(f"球队搜索失败: {e}")
        return results

    def get_match_info(self, match_id: str) -> Optional[Dict[str, Any]]:
        """获取赛事详情"""
        try:
            url = f"{self.base_url}/match/{match_id}"
            self.nav.goto(url)
            time.sleep(2)

            home_team = self.nav.extract_text('.home-team, .team-home')
            away_team = self.nav.extract_text('.away-team, .team-away')
            score = self.nav.extract_text('.score, .match-score')
            time_str = self.nav.extract_text('.match-time, .time')

            return {
                "home_team": home_team,
                "away_team": away_team,
                "score": score,
                "time": time_str,
                "url": url,
                "source": "腾讯体育"
            }
        except Exception as e:
            self.logger.error(f"获取赛事详情失败: {e}")
            return None

    def get_news_list(self, category: str = "all", page: int = 1) -> List[Dict[str, Any]]:
        """获取新闻列表"""
        results = []
        try:
            url = f"{self.base_url}/news/{category}/page{page}/"
            self.nav.goto(url)
            time.sleep(2)

            items = self.nav.extract_elements('div.news-item, .news-list li')
            for item in items:
                title_el = item.find('a')
                link_el = item.find('a', has_attribute='href')
                date_el = item.find('span.date, .date')

                if title_el and link_el:
                    title = title_el.text.strip() if title_el.text else link_el.get('title', '')
                    link = link_el.get('href', '')
                    if link and not link.startswith('http'):
                        link = self.base_url + link
                    date = date_el.text.strip() if date_el and date_el.text else ""

                    results.append({
                        "title": title,
                        "url": link,
                        "date": date,
                        "category": category
                    })
        except Exception as e:
            self.logger.error(f"获取新闻列表失败: {e}")
        return results


if __name__ == "__main__":
    searcher = TencentSportsSearcher(headless=True)
    results = searcher.search("NBA")
    print(json.dumps([r.to_dict() for r in results[:5]], ensure_ascii=False, indent=2))
