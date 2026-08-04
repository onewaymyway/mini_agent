#!/usr/bin/env python
"""
lagou_search.py - 拉勾网职位搜索器

支持：
- 关键词搜索职位
- 城市筛选
- 薪资范围筛选
- 职位列表和详情抓取
- 反检测模式

技术难点：
- 拉勾网有反爬机制，需启用 stealth 模式
- 部分信息需要登录才能查看
- 职位详情页结构较复杂
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
class LagouConfig(SearcherConfig):
    """拉勾网搜索器专用配置"""
    # 搜索参数
    city: str = ""  # 城市名称
    salary_min: int = 0  # 最低薪资（千）
    salary_max: int = 0  # 最高薪资（千）
    industry: str = ""  # 行业
    stage: str = ""  # 融资阶段
    
    # 详情抓取
    fetch_detail: bool = False
    
    # 动态加载
    enable_infinite_scroll: bool = True
    max_scroll_pages: int = 3
    
    def to_dict(self) -> Dict:
        data = super().to_dict()
        data.update({
            'city': self.city,
            'salary_min': self.salary_min,
            'salary_max': self.salary_max,
            'industry': self.industry,
            'stage': self.stage,
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
    company_stage: str = ""  # 融资阶段
    company_size: str = ""  # 公司规模
    industry: str = ""  # 行业
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
            'company_stage': self.company_stage,
            'company_size': self.company_size,
            'industry': self.industry,
            'tags': self.tags,
            'description': self.description[:500] if self.description else '',
            'benefits': self.benefits[:10],
        })
        return data


class LagouSearcher(BaseSearcher):
    """
    拉勾网搜索器
    
    使用方式：
        config = LagouConfig(query="Python开发", city="北京", max_results=20)
        searcher = LagouSearcher(config=config)
        results = searcher.search()
        results.save_json('output/lagou_results.json')
    """
    
    BASE_URL = "https://www.lagou.com"
    SEARCH_URL = "https://www.lagou.com/jobs/list_"
    
    @property
    def source_name(self) -> str:
        return "lagou"
    
    @property
    def supported_types(self) -> List[str]:
        return ["job_search", "job_detail"]
    
    def __init__(self, config: LagouConfig = None):
        super().__init__(config or LagouConfig())
        self._session = None
        self._nav = None
        
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
        
        results = SearchResults(source="lagou", query=query)
        
        try:
            # 构建搜索 URL
            search_url = f"{self.SEARCH_URL}{query}"
            
            if self.config.city:
                search_url += f"?city={self.config.city}"
            
            logger.info(f"搜索职位: {query}, 城市: {self.config.city or '全部'}")
            
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
        
        return results.results
    
    async def get_detail(self, url: str, config: Optional[SearcherConfig] = None) -> Dict:
        """获取职位详情"""
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
            detail = self._extract_job_detail()
            
            return detail
            
        except Exception as e:
            logger.error(f"获取职位详情失败: {e}")
            return {}
    
    def _extract_jobs_from_page(self) -> List[JobInfo]:
        """
        从当前页面提取职位列表
        
        Returns:
            职位列表
        """
        js_code = """
        (function() {
            const jobs = [];
            const cards = document.querySelectorAll('.list_item_top_content, .job_card_main, [class*="job-item"]');
            
            cards.forEach(card => {
                const job = {
                    title: card.querySelector('.position_label .html')?.textContent?.trim() || 
                           card.querySelector('.position_name')?.textContent?.trim() || 
                           card.querySelector('.job-name')?.textContent?.trim() || '',
                    company: card.querySelector('.company_name')?.textContent?.trim() || 
                             card.querySelector('.company')?.textContent?.trim() || '',
                    salary: card.querySelector('.salary')?.textContent?.trim() || 
                            card.querySelector('.money')?.textContent?.trim() || '',
                    location: card.querySelector('.address')?.textContent?.trim() || 
                              card.querySelector('.job_area')?.textContent?.trim() || '',
                    experience: card.querySelector('.job_limit_exp p')?.textContent?.trim() || 
                                card.querySelector('.experience')?.textContent?.trim() || '',
                    education: card.querySelector('.job_limit_edu p')?.textContent?.trim() || 
                               card.querySelector('.education')?.textContent?.trim() || '',
                    tags: Array.from(card.querySelectorAll('.tag span, .position_tag span')).map(s => s.textContent.trim()),
                    url: card.querySelector('a')?.href || ''
                };
                if (job.title) jobs.push(job);
            });
            
            return JSON.stringify(jobs);
        })()
        """
        
        try:
            result = cmd_eval(self.session, js_code)
            if result and 'result' in result:
                job_data_list = json.loads(result['result'])
                return [self._parse_job_card(data) for data in job_data_list]
        except Exception as e:
            logger.error(f"提取职位列表失败: {e}")
        
        return []
    
    def _parse_job_card(self, card_data: Dict) -> Optional[JobInfo]:
        """
        解析职位卡片数据
        
        Args:
            card_data: 职位卡片数据
            
        Returns:
            JobInfo 对象
        """
        try:
            title = card_data.get('title', '').strip()
            if not title:
                return None
            
            job = JobInfo(
                source="lagou",
                title=title,
                url=card_data.get('url', '').strip(),
                company=card_data.get('company', '').strip(),
                salary=card_data.get('salary', '').strip(),
                location=card_data.get('location', '').strip(),
                experience=card_data.get('experience', '').strip(),
                education=card_data.get('education', '').strip(),
                tags=card_data.get('tags', []),
                scraped_at=datetime.now().isoformat()
            )
            
            return job
            
        except Exception as e:
            logger.error(f"解析职位卡片失败: {e}")
            return None
    
    def _extract_job_detail(self) -> Dict:
        """
        提取职位详情
        
        Returns:
            详情字典
        """
        js_code = """
        (function() {
            return JSON.stringify({
                title: document.querySelector('.position-content h1')?.textContent?.trim() || '',
                salary: document.querySelector('.job-name span')?.textContent?.trim() || '',
                company: document.querySelector('.company-name')?.textContent?.trim() || '',
                location: document.querySelector('.job-area')?.textContent?.trim() || '',
                experience: document.querySelector('.job-limit p:first-child')?.textContent?.trim() || '',
                education: document.querySelector('.job-limit p:nth-child(2)')?.textContent?.trim() || '',
                description: document.querySelector('.job-detail')?.textContent?.trim() || '',
                benefits: Array.from(document.querySelectorAll('.job-benefit li')).map(li => li.textContent.trim())
            });
        })()
        """
        
        try:
            result = cmd_eval(self.session, js_code)
            if result and 'result' in result:
                return json.loads(result['result'])
        except Exception as e:
            logger.error(f"提取职位详情失败: {e}")
        
        return {}
    
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
        
        existing_titles = {j.title for j in results}
        
        for i in range(self.config.max_scroll_pages):
            if len(results) >= max_results:
                break
            
            # 滚动加载
            self.session.eval_js("window.scrollBy(0, 800)")
            time.sleep(1.5)
            
            # 提取新职位
            new_jobs = self._extract_jobs_from_page()
            
            # 去重
            for job in new_jobs:
                if job.title not in existing_titles:
                    results.append(job)
                    existing_titles.add(job.title)
            
            logger.info(f"滚动加载第 {i+1} 页，当前共 {len(results)} 个职位")
        
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
        all_results = SearchResults(source="lagou", query="batch")
        
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
    
    parser = argparse.ArgumentParser(description='拉勾网职位搜索器')
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
    parser.add_argument('--detail', action='store_true', help='抓取职位详情')
    
    args = parser.parse_args()
    
    # 创建配置
    config = LagouConfig(
        port=args.port,
        tab_id=args.tab,
        query=args.keyword,
        city=args.city,
        salary_min=args.salary_min,
        salary_max=args.salary_max,
        max_results=args.max_results,
        stealth=not args.no_stealth,
        enable_infinite_scroll=not args.no_scroll,
        fetch_detail=args.detail,
    )
    
    # 创建搜索器
    searcher = LagouSearcher(config=config)
    
    try:
        # 执行搜索
        results = asyncio.run(searcher.search(args.keyword))
        
        # 输出结果
        if results:
            print("\n" + "="*60)
            print(f"搜索关键词: {args.keyword}")
            if args.city:
                print(f"城市: {args.city}")
            print(f"共找到 {len(results)} 个职位")
            print("="*60 + "\n")
            
            for i, job in enumerate(results[:20], 1):  # 只显示前20个
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
                with open(output_path, 'w', encoding='utf-8') as f:
                    json.dump([r.to_dict() for r in results], f, ensure_ascii=False, indent=2)
                print(f"[保存] 结果已保存到: {output_path}")
        else:
            print("[提示] 未找到相关职位")
    
    finally:
        asyncio.run(searcher.close())


if __name__ == '__main__':
    main()
