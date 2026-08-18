#!/usr/bin/env python
"""
wechat_search.py - 微信公众号文章搜索脚本（搜狗微信）

通过搜狗微信搜索获取公众号文章列表和详情。
搜狗微信是微信公众号内容的公开搜索引擎。

用法:
    python wechat_search.py "人工智能" --max-results 20
    python wechat_search.py "AI技术" --account-only --output-dir ./wechat_results
    python wechat_search.py "Python编程" --proxy http://user:pass@host:port

示例:
    python wechat_search.py "大模型" --max-results 10
    python wechat_search.py "区块链" --account-only --port 9333
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional
from urllib.parse import quote, unquote

try:
    from bs4 import BeautifulSoup
    import requests
except ImportError:
    print("[错误] 需要安装依赖: pip install beautifulsoup4 requests")
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.searchers.base import SearchResult, SearchResults
from src.searchers.utils import random_delay, save_results, clean_text
from src.searchers.browser_utils import ensure_browser, run_cmd, PYTHON_CMD, SKILL_DIR


class WechatSearcher:
    """微信公众号文章搜索器（搜狗微信）"""
    
    BASE_URL = "https://weixin.sogou.com"
    SEARCH_URL = f"{BASE_URL}/weixin?type=1&query={quote('{query}')}&ie=utf8"
    
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Cache-Control": "max-age=0",
    }
    
    def __init__(self, proxy: str = None):
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)
        if proxy:
            self.session.proxies = {
                "http": proxy,
                "https": proxy
            }
        self.timeout = 30
        self.rate_limit = 6  # 请求间隔秒数
    
    def search(
        self,
        query: str,
        max_results: int = 20,
        output_dir: str = None,
        account_only: bool = False,
        proxy: str = None,
        port: int = 9333
    ) -> SearchResults:
        """
        搜索微信公众号文章
        
        Args:
            query: 搜索关键词
            max_results: 最大结果数量
            output_dir: 输出目录
            account_only: 仅返回账号信息
            proxy: 代理地址
            port: CDP端口
        
        Returns:
            SearchResults对象
        """
        results = SearchResults(source="wechat_sogou", query=query)
        
        # 重新创建session以使用代理
        if proxy:
            self.session = requests.Session()
            self.session.headers.update(self.HEADERS)
            self.session.proxies = {"http": proxy, "https": proxy}
        
        try:
            # 第一步：搜索文章列表
            articles = self._search_articles(query, max_results)
            
            if not articles and account_only:
                # 尝试搜索账号
                accounts = self._search_accounts(query, max_results)
                results.results = [SearchResult.from_dict(a) for a in accounts]
            else:
                results.results = [SearchResult.from_dict(a) for a in articles]
            
            # 保存结果
            if output_dir and results.results:
                self._save_results(results, output_dir)
            
        except Exception as e:
            results.error = f"搜索失败: {str(e)}"
            print(f"[错误] {results.error}")
        
        return results
    
    def _search_articles(self, query: str, max_results: int) -> List[Dict]:
        """搜索文章列表"""
        articles = []
        url = self.SEARCH_URL.format(query=quote(query))
        
        try:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            
            # 处理编码
            if 'gbk' in response.headers.get('Content-Type', '').lower():
                response.encoding = 'gbk'
            elif response.apparent_encoding:
                response.encoding = response.apparent_encoding
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # 解析搜索结果
            items = soup.select('.news-box .txt-box')
            
            for item in items[:max_results]:
                article = self._parse_article(item)
                if article:
                    articles.append(article)
            
            print(f"[搜狗] 成功获取 {len(articles)} 篇文章")
            
        except Exception as e:
            print(f"[搜狗] 搜索失败: {e}")
        
        return articles
    
    def _parse_article(self, item) -> Optional[Dict]:
        """解析单个文章条目"""
        try:
            link = item.select_one('h3 a')
            if not link:
                return None
            
            title = clean_text(link.get_text())
            url = link.get('href', '')
            
            # 处理重定向链接
            if 'url=' in url:
                url = unquote(url.split('url=')[1].split('&')[0])
            
            # 提取账号信息
            account_tag = item.select_one('p[class*="s-p"]')
            account_name = ''
            account_url = ''
            if account_tag:
                account_link = account_tag.select_one('a')
                if account_link:
                    account_name = clean_text(account_link.get_text())
                    account_url = account_link.get('href', '')
            
            # 提取摘要
            desc_tag = item.select_one('.search-txt')
            description = clean_text(desc_tag.get_text()) if desc_tag else ''
            
            return {
                "title": title,
                "url": url,
                "account_name": account_name,
                "account_url": account_url,
                "description": description,
                "source": "wechat_sogou",
                "type": "wechat_article",
                "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            
        except Exception as e:
            print(f"[解析] 文章解析失败: {e}")
            return None
    
    def _search_accounts(self, query: str, max_results: int) -> List[Dict]:
        """搜索公众号账号"""
        accounts = []
        url = f"{self.BASE_URL}/weixin?type=2&query={quote(query)}"
        
        try:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            
            if 'gbk' in response.headers.get('Content-Type', '').lower():
                response.encoding = 'gbk'
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # 解析账号列表
            items = soup.select('.account-box')
            
            for item in items[:max_results]:
                account = self._parse_account(item)
                if account:
                    accounts.append(account)
            
            print(f"[搜狗] 成功获取 {len(accounts)} 个公众号")
            
        except Exception as e:
            print(f"[搜狗] 账号搜索失败: {e}")
        
        return accounts
    
    def _parse_account(self, item) -> Optional[Dict]:
        """解析单个账号条目"""
        try:
            link = item.select_one('h3 a')
            if not link:
                return None
            
            name = clean_text(link.get_text())
            url = link.get('href', '')
            
            # 提取认证信息
            verify_tag = item.select_one('.verify-wrap')
            verify = clean_text(verify_tag.get_text()) if verify_tag else ''
            
            # 提取简介
            desc_tag = item.select_one('.s-text')
            description = clean_text(desc_tag.get_text()) if desc_tag else ''
            
            return {
                "title": name,
                "url": url,
                "verify": verify,
                "description": description,
                "source": "wechat_sogou",
                "type": "wechat_account",
                "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            
        except Exception as e:
            return None
    
    def _save_results(self, results: SearchResults, output_dir: str):
        """保存搜索结果"""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_file = output_path / f"wechat_{timestamp}.json"
        md_file = output_path / f"wechat_{timestamp}.md"
        
        # 保存JSON
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump([r.to_dict() for r in results.results], f, ensure_ascii=False, indent=2)
        
        # 保存Markdown
        with open(md_file, 'w', encoding='utf-8') as f:
            f.write(f"# 微信搜搜结果 - {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
            f.write(f"共获取 {len(results.results)} 条结果\n\n")
            
            for i, result in enumerate(results.results, 1):
                data = result.to_dict()
                rtype = data.get('type', 'article')
                
                f.write(f"## {i}. {data.get('title', 'N/A')}\n\n")
                f.write(f"- **类型**: {rtype}\n")
                
                url = data.get('url', '')
                if url:
                    f.write(f"- **链接**: [{url}]({url})\n")
                
                if rtype == 'wechat_article':
                    account = data.get('account_name', '')
                    if account:
                        f.write(f"- **公众号**: {account}\n")
                    desc = data.get('description', '')
                    if desc:
                        f.write(f"- **摘要**: {desc[:200]}...\n")
                
                f.write(f"- **来源**: {result.source}\n")
                f.write(f"- **抓取时间**: {result.scraped_at}\n\n")
                f.write("---\n\n")
        
        print(f"[保存] 结果已保存到: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="微信公众号文章搜索（搜狗微信）")
    parser.add_argument("query", type=str, help="搜索关键词")
    parser.add_argument("--max-results", type=int, default=20, help="最大结果数量")
    parser.add_argument("--output-dir", type=str, default="./search_results/wechat",
                        help="输出目录")
    parser.add_argument("--account-only", action="store_true",
                        help="仅搜索公众号账号")
    parser.add_argument("--proxy", type=str, help="代理地址 (格式: http://user:pass@host:port)")
    parser.add_argument("--port", type=int, default=9333, help="浏览器调试端口")
    
    args = parser.parse_args()
    
    # 创建搜索器
    searcher = WechatSearcher(proxy=args.proxy)
    
    # 执行搜索
    results = searcher.search(
        query=args.query,
        max_results=args.max_results,
        output_dir=args.output_dir,
        account_only=args.account_only,
        proxy=args.proxy,
        port=args.port
    )
    
    # 输出结果
    if results:
        print(f"\n[结果] 共找到 {len(results.results)} 条结果")
        print(json.dumps([r.to_dict() for r in results.results], ensure_ascii=False, indent=2))
    else:
        print("[结果] 未找到结果")


if __name__ == "__main__":
    main()
