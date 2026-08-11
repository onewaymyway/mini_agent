#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hacker News 技术新闻搜索器
目标: news.ycombinator.com
难度: 低
API: 是
"""

import requests
from typing import List, Dict, Any
from datetime import datetime
import json

class HackerNewsSearcher:
    def __init__(self):
        self.api_url = "https://hacker-news.firebaseio.com/v0"
        self.base_url = "https://news.ycombinator.com"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
    
    def get_top_stories(self, limit: int = 20) -> List[Dict[str, Any]]:
        """获取热门故事"""
        try:
            url = f"{self.api_url}/topstories.json"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                story_ids = response.json()[:limit]
                stories = []
                for story_id in story_ids:
                    story_url = f"{self.api_url}/item/{story_id}.json"
                    story_resp = requests.get(story_url, headers=self.headers, timeout=10)
                    if story_resp.status_code == 200:
                        story = story_resp.json()
                        stories.append({
                            'id': story_id,
                            'title': story.get('title', ''),
                            'url': story.get('url', ''),
                            'score': story.get('score', 0),
                            'comments': story.get('descendants', 0)
                        })
                return stories
                
        except Exception as e:
            print(f"Hacker News热门错误: {e}")
        
        return []
    
    def search_stories(self, query: str) -> List[Dict[str, Any]]:
        """搜索故事"""
        try:
            url = f"{self.api_url}/search.json?q={query}"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                if 'results' in data:
                    return data['results'][:20]
                return data[:20]
                
        except Exception as e:
            print(f"Hacker News搜索错误: {e}")
        
        return []
    
    def run(self, query: str = None) -> Dict[str, Any]:
        """执行搜索任务"""
        result = {
            'source': 'Hacker News',
            'timestamp': datetime.now().isoformat(),
            'data': {}
        }
        
        if query:
            result['data']['search_results'] = self.search_stories(query)
        else:
            result['data']['top_stories'] = self.get_top_stories()
        
        return result

if __name__ == "__main__":
    searcher = HackerNewsSearcher()
    result = searcher.run()
    print(json.dumps(result, indent=2, ensure_ascii=False))
