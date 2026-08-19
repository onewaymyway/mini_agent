# -*- coding: utf-8 -*-
"""
Phase1 站点自动化测试执行脚本

执行十个 P0 站点的核心搜索测试用例，生成详细报告。
"""
import asyncio
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

# 添加 skill 路径
SKILL_DIR = Path(__file__).parent.parent.parent
sys.path.insert(0, str(SKILL_DIR))

from tests.fixtures.test_config_loader import load_test_config
from tests.site_specific.search_tests import (
    GenericSearchTestCase,
    create_test_case,
    TEST_CASE_FACTORY,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(Path(__file__).parent.parent / "logs" / "phase1_tests.log", encoding='utf-8'),
    ]
)
logger = logging.getLogger(__name__)


async def run_single_search_test(test_case: GenericSearchTestCase, keyword: str, test_id: str) -> dict:
    """运行单次搜索测试"""
    result = await test_case.run_search_test(keyword, test_id)
    return {
        "test_id": test_id,
        "site": test_case.site_name,
        "keyword": keyword,
        "status": result.status,
        "duration": round(result.duration_seconds, 3),
        "error": result.error_message,
        "metrics": result.metrics,
    }


async def run_site_test_suite(site_id: str, keywords: list, max_concurrent: int = 3) -> dict:
    """为单个站点运行完整测试套件"""
    logger.info(f"启动站点测试: {site_id}")
    
    # 创建测试用例实例
    test_case = create_test_case(site_id)
    if not test_case:
        return {"site_id": site_id, "status": "error", "message": "无法创建测试用例"}
    
    # 并发执行搜索测试
    tasks = []
    for i, keyword in enumerate(keywords, 1):
        task = run_single_search_test(test_case, keyword, f"{site_id}_search_{i}")
        tasks.append(task)
    
    # 限制并发数
    semaphore = asyncio.Semaphore(max_concurrent)
    
    async def limited_task(task):
        async with semaphore:
            return await task
    
    results = await asyncio.gather(*[limited_task(t) for t in tasks])
    
    passed = sum(1 for r in results if r['status'] == 'passed')
    failed = sum(1 for r in results if r['status'] == 'failed')
    errors = sum(1 for r in results if r['status'] == 'error')
    
    return {
        "site_id": site_id,
        "site_name": test_case.site_name,
        "keywords_tested": len(keywords),
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "results": results,
        "timestamp": datetime.now().isoformat(),
    }


async def main():
    """主执行函数"""
    start_time = time.time()
    
    # 加载配置
    try:
        config = load_test_config()
        logger.info(f"已加载配置: {len(config.phase1_sites)} 个 Phase1 站点")
    except Exception as e:
        logger.error(f"加载配置失败: {e}")
        return 1
    
    # 创建输出目录
    output_dir = SKILL_DIR / "output" / "test_reports" / "phase1"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 定义测试关键词（从配置中获取）
    test_keywords = {
        "gov_cn": ["国务院", "政策文件", "国民经济"],
        "stats_gov_cn": ["GDP", "CPI", "人口"],
        "gsxt_gov_cn": ["阿里巴巴", "腾讯", "华为"],
        "boss_zhipin": ["Python", "Java", "产品经理"],
        "51job": ["数据分析", "运营", "产品"],
        "lagou": ["前端", "后端", "算法"],
        "jd_com": ["手机", "笔记本电脑", "耳机"],
        "cls_cn": ["股市", "基金", "宏观经济"],
        "zhihu": ["AI", "机器学习", "Python"],
        "baidu_health": ["感冒", "高血压", "糖尿病"],
    }
    
    # 并行执行所有站点测试
    logger.info("开始并行执行所有站点测试...")
    tasks = []
    for site in config.phase1_sites:
        keywords = test_keywords.get(site.site_id, ["测试关键词"])
        tasks.append(run_site_test_suite(site.site_id, keywords))
    
    site_results = await asyncio.gather(*tasks)
    
    # 汇总结果
    total_tests = sum(r['keywords_tested'] for r in site_results)
    total_passed = sum(r['passed'] for r in site_results)
    total_failed = sum(r['failed'] for r in site_results)
    total_errors = sum(r['errors'] for r in site_results)
    
    overall_summary = {
        "run_timestamp": datetime.now().isoformat(),
        "total_duration_seconds": round(time.time() - start_time, 2),
        "total_sites": len(site_results),
        "total_test_cases": total_tests,
        "passed": total_passed,
        "failed": total_failed,
        "errors": total_errors,
        "skipped": 0,
        "pass_rate": round(total_passed / max(total_tests, 1) * 100, 2),
        "site_details": site_results,
    }
    
    # 保存结果
    summary_path = output_dir / f"phase1_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    summary_path.write_text(
        json.dumps(overall_summary, ensure_ascii=False, indent=2, default=str),
        encoding='utf-8'
    )
    
    # 保存各站点详细结果
    for site_result in site_results:
        site_path = output_dir / f"phase1_site_{site_result['site_id']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        site_path.write_text(
            json.dumps(site_result, ensure_ascii=False, indent=2, default=str),
            encoding='utf-8'
        )
    
    # 输出摘要
    print("\n" + "=" * 60)
    print("Phase1 测试执行摘要")
    print("=" * 60)
    print(f"总站点数: {overall_summary['total_sites']}")
    print(f"总测试用例: {overall_summary['total_test_cases']}")
    print(f"通过: {overall_summary['passed']}")
    print(f"失败: {overall_summary['failed']}")
    print(f"错误: {overall_summary['errors']}")
    print(f"跳过: {overall_summary['skipped']}")
    print(f"通过率: {overall_summary['pass_rate']}%")
    print(f"执行时长: {overall_summary['total_duration_seconds']}秒")
    print(f"\n详细报告: {summary_path}")
    print("=" * 60)
    
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
