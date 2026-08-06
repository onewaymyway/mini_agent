#!/usr/bin/env python
"""
amazon_search.py - 亚马逊商品搜索器

支持：
- 关键词搜索商品
- 分类筛选
- 商品列表和详情抓取
- 价格/评分/评价提取
- 反检测模式

技术难点：
- 亚马逊有反爬机制，需启用 stealth 模式
- 部分商品信息需要登录才能查看
- 价格信息可能动态加载
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from datetime import datetime
from pathlib import Path

from src.searchers.base import BaseSearcher, SearcherConfig, SearchResult, SearchResults
from src.core.browser_nav import cmd_goto
from src.core.browser_console import cmd_eval
from src.core.stealth import StealthMode, StealthConfig

logger = logging.getLogger(__name__)


@dataclass
class AmazonConfig(SearcherConfig):
    """亚马逊搜索器专用配置"""
    # 搜索参数
    marketplace: str = "us"  # 站点：us/uk/de/fr/jp
    category: str = ""  # 商品分类
    
    # 详情抓取
    fetch_detail: bool = False
    fetch_reviews: bool = False
    
    # 动态加载
    enable_infinite_scroll: bool = True
    max_scroll_pages: int = 3
    
    def to_dict(self) -> Dict:
        data = super().to_dict()
        data.update({
            'marketplace': self.marketplace,
            'category': self.category,
            'fetch_detail': self.fetch_detail,
            'fetch_reviews': self.fetch_reviews,
            'enable_infinite_scroll': self.enable_infinite_scroll,
            'max_scroll_pages': self.max_scroll_pages,
        })
        return data


@dataclass
class ProductInfo(SearchResult):
    """商品信息数据结构"""
    brand: str = ""  # 品牌
    price: str = ""  # 价格
    original_price: str = ""  # 原价
    rating: str = ""  # 评分
    review_count: str = ""  # 评价数
    availability: str = ""  # 库存状态
    prime: bool = False  # 是否Prime
    category: str = ""  # 分类
    
    def to_dict(self) -> Dict:
        data = super().to_dict()
        data.update({
            'brand': self.brand,
            'price': self.price,
            'original_price': self.original_price,
            'rating': self.rating,
            'review_count': self.review_count,
            'availability': self.availability,
            'prime': self.prime,
            'category': self.category,
        })
        return data


@dataclass
class ReviewInfo(SearchResult):
    """评价信息数据结构"""
    reviewer: str = ""  # 评价者
    rating: str = ""  # 评分
    title: str = ""  # 评价标题
    content: str = ""  # 评价内容
    date: str = ""  # 评价日期
    verified: bool = False  # 是否验证购买
    
    def to_dict(self) -> Dict:
        data = super().to_dict()
        data.update({
            'reviewer': self.reviewer,
            'rating': self.rating,
            'title': self.title,
            'content': self.content,
            'date': self.date,
            'verified': self.verified,
        })
        return data


class AmazonSearcher(BaseSearcher):
    """
    亚马逊搜索器
    
    使用方式：
        config = AmazonConfig(query="laptop", marketplace="us", max_results=20)
        searcher = AmazonSearcher(config=config)
        results = searcher.search()
        results.save_json('output/amazon_results.json')
    """
    
    BASE_URLS = {
        "us": "https://www.amazon.com",
        "uk": "https://www.amazon.co.uk",
        "de": "https://www.amazon.de",
        "fr": "https://www.amazon.fr",
        "jp": "https://www.amazon.co.jp",
    }
    
    @property
    def source_name(self) -> str:
        return "amazon"
    
    @property
    def supported_types(self) -> List[str]:
        return ["product_search", "product_detail", "review_search"]
    
    def __init__(self, config: AmazonConfig = None):
        super().__init__(config or AmazonConfig())
        self._session = None
        
    @property
    def session(self):
        """获取 CDP session"""
        if self._session is None:
            from src.core.utils import get_session
            from src.core.utils import add_connection_args
            import argparse
            
            parser = argparse.ArgumentParser()
            add_connection_args(parser)
            args = parser.parse_args([])
            args.port = self.config.port
            args.tab = self.config.tab_id
            self._session = get_session(args)
        return self._session
    
    async def search(self, query: str, config: Optional[SearcherConfig] = None) -> List[SearchResult]:
        """执行搜索"""
        if config:
            self.config = config
        if query:
            self.config.query = query
        
        results = SearchResults(source="amazon", query=query)
        
        try:
            # 构建搜索 URL
            base_url = self.BASE_URLS.get(self.config.marketplace, self.BASE_URLS["us"])
            search_url = f"{base_url}/s?k={query}"
            
            if self.config.category:
                search_url += f"&i={self.config.category}"
            
            logger.info(f"搜索商品: {query}, 站点: {self.config.marketplace}")
            
            # 导航到搜索页面
            cmd_goto(
                self.session,
                search_url,
                wait_load=True,
                timeout=self.config.wait_timeout,
                wait_for=self.config.wait_strategy,
                enable_stealth=self.config.stealth,
            )
            
            # 等待页面加载
            time.sleep(2)
            
            # 提取商品列表
            products = self._extract_products_from_page()
            results.results.extend(products)
            
            # 无限滚动加载更多
            results.results = await self._scroll_and_collect(results.results, self.config.max_results)
            
            # 去重
            results.deduplicate(by=self.config.dedup_by, threshold=self.config.dedup_threshold)
            
            logger.info(f"搜索完成，共获取 {len(results.results)} 个商品")
            
        except Exception as e:
            logger.error(f"搜索失败: {e}")
            results.error = str(e)
        
        return results.results
    
    async def get_detail(self, url: str, config: Optional[SearcherConfig] = None) -> Dict:
        """获取商品详情"""
        try:
            # 导航到详情页
            cmd_goto(
                self.session,
                url,
                wait_load=True,
                timeout=self.config.wait_timeout,
                wait_for="networkidle",
                enable_stealth=self.config.stealth,
            )
            time.sleep(2)
            
            # 提取详情
            detail = self._extract_product_detail()
            
            # 如果需要评价
            if self.config.fetch_reviews:
                reviews = self._extract_reviews()
                detail['reviews'] = reviews
            
            return detail
            
        except Exception as e:
            logger.error(f"获取商品详情失败: {e}")
            return {}
    
    def _extract_products_from_page(self) -> List[ProductInfo]:
        """
        从当前页面提取商品列表
        
        Returns:
            商品列表
        """
        js_code = """
        (function() {
            const products = [];
            const cards = document.querySelectorAll('[data-component-type="s-search-result"]');
            
            cards.forEach(card => {
                const product = {
                    title: card.querySelector('h2 a span')?.textContent?.trim() || 
                           card.querySelector('[data-asin] h2 a span')?.textContent?.trim() || '',
                    price: card.querySelector('.a-price .a-offscreen')?.textContent?.trim() || 
                           card.querySelector('[data-asin] .a-price .a-offscreen')?.textContent?.trim() || '',
                    original_price: card.querySelector('.a-text-price')?.textContent?.trim() || '',
                    rating: card.querySelector('.a-icon-star-small .a-icon-alt')?.textContent?.trim() || '',
                    review_count: card.querySelector('[data-asin] .a-icon-alt')?.textContent?.trim() || '',
                    availability: card.querySelector('.availability span')?.textContent?.trim() || '',
                    prime: card.querySelector('.prime') !== null,
                    url: card.querySelector('[data-asin] a')?.href || '',
                    asin: card.getAttribute('data-asin') || ''
                };
                if (product.title) products.push(product);
            });
            
            return JSON.stringify(products);
        })()
        """
        
        try:
            result = cmd_eval(self.session, js_code)
            if result and 'result' in result:
                product_data_list = json.loads(result['result'])
                return [self._parse_product_card(data) for data in product_data_list]
        except Exception as e:
            logger.error(f"提取商品列表失败: {e}")
        
        return []
    
    def _parse_product_card(self, card_data: Dict) -> Optional[ProductInfo]:
        """
        解析商品卡片数据
        
        Args:
            card_data: 商品卡片数据
            
        Returns:
            ProductInfo 对象
        """
        try:
            title = card_data.get('title', '').strip()
            if not title:
                return None
            
            product = ProductInfo(
                source="amazon",
                title=title,
                url=card_data.get('url', '').strip(),
                price=card_data.get('price', '').strip(),
                original_price=card_data.get('original_price', '').strip(),
                rating=card_data.get('rating', '').strip(),
                review_count=card_data.get('review_count', '').strip(),
                availability=card_data.get('availability', '').strip(),
                prime=card_data.get('prime', False),
                asin=card_data.get('asin', '').strip(),
                scraped_at=datetime.now().isoformat()
            )
            
            return product
            
        except Exception as e:
            logger.error(f"解析商品卡片失败: {e}")
            return None
    
    def _extract_product_detail(self) -> Dict:
        """
        提取商品详情
        
        Returns:
            详情字典
        """
        js_code = """
        (function() {
            return JSON.stringify({
                title: document.querySelector('#productTitle')?.textContent?.trim() || '',
                price: document.querySelector('#priceblock_ourprice')?.textContent?.trim() || 
                       document.querySelector('#priceblock_dealprice')?.textContent?.trim() || '',
                rating: document.querySelector('#acrCustomerReviewText')?.textContent?.trim() || '',
                review_count: document.querySelector('#acrCustomerReviewText')?.textContent?.trim() || '',
                availability: document.querySelector('#availability span')?.textContent?.trim() || '',
                description: document.querySelector('#productDescription')?.textContent?.trim() || '',
                features: Array.from(document.querySelectorAll('#feature-bullets li')).map(li => li.textContent.trim()),
                specifications: Array.from(document.querySelectorAll('#productSpecification tr')).map(tr => {
                    const tds = tr.querySelectorAll('td');
                    return tds.length >= 2 ? tds[0].textContent.trim() + ': ' + tds[1].textContent.trim() : '';
                }).filter(s => s)
            });
        })()
        """
        
        try:
            result = cmd_eval(self.session, js_code)
            if result and 'result' in result:
                return json.loads(result['result'])
        except Exception as e:
            logger.error(f"提取商品详情失败: {e}")
        
        return {}
    
    def _extract_reviews(self) -> List[ReviewInfo]:
        """
        提取商品评价
        
        Returns:
            评价列表
        """
        js_code = """
        (function() {
            const reviews = [];
            const reviewElements = document.querySelectorAll('[data-hook="review"]');
            
            reviewElements.forEach(el => {
                const review = {
                    reviewer: el.querySelector('[data-hook="review-author"]')?.textContent?.trim() || '',
                    rating: el.querySelector('[data-hook="review-star-rating"]')?.textContent?.trim() || '',
                    title: el.querySelector('[data-hook="review-title"]')?.textContent?.trim() || '',
                    content: el.querySelector('[data-hook="review-body"]')?.textContent?.trim() || '',
                    date: el.querySelector('[data-hook="review-date"]')?.textContent?.trim() || '',
                    verified: el.querySelector('[data-hook="avp-purchased"]') !== null
                };
                if (review.content) reviews.push(review);
            });
            
            return JSON.stringify(reviews);
        })()
        """
        
        try:
            result = cmd_eval(self.session, js_code)
            if result and 'result' in result:
                review_data_list = json.loads(result['result'])
                return [self._parse_review(data) for data in review_data_list]
        except Exception as e:
            logger.error(f"提取评价失败: {e}")
        
        return []
    
    def _parse_review(self, review_data: Dict) -> Optional[ReviewInfo]:
        """
        解析评价数据
        
        Args:
            review_data: 评价数据
            
        Returns:
            ReviewInfo 对象
        """
        try:
            content = review_data.get('content', '').strip()
            if not content:
                return None
            
            review = ReviewInfo(
                source="amazon",
                title=review_data.get('title', '').strip(),
                url="",
                content=content,
                reviewer=review_data.get('reviewer', '').strip(),
                rating=review_data.get('rating', '').strip(),
                date=review_data.get('date', '').strip(),
                verified=review_data.get('verified', False),
                scraped_at=datetime.now().isoformat()
            )
            
            return review
            
        except Exception as e:
            logger.error(f"解析评价失败: {e}")
            return None
    
    async def _scroll_and_collect(self, results: List[ProductInfo], max_results: int) -> List[ProductInfo]:
        """
        无限滚动加载更多商品
        
        Args:
            results: 已有结果
            max_results: 最大结果数
            
        Returns:
            更新后的结果列表
        """
        if not self.config.enable_infinite_scroll:
            return results
        
        existing_asins = {p.asin for p in results if hasattr(p, 'asin') and p.asin}
        
        for i in range(self.config.max_scroll_pages):
            if len(results) >= max_results:
                break
            
            # 滚动加载
            self.session.eval_js("window.scrollBy(0, 800)")
            time.sleep(1.5)
            
            # 提取新商品
            new_products = self._extract_products_from_page()
            
            # 去重
            for product in new_products:
                asin = getattr(product, 'asin', '')
                if asin and asin not in existing_asins:
                    results.append(product)
                    existing_asins.add(asin)
            
            logger.info(f"滚动加载第 {i+1} 页，当前共 {len(results)} 个商品")
        
        return results
    
    def _random_delay(self) -> float:
        """
        生成随机延迟时间（1-3秒）
        
        Returns:
            随机延迟秒数
        """
        return random.uniform(1.0, 3.0)
    
    async def search_batch(self, queries: List[str], **kwargs) -> SearchResults:
        """
        批量搜索
        
        Args:
            queries: 关键词列表
            **kwargs: 其他参数
        
        Returns:
            合并后的搜索结果
        """
        all_results = SearchResults(source="amazon", query="batch")
        
        for i, query in enumerate(queries):
            logger.info(f"批量搜索 [{i+1}/{len(queries)}]: {query}")
            
            # 搜索
            results_list = await self.search(query)
            all_results.results.extend(results_list)
            
            # 随机延迟
            time.sleep(self._random_delay())
        
        # 去重
        all_results.deduplicate(by=self.config.dedup_by, threshold=self.config.dedup_threshold)
        
        return all_results
    
    async def close(self):
        """关闭浏览器资源"""
        if self._session:
            try:
                self._session.close()
            except Exception as e:
                logger.warning(f"关闭浏览器时出错: {e}")


def main():
    """命令行入口"""
    import argparse
    
    parser = argparse.ArgumentParser(description='亚马逊商品搜索器')
    parser.add_argument('--port', type=int, default=9333, help='CDP 调试端口')
    parser.add_argument('--tab', type=str, required=True, help='浏览器 tab ID')
    parser.add_argument('--keyword', type=str, required=True, help='搜索关键词')
    parser.add_argument('--marketplace', type=str, default='us', choices=['us', 'uk', 'de', 'fr', 'jp'], help='站点')
    parser.add_argument('--category', type=str, default='', help='商品分类')
    parser.add_argument('--max-results', type=int, default=50, help='最大结果数')
    parser.add_argument('--output', type=str, help='输出文件路径')
    parser.add_argument('--no-stealth', action='store_true', help='禁用反检测模式')
    parser.add_argument('--no-scroll', action='store_true', help='禁用无限滚动')
    parser.add_argument('--detail', action='store_true', help='抓取商品详情')
    parser.add_argument('--reviews', action='store_true', help='抓取商品评价')
    
    args = parser.parse_args()
    
    # 创建配置
    config = AmazonConfig(
        port=args.port,
        tab_id=args.tab,
        query=args.keyword,
        marketplace=args.marketplace,
        category=args.category,
        max_results=args.max_results,
        stealth=not args.no_stealth,
        enable_infinite_scroll=not args.no_scroll,
        fetch_detail=args.detail,
        fetch_reviews=args.reviews,
    )
    
    # 创建搜索器
    searcher = AmazonSearcher(config=config)
    
    try:
        # 执行搜索
        results = asyncio.run(searcher.search(args.keyword))
        
        # 输出结果
        if results:
            print("\n" + "="*60)
            print(f"搜索关键词: {args.keyword}")
            print(f"站点: {args.marketplace}")
            print(f"共找到 {len(results)} 个商品")
            print("="*60 + "\n")
            
            for i, product in enumerate(results[:20], 1):  # 只显示前20个
                print(f"【{i}】{product.title}")
                print(f"    价格: {product.price}")
                print(f"    评分: {product.rating} | 评价数: {product.review_count}")
                print(f"    库存: {product.availability}")
                if product.prime:
                    print(f"    Prime: 是")
                print()
            
            # 保存结果
            if args.output:
                output_path = Path(args.output)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, 'w', encoding='utf-8') as f:
                    json.dump([r.to_dict() for r in results], f, ensure_ascii=False, indent=2)
                print(f"[保存] 结果已保存到: {output_path}")
        else:
            print("[提示] 未找到相关商品")
    
    finally:
        asyncio.run(searcher.close())


if __name__ == '__main__':
    main()
