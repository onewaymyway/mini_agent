"""
自动化评估主入口脚本

用法：
  python run_auto_evaluation.py --site baidu
  python run_auto_evaluation.py --priority P0
  python run_auto_evaluation.py --all
  python run_auto_evaluation.py --site baidu --compatibility
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.eval_config import WEBSITE_CONFIGS, get_website_by_name, get_websites_by_priority, ensure_output_dirs
from scripts.auto_evaluator import AutoEvaluator, run_auto_evaluation
from scripts.compatibility_checker import CompatibilityChecker, run_compatibility_check
from scripts.report_generator import (
    generate_evaluation_report,
    generate_compatibility_report,
    generate_batch_report,
    generate_comparison_report,
)

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="browser-cdp 自动化评估工具")
    parser.add_argument("--site", "-s", help="网站名称 (e.g. baidu)")
    parser.add_argument("--priority", "-p", choices=["P0", "P1", "P2", "P3"])
    parser.add_argument("--all", "-a", action="store_true", help="评估所有网站")
    parser.add_argument("--compatibility", "-c", action="store_true", help="执行兼容性检测")
    parser.add_argument("--output-dir", "-o", default=None)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    output_dir = Path(args.output_dir) if args.output_dir else ensure_output_dirs()[0]
    output_dir.mkdir(parents=True, exist_ok=True)

    # 获取网站列表
    if args.site:
        config = get_website_by_name(args.site)
        if not config:
            logger.error(f"网站未找到: {args.site}")
            sys.exit(1)
        websites = [config]
    elif args.priority:
        websites = get_websites_by_priority(args.priority)
    elif args.all:
        websites = WEBSITE_CONFIGS
    else:
        logger.error("请指定 --site、--priority 或 --all")
        sys.exit(1)

    logger.info(f"准备评估 {len(websites)} 个网站，输出目录: {output_dir}")

    if args.compatibility:
        # 执行兼容性检测
        checker = CompatibilityChecker(output_dir=output_dir)
        results = []
        for config in websites:
            result = checker.check_website(config)
            checker.save_results(result)
            results.append(result)

        # 生成汇总报告
        summary_path = output_dir / "compatibility_summary.md"
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write(checker.generate_summary_report())

        logger.info(f"兼容性检测完成，汇总报告: {summary_path}")
        print(f"\n兼容性检测完成，共 {len(results)} 个网站")
        for r in results:
            print(f"  {r['website_name']}: {r['compatibility_score']}/100 ({r['compatibility_level']})")
    else:
        # 执行评估
        evaluator = AutoEvaluator(output_dir=output_dir)
        results = []
        for config in websites:
            result = evaluator.evaluate_website(config)
            evaluator.save_results(result)
            results.append(result)

        # 生成汇总报告
        summary_path = output_dir / "evaluation_summary.md"
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write(evaluator.generate_summary_report())

        # 生成对比报告
        comparison_path = output_dir / "comparison_websites.md"
        with open(comparison_path, "w", encoding="utf-8") as f:
            f.write(generate_comparison_report(results, "websites", output_dir=output_dir).read() if False else evaluator.generate_summary_report())

        logger.info(f"评估完成，汇总报告: {summary_path}")
        print(f"\n评估完成，共 {len(results)} 个网站")
        for r in results:
            print(f"  {r['website_name']}: {r['overall_score']}/100 ({r['grade']})")


if __name__ == "__main__":
    main()
