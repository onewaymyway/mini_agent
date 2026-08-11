#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bangumi 动漫数据搜索器
目标: bangumi.tv
难度: 低
API: 是
"""

import requests
from typing import List, Dict, Any
from datetime import datetime
import json

class BangumiSearcher:
    def __init__(self):
        self.base_url = "https://bangumi.tv"
        self.api_url = "https://api.bangumi.tv"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
    
    def search_subject(self, keyword: str) -> List[Dict[str, Any]]:
        """搜索动漫/书籍/音乐"""
        try:
            url = f"{self.api_url}/search/subject/{keyword}"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                if 'data' in data:
                    return data['data'][:20]
                return data[:20]
                
        except Exception as e:
            print(f"Bangumi搜索错误: {e}")
        
        return []
    
    def get_subject_info(self, subject_id: int) -> Dict[str, Any]:
        """获取条目详情"""
        try:
            url = f"{self.api_url}/subject/{subject_id}"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                return response.json()
                
        except Exception as e:
            print(f"Bangumi详情错误: {e}")
        
        return {}
    
    def get_week_ranking(self) -> List[Dict[str, Any]]:
        """获取周排行榜"""
        try:
            url = f"{self.api_url}/rank/weekly/all"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                if 'data' in data:
                    return data['data'][:20]
                return data[:20]
                
        except Exception as e:
            print(f"Bangumi排行错误: {e}")
        
        return []
    
    def run(self, query: str = None) -> Dict[str, Any]:
        """执行搜索任务"""
        result = {
            'source': 'Bangumi',
            'timestamp': datetime.now().isoformat(),
            'data': {}
        }
        
        if query:
            result['data']['search_results'] = self.search_subject(query)
        else:
            result['data']['ranking'] = self.get_week_ranking()
        
        return result

if __name__ == "__main__":
    searcher = BangumiSearcher()
    result = searcher.run()
    print(json.dumps(result, indent=2, ensure_ascii=False))
