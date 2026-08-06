#!/usr/bin/env python3
"""
网站操作能力评估演示脚本

演示如何使用评估工具进行网站操作能力评估。
此脚本使用模拟数据进行评估，实际使用时需要替换为真实浏览器采集数据。
"""

import sys
import json
from pathlib import Path
from datetime import datetime

# 添加 skill 目录到路径
SKILL_DIR = Path(__file__).parent.parent
SRC_DIR = SKILL_DIR / "src"
sys.path.insert(0, str(SKILL_DIR))

from src.evaluators.website_evaluator import WebsiteEvaluator
from src.evaluators.performance_evaluator import PerformanceEvaluator
from src.evaluators.element_evaluator import ElementEvaluator
from src.evaluators.success_rate_evaluator import SuccessRateEvaluator
from src.evaluators.anti_detection_evaluator import AntiDetectionEvaluator
from src.evaluators.stability_evaluator import StabilityEvaluator
from src.evaluators.error_recovery_evaluator import ErrorRecoveryEvaluator


def demo_single_dimension():
    """演示单个维度评估"""
    print("\n" + "="*60)
    print("演示：单个维度评估")
    print("="*60)
    
    # 页面加载能力评估
    print("\n【页面加载能力评估】")
    perf_eval = PerformanceEvaluator()
    perf_result = perf_eval.evaluate({
        "total_attempts": 10,
        "successful_visits": 9,
        "first_contentful_paint": 2.5,
        "page_load_time": 8.2,
        "total_timeouts": 2,
        "handled_timeouts": 2,
    })
    print(f"  得分: {perf_result['score']:.1f}")
    for metric in perf_result['metrics']:
        print(f"    - {metric['name']}: {metric['value']}{metric['unit']}")
    
    # 元素定位能力评估
    print("\n【元素定位能力评估】")
    elem_eval = ElementEvaluator()
    elem_result = elem_eval.evaluate({
        "total_locate_attempts": 20,
        "successful_locates": 18,
        "total_interaction_attempts": 15,
        "successful_interactions": 13,
        "total_dynamic_elements": 10,
        "identified_dynamic_elements": 8,
        "verified_strategies": 5,
        "total_strategies": 7,
    })
    print(f"  得分: {elem_result['score']:.1f}")
    for metric in elem_result['metrics']:
        print(f"    - {metric['name']}: {metric['value']}{metric['unit']}")
    
    # 数据提取能力评估
    print("\n【数据提取能力评估】")
    scrap_eval = SuccessRateEvaluator()
    scrap_result = scrap_eval.evaluate({
        "total_extractions": 50,
        "correct_extractions": 45,
        "expected_fields": 10,
        "extracted_fields": 8,
        "total_structured_attempts": 20,
        "successful_structured": 16,
    })
    print(f"  得分: {scrap_result['score']:.1f}")
    for metric in scrap_result['metrics']:
        print(f"    - {metric['name']}: {metric['value']}{metric['unit']}")
    
    # 反检测能力评估
    print("\n【反检测能力评估】")
    anti_eval = AntiDetectionEvaluator()
    anti_result = anti_eval.evaluate({
        "total_crawl_triggers": 10,
        "successful_bypasses": 7,
        "total_captchas": 3,
        "passed_captchas": 2,
        "total_checks": 20,
        "identified_as_bot": 3,
        "total_operations": 50,
        "human_like_operations": 40,
    })
    print(f"  得分: {anti_result['score']:.1f}")
    for metric in anti_result['metrics']:
        print(f"    - {metric['name']}: {metric['value']}{metric['unit']}")
    
    # 稳定性评估
    print("\n【稳定性评估】")
    stab_eval = StabilityEvaluator()
    stab_result = stab_eval.evaluate({
        "total_executions": 10,
        "consistent_executions": 9,
        "total_errors": 5,
        "successful_recoveries": 4,
        "total_runtime": 1800,
        "connected_time": 1750,
        "start_memory": 100,
        "end_memory": 108,
        "runtime_hours": 0.5,
    })
    print(f"  得分: {stab_result['score']:.1f}")
    for metric in stab_result['metrics']:
        print(f"    - {metric['name']}: {metric['value']}{metric['unit']}")
    
    # 错误恢复评估
    print("\n【错误恢复能力评估】")
    error_eval = ErrorRecoveryEvaluator()
    error_result = error_eval.evaluate({
        "total_errors": 10,
        "correctly_classified": 9,
        "total_retries": 15,
        "successful_retries": 11,
        "total_fallback_attempts": 5,
        "successful_fallbacks": 3,
    })
    print(f"  得分: {error_result['score']:.1f}")
    for metric in error_result['metrics']:
        print(f"    - {metric['name']}: {metric['value']}{metric['unit']}")


