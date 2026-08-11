#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MyAnimeList 动漫数据搜索器
目标: myanimelist.net
难度: 低
API: 是
"""

import requests
from typing import List, Dict, Any
from datetime import datetime
import json

class MyAnimeListSearcher:
    def __init__(self):
        self.base_url = "https://myanimelist.net"
        self.api_url = "https://api.myanimelist.net/v2"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
    
    def search_anime(self, query: str) -> List[Dict[str, Any]]:
        """搜索动漫"""
        try:
            url = f"{self.api_url}/anime?q={query}&limit=20"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                if 'data' in data:
                    return data['data'][:20]
                return data[:20]
                
        except Exception as e:
            print(f"MAL搜索错误: {e}")
        
        return []
    
    def get_top_anime(self, limit: int = 20) -> List[Dict[str, Any]]:
        """获取热门动漫"""
        try:
            url = f"{self.api_url}/anime/topanime?limit={limit}"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                if 'data' in data:
                    return data['data'][:limit]
                return data[:limit]
                
        except Exception as e:
            print(f"MAL热门错误: {e}")
        
        return []
    
    def get_anime_info(self, anime_id: int) -> Dict[str, Any]:
        """获取动漫详情"""
        try:
            url = f"{self.api_url}/anime/{anime_id}"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                return response.json()
                
        except Exception as e:
            print(f"MAL详情错误: {e}")
        
        return {}
    
    def run(self, query: str = None) -> Dict[str, Any]:
        """执行搜索任务"""
        result = {
            'source': 'MyAnimeList',
            'timestamp': datetime.now().isoformat(),
            'data': {}
        }
        
        if query:
            result['data']['search_results'] = self.search_anime(query)
        else:
            result['data']['top_anime'] = self.get_top_anime()
        
        return result

if __name__ == "__main__":
    searcher = MyAnimeListSearcher()
    result = searcher.run()
    print(json.dumps(result, indent=2, ensure_ascii=False))
