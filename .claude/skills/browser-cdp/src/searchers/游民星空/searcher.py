#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
游民星空 游戏资讯搜索器
目标: www.gamer.com.cn
难度: 低
API: 否
"""

import requests
from typing import List, Dict, Any
from datetime import datetime
import json

class GamerSearcher:
    def __init__(self):
        self.base_url = "https://www.gamer.com.cn"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
    
    def get_news_list(self, category: str = 'news', limit: int = 20) -> List[Dict[str, Any]]:
        """获取新闻列表"""
        try:
            url = f"{self.base_url}/{category}/"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                import re
                news = []
                news_pattern = r'<a href="(?P<url>[^"]+)"[^>]*>.*?<h2[^>]*>(?P<title>[^<]+)</h2>.*?<span class="date">(?P<time>[^<]+)</span>'
                matches = re.findall(news_pattern, response.text, re.DOTALL)
                
                for match in matches[:limit]:
                    news.append({
                        'title': match[0],
                        'url': match[1],
                        'time': match[2]
                    })
                return news
                
        except Exception as e:
            print(f"游民星空新闻错误: {e}")
        
        return []
    
    def search_game(self, query: str) -> List[Dict[str, Any]]:
        """搜索游戏"""
        try:
            url = f"{self.base_url}/s/?q={query}"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                import re
                games = []
                game_pattern = r'<a href="(?P<url>[^"]+)"[^>]*>.*?<img src="(?P<icon>[^"]+)"[^>]*>.*?<span class="name">(?P<name>[^<]+)</span>'
                matches = re.findall(game_pattern, response.text, re.DOTALL)
                
                for match in matches[:15]:
                    games.append({
                        'name': match[2],
                        'url': match[0],
                        'icon': match[1]
                    })
                return games
                
        except Exception as e:
            print(f"游民星空搜索错误: {e}")
        
        return []
    
    def run(self, query: str = None) -> Dict[str, Any]:
        """执行搜索任务"""
        result = {
            'source': '游民星空',
            'timestamp': datetime.now().isoformat(),
            'data': {}
        }
        
        if query:
            result['data']['search_results'] = self.search_game(query)
        else:
            result['data']['news_list'] = self.get_news_list()
        
        return result

if __name__ == "__main__":
    searcher = GamerSearcher()
    result = searcher.run()
    print(json.dumps(result, indent=2, ensure_ascii=False))
