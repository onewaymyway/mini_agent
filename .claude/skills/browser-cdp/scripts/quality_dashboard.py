"""
质量报告生成器

生成可视化质量报告和优化建议。

用法：
  python quality_dashboard.py --site baidu --format markdown
  python quality_dashboard.py --all --format html
  python quality_dashboard.py --site baidu --days 7 --output report.md
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.data_quality_tracker import DataQualityTracker
from scripts.optimization_suggester import OptimizationSuggester
from src.evaluators.data_quality_evaluator import DataQualityEvaluator, DataQualityMonitor

logger = logging.getLogger(__name__)


class QualityDashboard:
    """质量报告生成器"""

    def __init__(self, tracker: DataQualityTracker, suggester: OptimizationSuggester):
        self.tracker = tracker
        self.suggester = suggester
        self.evaluator = DataQualityEvaluator()
        self.monitor = DataQualityMonitor()

    def generate_site_report(self, site_name: str, days: int = 7, fmt: str = "markdown") -> str:
        """生成单个站点的质量报告"""
        summary = self.tracker.get_summary(site_name, days)
        suggestions = self.suggester.generate_suggestions(site_name, days)
        trend_data = self.tracker.get_trend(site_name, "overall_score", days=14)

        if fmt == "markdown":
            return self._generate_markdown_report(site_name, summary, suggestions, trend_data)
        elif fmt == "html":
            return self._generate_html_report(site_name, summary, suggestions, trend_data)
        elif fmt == "json":
            return self._generate_json_report(site_name, summary, suggestions, trend_data)
        else:
            return self._generate_markdown_report(site_name, summary, suggestions, trend_data)

    def generate_all_report(self, days: int = 7, fmt: str = "markdown") -> str:
        """生成所有站点的质量报告"""
        summary = self.tracker.get_summary(days=days)
        sites = summary.get("sites", {})

        lines = ["# Browser-CDP 数据质量总览", ""]
        lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"**数据范围**: 最近 {days} 天")
        lines.append(f"**总记录数**: {summary.get('total_records', 0)}")
        lines.append(f"**平均得分**: {summary.get('avg_score', 0):.1f}")
        lines.append("")

        if sites:
            lines.append("## 各站点质量排名")
            lines.append("")
            lines.append("| 站点 | 平均得分 | 评估次数 | 状态 |")
            lines.append("|------|---------|---------|------|")
            for name, stats in sorted(sites.items(), key=lambda x: x[1].get("avg_score", 0), reverse=True):
                score = stats.get("avg_score", 0)
                count = stats.get("record_count", 0)
                if score >= 80:
                    status = "✅ 优秀"
                elif score >= 60:
                    status = "⚠️ 一般"
                else:
                    status = "❌ 需改进"
                lines.append(f"| {name} | {score:.1f} | {count} | {status} |")
            lines.append("")

        return "\n".join(lines)

    def _generate_markdown_report(self, site_name: str, summary: Dict, suggestions: List[Dict], trend_data: List[Dict]) -> str:
        """生成 Markdown 格式报告"""
        lines = [f"# {site_name} 数据质量报告", ""]
        lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"**数据范围**: 最近 {summary.get('days', 7)} 天")
        lines.append(f"**总记录数**: {summary.get('total_records', 0)}")
        lines.append("")

        # 综合得分
        latest = self.tracker._get_latest_record(site_name)
        if latest:
            score = latest.get("overall_score", 0)
            lines.append(f"## 综合得分: {score:.1f}/100")
            lines.append("")
            if score >= 90:
                grade = "优秀 (A)"
            elif score >= 75:
                grade = "良好 (B)"
            elif score >= 60:
                grade = "合格 (C)"
            elif score >= 40:
                grade = "待改进 (D)"
            else:
                grade = "不可用 (F)"
            lines.append(f"**评级**: {grade}")
            lines.append("")

        # 趋势图表
        if trend_data:
            lines.append("## 质量趋势")
            lines.append("")
            lines.append("| 时间 | 综合得分 |")
            lines.append("|------|---------|")
            for d in trend_data[-10:]:
                ts = d["timestamp"][:19].replace("T", " ")
                lines.append(f"| {ts} | {d['value']:.1f} |")
            lines.append("")

        # 优化建议
        if suggestions:
            lines.append("## 优化建议")
            lines.append("")
            critical = [s for s in suggestions if s.get("type") == "critical"]
            warning = [s for s in suggestions if s.get("type") == "warning"]
            info = [s for s in suggestions if s.get("type") == "info"]

            if critical:
                lines.append("### 🔴 严重问题")
                for s in critical:
                    lines.append(f"- {s['message']}")
                lines.append("")

            if warning:
                lines.append("### 🟡 需要关注")
                for s in warning:
                    lines.append(f"- {s['message']}")
                lines.append("")

            if info:
                lines.append("### ℹ️ 提示信息")
                for s in info:
                    lines.append(f"- {s['message']}")
                lines.append("")
        else:
            lines.append("## ✅ 所有指标正常，无需优化")
            lines.append("")

        return "\n".join(lines)

    def _generate_html_report(self, site_name: str, summary: Dict, suggestions: List[Dict], trend_data: List[Dict]) -> str:
        """生成 HTML 格式报告"""
        latest = self.tracker._get_latest_record(site_name)
        score = latest.get("overall_score", 0) if latest else 0

        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>{site_name} 数据质量报告</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 40px; }}
        h1 {{ color: #333; }}
        h2 {{ color: #666; border-bottom: 2px solid #eee; padding-bottom: 10px; }}
        .score {{ font-size: 48px; font-weight: bold; color: {'#28a745' if score >= 80 else '#ffc107' if score >= 60 else '#dc3545'}; }}
        .trend {{ margin: 20px 0; }}
        table {{ border-collapse: collapse; width: 100%; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background-color: #f5f5f5; }}
        .critical {{ color: #dc3545; }}
        .warning {{ color: #ffc107; }}
        .info {{ color: #17a2b8; }}
    </style>
</head>
<body>
    <h1>{site_name} 数据质量报告</h1>
    <p>生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    <p>数据范围: 最近 {summary.get('days', 7)} 天</p>
    <h2>综合得分</h2>
    <div class="score">{score:.1f}</div>
"""

        if trend_data:
            html += "<h2>质量趋势</h2><div class='trend'><table><tr><th>时间</th><th>得分</th></tr>"
            for d in trend_data[-10:]:
                ts = d["timestamp"][:19].replace("T", " ")
                html += f"<tr><td>{ts}</td><td>{d['value']:.1f}</td></tr>"
            html += "</table></div>"

        if suggestions:
            html += "<h2>优化建议</h2>"
            for s in suggestions:
                cls = s.get("type", "info")
                html += f"<p class='{cls}'>{'🔴' if cls == 'critical' else '🟡' if cls == 'warning' else 'ℹ️'} {s['message']}</p>"

        html += "</body></html>"
        return html

    def _generate_json_report(self, site_name: str, summary: Dict, suggestions: List[Dict], trend_data: List[Dict]) -> str:
        """生成 JSON 格式报告"""
        report = {
            "site_name": site_name,
            "generated_at": datetime.now().isoformat(),
            "data_range_days": summary.get("days", 7),
            "total_records": summary.get("total_records", 0),
            "suggestions": suggestions,
            "trend": trend_data[-10:] if trend_data else [],
        }
        return json.dumps(report, ensure_ascii=False, indent=2)

    def export_report(self, site_name: str, output_path: str, days: int = 7, fmt: str = "markdown"):
        """导出报告到文件"""
        report = self.generate_site_report(site_name, days, fmt)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(report)
        logger.info(f"报告已导出: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="质量报告生成器")
    parser.add_argument("--site", "-s", help="站点名称")
    parser.add_argument("--all", "-a", action="store_true", help="处理所有站点")
    parser.add_argument("--format", "-f", choices=["markdown", "html", "json"], default="markdown", help="输出格式")
    parser.add_argument("--days", type=int, default=7, help="查询天数")
    parser.add_argument("--output", "-o", help="输出文件路径")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    tracker = DataQualityTracker()
    suggester = OptimizationSuggester(tracker)
    dashboard = QualityDashboard(tracker, suggester)

    if args.site:
        report = dashboard.generate_site_report(args.site, args.days, args.format)
        if args.output:
            dashboard.export_report(args.site, args.output, args.days, args.format)
        else:
            print(report)
    elif args.all:
        report = dashboard.generate_all_report(args.days, args.format)
        print(report)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
