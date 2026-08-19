# -*- coding: utf-8 -*-
"""
Phase 1 P0 站点测试运行器

运行十个 P0 站点的搜索测试用例，支持：
- mock 模式（无需浏览器，用于框架验证）
- real 模式（需要真实浏览器）
- 并发执行
- 结果汇总报告
"""
import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# 添加项目路径
SKILL_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SKILL_DIR))

from tests.site_specific.p0_test_cases import (
    get_all_p0_test_cases,
    create_p0_test_case,
    P0_TEST_CASE_FACTORY,
)
from tests.framework.unified_test_runner import UnifiedTestRunner, TestMode
from tests.framework.load_tester import LoadTester

logger = logging.getLogger(__name__)


# 测试关键词配置
TEST_KEYWORDS = {
    "gov_cn": "人工智能政策",
    "stats_gov_cn": "GDP数据",
    "gsxt_gov_cn": "阿里巴巴",
    "boss_zhipin": "Python开发",
    "51job": "产品经理",
    "lagou": "Java工程师",
    "jd_com": "笔记本电脑",
    "cls_cn": "股市行情",
    "zhihu": "机器学习",
    "baidu_health": "感冒症状",
}


class Phase1P0TestRunner:
    """Phase 1 P0 测试运行器"""
    
    def __init__(self, mode: str = "mock", output_dir: Optional[Path] = None):
        self.mode = mode
        self.output_dir = output_dir or SKILL_DIR / "tests" / "output" / "phase1_p0_results"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.results: Dict[str, List] = {}
        self.start_time = None
        self.end_time = None
    
    async def run_all(self) -> Dict:
        """运行所有P0测试"""
        self.start_time = time.time()
        logger.info(f"Starting Phase 1 P0 tests in {self.mode} mode")
        
        all_results = []
        test_cases = get_all_p0_test_cases()
        
        # 并发执行测试
        tasks = []
        for case in test_cases:
            keyword = TEST_KEYWORDS.get(case.website, "测试关键词")
            tasks.append(self._run_single_test(case, keyword))
        
        # 并行执行
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Test failed with exception: {result}")
            else:
                all_results.append(result)
                self.results[result.website] = [result]
        
        self.end_time = time.time()
        duration = self.end_time - self.start_time
        
        # 生成汇总报告
        summary = self._generate_summary(all_results, duration)
        
        # 保存结果
        self._save_results(all_results, summary)
        
        return summary
    
    async def _run_single_test(self, case, keyword: str) -> object:
        """运行单个测试用例"""
        test_id = f"{case.website}_search_{keyword[:4]}"
        logger.info(f"Running test: {test_id}")
        
        try:
            await case.setup()
            result = await case.run_search_test(keyword, test_id)
            await case.teardown()
            return result
        except Exception as e:
            logger.error(f"Test {test_id} failed: {e}")
            return case.TestResult(
                test_id=test_id,
                website=case.website,
                scenario_id="error",
                scenario_name=f"搜索: {keyword}",
                status="error",
                duration_seconds=0,
                error_message=str(e),
            )
    
    def _generate_summary(self, results: List, duration: float) -> Dict:
        """生成汇总报告"""
        passed = sum(1 for r in results if r.status == "passed")
        failed = sum(1 for r in results if r.status == "failed")
        errors = sum(1 for r in results if r.status == "error")
        skipped = sum(1 for r in results if r.status == "skipped")
        
        avg_score = sum(r.score for r in results if r.score is not None) / max(len(results), 1)
        avg_duration = sum(r.duration_seconds for r in results) / max(len(results), 1)
        
        summary = {
            "run_timestamp": datetime.now().isoformat(),
            "mode": self.mode,
            "total_tests": len(results),
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "skipped": skipped,
            "pass_rate": round(passed / max(len(results), 1) * 100, 2),
            "avg_score": round(avg_score, 2),
            "total_duration_seconds": round(duration, 2),
            "avg_duration_seconds": round(avg_duration, 3),
            "results": [r.to_dict() for r in results],
        }
        
        return summary
    
    def _save_results(self, results: List, summary: Dict):
        """保存测试结果"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # 保存详细结果
        results_file = self.output_dir / f"phase1_p0_results_{timestamp}.json"
        results_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        logger.info(f"Results saved to {results_file}")
        
        # 保存摘要报告
        summary_file = self.output_dir / f"phase1_p0_summary_{timestamp}.md"
        summary_md = self._format_summary_md(summary)
        summary_file.write_text(summary_md, encoding="utf-8")
        logger.info(f"Summary saved to {summary_file}")
    
    def _format_summary_md(self, summary: Dict) -> str:
        """格式化为 Markdown 报告"""
        lines = [
            "# Phase 1 P0 站点测试报告\n",
            f"**运行时间**: {summary['run_timestamp']}\n",
            f"**测试模式**: {summary['mode']}\n",
            f"**总耗时**: {summary['total_duration_seconds']:.2f}秒\n\n",
            "## 测试结果概览\n",
            f"- **总测试数**: {summary['total_tests']}\n",
            f"- **通过**: {summary['passed']}\n",
            f"- **失败**: {summary['failed']}\n",
            f"- **错误**: {summary['errors']}\n",
            f"- **通过率**: {summary['pass_rate']}%\n",
            f"- **平均得分**: {summary['avg_score']}\n",
            f"- **平均耗时**: {summary['avg_duration_seconds']:.3f}秒\n\n",
            "## 各站点测试详情\n",
        ]
        
        for r in summary['results']:
            status_icon = "✓" if r['status'] == 'passed' else "✗"
            lines.append(f"### {status_icon} {r['website']}\n")
            lines.append(f"- **场景**: {r['scenario_name']}\n")
            lines.append(f"- **状态**: {r['status']}\n")
            lines.append(f"- **耗时**: {r['duration_seconds']:.3f}秒\n")
            if r.get('score'):
                lines.append(f"- **得分**: {r['score']}\n")
            if r.get('metrics', {}).get('items_count'):
                lines.append(f"- **结果数**: {r['metrics']['items_count']}\n")
            if r.get('error_message'):
                lines.append(f"- **错误**: {r['error_message']}\n")
            lines.append("")
        
        return "\n".join(lines)


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="Phase 1 P0 站点测试运行器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python run_phase1_p0_tests.py --mode mock
    python run_phase1_p0_tests.py --mode real --port 9333
    python run_phase1_p0_tests.py --site boss_zhipin --keyword Python
"""
    )
    
    parser.add_argument(
        "--mode",
        type=str,
        default="mock",
        choices=["mock", "real", "stress"],
        help="测试模式 (默认: mock)"
    )
    parser.add_argument(
        "--site",
        type=str,
        default=None,
        help="指定站点ID（可选，默认运行所有）"
    )
    parser.add_argument(
        "--keyword",
        type=str,
        default=None,
        help="指定搜索关键词（可选）"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=9333,
        help="浏览器调试端口 (默认: 9333)"
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=3,
        help="并发数 (默认: 3)"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="输出目录"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="详细输出"
    )
    
    args = parser.parse_args()
    
    # 配置日志
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    )
    
    # 创建运行器
    output_dir = Path(args.output_dir) if args.output_dir else None
    runner = Phase1P0TestRunner(mode=args.mode, output_dir=output_dir)
    
    # 运行测试
    print("\n" + "="*60)
    print("Phase 1 P0 站点测试")
    print("="*60)
    
    start = time.time()
    summary = asyncio.run(runner.run_all())
    elapsed = time.time() - start
    
    # 打印结果
    print("\n" + "="*60)
    print("测试完成")
    print("="*60)
    print(f"总测试数: {summary['total_tests']}")
    print(f"通过: {summary['passed']}")
    print(f"失败: {summary['failed']}")
    print(f"错误: {summary['errors']}")
    print(f"通过率: {summary['pass_rate']}%")
    print(f"总耗时: {elapsed:.2f}秒")
    print(f"\n结果文件已保存到: {runner.output_dir}")
    
    # 返回状态码
    sys.exit(0 if summary['failed'] == 0 and summary['errors'] == 0 else 1)


if __name__ == "__main__":
    main()