def demo_full_evaluation():
    """演示完整评估流程"""
    print("\n" + "="*60)
    print("演示：完整评估流程")
    print("="*60)
    
    # 初始化评估器
    evaluator = WebsiteEvaluator(
        website_url="https://www.baidu.com",
        website_name="百度"
    )
    
    # 准备评估数据
    context = {
        "performance": {
            "total_attempts": 10,
            "successful_visits": 9,
            "first_contentful_paint": 2.5,
            "page_load_time": 8.2,
            "total_timeouts": 2,
            "handled_timeouts": 2,
        },
        "element": {
            "total_locate_attempts": 20,
            "successful_locates": 18,
            "total_interaction_attempts": 15,
            "successful_interactions": 13,
            "total_dynamic_elements": 10,
            "identified_dynamic_elements": 8,
            "verified_strategies": 5,
            "total_strategies": 7,
        },
        "scraping": {
            "total_extractions": 50,
            "correct_extractions": 45,
            "expected_fields": 10,
            "extracted_fields": 8,
            "total_structured_attempts": 20,
            "successful_structured": 16,
        },
        "anti_detection": {
            "total_crawl_triggers": 10,
            "successful_bypasses": 7,
            "total_captchas": 3,
            "passed_captchas": 2,
            "total_checks": 20,
            "identified_as_bot": 3,
            "total_operations": 50,
            "human_like_operations": 40,
        },
        "stability": {
            "total_executions": 10,
            "consistent_executions": 9,
            "total_errors": 5,
            "successful_recoveries": 4,
            "total_runtime": 1800,
            "connected_time": 1750,
            "start_memory": 100,
            "end_memory": 108,
            "runtime_hours": 0.5,
        },
        "error_recovery": {
            "total_errors": 10,
            "correctly_classified": 9,
            "total_retries": 15,
            "successful_retries": 11,
            "total_fallback_attempts": 5,
            "successful_fallbacks": 3,
        },
    }
    
    # 执行评估
    result = evaluator.evaluate(context)
    
    # 输出结果
    print(f"\n评估网站: {result['website_name']}")
    print(f"评估时间: {result.get('report_date', 'N/A')}")
    print(f"综合评分: {result['overall_score']}")
    print(f"等级: {result['grade']}")
    
    print("\n各维度得分:")
    for dim_name, dim_result in result['dimensions'].items():
        print(f"  {dim_name}: {dim_result['score']:.1f} (权重 {dim_result['weight']:.0%}, 加权得分 {dim_result['weighted_score']:.2f})")
    
    # 输出关键发现
    if result.get('findings'):
        print("\n关键发现:")
        for finding in result['findings']:
            print(f"  - {finding}")
    
    # 输出改进建议
    if result.get('recommendations'):
        print("\n改进建议:")
        for rec in result['recommendations']:
            print(f"  - {rec}")
    
    return result


