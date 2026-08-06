#!/usr/bin/env python
"""
meituan_search.py - 美团商户搜索器

支持：
- 商户关键词搜索
- 城市筛选
- 商户列表和详情抓取
- 评价抓取
- 反检测模式

技术难点：
- 美团有反爬机制，需启用 stealth 模式
- 部分商户信息需要登录才能查看
- 评价列表需要滚动加载
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
class MeituanConfig(SearcherConfig):
    """美团搜索器专用配置"""
    # 搜索参数
    city: str = ""  # 城市名称
    category: str = ""  # 商户分类
    
    # 详情抓取
    fetch_detail: bool = False
    fetch_reviews: bool = False
    
    # 动态加载
    enable_infinite_scroll: bool = True
    max_scroll_pages: int = 3
    
    def to_dict(self) -> Dict:
        data = super().to_dict()
        data.update({
            'city': self.city,
            'category': self.category,
            'fetch_detail': self.fetch_detail,
            'fetch_reviews': self.fetch_reviews,
            'enable_infinite_scroll': self.enable_infinite_scroll,
            'max_scroll_pages': self.max_scroll_pages,
        })
        return data


@dataclass
class MerchantInfo(SearchResult):
    """商户信息数据结构"""
    category: str = ""  # 商户分类
    address: str = ""  # 地址
    phone: str = ""  # 电话
    rating: str = ""  # 评分
    review_count: str = ""  # 评价数量
    price_per_person: str = ""  # 人均消费
    tags: List[str] = field(default_factory=list)  # 标签
    distance: str = ""  # 距离
    
    def to_dict(self) -> Dict:
        data = super().to_dict()
        data.update({
            'category': self.category,
            'address': self.address,
            'phone': self.phone,
            'rating': self.rating,
            'review_count': self.review_count,
            'price_per_person': self.price_per_person,
            'tags': self.tags,
            'distance': self.distance,
        })
        return data


@dataclass
class ReviewInfo(SearchResult):
    """评价信息数据结构"""
    user_name: str = ""  # 用户名
    rating: str = ""  # 评分
    content: str = ""  # 评价内容
    date: str = ""  # 评价日期
    merchant_name: str = ""  # 商户名称
    
    def to_dict(self) -> Dict:
        data = super().to_dict()
        data.update({
            'user_name': self.user_name,
            'rating': self.rating,
            'content': self.content,
            'date': self.date,
            'merchant_name': self.merchant_name,
        })
        return data


class MeituanSearcher(BaseSearcher):
    """
    美团搜索器
    
    使用方式：
        config = MeituanConfig(query="火锅", city="北京", max_results=20)
        searcher = MeituanSearcher(config=config)
        results = searcher.search()
        results.save_json('output/meituan_results.json')
    """
    
    BASE_URL = "https://www.meituan.com"
    SEARCH_URL = "https://www.meituan.com/meishi/"
    
    @property
    def source_name(self) -> str:
        return "meituan"
    
    @property
    def supported_types(self) -> List[str]:
        return ["merchant_search", "merchant_detail", "review_search"]
    
    def __init__(self, config: MeituanConfig = None):
        super().__init__(config or MeituanConfig())
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
        
        results = SearchResults(source="meituan", query=query)
        
        try:
            # 构建搜索 URL
            search_url = f"{self.SEARCH_URL}"
            
            logger.info(f"搜索商户: {query}, 城市: {self.config.city or '全部'}")
            
            # 导航到搜索页面
            cmd_goto(
                self.session,
                self.BASE_URL,
                wait_load=True,
                timeout=self.config.wait_timeout,
                wait_for=self.config.wait_strategy,
                enable_stealth=self.config.stealth,
            )
            
            # 等待页面加载
            time.sleep(2)
            
            # 执行搜索
            self._perform_search(query)
            
            # 等待搜索结果加载
            time.sleep(2)
            
            # 提取商户列表
            merchants = self._extract_merchants_from_page()
            results.results.extend(merchants)
            
            # 无限滚动加载更多
            results.results = await self._scroll_and_collect(results.results, self.config.max_results)
            
            # 去重
            results.deduplicate(by=self.config.dedup_by, threshold=self.config.dedup_threshold)
            
            logger.info(f"搜索完成，共获取 {len(results.results)} 个商户")
            
        except Exception as e:
            logger.error(f"搜索失败: {e}")
            results.error = str(e)
        
        return results.results
    
    def _perform_search(self, query: str):
        """执行搜索操作"""
        # 在搜索框中输入关键词
        search_box = self.session.query_selector('#search-input')
        if search_box:
            search_box.click()
            search_box.type_text(query)
            time.sleep(1)
            
            # 点击搜索按钮
            search_btn = self.session.query_selector('.search-btn, [class*="search-btn"]')
            if search_btn:
                search_btn.click()
                time.sleep(2)
    
    async def get_detail(self, url: str, config: Optional[SearcherConfig] = None) -> Dict:
        """获取商户详情"""
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
            detail = self._extract_merchant_detail()
            
            # 如果需要评价
            if self.config.fetch_reviews:
                reviews = self._extract_reviews()
                detail['reviews'] = reviews
            
            return detail
            
        except Exception as e:
            logger.error(f"获取商户详情失败: {e}")
            return {}
    
    def _extract_merchants_from_page(self) -> List[MerchantInfo]:
        """
        从当前页面提取商户列表
        
        Returns:
            商户列表
        """
        js_code = """
        (function() {
            const merchants = [];
            const cards = document.querySelectorAll('.merchant-item, .shop-item, [class*="merchant-item"]');
            
            cards.forEach(card => {
                const merchant = {
                    title: card.querySelector('.merchant-name, .shop-name')?.textContent?.trim() || 
                           card.querySelector('[class*="merchant-name"]')?.textContent?.trim() || '',
                    category: card.querySelector('.category, [class*="category"]')?.textContent?.trim() || '',
                    address: card.querySelector('.address, [class*="address"]')?.textContent?.trim() || '',
                    rating: card.querySelector('.rating, [class*="rating"]')?.textContent?.trim() || '',
                    review_count: card.querySelector('.review-count, [class*="review-count"]')?.textContent?.trim() || '',
                    price_per_person: card.querySelector('.price, [class*="price"]')?.textContent?.trim() || '',
                    tags: Array.from(card.querySelectorAll('.tag, [class*="tag"]')).map(t => t.textContent.trim()),
                    distance: card.querySelector('.distance, [class*="distance"]')?.textContent?.trim() || '',
                    url: card.querySelector('a')?.href || ''
                };
                if (merchant.title) merchants.push(merchant);
            });
            
            return JSON.stringify(merchants);
        })()
        """
        
        try:
            result = cmd_eval(self.session, js_code)
            if result and 'result' in result:
                merchant_data_list = json.loads(result['result'])
                return [self._parse_merchant_card(data) for data in merchant_data_list]
        except Exception as e:
            logger.error(f"提取商户列表失败: {e}")
        
        return []
    
    def _parse_merchant_card(self, card_data: Dict) -> Optional[MerchantInfo]:
        """
        解析商户卡片数据
        
        Args:
            card_data: 商户卡片数据
            
        Returns:
            MerchantInfo 对象
        """
        try:
            title = card_data.get('title', '').strip()
            if not title:
                return None
            
            merchant = MerchantInfo(
                source="meituan",
                title=title,
                url=card_data.get('url', '').strip(),
                category=card_data.get('category', '').strip(),
                address=card_data.get('address', '').strip(),
                rating=card_data.get('rating', '').strip(),
                review_count=card_data.get('review_count', '').strip(),
                price_per_person=card_data.get('price_per_person', '').strip(),
                tags=card_data.get('tags', []),
                distance=card_data.get('distance', '').strip(),
                scraped_at=datetime.now().isoformat()
            )
            
            return merchant
            
        except Exception as e:
            logger.error(f"解析商户卡片失败: {e}")
            return None
    
    def _extract_merchant_detail(self) -> Dict:
        """
        提取商户详情
        
        Returns:
            详情字典
        """
        js_code = """
        (function() {
            return JSON.stringify({
                title: document.querySelector('.merchant-title, .shop-title')?.textContent?.trim() || '',
                rating: document.querySelector('.rating-score, [class*="rating-score"]')?.textContent?.trim() || '',
                review_count: document.querySelector('.review-count-total')?.textContent?.trim() || '',
                price_per_person: document.querySelector('.price-per-person')?.textContent?.trim() || '',
                address: document.querySelector('.address-text')?.textContent?.trim() || '',
                phone: document.querySelector('.phone-number')?.textContent?.trim() || '',
                business_hours: document.querySelector('.business-hours')?.textContent?.trim() || '',
                description: document.querySelector('.merchant-description')?.textContent?.trim() || '',
                tags: Array.from(document.querySelectorAll('.tag-item')).map(t => t.textContent.trim())
            });
        })()
        """
        
        try:
            result = cmd_eval(self.session, js_code)
            if result and 'result' in result:
                return json.loads(result['result'])
        except Exception as e:
            logger.error(f"提取商户详情失败: {e}")
        
        return {}
    
    def _extract_reviews(self) -> List[ReviewInfo]:
        """
        提取商户评价
        
        Returns:
            评价列表
        """
        js_code = """
        (function() {
            const reviews = [];
            const cards = document.querySelectorAll('.review-item, .comment-item, [class*="review-item"]');
            
            cards.forEach(card => {
                const review = {
                    user_name: card.querySelector('.user-name')?.textContent?.trim() || '',
                    rating: card.querySelector('.rating')?.textContent?.trim() || '',
                    content: card.querySelector('.review-content')?.textContent?.trim() || '',
                    date: card.querySelector('.review-date')?.textContent?.trim() || '',
                    merchant_name: card.querySelector('.merchant-name')?.textContent?.trim() || ''
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
                source="meituan",
                title=review_data.get('merchant_name', '').strip(),
                url="",
                content=content,
                user_name=review_data.get('user_name', '').strip(),
                rating=review_data.get('rating', '').strip(),
                date=review_data.get('date', '').strip(),
                merchant_name=review_data.get('merchant_name', '').strip(),
                scraped_at=datetime.now().isoformat()
            )
            
            return review
            
        except Exception as e:
            logger.error(f"解析评价失败: {e}")
            return None
    
    async def _scroll_and_collect(self, results: List[MerchantInfo], max_results: int) -> List[MerchantInfo]:
        """
        无限滚动加载更多商户
        
        Args:
            results: 已有结果
            max_results: 最大结果数
            
        Returns:
            更新后的结果列表
        """
        if not self.config.enable_infinite_scroll:
            return results
        
        existing_titles = {m.title for m in results}
        
        for i in range(self.config.max_scroll_pages):
            if len(results) >= max_results:
                break
            
            # 滚动加载
            self.session.eval_js("window.scrollBy(0, 800)")
            time.sleep(1.5)
            
            # 提取新商户
            new_merchants = self._extract_merchants_from_page()
            
            # 去重
            for merchant in new_merchants:
                if merchant.title not in existing_titles:
                    results.append(merchant)
                    existing_titles.add(merchant.title)
            
            logger.info(f"滚动加载第 {i+1} 页，当前共 {len(results)} 个商户")
        
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
        all_results = SearchResults(source="meituan", query="batch")
        
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
    
    parser = argparse.ArgumentParser(description='美团商户搜索器')
    parser.add_argument('--port', type=int, default=9333, help='CDP 调试端口')
    parser.add_argument('--tab', type=str, required=True, help='浏览器 tab ID')
    parser.add_argument('--keyword', type=str, required=True, help='搜索关键词')
    parser.add_argument('--city', type=str, default='', help='城市名称')
    parser.add_argument('--category', type=str, default='', help='商户分类')
    parser.add_argument('--max-results', type=int, default=50, help='最大结果数')
    parser.add_argument('--output', type=str, help='输出文件路径')
    parser.add_argument('--no-stealth', action='store_true', help='禁用反检测模式')
    parser.add_argument('--no-scroll', action='store_true', help='禁用无限滚动')
    parser.add_argument('--detail', action='store_true', help='抓取商户详情')
    parser.add_argument('--reviews', action='store_true', help='抓取商户评价')
    
    args = parser.parse_args()
    
    # 创建配置
    config = MeituanConfig(
        port=args.port,
        tab_id=args.tab,
        query=args.keyword,
        city=args.city,
        category=args.category,
        max_results=args.max_results,
        stealth=not args.no_stealth,
        enable_infinite_scroll=not args.no_scroll,
        fetch_detail=args.detail,
        fetch_reviews=args.reviews,
    )
    
    # 创建搜索器
    searcher = MeituanSearcher(config=config)
    
    try:
        # 执行搜索
        results = asyncio.run(searcher.search(args.keyword))
        
        # 输出结果
        if results:
            print("\n" + "="*60)
            print(f"搜索关键词: {args.keyword}")
            if args.city:
                print(f"城市: {args.city}")
            print(f"共找到 {len(results)} 个商户")
            print("="*60 + "\n")
            
            for i, merchant in enumerate(results[:20], 1):  # 只显示前20个
                print(f"【{i}】{merchant.title}")
                print(f"    分类: {merchant.category}")
                print(f"    评分: {merchant.rating} | 评价数: {merchant.review_count}")
                print(f"    人均: {merchant.price_per_person}")
                print(f"    地址: {merchant.address}")
                if merchant.tags:
                    print(f"    标签: {', '.join(merchant.tags)}")
                print()
            
            # 保存结果
            if args.output:
                output_path = Path(args.output)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, 'w', encoding='utf-8') as f:
                    json.dump([r.to_dict() for r in results], f, ensure_ascii=False, indent=2)
                print(f"[保存] 结果已保存到: {output_path}")
        else:
            print("[提示] 未找到相关商户")
    
    finally:
        asyncio.run(searcher.close())


if __name__ == '__main__':
    main()
