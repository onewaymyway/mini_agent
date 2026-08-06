"""
兼容性测试框架主入口

提供命令行接口，支持运行兼容性测试。
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from .models import Category, Priority, WebsiteConfig
from .scheduler import TestScheduler
from .collector import ResultCollector
from .evaluator import EvaluationMetrics
from .test_cases import get_all_websites, get_all_test_cases

logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False) -> None:
    """配置日志"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )


def parse_args() -> argparse.Namespace:
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="网站兼容性测试框架",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 运行所有测试
  python -m compatibility_test

  # 运行电商类测试
  python -m compatibility_test --category ECOM

  # 运行 P0 优先级测试
  python -m compatibility_test --priority P0

  # 运行指定网站测试
  python -m compatibility_test --websites 京东 淘宝

  # 详细输出
  python -m compatibility_test --verbose
""",
    )

    parser.add_argument(
        "--category",
        type=str,
        choices=[c.value for c in Category],
        help="按分类运行测试",
    )
    parser.add_argument(
        "--priority",
        type=str,
        choices=[p.value for p in Priority],
        help="按优先级运行测试",
    )
    parser.add_argument(
        "--websites",
        nargs="+",
        help="指定网站名称运行测试",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="test_reports",
        help="测试结果输出目录",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=3,
        help="最大并发数 (默认: 3)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="详细输出",
    )

    return parser.parse_args()


async def run_tests(args: argparse.Namespace) -> int:
    """
    运行兼容性测试

    Args:
        args: 命令行参数

    Returns:
        退出码 (0 表示成功)
    """
    # 初始化组件
    scheduler = TestScheduler(max_concurrency=args.concurrency)
    collector = ResultCollector(output_dir=args.output_dir)
    evaluator = EvaluationMetrics()

    # 获取网站配置
    all_websites = get_all_websites()
    all_test_cases = get_all_test_cases()

    # 过滤网站
    if args.category:
        websites = [w for w in all_websites if w.category.value == args.category]
    elif args.priority:
        websites = [w for w in all_websites if w.priority == Priority(args.priority)]
    elif args.websites:
        websites = [w for w in all_websites if w.name in args.websites]
    else:
        websites = all_websites

    if not websites:
        logger.error("没有找到匹配的网站配置")
        return 1

    logger.info(f"准备运行 {len(websites)} 个网站的测试")

    # 注册网站
    for website in websites:
        scheduler.register_website(website)

    # 运行测试
    total_runs = 0
    total_cases = 0
    total_passed = 0

    for website in websites:
        logger.info(f"开始测试: {website.name}")

        test_cases = all_test_cases.get(website.name, [])
        if not test_cases:
            logger.warning(f"网站 {website.name} 没有配置测试用例，跳过")
            continue

        try:
            run = await scheduler.run_test(website.name, test_cases)
            collector.collect(run)

            # 评估结果
            evaluation = evaluator.evaluate_run(run)
            logger.info(f"评估结果: {evaluation['summary']}")

            total_runs += 1
            total_cases += run.total_cases
            total_passed += run.passed_cases

        except Exception as e:
            logger.error(f"测试 {website.name} 失败: {e}")

    # 输出汇总
    logger.info("=" * 50)
    logger.info("测试完成汇总")
    logger.info(f"  总运行次数: {total_runs}")
    logger.info(f"  总用例数: {total_cases}")
    logger.info(f"  通过数: {total_passed}")
    logger.info(f"  成功率: {total_passed / total_cases:.1%}" if total_cases > 0 else "  成功率: N/A")
    logger.info("=" * 50)

    return 0


def main() -> int:
    """主函数"""
    args = parse_args()
    setup_logging(args.verbose)

    return asyncio.run(run_tests(args))


if __name__ == "__main__":
    sys.exit(main())
