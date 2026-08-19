"""
browser-cdp 核心网站抓取能力测试套件

基于 v2 验收标准，测试 10 个 P0 优先级网站的抓取能力。
包含：成功率、字段完整性、响应时间、错误处理等维度。
"""

import json
import time
import asyncio
import sys
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional, Any, List

# 添加项目路径
SKILL_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SKILL_DIR / "src"))
sys.path.insert(0, str(SKILL_DIR / "tests"))


@dataclass
class TestResult:
    """单个测试用例的结果"""
    site_name: str
    query: str
    success: bool
    result_count: int
    expected_min: int
    field_completeness: float
    response_time_ms: float
    error_message: Optional[str] = None
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    def to_dict(self):
        return asdict(self)


class CoreSitesTestRunner:
    """核心网站测试执行器"""

    # 核心测试网站定义
    CORE_SITES = [
        {"name": "baidu", "class": "BaiduSearcher", "queries": ["AI人工智能", "Python编程教程"], "expected_min_results": 5},
        {"name": "google", "class": "GoogleSearcher", "queries": ["artificial intelligence", "python tutorial"], "expected_min_results": 5},
        {"name": "bing", "class": "BingSearcher", "queries": ["weather forecast", "AI news"], "expected_min_results": 5},
        {"name": "zhihu", "class": "ZhihuSearcher", "queries": ["Python编程", "人工智能"], "expected_min_results": 3},
        {"name": "github", "class": "GitHubSearcher", "queries": ["python tutorial", "machine learning"], "expected_min_results": 5},
        {"name": "csdn", "class": "CSDNSearcher", "queries": ["深度学习", "Python教程"], "expected_min_results": 3},
        {"name": "jb51", "class": "JB51Searcher", "queries": ["Python基础教程"], "expected_min_results": 3},
        {"name": "toutiao", "class": "ToutiaoSearcher", "queries": ["人工智能", "科技新闻"], "expected_min_results": 3},
        {"name": "eastmoney", "class": "EastmoneySearcher", "queries": ["股票", "基金"], "expected_min_results": 3},
        {"name": "xueqiu", "class": "XueqiuSearcher", "queries": ["投资", "股票分析"], "expected_min_results": 3},
    ]

    # 验收标准
    CRITERIA = {
        "success_rate_threshold": 0.90,
        "excellent_threshold": 0.95,
        "field_completeness_threshold": 0.80,
        "response_time_p95_ms": 3000,
        "error_recovery_rate": 0.95
    }

    # 必需字段
    REQUIRED_FIELDS = ["title", "url", "snippet", "source", "category"]

    def __init__(self):
        self.results: List[TestResult] = []
        self.start_time = None
        self.end_time = None

    def _validate_fields(self, result: dict) -> float:
        """验证必需字段的完整性"""
        if not result:
            return 0.0
        filled = sum(1 for field in self.REQUIRED_FIELDS if result.get(field))
        return filled / len(self.REQUIRED_FIELDS) if self.REQUIRED_FIELDS else 1.0

    def _get_searcher(self, site_name: str):
        """动态导入并获取搜索器实例"""
        try:
            # 尝试从 searchers 包导入
            from searchers import get_searcher
            return get_searcher(site_name)
        except ImportError:
            # 尝试直接导入
            module_name = f"src.searchers.{site_name.lower()}_search"
            try:
                module = __import__(module_name, fromlist=["Searcher"])
                return module.Searcher()
            except (ImportError, AttributeError):
                pass
        return None

    async def _test_site(self, site_config: dict) -> TestResult:
        """测试单个网站的抓取能力"""
        site_name = site_config["name"]
        queries = site_config.get("queries", [])
        expected_min = site_config.get("expected_min_results", 3)

        # 获取搜索器
        searcher = self._get_searcher(site_name)
        if searcher is None:
            return TestResult(
                site_name=site_name,
                query="N/A",
                success=False,
                result_count=0,
                expected_min=expected_min,
                field_completeness=0.0,
                response_time_ms=0,
                error_message=f"搜索器 '{site_name}' 未实现或导入失败"
            )

        # 测试每个查询
        total_results = 0
        total_completeness = 0.0
        test_queries = queries[:2] if len(queries) > 2 else queries
        response_times = []

        for query in test_queries:
            start = time.time()
            try:
                results = await searcher.search(query, num_results=5)
                elapsed = (time.time() - start) * 1000  # 转为毫秒
                response_times.append(elapsed)

                if results:
                    total_results += len(results)
                    completions = [self._validate_fields(r) for r in results[:5]]
                    total_completeness += sum(completions) / len(completions)
                    success = True
                    error = None
                else:
                    total_completeness += 0
                    success = False
                    error = "无结果返回"

            except Exception as e:
                elapsed = (time.time() - start) * 1000
                response_times.append(elapsed)
                total_results += 0
                total_completeness += 0
                success = False
                error = str(e)

        avg_response_time = sum(response_times) / len(response_times) if response_times else 0
        avg_completeness = total_completeness / max(1, len(test_queries))

        return TestResult(
            site_name=site_name,
            query="|".join(test_queries),
            success=total_results >= expected_min,
            result_count=total_results,
            expected_min=expected_min,
            field_completeness=avg_completeness,
            response_time_ms=avg_response_time,
            error_message=error
        )

    async def run_all_tests(self):
        """运行所有测试"""
        self.start_time = datetime.now()
        print(f"\n{'='*60}")
        print(f"开始执行核心网站抓取能力测试 (v2)")
        print(f"测试时间: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"测试网站数: {len(self.CORE_SITES)}")
        print(f"{'='*60}\n")

        # 串行执行（避免并发限流）
        for site in self.CORE_SITES:
            print(f"[测试] {site['name']}...")
            result = await self._test_site(site)
            self.results.append(result)

            status = "PASS" if result.success else "FAIL"
            print(f"  [{status}] 结果数: {result.result_count}/{result.expected_min}, "
                  f"完整率: {result.field_completeness:.1%}, "
                  f"耗时: {result.response_time_ms:.0f}ms")
            if result.error_message:
                print(f"  错误: {result.error_message}")

        self.end_time = datetime.now()
        duration = (self.end_time - self.start_time).total_seconds()
        print(f"\n{'='*60}")
        print(f"测试完成，耗时: {duration:.1f}s")
        print(f"{'='*60}\n")

    def generate_report(self) -> dict:
        """生成测试报告"""
        total = len(self.results)
        passed = sum(1 for r in self.results if r.success)
        failed = total - passed

        avg_completeness = sum(r.field_completeness for r in self.results) / total if total else 0
        avg_response_time = sum(r.response_time_ms for r in self.results) / total if total else 0
        pass_rate = passed / total if total else 0

        return {
            "run_id": f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "execution_date": self.start_time.isoformat() if self.start_time else "",
            "duration_seconds": (self.end_time - self.start_time).total_seconds() if self.end_time and self.start_time else 0,
            "total_cases": total,
            "passed": passed,
            "failed": failed,
            "pass_rate": pass_rate,
            "avg_field_completeness": avg_completeness,
            "avg_response_time_ms": avg_response_time,
            "criteria": self.CRITERIA,
            "status": self._determine_status(pass_rate, avg_completeness, avg_response_time),
            "details": [r.to_dict() for r in self.results]
        }

    def _determine_status(self, pass_rate: float, completeness: float, response_time: float) -> str:
        """判定整体状态"""
        if pass_rate >= self.CRITERIA["success_rate_threshold"] and \
           completeness >= self.CRITERIA["field_completeness_threshold"] and \
           response_time <= self.CRITERIA["response_time_p95_ms"]:
            return "PASS"
        elif pass_rate >= 0.70 or completeness >= 0.60:
            return "WARN"
        else:
            return "FAIL"

    def save_report(self, output_dir: str = None):
        """保存测试报告"""
        report = self.generate_report()
        output_dir = Path(output_dir or SKILL_DIR / "test_results" / "v2")
        output_dir.mkdir(parents=True, exist_ok=True)

        # 保存 JSON 报告
        json_path = output_dir / f"test_report_{report['run_id']}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        # 保存 Markdown 报告
        md_path = output_dir / f"test_report_{report['run_id']}.md"
        self._save_markdown_report(report, md_path)

        print(f"\n测试报告已保存到: {output_dir}")
        return report

    def _save_markdown_report(self, report: dict, path: Path):
        """保存 Markdown 格式报告"""
        lines = [
            "# Browser-CDP 核心网站测试报告 (v2)",
            "",
            f"**运行 ID**: {report['run_id']}",
            f"**执行时间**: {report['execution_date']}",
            f"**耗时**: {report['duration_seconds']:.1f}s",
            f"**整体状态**: {report['status']}",
            "",
            "## 执行摘要",
            "",
            "| 指标 | 数值 | 阈值 | 状态 |",
            "|------|------|------|------|",
            f"| 测试用例 | {report['total_cases']} | - | - |",
            f"| 通过 | {report['passed']} | ≥90% | {'PASS' if report['pass_rate'] >= 0.9 else 'FAIL'} |",
            f"| 失败 | {report['failed']} | - | - |",
            f"| 字段完整率 | {report['avg_field_completeness']:.1%} | ≥80% | {'PASS' if report['avg_field_completeness'] >= 0.8 else 'WARN'} |",
            f"| 平均响应时间 | {report['avg_response_time_ms']:.0f}ms | <3000ms | {'PASS' if report['avg_response_time_ms'] < 3000 else 'WARN'} |",
            "",
            "## 详细结果",
            "",
            "| 网站 | 查询 | 结果数 | 完整率 | 耗时 | 状态 |",
            "|------|------|--------|--------|------|------|",
        ]

        for detail in report['details']:
            status = "PASS" if detail['success'] else "FAIL"
            lines.append(
                f"| {detail['site_name']} | {detail['query'][:30]}... | "
                f"{detail['result_count']}/{detail['expected_min']} | "
                f"{detail['field_completeness']:.1%} | "
                f"{detail['response_time_ms']:.0f}ms | {status} |"
            )

        lines.extend([
            "",
            "## 失败分析",
            "",
        ])

        failed_items = [d for d in report['details'] if not d['success']]
        if failed_items:
            for item in failed_items:
                lines.append(f"- **{item['site_name']}**: {item.get('error_message', '无结果')}")
        else:
            lines.append("所有测试通过 ✅")

        lines.extend([
            "",
            "---",
            f"*报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
        ])

        path.write_text("\n".join(lines), encoding="utf-8")


async def main():
    """主入口"""
    runner = CoreSitesTestRunner()
    await runner.run_all_tests()
    report = runner.save_report()

    # 输出汇总到控制台
    print(f"\n测试结果汇总:")
    print(f"   通过率: {report['pass_rate']:.1%}")
    print(f"   字段完整率: {report['avg_field_completeness']:.1%}")
    print(f"   平均响应时间: {report['avg_response_time_ms']:.0f}ms")
    print(f"   整体状态: {report['status']}")


if __name__ == "__main__":
    asyncio.run(main())
