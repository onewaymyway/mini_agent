#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2EX 技术社区搜索器
目标: www.v2ex.com
难度: 低
API: 是
"""

import requests
from typing import List, Dict, Any
from datetime import datetime
import json

class V2EXSearcher:
    def __init__(self):
        self.base_url = "https://www.v2ex.com"
        self.api_url = "https://www.v2ex.com/api"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
    
    def search_nodes(self, keyword: str) -> List[Dict[str, Any]]:
        """搜索节点"""
        try:
            url = f"{self.api_url}/nodes/search.json?keyword={keyword}"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                return response.json()[:20]
                
        except Exception as e:
            print(f"V2EX节点搜索错误: {e}")
        
        return []
    
    def search_topics(self, keyword: str) -> List[Dict[str, Any]]:
        """搜索话题"""
        try:
            url = f"{self.api_url}/topics/search.json?keyword={keyword}"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                return response.json()[:20]
                
        except Exception as e:
            print(f"V2EX话题搜索错误: {e}")
        
        return []
    
    def get_hot_topics(self, limit: int = 20) -> List[Dict[str, Any]]:
        """获取热门话题"""
        try:
            url = f"{self.api_url}/topics/hot.json?limit={limit}"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                return response.json()[:limit]
                
        except Exception as e:
            print(f"V2EX热门错误: {e}")
        
        return []
    
    def run(self, query: str = None) -> Dict[str, Any]:
        """执行搜索任务"""
        result = {
            'source': 'V2EX',
            'timestamp': datetime.now().isoformat(),
            'data': {}
        }
        
        if query:
            result['data']['topics'] = self.search_topics(query)
            result['data']['nodes'] = self.search_nodes(query)
        else:
            result['data']['hot_topics'] = self.get_hot_topics()
        
        return result

if __name__ == "__main__":
    searcher = V2EXSearcher()
    result = searcher.run()
    print(json.dumps(result, indent=2, ensure_ascii=False))
