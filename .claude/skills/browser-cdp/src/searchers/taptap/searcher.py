#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TapTap 游戏社区搜索器
目标: www.taptap.cn
难度: 低
API: 否
"""

import requests
from typing import List, Dict, Any
from datetime import datetime
import json

class TapTapSearcher:
    def __init__(self):
        self.base_url = "https://www.taptap.cn"
        self.api_url = "https://api.taptap.cn"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15'
        }
    
    def get_hot_games(self, limit: int = 20) -> List[Dict[str, Any]]:
        """获取热门游戏榜单"""
        try:
            url = f"{self.base_url}/app/hot"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                import re
                games = []
                # 匹配游戏卡片
                game_pattern = r'<a href="/(?P<id>\d+)"[^>]*>.*?<img src="(?P<icon>[^"]+)"[^>]*>.*?<div class="name">(?P<name>[^<]+)</div>.*?<div class="score">(?P<score>[^<]+)</div>'
                matches = re.findall(game_pattern, response.text, re.DOTALL)
                
                for match in matches[:limit]:
                    games.append({
                        'id': match[0],
                        'name': match[2],
                        'icon': match[1],
                        'score': match[3]
                    })
                return games
                
        except Exception as e:
            print(f"TapTap热门游戏错误: {e}")
        
        return []
    
    def search_game(self, query: str) -> List[Dict[str, Any]]:
        """搜索游戏"""
        try:
            url = f"{self.base_url}/search?q={query}"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                import re
                games = []
                game_pattern = r'<a href="/(?P<id>\d+)"[^>]*>.*?<img src="(?P<icon>[^"]+)"[^>]*>.*?<div class="name">(?P<name>[^<]+)</div>'
                matches = re.findall(game_pattern, response.text, re.DOTALL)
                
                for match in matches[:15]:
                    games.append({
                        'id': match[0],
                        'name': match[2],
                        'icon': match[1]
                    })
                return games
                
        except Exception as e:
            print(f"TapTap搜索错误: {e}")
        
        return []
    
    def get_reviews(self, game_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """获取游戏评论"""
        try:
            url = f"{self.base_url}/app/{game_id}/review"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                import re
                reviews = []
                review_pattern = r'<div class="review-item">.*?<div class="review-content">(?P<content>[^<]+)</div>.*?<span class="review-score">(?P<score>[^<]+)</span>'
                matches = re.findall(review_pattern, response.text, re.DOTALL)
                
                for match in matches[:limit]:
                    reviews.append({
                        'content': match[0],
                        'score': match[1]
                    })
                return reviews
                
        except Exception as e:
            print(f"TapTap评论错误: {e}")
        
        return []
    
    def run(self, query: str = None) -> Dict[str, Any]:
        """执行搜索任务"""
        result = {
            'source': 'TapTap',
            'timestamp': datetime.now().isoformat(),
            'data': {}
        }
        
        if query:
            result['data']['search_results'] = self.search_game(query)
        else:
            result['data']['hot_games'] = self.get_hot_games()
        
        return result

if __name__ == "__main__":
    searcher = TapTapSearcher()
    result = searcher.run()
    print(json.dumps(result, indent=2, ensure_ascii=False))
