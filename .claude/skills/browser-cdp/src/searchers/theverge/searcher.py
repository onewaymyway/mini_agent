#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
The Verge 科技新闻搜索器
目标: www.theverge.com
难度: 低
API: 否
"""

import requests
from typing import List, Dict, Any
from datetime import datetime
import json

class TheVergeSearcher:
    def __init__(self):
        self.base_url = "https://www.theverge.com"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
    
    def get_latest_news(self, limit: int = 20) -> List[Dict[str, Any]]:
        """获取最新新闻"""
        try:
            url = f"{self.base_url}/tech/"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                import re
                news = []
                news_pattern = r'<a href="(?P<url>[^"]+)"[^>]*>.*?<h2[^>]*>(?P<title>[^<]+)</h2>.*?<time[^>]*>(?P<time>[^<]+)</time>'
                matches = re.findall(news_pattern, response.text, re.DOTALL)
                
                for match in matches[:limit]:
                    news.append({
                        'title': match[0],
                        'url': match[1],
                        'time': match[2]
                    })
                return news
                
        except Exception as e:
            print(f"The Verge新闻错误: {e}")
        
        return []
    
    def search_news(self, query: str) -> List[Dict[str, Any]]:
        """搜索新闻"""
        try:
            url = f"{self.base_url}/search/?q={query}"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                import re
                news = []
                news_pattern = r'<a href="(?P<url>[^"]+)"[^>]*>.*?<h2[^>]*>(?P<title>[^<]+)</h2>'
                matches = re.findall(news_pattern, response.text, re.DOTALL)
                
                for match in matches[:15]:
                    news.append({
                        'title': match[0],
                        'url': match[1]
                    })
                return news
                
        except Exception as e:
            print(f"The Verge搜索错误: {e}")
        
        return []
    
    def run(self, query: str = None) -> Dict[str, Any]:
        """执行搜索任务"""
        result = {
            'source': 'The Verge',
            'timestamp': datetime.now().isoformat(),
            'data': {}
        }
        
        if query:
            result['data']['search_results'] = self.search_news(query)
        else:
            result['data']['latest_news'] = self.get_latest_news()
        
        return result

if __name__ == "__main__":
    searcher = TheVergeSearcher()
    result = searcher.run()
    print(json.dumps(result, indent=2, ensure_ascii=False))
