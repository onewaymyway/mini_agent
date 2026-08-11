#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
币安 加密货币搜索器
目标: www.binance.com
难度: 中
API: 是
"""

import requests
from typing import List, Dict, Any
from datetime import datetime
import json

class BinanceSearcher:
    def __init__(self):
        self.base_url = "https://www.binance.com"
        self.api_url = "https://api.binance.com/api/v3"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
    
    def get_ticker_price(self, symbol: str = 'BTCUSDT') -> Dict[str, Any]:
        """获取价格"""
        try:
            url = f"{self.api_url}/ticker/price?symbol={symbol}"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                return response.json()
                
        except Exception as e:
            print(f"币安价格错误: {e}")
        
        return {}
    
    def get_24hr_ticker(self) -> List[Dict[str, Any]]:
        """获取24小时行情"""
        try:
            url = f"{self.api_url}/ticker/24hr"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                # 按交易量排序，取前20
                sorted_data = sorted(data, key=lambda x: float(x.get('quoteVolume', 0)), reverse=True)[:20]
                return sorted_data
                
        except Exception as e:
            print(f"币安行情错误: {e}")
        
        return []
    
    def search_coin(self, query: str) -> List[Dict[str, Any]]:
        """搜索加密货币"""
        try:
            url = f"{self.base_url}/search?keyword={query}"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                import re
                coins = []
                coin_pattern = r'<a href="/(?P<symbol>[^"]+)"[^>]*>.*?<span class="symbol">(?P<name>[^<]+)</span>'
                matches = re.findall(coin_pattern, response.text, re.DOTALL)
                
                for match in matches[:15]:
                    coins.append({
                        'symbol': match[0],
                        'name': match[1]
                    })
                return coins
                
        except Exception as e:
            print(f"币安搜索错误: {e}")
        
        return []
    
    def run(self, query: str = None) -> Dict[str, Any]:
        """执行搜索任务"""
        result = {
            'source': '币安',
            'timestamp': datetime.now().isoformat(),
            'data': {}
        }
        
        if query:
            result['data']['search_results'] = self.search_coin(query)
        else:
            result['data']['ticker_24hr'] = self.get_24hr_ticker()
            result['data']['btc_price'] = self.get_ticker_price('BTCUSDT')
            result['data']['eth_price'] = self.get_ticker_price('ETHUSDT')
        
        return result

if __name__ == "__main__":
    searcher = BinanceSearcher()
    result = searcher.run()
    print(json.dumps(result, indent=2, ensure_ascii=False))