def demo_batch_evaluation():
    """演示批量评估"""
    print("\n" + "="*60)
    print("演示：批量评估")
    print("="*60)
    
    from src.evaluators.website_evaluator import batch_evaluate
    
    # 定义要评估的网站列表
    sites = [
        {
            "url": "https://www.baidu.com",
            "name": "百度",
            "context": {
                "performance": {"total_attempts": 10, "successful_visits": 9, "first_contentful_paint": 2.5, "page_load_time": 8.2, "total_timeouts": 2, "handled_timeouts": 2},
                "element": {"total_locate_attempts": 20, "successful_locates": 18, "total_interaction_attempts": 15, "successful_interactions": 13, "total_dynamic_elements": 10, "identified_dynamic_elements": 8, "verified_strategies": 5, "total_strategies": 7},
                "scraping": {"total_extractions": 50, "correct_extractions": 45, "expected_fields": 10, "extracted_fields": 8, "total_structured_attempts": 20, "successful_structured": 16},
                "anti_detection": {"total_crawl_triggers": 10, "successful_bypasses": 7, "total_captchas": 3, "passed_captchas": 2, "total_checks": 20, "identified_as_bot": 3, "total_operations": 50, "human_like_operations": 40},
                "stability": {"total_executions": 10, "consistent_executions": 9, "total_errors": 5, "successful_recoveries": 4, "total_runtime": 1800, "connected_time": 1750, "start_memory": 100, "end_memory": 108, "runtime_hours": 0.5},
                "error_recovery": {"total_errors": 10, "correctly_classified": 9, "total_retries": 15, "successful_retries": 11, "total_fallback_attempts": 5, "successful_fallbacks": 3},
            }
        },
        {
            "url": "https://www.bing.com",
            "name": "Bing",
            "context": {
                "performance": {"total_attempts": 10, "successful_visits": 10, "first_contentful_paint": 1.8, "page_load_time": 6.5, "total_timeouts": 1, "handled_timeouts": 1},
                "element": {"total_locate_attempts": 20, "successful_locates": 19, "total_interaction_attempts": 15, "successful_interactions": 14, "total_dynamic_elements": 10, "identified_dynamic_elements": 9, "verified_strategies": 6, "total_strategies": 7},
                "scraping": {"total_extractions": 50, "correct_extractions": 48, "expected_fields": 10, "extracted_fields": 9, "total_structured_attempts": 20, "successful_structured": 18},
                "anti_detection": {"total_crawl_triggers": 10, "successful_bypasses": 8, "total_captchas": 2, "passed_captchas": 2, "total_checks": 20, "identified_as_bot": 2, "total_operations": 50, "human_like_operations": 45},
                "stability": {"total_executions": 10, "consistent_executions": 10, "total_errors": 3, "successful_recoveries": 3, "total_runtime": 1800, "connected_time": 1790, "start_memory": 95, "end_memory": 100, "runtime_hours": 0.5},
                "error_recovery": {"total_errors": 10, "correctly_classified": 10, "total_retries": 12, "successful_retries": 11, "total_fallback_attempts": 4, "successful_fallbacks": 4},
            }
        },
        {
            "url": "https://www.zhihu.com",
            "name": "知乎",
            "context": {
                "performance": {"total_attempts": 10, "successful_visits": 8, "first_contentful_paint": 3.2, "page_load_time": 12.5, "total_timeouts": 3, "handled_timeouts": 2},
                "element": {"total_locate_attempts": 20, "successful_locates": 16, "total_interaction_attempts": 15, "successful_interactions": 12, "total_dynamic_elements": 10, "identified_dynamic_elements": 7, "verified_strategies": 4, "total_strategies": 7},
                "scraping": {"total_extractions": 50, "correct_extractions": 40, "expected_fields": 10, "extracted_fields": 7, "total_structured_attempts": 20, "successful_structured": 14},
                "anti_detection": {"total_crawl_triggers": 10, "successful_bypasses": 5, "total_captchas": 5, "passed_captchas": 2, "total_checks": 20, "identified_as_bot": 6, "total_operations": 50, "human_like_operations": 35},
                "stability": {"total_executions": 10, "consistent_executions": 8, "total_errors": 8, "successful_recoveries": 5, "total_runtime": 1800, "connected_time": 1700, "start_memory": 110, "end_memory": 125, "runtime_hours": 0.5},
                "error_recovery": {"total_errors": 10, "correctly_classified": 8, "total_retries": 18, "successful_retries": 12, "total_fallback_attempts": 6, "successful_fallbacks": 3},
            }
        },
    ]
    
    # 批量评估
    results = batch_evaluate(sites)
    
    # 输出汇总报告
    print("\n=== 批量评估汇总 ===")
    print(f"{'网站':<10} {'综合评分':<10} {'等级':<10} {'页面加载':<10} {'元素定位':<10} {'数据提取':<10} {'反检测':<10} {'稳定性':<10}")
    print("-" * 80)
    
    for result in results:
        dims = result.get('dimensions', {})
        print(f"{result['website_name']:<10} {result['overall_score']:<10.1f} {result['grade']:<10} "
              f"{dims.get('performance', {}).get('score', 0):<10.1f} "
              f"{dims.get('element', {}).get('score', 0):<10.1f} "
              f"{dims.get('scraping', {}).get('score', 0):<10.1f} "
              f"{dims.get('anti_detection', {}).get('score', 0):<10.1f} "
              f"{dims.get('stability', {}).get('score', 0):<10.1f}")
    
    return results


