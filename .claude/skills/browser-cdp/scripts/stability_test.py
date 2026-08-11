#!/usr/bin/env python
"""
稳定性测试框架 - 72小时持续测试

用于验证抓取系统的长期稳定性和成功率
"""

import sys
import json
import time
import logging
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from collections import defaultdict

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.searchers import (
    BaiduSearcher, BingSearcher, GoogleSearcher,
    ZhihuSearcher, WeiboSearcher, XiaohongshuSearcher,
    BilibiliSearcher, CSDNSearcher, JuejinSearcher,
    GitHubSearcher, StackOverflowSearcher,
    JDSearcher, TaobaoSearcher, AmazonSearcher,
    EastmoneyGubaSearcher, XueqiuSearcher,
    ToutiaoSearcher, RedditSearcher,
    YouTubeSearcher, TwitterSearcher, LinkedInSearcher,
)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('stability_test.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


@dataclass
class TestResult:
    """测试结果数据类"""
    timestamp: str
    website: str
    query: str
    success: bool
    duration: float
    results_count: int
    error: Optional[str] = None
    
    def to_dict(self) -> Dict:
        return asdict(self)


class StabilityTester:
    """稳定性测试器"""
    
    def __init__(self, output_dir: str = "./test_results"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.results: List[TestResult] = []
        self.stats = defaultdict(lambda: {"total": 0, "success": 0, "fail": 0})
        self.start_time = datetime.now()
        self.test_queries = {
            "baidu": ["AI人工智能", "Python编程", "机器学习"],
            "bing": ["AI artificial intelligence", "Python programming"],
            "google": ["AI artificial intelligence", "Python programming"],
            "zhihu": ["人工智能", "Python", "机器学习"],
            "weibo": ["AI", "人工智能", "科技"],
            "xiaohongshu": ["AI", "人工智能", "科技"],
            "bilibili": ["AI", "人工智能", "Python"],
            "csdn": ["Python", "人工智能", "机器学习"],
            "juejin": ["Python", "人工智能", "机器学习"],
            "github": ["python", "ai", "machine-learning"],
            "stackoverflow": ["python", "javascript", "react"],
            "jd": ["手机", "笔记本电脑", "耳机"],
            "taobao": ["手机", "笔记本电脑", "耳机"],
            "amazon": ["phone", "laptop", "headphones"],
            "eastmoney": ["股票", "基金", "财经"],
            "xueqiu": ["股票", "基金", "投资"],
            "toutiao": ["AI", "人工智能", "科技"],
            "reddit": ["AI", "programming", "technology"],
            "youtube": ["AI", "programming", "tutorial"],
            "twitter": ["AI", "technology", "programming"],
            "linkedin": ["AI", "technology", "programming"],
        }
        
        # 测试器映射
        self.searchers = {
            "baidu": BaiduSearcher,
            "bing": BingSearcher,
            "google": GoogleSearcher,
            "zhihu": ZhihuSearcher,
            "weibo": WeiboSearcher,
            "xiaohongshu": XiaohongshuSearcher,
            "bilibili": BilibiliSearcher,
            "csdn": CSDNSearcher,
            "juejin": JuejinSearcher,
            "github": GitHubSearcher,
            "stackoverflow": StackOverflowSearcher,
            "jd": JDSearcher,
            "taobao": TaobaoSearcher,
            "amazon": AmazonSearcher,
            "eastmoney": EastmoneyGubaSearcher,
            "xueqiu": XueqiuSearcher,
            "toutiao": ToutiaoSearcher,
            "reddit": RedditSearcher,
            "youtube": YouTubeSearcher,
            "twitter": TwitterSearcher,
            "linkedin": LinkedInSearcher,
        }
    
    def run_single_test(self, website: str, query: str) -> TestResult:
        """运行单个测试"""
        start = time.time()
        timestamp = datetime.now().isoformat()
        
        try:
            searcher_class = self.searchers.get(website)
            if not searcher_class:
                return TestResult(
                    timestamp=timestamp,
                    website=website,
                    query=query,
                    success=False,
                    duration=time.time() - start,
                    results_count=0,
                    error=f"未找到搜索器: {website}"
                )
            
            searcher = searcher_class()
            results = searcher.search(query, max_results=10)
            
            success = results.success and len(results.results) > 0
            return TestResult(
                timestamp=timestamp,
                website=website,
                query=query,
                success=success,
                duration=time.time() - start,
                results_count=len(results.results) if success else 0,
                error=results.error if not success else None
            )
        
        except Exception as e:
            return TestResult(
                timestamp=timestamp,
                website=website,
                query=query,
                success=False,
                duration=time.time() - start,
                results_count=0,
                error=str(e)
            )
    
    def run_test_cycle(self) -> List[TestResult]:
        """运行一轮测试（所有网站）"""
        cycle_results = []
        
        for website, queries in self.test_queries.items():
            for query in queries:
                result = self.run_single_test(website, query)
                cycle_results.append(result)
                self.results.append(result)
                
                # 更新统计
                self.stats[website]["total"] += 1
                if result.success:
                    self.stats[website]["success"] += 1
                else:
                    self.stats[website]["fail"] += 1
                
                logger.info(
                    f"[{website}] {query}: {'✅' if result.success else '❌'} "
                    f"({result.duration:.2f}s, {result.results_count} results)"
                )
        
        return cycle_results
    
    def generate_report(self) -> Dict[str, Any]:
        """生成测试报告"""
        total_tests = len(self.results)
        total_success = sum(1 for r in self.results if r.success)
        overall_rate = total_success / total_tests if total_tests > 0 else 0
        
        # 按网站统计
        website_stats = {}
        for website, stats in self.stats.items():
            website_stats[website] = {
                "total": stats["total"],
                "success": stats["success"],
                "fail": stats["fail"],
                "success_rate": stats["success"] / stats["total"] if stats["total"] > 0 else 0
            }
        
        # 按小时统计
        hourly_stats = defaultdict(lambda: {"total": 0, "success": 0})
        for result in self.results:
            hour = result.timestamp[:13]  # YYYY-MM-DDTHH
            hourly_stats[hour]["total"] += 1
            if result.success:
                hourly_stats[hour]["success"] += 1
        
        report = {
            "test_start": self.start_time.isoformat(),
            "test_end": datetime.now().isoformat(),
            "duration_hours": (datetime.now() - self.start_time).total_seconds() / 3600,
            "total_tests": total_tests,
            "total_success": total_success,
            "overall_success_rate": overall_rate,
            "website_stats": website_stats,
            "hourly_stats": dict(hourly_stats),
            "recent_results": [r.to_dict() for r in self.results[-100:]]
        }
        
        return report
    
    def save_report(self, filename: Optional[str] = None):
        """保存测试报告"""
        if filename is None:
            filename = f"stability_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        report = self.generate_report()
        output_path = self.output_dir / filename
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        
        logger.info(f"报告已保存: {output_path}")
        return output_path
    
    def run_continuous_test(self, hours: int = 72, interval: int = 300):
        """运行持续测试"""
        logger.info(f"开始 {hours} 小时稳定性测试，每 {interval} 秒一轮")
        
        end_time = datetime.now() + timedelta(hours=hours)
        cycle_count = 0
        
        while datetime.now() < end_time:
            cycle_count += 1
            logger.info(f"=== 第 {cycle_count} 轮测试开始 ===")
            
            self.run_test_cycle()
            self.save_report(f"cycle_{cycle_count:04d}.json")
            
            logger.info(f"第 {cycle_count} 轮测试完成，等待 {interval} 秒...")
            time.sleep(interval)
        
        # 最终报告
        final_report = self.generate_report()
        final_path = self.output_dir / f"final_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(final_path, 'w', encoding='utf-8') as f:
            json.dump(final_report, f, ensure_ascii=False, indent=2)
        
        logger.info(f"=== 测试完成 ===")
        logger.info(f"总测试数: {final_report['total_tests']}")
        logger.info(f"成功率: {final_report['overall_success_rate']:.2%}")
        logger.info(f"最终报告: {final_path}")
        
        return final_report


def main():
    parser = argparse.ArgumentParser(description='稳定性测试框架')
    parser.add_argument('--hours', type=int, default=1, help='测试时长（小时）')
    parser.add_argument('--interval', type=int, default=300, help='测试间隔（秒）')
    parser.add_argument('--output-dir', default='./test_results')
    parser.add_argument('--quick', action='store_true', help='快速测试模式（只运行一轮）')
    args = parser.parse_args()
    
    tester = StabilityTester(output_dir=args.output_dir)
    
    if args.quick:
        # 快速测试模式
        logger.info("快速测试模式：运行一轮测试")
        tester.run_test_cycle()
        tester.save_report("quick_test.json")
    else:
        # 持续测试模式
        tester.run_continuous_test(hours=args.hours, interval=args.interval)


if __name__ == "__main__":
    main()
