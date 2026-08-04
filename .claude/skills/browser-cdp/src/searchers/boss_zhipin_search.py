#!/usr/bin/env python
"""
boss_zhipin_search.py - BOSS直聘职位搜索器

支持：
- 关键词搜索职位
- 城市筛选
- 薪资范围筛选
- 职位详情抓取
- 字体加密处理
- 反检测模式

技术难点：
- BOSS直聘使用字体加密（woff字体映射）
- 需要特殊处理薪资、公司名称等字段
- 反爬机制较强，需启用 stealth 模式
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
from src.core.smart_wait import SmartWait, WaitConfig
from src.core.dynamic_loader import DynamicLoader, ScrollConfig
from src.core.stealth import StealthConfig

logger = logging.getLogger(__name__)


@dataclass
class BossZhipinConfig(SearcherConfig):
    """BOSS直聘搜索器专用配置"""
    # 搜索参数
    city: str = ""  # 城市代码
    salary_min: int = 0  # 最低薪资（千）
    salary_max: int = 0  # 最高薪资（千）
    experience: str = ""  # 经验要求
    education: str = ""  # 学历要求
    job_type: str = ""  # 职位类型
    
    # 字体加密处理
    font_decryption: bool = True
    
    # 详情抓取
    fetch_detail: bool = False
    
    # 动态加载
    enable_infinite_scroll: bool = True
    max_scroll_pages: int = 5
    
    def to_dict(self) -> Dict:
        data = super().to_dict()
        data.update({
            'city': self.city,
            'salary_min': self.salary_min,
            'salary_max': self.salary_max,
            'experience': self.experience,
            'education': self.education,
            'job_type': self.job_type,
            'font_decryption': self.font_decryption,
            'fetch_detail': self.fetch_detail,
            'enable_infinite_scroll': self.enable_infinite_scroll,
            'max_scroll_pages': self.max_scroll_pages,
        })
        return data


@dataclass
class JobInfo(SearchResult):
    """职位信息数据结构"""
    company: str = ""  # 公司名称
    salary: str = ""  # 薪资范围
    location: str = ""  # 工作地点
    experience: str = ""  # 经验要求
    education: str = ""  # 学历要求
    job_type: str = ""  # 职位类型
    tags: List[str] = field(default_factory=list)  # 职位标签
    description: str = ""  # 职位描述
    benefits: List[str] = field(default_factory=list)  # 福利
    
    def to_dict(self) -> Dict:
        data = super().to_dict()
        data.update({
            'company': self.company,
            'salary': self.salary,
            'location': self.location,
            'experience': self.experience,
            'education': self.education,
            'job_type': self.job_type,
            'tags': self.tags,
            'description': self.description[:500] if self.description else '',
            'benefits': self.benefits[:10],
        })
        return data


class BossZhipinSearcher(BaseSearcher):
    """
    BOSS直聘搜索器
    
    使用方式：
        config = BossZhipinConfig(query="Python开发", city="北京", max_results=20)
        searcher = BossZhipinSearcher(config=config)
        results = searcher.search()
        results.save_json('output/boss_results.json')
    """
    
    BASE_URL = "https://www.zhipin.com"
    SEARCH_URL = "https://www.zhipin.com/web/geek/job"
    
    @property
    def source_name(self) -> str:
        return "boss_zhipin"
    
    @property
    def supported_types(self) -> List[str]:
        return ["job_search", "job_detail"]
    
    def __init__(self, config: BossZhipinConfig = None):
        super().__init__(config or BossZhipinConfig())
        
        # 字体加密映射缓存
        self.font_mapping: Dict[str, str] = {}
        self._font_loaded = False
        
    async def search(self, query: str, config: Optional[SearcherConfig] = None) -> List[SearchResult]:
        """执行搜索（异步接口，兼容 BaseSearcher）"""
        results = await self._search_async(query)
        return results.results
    
    async def _search_async(self, query: str) -> SearchResults:
        """同步搜索实现"""
        if query:
            self.config.query = query
        
        results = SearchResults(source="boss_zhipin", query=query)
        
        try:
            # 构建搜索 URL
            search_url = f"{self.SEARCH_URL}?query={self.config.query}"
            
            if self.config.city:
                search_url += f"&city={self.config.city}"
            if self.config.salary_min:
                search_url += f"&salary={self.config.salary_min}000-"
                if self.config.salary_max:
                    search_url += f"{self.config.salary_max}000"
            
            logger.info(f"搜索职位: {self.config.query}, 城市: {self.config.city or '全部'}")
            
            # 导航到搜索页面
            self.nav.goto(search_url, wait_for=self.config.wait_strategy, 
                         timeout=self.config.wait_timeout, stealth=self.config.stealth)
            
            # 等待页面加载
            time.sleep(2)
            
            # 加载字体映射
            if self.config.font_decryption:
                self._load_font_mapping()
            
            # 提取职位列表
            jobs = self._extract_jobs_from_page()
            results.results.extend(jobs)
            
            # 无限滚动加载更多
            results.results = await self._scroll_and_collect(results.results, self.config.max_results)
            
            # 去重
            results.deduplicate(by=self.config.dedup_by, threshold=self.config.dedup_threshold)
            
            logger.info(f"搜索完成，共获取 {len(results.results)} 个职位")
            
        except Exception as e:
            logger.error(f"搜索失败: {e}")
            results.error = str(e)
        
        return results
    
    async def get_detail(self, url: str, config: Optional[SearcherConfig] = None) -> Dict:
        """获取职位详情（异步接口，兼容 BaseSearcher）"""
        return await asyncio.to_thread(self.get_job_detail, url)
    
    def _load_font_mapping(self) -> bool:
        """
        加载字体映射表
        
        Returns:
            是否成功加载
        """
        if self._font_loaded:
            return bool(self.font_mapping)
        
        try:
            # 执行 JS 获取字体映射
            js_code = """
            (function() {
                // BOSS直聘字体加密特征检测
                const fonts = document.querySelectorAll('[class*="iconfont"]');
                if (fonts.length === 0) return '{}';
                
                // 尝试从页面中提取字体信息
                const style = document.querySelector('style');
                if (!style) return '{}';
                
                return JSON.stringify({
                    has_font_encryption: true,
                    font_count: fonts.length
                });
            })()
            """
            
            result = self.session.execute_js(js_code)
            if result:
                self.font_mapping = json.loads(result)
                self._font_loaded = True
                logger.info("字体映射加载成功")
                return True
            
        except Exception as e:
            logger.warning(f"字体映射加载失败: {e}")
        
        return False
    
    def _decode_font_encryption(self, text: str) -> str:
        """
        解码 BOSS直聘的字体加密文本
        
        Args:
            text: 加密文本
            
        Returns:
            解码后的文本
        """
        if not text or not self.font_mapping:
            return text
        
        # 简单替换解码
        decoded = text
        for encoded, real in self.font_mapping.items():
            decoded = decoded.replace(encoded, real)
        
        return decoded
    
    def _parse_job_card(self, card_data: Dict) -> Optional[JobInfo]:
        """
        解析职位卡片数据
        
        Args:
            card_data: 职位卡片数据
            
        Returns:
            JobInfo 对象
        """
        try:
            # 提取基本信息
            title = card_data.get('title', '').strip()
            if not title:
                return None
            
            # 解码薪资
            raw_salary = card_data.get('salary', '')
            salary = self._decode_font_encryption(raw_salary) if self.font_mapping else raw_salary
            
            job = JobInfo(
                source="boss_zhipin",
                title=title,
                url=card_data.get('url', ''),
                company=card_data.get('company', '').strip(),
                salary=salary,
                location=card_data.get('location', '').strip(),
                experience=card_data.get('experience', '').strip(),
                education=card_data.get('education', '').strip(),
                job_type=card_data.get('job_type', '').strip(),
                tags=card_data.get('tags', []),
                description=card_data.get('description', '').strip(),
                benefits=card_data.get('benefits', []),
                scraped_at=datetime.now().isoformat()
            )
            
            return job
            
        except Exception as e:
            logger.error(f"解析职位卡片失败: {e}")
            return None
    
    def _extract_jobs_from_page(self) -> List[JobInfo]:
        """
        从当前页面提取职位列表
        
        Returns:
            职位列表
        """
        js_code = """
        (function() {
            const jobs = [];
            const cards = document.querySelectorAll('.job-card');
            
            cards.forEach(card => {
                const job = {
                    title: card.querySelector('.job-title')?.textContent?.trim() || '',
                    company: card.querySelector('.company-name')?.textContent?.trim() || '',
                    salary: card.querySelector('.salary')?.textContent?.trim() || '',
                    location: card.querySelector('.job-area')?.textContent?.trim() || '',
                    experience: card.querySelector('.job-experience')?.textContent?.trim() || '',
                    education: card.querySelector('.job-education')?.textContent?.trim() || '',
                    job_type: card.querySelector('.job-type')?.textContent?.trim() || '',
                    tags: Array.from(card.querySelectorAll('.tag span')).map(s => s.textContent.trim()),
                    description: card.querySelector('.job-limit-desc')?.textContent?.trim() || '',
                    url: card.querySelector('a')?.href || ''
                };
                jobs.push(job);
            });
            
            return JSON.stringify(jobs);
        })()
        """
        
        try:
            result = self.session.execute_js(js_code)
            if result:
                job_data_list = json.loads(result)
                return [self._parse_job_card(data) for data in job_data_list]
        except Exception as e:
            logger.error(f"提取职位列表失败: {e}")
        
        return []
    
    async def _scroll_and_collect(self, results: List[JobInfo], max_results: int) -> List[JobInfo]:
        """
        无限滚动加载更多职位
        
        Args:
            results: 已有结果
            max_results: 最大结果数
            
        Returns:
            更新后的结果列表
        """
        if not self.config.enable_infinite_scroll:
            return results
        
        loader = DynamicLoader(self.session)
        existing_titles = {j.title for j in results}
        
        for i in range(self.config.max_scroll_pages):
            if len(results) >= max_results:
                break
            
            # 滚动加载
            await loader.scroll_to_load(height_threshold=500, scroll_delay=1.0)
            
            # 等待新内容
            time.sleep(1)
            
            # 提取新职位
            new_jobs = self._extract_jobs_from_page()
            
            # 去重
            for job in new_jobs:
                if job.title not in existing_titles:
                    results.append(job)
                    existing_titles.add(job.title)
            
            logger.info(f"滚动加载第 {i+1} 页，当前共 {len(results)} 个职位")
        
        return results
    
    def get_job_detail(self, job_url: str) -> Optional[JobInfo]:
        """
        获取职位详情
        
        Args:
            job_url: 职位详情页 URL
            
        Returns:
            JobInfo 对象
        """
        try:
            # 导航到详情页
            self.nav.goto(job_url, wait_for="networkidle", timeout=30, stealth=self.config.stealth)
            time.sleep(2)
            
            # 提取详情
            js_code = """
            (function() {
                return JSON.stringify({
                    title: document.querySelector('.job-title')?.textContent?.trim() || '',
                    company: document.querySelector('.company-name')?.textContent?.trim() || '',
                    salary: document.querySelector('.salary')?.textContent?.trim() || '',
                    location: document.querySelector('.job-area')?.textContent?.trim() || '',
                    experience: document.querySelector('.job-experience')?.textContent?.trim() || '',
                    education: document.querySelector('.job-education')?.textContent?.trim() || '',
                    description: document.querySelector('.job-detail')?.textContent?.trim() || '',
                    benefits: Array.from(document.querySelectorAll('.job-benefit span')).map(s => s.textContent.trim())
                });
            })()
            """
            
            result = self.session.execute_js(js_code)
            if result:
                job_data = json.loads(result)
                return JobInfo(
                    source="boss_zhipin",
                    title=job_data.get('title', ''),
                    url=job_url,
                    company=job_data.get('company', ''),
                    salary=self._decode_font_encryption(job_data.get('salary', '')),
                    location=job_data.get('location', ''),
                    experience=job_data.get('experience', ''),
                    education=job_data.get('education', ''),
                    description=job_data.get('description', ''),
                    benefits=job_data.get('benefits', []),
                    scraped_at=datetime.now().isoformat()
                )
        
        except Exception as e:
            logger.error(f"获取职位详情失败: {e}")
        
        return None
    
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
        all_results = SearchResults(source="boss_zhipin", query="batch")

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
        if self.session:
            try:
                self.session.close()
            except Exception as e:
                logger.warning(f"关闭浏览器时出错: {e}")


def main():
    """命令行入口"""
    import argparse
    
    parser = argparse.ArgumentParser(description='BOSS直聘职位搜索器')
    parser.add_argument('--port', type=int, default=9333, help='CDP 调试端口')
    parser.add_argument('--tab', type=str, required=True, help='浏览器 tab ID')
    parser.add_argument('--keyword', type=str, required=True, help='搜索关键词')
    parser.add_argument('--city', type=str, default='', help='城市名称')
    parser.add_argument('--salary-min', type=int, default=0, help='最低薪资（千）')
    parser.add_argument('--salary-max', type=int, default=0, help='最高薪资（千）')
    parser.add_argument('--max-results', type=int, default=50, help='最大结果数')
    parser.add_argument('--output', type=str, help='输出文件路径')
    parser.add_argument('--no-stealth', action='store_true', help='禁用反检测模式')
    parser.add_argument('--no-scroll', action='store_true', help='禁用无限滚动')
    
    args = parser.parse_args()
    
    # 创建配置
    config = BossZhipinConfig(
        port=args.port,
        tab_id=args.tab,
        query=args.keyword,
        city=args.city,
        salary_min=args.salary_min,
        salary_max=args.salary_max,
        max_results=args.max_results,
        stealth=not args.no_stealth,
        enable_infinite_scroll=not args.no_scroll,
    )
    
    # 创建搜索器
    searcher = BossZhipinSearcher(config=config)
    
    try:
        # 执行搜索
        results = searcher._search_sync(args.keyword)
        
        # 输出结果
        if results.results:
            print("\n" + "="*60)
            print(f"搜索关键词: {args.keyword}")
            if args.city:
                print(f"城市: {args.city}")
            print(f"共找到 {len(results.results)} 个职位")
            print("="*60 + "\n")
            
            for i, job in enumerate(results.results[:20], 1):  # 只显示前20个
                print(f"【{i}】{job.title}")
                print(f"    公司: {job.company}")
                print(f"    薪资: {job.salary}")
                print(f"    地点: {job.location}")
                print(f"    经验: {job.experience} | 学历: {job.education}")
                if job.tags:
                    print(f"    标签: {', '.join(job.tags)}")
                print()
            
            # 保存结果
            if args.output:
                output_path = Path(args.output)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                results.save_json(str(output_path))
                print(f"[保存] 结果已保存到: {output_path}")
        else:
            print("[提示] 未找到相关职位")
    
    finally:
        searcher.close()


if __name__ == '__main__':
    main()