def demo_report_generation():
    """演示报告生成"""
    print("\n" + "="*60)
    print("演示：报告生成")
    print("="*60)
    
    evaluator = WebsiteEvaluator(
        website_url="https://www.baidu.com",
        website_name="百度"
    )
    
    context = {
        "performance": {"total_attempts": 10, "successful_visits": 9, "first_contentful_paint": 2.5, "page_load_time": 8.2, "total_timeouts": 2, "handled_timeouts": 2},
        "element": {"total_locate_attempts": 20, "successful_locates": 18, "total_interaction_attempts": 15, "successful_interactions": 13, "total_dynamic_elements": 10, "identified_dynamic_elements": 8, "verified_strategies": 5, "total_strategies": 7},
        "scraping": {"total_extractions": 50, "correct_extractions": 45, "expected_fields": 10, "extracted_fields": 8, "total_structured_attempts": 20, "successful_structured": 16},
        "anti_detection": {"total_crawl_triggers": 10, "successful_bypasses": 7, "total_captchas": 3, "passed_captchas": 2, "total_checks": 20, "identified_as_bot": 3, "total_operations": 50, "human_like_operations": 40},
        "stability": {"total_executions": 10, "consistent_executions": 9, "total_errors": 5, "successful_recoveries": 4, "total_runtime": 1800, "connected_time": 1750, "start_memory": 100, "end_memory": 108, "runtime_hours": 0.5},
        "error_recovery": {"total_errors": 10, "correctly_classified": 9, "total_retries": 15, "successful_retries": 11, "total_fallback_attempts": 5, "successful_fallbacks": 3},
    }
    
    result = evaluator.evaluate(context)
    
    # 生成 Markdown 报告
    markdown_report = evaluator.get_markdown_report()
    print("\nMarkdown 报告预览:")
    print("-" * 60)
    print(markdown_report[:1000] + "...")
    
    # 保存报告
    output_dir = SKILL_DIR / "output" / "eval_results"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    json_path = output_dir / f"eval_baidu_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    md_path = output_dir / f"eval_baidu_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    
    evaluator.save_report(str(json_path), format="json")
    evaluator.save_report(str(md_path), format="markdown")
    
    print(f"\n报告已保存:")
    print(f"  JSON: {json_path}")
    print(f"  Markdown: {md_path}")


def main():
    """主函数"""
    print("\n" + "#"*60)
    print("# 网站操作能力评估工具演示")
    print("# 创建时间: 2026-08-06")
    print("#"*60)
    
    # 演示单个维度评估
    demo_single_dimension()
    
    # 演示完整评估流程
    demo_full_evaluation()
    
    # 演示批量评估
    demo_batch_evaluation()
    
    # 演示报告生成
    demo_report_generation()
    
    print("\n" + "="*60)
    print("演示完成！")
    print("="*60)
    print("\n使用说明:")
    print("1. 修改 context 参数中的评估数据为真实浏览器采集数据")
    print("2. 运行脚本: python scripts/run_eval_demo.py")
    print("3. 查看生成的报告: output/eval_results/")
    print("\n参考文档:")
    print("- docs/evaluation-standards-v2.md (评估标准)")
    print("- docs/evaluation-tools-guide.md (工具使用指南)")
    print("- references/assessment-metrics-v2.md (评估指标体系)")


if __name__ == "__main__":
    main()