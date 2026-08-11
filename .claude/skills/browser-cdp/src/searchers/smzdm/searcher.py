#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
什么值得买 优惠信息搜索器
目标: www.smzdm.com
难度: 低
API: 否
"""

import requests
from typing import List, Dict, Any
from datetime import datetime
import json

class SMZDMSearcher:
    def __init__(self):
        self.base_url = "https://www.smzdm.com"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
    
    def get_hot_deals(self, category: str = 'hot', limit: int = 20) -> List[Dict[str, Any]]:
        """获取热门优惠"""
        try:
            url = f"{self.base_url}/{category}/"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                import re
                deals = []
                deal_pattern = r'<a href="(?P<url>[^"]+)"[^>]*>.*?<img src="(?P<image>[^"]+)"[^>]*>.*?<h3[^>]*>(?P<title>[^<]+)</h3>.*?<span class="price">(?P<price>[^<]+)</span>'
                matches = re.findall(deal_pattern, response.text, re.DOTALL)
                
                for match in matches[:limit]:
                    deals.append({
                        'title': match[2],
                        'url': match[0],
                        'price': match[3],
                        'image': match[1]
                    })
                return deals
                
        except Exception as e:
            print(f"什么值得买优惠错误: {e}")
        
        return []
    
    def search_deal(self, query: str) -> List[Dict[str, Any]]:
        """搜索优惠"""
        try:
            url = f"{self.base_url}/s/?q={query}"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                import re
                deals = []
                deal_pattern = r'<a href="(?P<url>[^"]+)"[^>]*>.*?<img src="(?P<image>[^"]+)"[^>]*>.*?<h3[^>]*>(?P<title>[^<]+)</h3>.*?<span class="price">(?P<price>[^<]+)</span>'
                matches = re.findall(deal_pattern, response.text, re.DOTALL)
                
                for match in matches[:15]:
                    deals.append({
                        'title': match[2],
                        'url': match[0],
                        'price': match[3],
                        'image': match[1]
                    })
                return deals
                
        except Exception as e:
            print(f"什么值得买搜索错误: {e}")
        
        return []
    
    def run(self, query: str = None) -> Dict[str, Any]:
        """执行搜索任务"""
        result = {
            'source': '什么值得买',
            'timestamp': datetime.now().isoformat(),
            'data': {}
        }
        
        if query:
            result['data']['search_results'] = self.search_deal(query)
        else:
            result['data']['hot_deals'] = self.get_hot_deals()
        
        return result

if __name__ == "__main__":
    searcher = SMZDMSearcher()
    result = searcher.run()
    print(json.dumps(result, indent=2, ensure_ascii=False))
