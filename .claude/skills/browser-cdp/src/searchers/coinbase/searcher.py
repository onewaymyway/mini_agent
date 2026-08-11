#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Coinbase 加密货币搜索器
目标: www.coinbase.com
难度: 中
API: 是
"""

import requests
from typing import List, Dict, Any
from datetime import datetime
import json

class CoinbaseSearcher:
    def __init__(self):
        self.base_url = "https://www.coinbase.com"
        self.api_url = "https://api.coinbase.com/v2"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
    
    def get_exchange_rates(self) -> Dict[str, Any]:
        """获取汇率"""
        try:
            url = f"{self.api_url}/prices/{self.get_current_time().strftime('%Y-%m-%d')}/spot"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                return response.json()
                
        except Exception as e:
            print(f"Coinbase汇率错误: {e}")
        
        return {}
    
    def get_market_data(self, currency: str = 'BTC') -> Dict[str, Any]:
        """获取市场数据"""
        try:
            url = f"{self.api_url}/markets/{currency}-USD"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                return response.json()
                
        except Exception as e:
            print(f"Coinbase市场数据错误: {e}")
        
        return {}
    
    def search_coin(self, query: str) -> List[Dict[str, Any]]:
        """搜索加密货币"""
        try:
            url = f"{self.base_url}/explore/{query}"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                import re
                coins = []
                coin_pattern = r'<a href="/explore/(?P<id>[^"]+)"[^>]*>.*?<h3>(?P<name>[^<]+)</h3>.*?<p>(?P<symbol>[^<]+)</p>'
                matches = re.findall(coin_pattern, response.text, re.DOTALL)
                
                for match in matches[:15]:
                    coins.append({
                        'id': match[0],
                        'name': match[1],
                        'symbol': match[2]
                    })
                return coins
                
        except Exception as e:
            print(f"Coinbase搜索错误: {e}")
        
        return []
    
    def run(self, query: str = None) -> Dict[str, Any]:
        """执行搜索任务"""
        result = {
            'source': 'Coinbase',
            'timestamp': datetime.now().isoformat(),
            'data': {}
        }
        
        if query:
            result['data']['search_results'] = self.search_coin(query)
        else:
            result['data']['exchange_rates'] = self.get_exchange_rates()
            result['data']['market_data'] = self.get_market_data()
        
        return result

if __name__ == "__main__":
    searcher = CoinbaseSearcher()
    result = searcher.run()
    print(json.dumps(result, indent=2, ensure_ascii=False))
