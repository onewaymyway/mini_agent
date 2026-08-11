#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
区块链浏览器 链上数据搜索器
目标: blockchain.com
难度: 低
API: 是
"""

import requests
from typing import List, Dict, Any
from datetime import datetime
import json

class BlockchainSearcher:
    def __init__(self):
        self.base_url = "https://www.blockchain.com"
        self.api_url = "https://blockchain.info"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
    
    def get_btc_price(self) -> Dict[str, Any]:
        """获取BTC价格"""
        try:
            url = f"{self.api_url}/tobtc"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                return {'price_btc': response.json()}
                
        except Exception as e:
            print(f"区块链浏览器BTC价格错误: {e}")
        
        return {}
    
    def get_btc_usd_price(self) -> Dict[str, Any]:
        """获取BTC美元价格"""
        try:
            url = f"{self.api_url}/tobtc"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                return {'price_usd': response.json()}
                
        except Exception as e:
            print(f"区块链浏览器USD价格错误: {e}")
        
        return {}
    
    def get_network_stats(self) -> Dict[str, Any]:
        """获取网络统计"""
        try:
            url = f"{self.api_url}/stats"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                return response.json()
                
        except Exception as e:
            print(f"区块链浏览器统计错误: {e}")
        
        return {}
    
    def search_address(self, address: str) -> Dict[str, Any]:
        """搜索地址"""
        try:
            url = f"{self.api_url}/address/{address}"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                return response.json()
                
        except Exception as e:
            print(f"区块链浏览器地址搜索错误: {e}")
        
        return {}
    
    def run(self, query: str = None) -> Dict[str, Any]:
        """执行搜索任务"""
        result = {
            'source': '区块链浏览器',
            'timestamp': datetime.now().isoformat(),
            'data': {}
        }
        
        if query:
            result['data']['address_info'] = self.search_address(query)
        else:
            result['data']['btc_price'] = self.get_btc_price()
            result['data']['btc_usd_price'] = self.get_btc_usd_price()
            result['data']['network_stats'] = self.get_network_stats()
        
        return result

if __name__ == "__main__":
    searcher = BlockchainSearcher()
    result = searcher.run()
    print(json.dumps(result, indent=2, ensure_ascii=False))
