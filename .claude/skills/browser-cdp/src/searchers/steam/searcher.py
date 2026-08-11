#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Steam 游戏数据搜索器
目标: store.steampowered.com
难度: 中
API: 否
"""

import requests
from typing import List, Dict, Any
from datetime import datetime
import json
import time

class SteamSearcher:
    def __init__(self):
        self.base_url = "https://store.steampowered.com"
        self.api_url = "http://api.steampowered.com"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
    
    def get_top_sellers(self, days: int = 1) -> List[Dict[str, Any]]:
        """获取热销游戏榜单"""
        try:
            # 使用Steam API获取热销数据
            url = f"{self.api_url}/IStoreService/GetTopSellers/v0001/?key=&days={days}"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                if 'response' in data and 'items' in data['response']:
                    return data['response']['items'][:20]
            
            # 备用方案：爬取网页
            return self._scrape_top_sellers()
            
        except Exception as e:
            print(f"Steam API错误: {e}")
            return self._scrape_top_sellers()
    
    def _scrape_top_sellers(self) -> List[Dict[str, Any]]:
        """爬取热销游戏榜单"""
        try:
            url = f"{self.base_url}/top/sellers/"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                # 解析HTML获取游戏数据
                import re
                games = []
                # 简单的正则匹配游戏名称和价格
                game_pattern = r'<a class="search_result_row"[^>]*>.*?<div class="col search_collapse">.*?<div class="col search_desc">.*?<a href="(?P<url>[^"]+)"[^>]*>.*?<div class="title">(?P<name>[^<]+)</div>.*?<div class="search_price">.*?<span class="search_price_block">(?P<price>[^<]+)</span>'
                matches = re.findall(game_pattern, response.text, re.DOTALL)
                
                for match in matches[:20]:
                    games.append({
                        'name': match[1],
                        'url': match[0],
                        'price': match[2]
                    })
                return games
                
        except Exception as e:
            print(f"Steam爬取错误: {e}")
        
        return []
    
    def search_game(self, query: str) -> List[Dict[str, Any]]:
        """搜索游戏"""
        try:
            url = f"{self.base_url}/search/results/?q={query}"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                # 解析搜索结果
                import re
                games = []
                game_pattern = r'<a class="search_result_row"[^>]*>.*?<div class="col search_collapse">.*?<div class="col search_desc">.*?<a href="(?P<url>[^"]+)"[^>]*>.*?<div class="title">(?P<name>[^<]+)</div>'
                matches = re.findall(game_pattern, response.text, re.DOTALL)
                
                for match in matches[:15]:
                    games.append({
                        'name': match[1],
                        'url': match[0]
                    })
                return games
                
        except Exception as e:
            print(f"Steam搜索错误: {e}")
        
        return []
    
    def get_specials(self) -> List[Dict[str, Any]]:
        """获取特价游戏"""
        try:
            url = f"{self.base_url}/specials/"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                import re
                games = []
                game_pattern = r'<a class="search_result_row"[^>]*>.*?<div class="col search_collapse">.*?<div class="col search_desc">.*?<a href="(?P<url>[^"]+)"[^>]*>.*?<div class="title">(?P<name>[^<]+)</div>.*?<div class="search_price">.*?<span class="search_discount">.*?<div class="search_price_block">.*?<span class="search_original_price">(?P<original>[^<]+)</span>.*?<span class="search_final_price">(?P<final>[^<]+)</span>'
                matches = re.findall(game_pattern, response.text, re.DOTALL)
                
                for match in matches[:20]:
                    games.append({
                        'name': match[0],
                        'url': match[1],
                        'original_price': match[2],
                        'final_price': match[3]
                    })
                return games
                
        except Exception as e:
            print(f"Steam特价错误: {e}")
        
        return []
    
    def run(self, query: str = None) -> Dict[str, Any]:
        """执行搜索任务"""
        result = {
            'source': 'Steam',
            'timestamp': datetime.now().isoformat(),
            'data': {}
        }
        
        if query:
            result['data']['search_results'] = self.search_game(query)
        else:
            result['data']['top_sellers'] = self.get_top_sellers()
            result['data']['specials'] = self.get_specials()
        
        return result

if __name__ == "__main__":
    searcher = SteamSearcher()
    result = searcher.run()
    print(json.dumps(result, indent=2, ensure_ascii=False))
