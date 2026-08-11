#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Behance 设计项目搜索器
目标: www.behance.net
难度: 低
API: 是
"""

import requests
from typing import List, Dict, Any
from datetime import datetime
import json

class BehanceSearcher:
    def __init__(self):
        self.base_url = "https://www.behance.net"
        self.api_url = "https://behance.net/v2"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
    
    def search_projects(self, query: str, per_page: int = 20) -> List[Dict[str, Any]]:
        """搜索设计项目"""
        try:
            url = f"{self.base_url}/search/projects?q={query}"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                import re
                projects = []
                project_pattern = r'<a href="/(?P<username>[^/]+)/(?P<project_id>\d+)"[^>]*>.*?<img src="(?P<image>[^"]+)"[^>]*>.*?<h3>(?P<title>[^<]+)</h3>'
                matches = re.findall(project_pattern, response.text, re.DOTALL)
                
                for match in matches[:per_page]:
                    projects.append({
                        'username': match[0],
                        'project_id': match[1],
                        'title': match[3],
                        'image': match[2]
                    })
                return projects
                
        except Exception as e:
            print(f"Behance搜索错误: {e}")
        
        return []
    
    def get_featured_projects(self, per_page: int = 20) -> List[Dict[str, Any]]:
        """获取精选项目"""
        try:
            url = f"{self.base_url}/featured"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                import re
                projects = []
                project_pattern = r'<a href="/(?P<username>[^/]+)/(?P<project_id>\d+)"[^>]*>.*?<img src="(?P<image>[^"]+)"[^>]*>.*?<h3>(?P<title>[^<]+)</h3>'
                matches = re.findall(project_pattern, response.text, re.DOTALL)
                
                for match in matches[:per_page]:
                    projects.append({
                        'username': match[0],
                        'project_id': match[1],
                        'title': match[3],
                        'image': match[2]
                    })
                return projects
                
        except Exception as e:
            print(f"Behance精选错误: {e}")
        
        return []
    
    def run(self, query: str = None) -> Dict[str, Any]:
        """执行搜索任务"""
        result = {
            'source': 'Behance',
            'timestamp': datetime.now().isoformat(),
            'data': {}
        }
        
        if query:
            result['data']['search_results'] = self.search_projects(query)
        else:
            result['data']['featured_projects'] = self.get_featured_projects()
        
        return result

if __name__ == "__main__":
    searcher = BehanceSearcher()
    result = searcher.run()
    print(json.dumps(result, indent=2, ensure_ascii=False))
