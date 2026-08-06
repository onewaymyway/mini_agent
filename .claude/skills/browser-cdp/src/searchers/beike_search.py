#!/usr/bin/env python
"""
beike_search.py - 贝壳找房房源搜索器

支持：
- 二手房/租房搜索
- 城市筛选
- 小区/商圈筛选
- 房源列表和详情抓取
- 反检测模式

技术难点：
- 贝壳有反爬机制，需启用 stealth 模式
- 部分房源信息需要登录才能查看
- 详情页结构较复杂
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
class BeikeConfig(SearcherConfig):
    """贝壳搜索器专用配置"""
    # 搜索参数
    city: str = ""  # 城市名称
    house_type: str = "ershoufang"  # 房源类型：ershoufang/zuizu
    
    # 详情抓取
    fetch_detail: bool = False
    
    # 动态加载
    enable_infinite_scroll: bool = True
    max_scroll_pages: int = 3
    
    def to_dict(self) -> Dict:
        data = super().to_dict()
        data.update({
            'city': self.city,
            'house_type': self.house_type,
            'fetch_detail': self.fetch_detail,
            'enable_infinite_scroll': self.enable_infinite_scroll,
            'max_scroll_pages': self.max_scroll_pages,
        })
        return data


@dataclass
class HouseInfo(SearchResult):
    """房源信息数据结构"""
    community: str = ""  # 小区名称
    district: str = ""  # 区域
    price: str = ""  # 总价
    unit_price: str = ""  # 单价
    area: str = ""  # 面积
    layout: str = ""  # 户型
    floor: str = ""  # 楼层
    direction: str = ""  # 朝向
    decoration: str = ""  # 装修
    tags: List[str] = field(default_factory=list)  # 标签
    
    def to_dict(self) -> Dict:
        data = super().to_dict()
        data.update({
            'community': self.community,
            'district': self.district,
            'price': self.price,
            'unit_price': self.unit_price,
            'area': self.area,
            'layout': self.layout,
            'floor': self.floor,
            'direction': self.direction,
            'decoration': self.decoration,
            'tags': self.tags,
        })
        return data


class BeikeSearcher(BaseSearcher):
    """
    贝壳找房搜索器
    
    使用方式：
        config = BeikeConfig(query="三居室", city="北京", house_type="ershoufang", max_results=20)
        searcher = BeikeSearcher(config=config)
        results = searcher.search()
        results.save_json('output/beike_results.json')
    """
    
    BASE_URL = "https://www.ke.com"
    SEARCH_URL = "https://www.ke.com/ershoufang/"
    
    @property
    def source_name(self) -> str:
        return "beike"
    
    @property
    def supported_types(self) -> List[str]:
        return ["house_search", "house_detail"]
    
    def __init__(self, config: BeikeConfig = None):
        super().__init__(config or BeikeConfig())
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
        
        results = SearchResults(source="beike", query=query)
        
        try:
            # 构建搜索 URL
            base_url = self.SEARCH_URL if self.config.house_type == "ershoufang" else "https://www.ke.com/zufang/"
            
            logger.info(f"搜索房源: {query}, 城市: {self.config.city or '全部'}, 类型: {self.config.house_type}")
            
            # 导航到搜索页面
            cmd_goto(
                self.session,
                base_url,
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
            
            # 提取房源列表
            houses = self._extract_houses_from_page()
            results.results.extend(houses)
            
            # 无限滚动加载更多
            results.results = await self._scroll_and_collect(results.results, self.config.max_results)
            
            # 去重
            results.deduplicate(by=self.config.dedup_by, threshold=self.config.dedup_threshold)
            
            logger.info(f"搜索完成，共获取 {len(results.results)} 套房源")
            
        except Exception as e:
            logger.error(f"搜索失败: {e}")
            results.error = str(e)
        
        return results.results
    
    def _perform_search(self, query: str):
        """执行搜索操作"""
        # 在搜索框中输入关键词
        search_box = self.session.query_selector('#searchContent input')
        if search_box:
            search_box.click()
            search_box.type_text(query)
            time.sleep(1)
            
            # 点击搜索按钮
            search_btn = self.session.query_selector('#searchContent button')
            if search_btn:
                search_btn.click()
                time.sleep(2)
    
    async def get_detail(self, url: str, config: Optional[SearcherConfig] = None) -> Dict:
        """获取房源详情"""
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
            detail = self._extract_house_detail()
            
            return detail
            
        except Exception as e:
            logger.error(f"获取房源详情失败: {e}")
            return {}
    
    def _extract_houses_from_page(self) -> List[HouseInfo]:
        """
        从当前页面提取房源列表
        
        Returns:
            房源列表
        """
        js_code = """
        (function() {
            const houses = [];
            const cards = document.querySelectorAll('.sellListContent li, .sellListContent .logo', '.listItem');
            
            cards.forEach(card => {
                const house = {
                    title: card.querySelector('.title a')?.textContent?.trim() || 
                           card.querySelector('.title')?.textContent?.trim() || '',
                    community: card.querySelector('.communityName')?.textContent?.trim() || 
                               card.querySelector('[class*="community"]')?.textContent?.trim() || '',
                    district: card.querySelector('.districtName')?.textContent?.trim() || 
                              card.querySelector('[class*="district"]')?.textContent?.trim() || '',
                    price: card.querySelector('.totalPrice')?.textContent?.trim() || 
                           card.querySelector('[class*="totalPrice"]')?.textContent?.trim() || '',
                    unit_price: card.querySelector('.unitPrice')?.textContent?.trim() || 
                                card.querySelector('[class*="unitPrice"]')?.textContent?.trim() || '',
                    area: card.querySelector('.houseInfo')?.textContent?.trim() || '',
                    layout: card.querySelector('.houseInfo')?.textContent?.trim() || '',
                    floor: card.querySelector('.houseFlood')?.textContent?.trim() || '',
                    direction: card.querySelector('.houseDirection')?.textContent?.trim() || '',
                    tags: Array.from(card.querySelectorAll('.tag span')).map(t => t.textContent.trim()),
                    url: card.querySelector('a')?.href || ''
                };
                if (house.title) houses.push(house);
            });
            
            return JSON.stringify(houses);
        })()
        """
        
        try:
            result = cmd_eval(self.session, js_code)
            if result and 'result' in result:
                house_data_list = json.loads(result['result'])
                return [self._parse_house_card(data) for data in house_data_list]
        except Exception as e:
            logger.error(f"提取房源列表失败: {e}")
        
        return []
    
    def _parse_house_card(self, card_data: Dict) -> Optional[HouseInfo]:
        """
        解析房源卡片数据
        
        Args:
            card_data: 房源卡片数据
            
        Returns:
            HouseInfo 对象
        """
        try:
            title = card_data.get('title', '').strip()
            if not title:
                return None
            
            house = HouseInfo(
                source="beike",
                title=title,
                url=card_data.get('url', '').strip(),
                community=card_data.get('community', '').strip(),
                district=card_data.get('district', '').strip(),
                price=card_data.get('price', '').strip(),
                unit_price=card_data.get('unit_price', '').strip(),
                area=card_data.get('area', '').strip(),
                layout=card_data.get('layout', '').strip(),
                floor=card_data.get('floor', '').strip(),
                direction=card_data.get('direction', '').strip(),
                tags=card_data.get('tags', []),
                scraped_at=datetime.now().isoformat()
            )
            
            return house
            
        except Exception as e:
            logger.error(f"解析房源卡片失败: {e}")
            return None
    
    def _extract_house_detail(self) -> Dict:
        """
        提取房源详情
        
        Returns:
            详情字典
        """
        js_code = """
        (function() {
            return JSON.stringify({
                title: document.querySelector('.title')?.textContent?.trim() || '',
                community: document.querySelector('.communityName')?.textContent?.trim() || '',
                district: document.querySelector('.districtName')?.textContent?.trim() || '',
                price: document.querySelector('.totalPrice')?.textContent?.trim() || '',
                unit_price: document.querySelector('.unitPrice')?.textContent?.trim() || '',
                area: document.querySelector('.houseArea')?.textContent?.trim() || '',
                layout: document.querySelector('.houseModel')?.textContent?.trim() || '',
                floor: document.querySelector('.houseFlood')?.textContent?.trim() || '',
                direction: document.querySelector('.houseDirection')?.textContent?.trim() || '',
                decoration: document.querySelector('.houseDecoration')?.textContent?.trim() || '',
                tags: Array.from(document.querySelectorAll('.tag span')).map(t => t.textContent.trim())
            });
        })()
        """
        
        try:
            result = cmd_eval(self.session, js_code)
            if result and 'result' in result:
                return json.loads(result['result'])
        except Exception as e:
            logger.error(f"提取房源详情失败: {e}")
        
        return {}
    
    async def _scroll_and_collect(self, results: List[HouseInfo], max_results: int) -> List[HouseInfo]:
        """
        无限滚动加载更多房源
        
        Args:
            results: 已有结果
            max_results: 最大结果数
            
        Returns:
            更新后的结果列表
        """
        if not self.config.enable_infinite_scroll:
            return results
        
        existing_titles = {h.title for h in results}
        
        for i in range(self.config.max_scroll_pages):
            if len(results) >= max_results:
                break
            
            # 滚动加载
            self.session.eval_js("window.scrollBy(0, 800)")
            time.sleep(1.5)
            
            # 提取新房源
            new_houses = self._extract_houses_from_page()
            
            # 去重
            for house in new_houses:
                if house.title not in existing_titles:
                    results.append(house)
                    existing_titles.add(house.title)
            
            logger.info(f"滚动加载第 {i+1} 页，当前共 {len(results)} 套房源")
        
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
        all_results = SearchResults(source="beike", query="batch")
        
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
    
    parser = argparse.ArgumentParser(description='贝壳找房房源搜索器')
    parser.add_argument('--port', type=int, default=9333, help='CDP 调试端口')
    parser.add_argument('--tab', type=str, required=True, help='浏览器 tab ID')
    parser.add_argument('--keyword', type=str, required=True, help='搜索关键词')
    parser.add_argument('--city', type=str, default='', help='城市名称')
    parser.add_argument('--type', type=str, default='ershoufang', choices=['ershoufang', 'zuizu'], help='房源类型')
    parser.add_argument('--max-results', type=int, default=50, help='最大结果数')
    parser.add_argument('--output', type=str, help='输出文件路径')
    parser.add_argument('--no-stealth', action='store_true', help='禁用反检测模式')
    parser.add_argument('--no-scroll', action='store_true', help='禁用无限滚动')
    parser.add_argument('--detail', action='store_true', help='抓取房源详情')
    
    args = parser.parse_args()
    
    # 创建配置
    config = BeikeConfig(
        port=args.port,
        tab_id=args.tab,
        query=args.keyword,
        city=args.city,
        house_type=args.type,
        max_results=args.max_results,
        stealth=not args.no_stealth,
        enable_infinite_scroll=not args.no_scroll,
        fetch_detail=args.detail,
    )
    
    # 创建搜索器
    searcher = BeikeSearcher(config=config)
    
    try:
        # 执行搜索
        results = asyncio.run(searcher.search(args.keyword))
        
        # 输出结果
        if results:
            print("\n" + "="*60)
            print(f"搜索关键词: {args.keyword}")
            if args.city:
                print(f"城市: {args.city}")
            print(f"共找到 {len(results)} 套房源")
            print("="*60 + "\n")
            
            for i, house in enumerate(results[:20], 1):  # 只显示前20个
                print(f"【{i}】{house.title}")
                print(f"    小区: {house.community}")
                print(f"    区域: {house.district}")
                print(f"    总价: {house.price} | 单价: {house.unit_price}")
                print(f"    户型: {house.layout} | 面积: {house.area}")
                print(f"    楼层: {house.floor} | 朝向: {house.direction}")
                if house.tags:
                    print(f"    标签: {', '.join(house.tags)}")
                print()
            
            # 保存结果
            if args.output:
                output_path = Path(args.output)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, 'w', encoding='utf-8') as f:
                    json.dump([r.to_dict() for r in results], f, ensure_ascii=False, indent=2)
                print(f"[保存] 结果已保存到: {output_path}")
        else:
            print("[提示] 未找到相关房源")
    
    finally:
        asyncio.run(searcher.close())


if __name__ == '__main__':
    main()
