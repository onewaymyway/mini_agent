#!/usr/bin/env python
"""
快速验证脚本 - 验证核心网站抓取功能

用于在部署前快速验证关键网站是否正常工作
"""

import sys
import json
import time
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.searchers import (
    BaiduSearcher, BingSearcher, GoogleSearcher,
    ZhihuSearchSimple, WeiboSearcher, XiaohongshuSearcher,
    BilibiliSearcher,
    GitHubSearcher, StackOverflowSearcher,
    JDSearcher, TaobaoSearcher, AmazonSearcher,
    EastmoneyGubaSearcher, XueqiuSearcher,
    ToutiaoSearcher, RedditSearcher,
    YouTubeSearcher, TwitterSearcher, LinkedInSearcher,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class QuickValidator:
    """快速验证器"""
    
    def __init__(self):
        self.results: List[Dict[str, Any]] = []
        self.test_cases = [
            # 搜索引擎
            ("baidu", BaiduSearcher, "Python编程", 5),
            ("bing", BingSearcher, "Python programming", 5),
            ("google", GoogleSearcher, "Python programming", 5),
            
            # 社交/社区
            ("zhihu", ZhihuSearchSimple, "Python", 5),
            ("weibo", WeiboSearcher, "Python", 5),
            ("xiaohongshu", XiaohongshuSearcher, "Python", 5),
            ("bilibili", BilibiliSearcher, "Python", 5),
            ("reddit", RedditSearcher, "Python", 5),
            
            # 技术社区
            ("github", GitHubSearcher, "python", 5),
            ("stackoverflow", StackOverflowSearcher, "python", 5),
            
            # 电商
            ("jd", JDSearcher, "手机", 5),
            ("taobao", TaobaoSearcher, "手机", 5),
            ("amazon", AmazonSearcher, "phone", 5),
            
            # 金融
            ("eastmoney", EastmoneyGubaSearcher, "股票", 5),
            ("xueqiu", XueqiuSearcher, "股票", 5),
            
            # 资讯
            ("toutiao", ToutiaoSearcher, "AI", 5),
            
            # 视频/社交
            ("youtube", YouTubeSearcher, "Python tutorial", 5),
            ("twitter", TwitterSearcher, "Python", 5),
            ("linkedin", LinkedInSearcher, "Python", 5),
        ]
    
    def run_test(self, name: str, searcher_class, query: str, max_results: int) -> Dict[str, Any]:
        """运行单个测试"""
        start = time.time()
        
        try:
            searcher = searcher_class()
            results = searcher.search(query, max_results=max_results)
            
            duration = time.time() - start
            success = results.success and len(results.results) > 0
            
            return {
                "name": name,
                "query": query,
                "success": success,
                "duration": duration,
                "results_count": len(results.results) if success else 0,
                "error": results.error if not success else None,
                "sample_results": [
                    {"title": r.title, "url": r.url}
                    for r in results.results[:3]
                ] if success else []
            }
        
        except Exception as e:
            duration = time.time() - start
            return {
                "name": name,
                "query": query,
                "success": False,
                "duration": duration,
                "results_count": 0,
                "error": str(e),
                "sample_results": []
            }
    
    def run_all(self) -> Dict[str, Any]:
        """运行所有测试"""
        logger.info("开始快速验证测试...")
        
        for name, searcher_class, query, max_results in self.test_cases:
            logger.info(f"测试 {name}...")
            result = self.run_test(name, searcher_class, query, max_results)
            self.results.append(result)
            
            status = "✅" if result["success"] else "❌"
            logger.info(f"  {status} {name}: {result['duration']:.2f}s, {result['results_count']} results")
        
        return self.generate_report()
    
    def generate_report(self) -> Dict[str, Any]:
        """生成报告"""
        total = len(self.results)
        success = sum(1 for r in self.results if r["success"])
        
        report = {
            "timestamp": datetime.now().isoformat(),
            "total_tests": total,
            "success_count": success,
            "failure_count": total - success,
            "success_rate": success / total if total > 0 else 0,
            "results": self.results,
            "summary": {
                "pass": [r["name"] for r in self.results if r["success"]],
                "fail": [r["name"] for r in self.results if not r["success"]]
            }
        }
        
        return report
    
    def save_report(self, output_path: str = "./test_results/quick_validation.json"):
        """保存报告"""
        report = self.generate_report()
        
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        
        logger.info(f"报告已保存: {output_path}")
        return report


def main():
    validator = QuickValidator()
    report = validator.run_all()
    validator.save_report()
    
    print("\n" + "="*60)
    print("快速验证结果")
    print("="*60)
    print(f"总测试数: {report['total_tests']}")
    print(f"成功数: {report['success_count']}")
    print(f"失败数: {report['failure_count']}")
    print(f"成功率: {report['success_rate']:.2%}")
    
    if report['summary']['fail']:
        print(f"\n失败网站: {', '.join(report['summary']['fail'])}")
    
    print("="*60)
    
    # 返回退出码
    sys.exit(0 if report['success_rate'] >= 0.95 else 1)


if __name__ == "__main__":
    main()
