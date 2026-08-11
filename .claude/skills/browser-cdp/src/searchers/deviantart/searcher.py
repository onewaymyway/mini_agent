#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeviantArt 艺术作品搜索器
目标: www.deviantart.com
难度: 中
API: 是
"""

import requests
from typing import List, Dict, Any
from datetime import datetime
import json

class DeviantArtSearcher:
    def __init__(self):
        self.base_url = "https://www.deviantart.com"
        self.api_url = "https://backend.deviantart.com/oembed"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
    
    def search_deviation(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """搜索艺术作品"""
        try:
            url = f"{self.base_url}/search/deviation?q={query}"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                import re
                artworks = []
                art_pattern = r'<a href="/(?P<username>[^/]+)/(?P<art_id>\d+)"[^>]*>.*?<img src="(?P<image>[^"]+)"[^>]*>.*?<span class="title">(?P<title>[^<]+)</span>'
                matches = re.findall(art_pattern, response.text, re.DOTALL)
                
                for match in matches[:limit]:
                    artworks.append({
                        'username': match[0],
                        'art_id': match[1],
                        'title': match[3],
                        'image': match[2]
                    })
                return artworks
                
        except Exception as e:
            print(f"DeviantArt搜索错误: {e}")
        
        return []
    
    def get_featured_art(self, limit: int = 20) -> List[Dict[str, Any]]:
        """获取精选作品"""
        try:
            url = f"{self.base_url}/featured"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                import re
                artworks = []
                art_pattern = r'<a href="/(?P<username>[^/]+)/(?P<art_id>\d+)"[^>]*>.*?<img src="(?P<image>[^"]+)"[^>]*>.*?<span class="title">(?P<title>[^<]+)</span>'
                matches = re.findall(art_pattern, response.text, re.DOTALL)
                
                for match in matches[:limit]:
                    artworks.append({
                        'username': match[0],
                        'art_id': match[1],
                        'title': match[3],
                        'image': match[2]
                    })
                return artworks
                
        except Exception as e:
            print(f"DeviantArt精选错误: {e}")
        
        return []
    
    def run(self, query: str = None) -> Dict[str, Any]:
        """执行搜索任务"""
        result = {
            'source': 'DeviantArt',
            'timestamp': datetime.now().isoformat(),
            'data': {}
        }
        
        if query:
            result['data']['search_results'] = self.search_deviation(query)
        else:
            result['data']['featured_art'] = self.get_featured_art()
        
        return result

if __name__ == "__main__":
    searcher = DeviantArtSearcher()
    result = searcher.run()
    print(json.dumps(result, indent=2, ensure_ascii=False))
