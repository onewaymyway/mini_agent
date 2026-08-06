"""
评估测试批量执行脚本

支持：
- 按优先级批量执行
- 指定网站执行
- Mock 模式 / 真实浏览器模式
- 结果报告生成
"""
import argparse
import json
import logging
import sys
from pathlib import Path

# 添加 skill 目录到路径
SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from tests.evaluation.test_runner import EvaluationRunner, EvaluationReport, run_evaluation
from scripts.eval_config import WEBSITE_CONFIGS, get_websites_by_priority, get_website_by_name

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="网站操作能力评估批量执行工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 执行所有 P0 级网站
  python -m tests.evaluation.batch_runner --priority P0

  # 执行指定网站
  python -m tests.evaluation.batch_runner --sites 百度 Bing

  # 执行所有网站，保存报告
  python -m tests.evaluation.batch_runner --output-dir ./output/eval_results

  # 详细输出
  python -m tests.evaluation.batch_runner --verbose
        """
    )
    parser.add_argument("--output-dir", "-o", default="output/eval_results",
                        help="输出目录 (default: output/eval_results)")
    parser.add_argument("--priority", choices=["P0", "P1", "P2", "P3"],
                        help="指定优先级")
    parser.add_argument("--sites", nargs="+", help="指定网站名称")
    parser.add_argument("--categories", nargs="+", help="指定网站分类")
    parser.add_argument("--no-mock", action="store_true", help="使用真实浏览器（需实现）")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    parser.add_argument("--json-only", action="store_true", help="仅生成 JSON 报告")
    parser.add_argument("--md-only", action="store_true", help="仅生成 Markdown 报告")
    parser.add_argument("--summary", action="store_true", help="仅输出汇总信息")

    args = parser.parse_args()

    # 配置日志
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # 筛选网站
    if args.priority:
        websites = get_websites_by_priority(args.priority)
        logger.info(f"筛选优先级 {args.priority}，共 {len(websites)} 个网站")
    elif args.sites:
        websites = []
        for name in args.sites:
            w = get_website_by_name(name)
            if w:
                websites.append(w)
            else:
                logger.warning(f"未找到网站: {name}")
        logger.info(f"指定网站 {len(websites)} 个")
    elif args.categories:
        websites = [w for w in WEBSITE_CONFIGS if w.category in args.categories]
        logger.info(f"筛选分类 {args.categories}，共 {len(websites)} 个网站")
    else:
        websites = WEBSITE_CONFIGS
        logger.info(f"执行全部 {len(websites)} 个网站")

    if not websites:
        logger.error("没有可执行的网站配置")
        sys.exit(1)

    # 运行评估
    output_dir = Path(args.output_dir)
    mock_mode = not args.no_mock

    logger.info(f"开始评估 {len(websites)} 个网站 (mock_mode={mock_mode})")
    report = run_evaluation(websites, output_dir=output_dir, mock_mode=mock_mode)

    # 输出汇总
    print("\n" + "=" * 60)
    print("评估汇总")
    print("=" * 60)
    print(f"评估网站数: {report.total_websites}")
    print(f"总场景数: {report.total_scenarios}")
    print(f"通过场景: {report.passed_scenarios}")
    print(f"失败场景: {report.failed_scenarios}")
    print(f"通过率: {report.overall_success_rate:.1f}%")
    print(f"平均得分: {report.avg_score:.1f}/100")
    print(f"总耗时: {report.total_duration:.2f}s")
    print("=" * 60)

    # 各网站得分
    print("\n各网站评分:")
    print(f"{'网站':<12} {'优先级':<6} {'分类':<10} {'得分':<8} {'等级':<12} {'通过率':<8}")
    print("-" * 60)
    for r in sorted(report.website_results, key=lambda x: x.overall_score, reverse=True):
        print(f"{r.website_name:<12} {r.priority:<6} {r.category:<10} "
              f"{r.overall_score:<8.1f} {r.grade:<12} {r.scenario_success_rate:.0f}%")
    print("=" * 60)

    # 保存报告
    if not args.summary:
        if not args.json_only:
            md_path = output_dir / f"summary_{report.generated_at.replace(' ', '_').replace(':', '-')}.md"
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(report.to_markdown())
            logger.info(f"Markdown 汇总报告: {md_path}")
        if not args.md_only:
            json_path = output_dir / f"summary_{report.generated_at.replace(' ', '_')}.json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
            logger.info(f"JSON 汇总报告: {json_path}")

    # 返回退出码
    if report.failed_scenarios > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
