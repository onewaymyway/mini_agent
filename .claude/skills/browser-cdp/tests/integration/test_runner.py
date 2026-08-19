#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
核心操作链路自动化测试 - 测试运行器

使用说明：
1. python test_runner.py          - 运行所有测试
2. python test_runner.py --quick  - 快速模式（仅关键测试）
3. python test_runner.py --debug  - 调试模式（显示详细日志）
"""

import asyncio
import argparse
import sys
from pathlib import Path
from datetime import datetime

SKILL_DIR = Path(__file__).parent.parent.parent
sys.path.insert(0, str(SKILL_DIR))

def print_header():
    print("""
╔══════════════════════════════════════════════════════════════╗
║           browser-cdp 核心操作链路自动化测试                  ║
║                    Step 3/5 - 测试开发                        ║
╚══════════════════════════════════════════════════════════════╝
""")

def print_footer(results):
    total = len(results)
    passed = sum(1 for r in results if r.get('status') == 'passed')
    failed = sum(1 for r in results if r.get('status') == 'failed')
    errors = sum(1 for r in results if r.get('status') == 'error')
    skipped = sum(1 for r in results if r.get('status') == 'skipped')
    success_rate = (passed / (total - skipped)) * 100 if (total - skipped) > 0 else 0
    
    print("\n" + "="*60)
    print("\U0001f4ca 测试执行总结")
    print("="*60)
    print(f"  TotaL:   {total} 个测试")
    print(f"  Checkmark: {passed} 个")
    print(f"  X: 失败: {failed} 个")
    print(f"  Warning: 错误: {errors} 个")
    print(f"  Circle: 跳过: {skipped} 个")
    print(f"  成功率: {success_rate:.1f}%")
    print("="*60)
    
    if success_rate >= 95:
        print("OK: 达到目标成功率 (>=95%)")
    elif success_rate >= 90:
        print("Warning: 接近目标，建议优化失败项")
    else:
        print("X: 未达到目标，需要修复错误项")
    print()

async def run_core_tests(quick_mode: bool = False):
    from tests.integration.test_core_operations import TestRunner, TestConfig
    config = TestConfig(headless=True, timeout_ms=15000)
    runner = TestRunner(config)
    async with runner:
        results = await runner.run_all_tests()
        report = runner.generate_report()
        report_path = runner.save_report()
    return results, report, report_path

async def run_enhanced_tests():
    from tests.integration.test_enhanced_scenarios import EnhancedTestRunner
    runner = EnhancedTestRunner(headless=True)
    results = await runner.run_all()
    report_path = runner.save_report()
    report = runner.generate_report()
    return results, report, report_path

def main():
    parser = argparse.ArgumentParser(description='browser-cdp 核心操作链路自动化测试')
    parser.add_argument('--quick', action='store_true', help='快速模式')
    parser.add_argument('--debug', action='store_true', help='调试模式')
    parser.add_argument('--enhanced', action='store_true', help='运行增强测试')
    args = parser.parse_args()
    
    print_header()
    
    import logging
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(level=log_level, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    all_results = []
    report_paths = []
    
    try:
        print(">▶ 开始执行核心操作链路测试...")
        core_results, core_report, core_path = asyncio.run(run_core_tests(quick_mode=args.quick))
        all_results.extend([
            {'test_id': r.test_id, 'test_name': r.test_name, 'category': r.category, 
             'status': r.status, 'duration_ms': r.duration_ms, 'error_msg': r.error_msg,
             'metrics': r.metrics}
            for r in core_results
        ])
        report_paths.append(core_path)
        print(f"  核心测试完成，报告已保存至: {core_path}")
        
        if not args.quick or args.enhanced:
            print("\n>▶ 开始执行增强场景测试...")
            enh_results, enh_report, enh_path = asyncio.run(run_enhanced_tests())
            report_paths.append(enh_path)
            print(f"  增强测试完成，报告已保存至: {enh_path}")
        
        print_footer(all_results)
        
        total = len(all_results)
        errors = sum(1 for r in all_results if r.get('status') == 'error')
        error_rate = (errors / total) * 100 if total > 0 else 0
        
        if error_rate < 5:
            print("OK 测试通过：错误率低于5%目标")
            return 0
        else:
            print(f"X 测试未通过：错误率 {error_rate:.1f}% 高于5%目标")
            print("\n错误详情：")
            for r in all_results:
                if r.get('status') == 'error':
                    print(f"  - {r['test_name']}: {r.get('error_msg', '')[:80]}")
            return 1
            
    except Exception as e:
        print(f"\nX 测试执行失败: {e}")
        import traceback
        traceback.print_exc()
        return 2

if __name__ == "__main__":
    sys.exit(main())