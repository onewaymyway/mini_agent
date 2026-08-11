#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unsplash 图片搜索器
目标: unsplash.com
难度: 低
API: 是
"""

import requests
from typing import List, Dict, Any
from datetime import datetime
import json

class UnsplashSearcher:
    def __init__(self):
        self.base_url = "https://unsplash.com"
        self.api_url = "https://api.unsplash.com"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
    
    def search_photos(self, query: str, per_page: int = 20) -> List[Dict[str, Any]]:
        """搜索图片"""
        try:
            url = f"{self.api_url}/search/photos?query={query}&per_page={per_page}"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                if 'results' in data:
                    return data['results'][:per_page]
                return data[:per_page]
                
        except Exception as e:
            print(f"Unsplash搜索错误: {e}")
        
        return []
    
    def get_random_photo(self) -> Dict[str, Any]:
        """获取随机图片"""
        try:
            url = f"{self.api_url}/photos/random"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                return response.json()
                
        except Exception as e:
            print(f"Unsplash随机错误: {e}")
        
        return {}
    
    def get_photo_info(self, photo_id: str) -> Dict[str, Any]:
        """获取图片详情"""
        try:
            url = f"{self.api_url}/photos/{photo_id}"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                return response.json()
                
        except Exception as e:
            print(f"Unsplash详情错误: {e}")
        
        return {}
    
    def run(self, query: str = None) -> Dict[str, Any]:
        """执行搜索任务"""
        result = {
            'source': 'Unsplash',
            'timestamp': datetime.now().isoformat(),
            'data': {}
        }
        
        if query:
            result['data']['photos'] = self.search_photos(query)
        else:
            result['data']['random_photo'] = self.get_random_photo()
        
        return result

if __name__ == "__main__":
    searcher = UnsplashSearcher()
    result = searcher.run()
    print(json.dumps(result, indent=2, ensure_ascii=False))
