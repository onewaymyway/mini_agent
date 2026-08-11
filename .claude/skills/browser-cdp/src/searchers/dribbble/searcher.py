#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dribbble 设计作品搜索器
目标: dribbble.com
难度: 中
API: 是
"""

import requests
from typing import List, Dict, Any
from datetime import datetime
import json

class DribbbleSearcher:
    def __init__(self):
        self.base_url = "https://dribbble.com"
        self.api_url = "https://api.dribbble.com/v2"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
    
    def search_shots(self, query: str, per_page: int = 20) -> List[Dict[str, Any]]:
        """搜索设计作品"""
        try:
            url = f"{self.api_url}/shots/search?q={query}&per_page={per_page}"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                if 'data' in data:
                    return data['data'][:per_page]
                return data[:per_page]
                
        except Exception as e:
            print(f"Dribbble搜索错误: {e}")
        
        return []
    
    def get_popular_shots(self, per_page: int = 20) -> List[Dict[str, Any]]:
        """获取热门作品"""
        try:
            url = f"{self.api_url}/shots/popular?per_page={per_page}"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                if 'data' in data:
                    return data['data'][:per_page]
                return data[:per_page]
                
        except Exception as e:
            print(f"Dribbble热门错误: {e}")
        
        return []
    
    def run(self, query: str = None) -> Dict[str, Any]:
        """执行搜索任务"""
        result = {
            'source': 'Dribbble',
            'timestamp': datetime.now().isoformat(),
            'data': {}
        }
        
        if query:
            result['data']['search_results'] = self.search_shots(query)
        else:
            result['data']['popular_shots'] = self.get_popular_shots()
        
        return result

if __name__ == "__main__":
    searcher = DribbbleSearcher()
    result = searcher.run()
    print(json.dumps(result, indent=2, ensure_ascii=False))
