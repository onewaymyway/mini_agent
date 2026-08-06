"""
优化建议生成器

根据数据质量追踪记录，自动生成优化建议。

用法：
  python optimization_suggester.py --site baidu
  python optimization_suggester.py --all
  python optimization_suggester.py --site baidu --days 7
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.data_quality_tracker import DataQualityTracker

logger = logging.getLogger(__name__)


class OptimizationSuggester:
    """优化建议生成器"""

    # 各维度阈值配置
    THRESHOLDS = {
        "overall_score": {"warning": 70, "critical": 50},
        "scraping_success": {"warning": 70, "critical": 50},
        "performance": {"warning": 60, "critical": 40},
        "element_accuracy": {"warning": 70, "critical": 50},
        "anti_detection": {"warning": 60, "critical": 40},
        "stability": {"warning": 70, "critical": 50},
        "error_recovery": {"warning": 60, "critical": 40},
    }

    # 优化建议模板
    SUGGESTIONS = {
        "overall_score": {
            "critical": [
                "综合得分过低，建议全面检查抓取流程",
                "考虑重新评估目标网站的技术架构",
                "检查是否需要更新反检测策略",
            ],
            "warning": [
                "综合得分偏低，建议优化关键指标",
                "检查最近一次评估结果中的低分项",
            ],
        },
        "scraping_success": {
            "critical": [
                "抓取成功率严重不足，检查网络连通性",
                "目标网站可能已更新反爬机制",
                "考虑使用代理池或调整请求频率",
                "检查是否需要更新登录态或 Cookie",
            ],
            "warning": [
                "抓取成功率偏低，建议检查请求头配置",
                "考虑增加重试次数或调整超时时间",
            ],
        },
        "performance": {
            "critical": [
                "页面加载性能严重不足",
                "检查网络延迟和服务器响应时间",
                "考虑使用更高效的等待策略",
            ],
            "warning": [
                "页面加载性能偏低，建议优化等待策略",
                "考虑使用智能等待替代固定延迟",
            ],
        },
        "element_accuracy": {
            "critical": [
                "元素定位准确率严重不足",
                "目标网站可能已更新 DOM 结构",
                "检查选择器是否需要更新",
            ],
            "warning": [
                "元素定位准确率偏低，建议检查选择器",
                "考虑使用更稳定的定位策略",
            ],
        },
        "anti_detection": {
            "critical": [
                "反检测能力严重不足，可能被识别为机器人",
                "检查 stealth 模式配置",
                "考虑更新请求头伪装策略",
            ],
            "warning": [
                "反检测能力偏低，建议启用 stealth 模式",
                "检查是否需要轮换 User-Agent",
            ],
        },
        "stability": {
            "critical": [
                "稳定性严重不足，抓取过程频繁失败",
                "检查网络连接稳定性",
                "考虑增加熔断器保护",
            ],
            "warning": [
                "稳定性偏低，建议增加重试机制",
                "检查是否需要调整并发控制",
            ],
        },
        "error_recovery": {
            "critical": [
                "错误恢复能力严重不足",
                "检查错误处理逻辑是否完善",
                "考虑增加降级策略",
            ],
            "warning": [
                "错误恢复能力偏低，建议优化错误处理",
                "检查是否需要增加重试策略",
            ],
        },
    }

    def __init__(self, tracker: DataQualityTracker):
        self.tracker = tracker

    def generate_suggestions(self, site_name: str, days: int = 7) -> List[Dict[str, Any]]:
        """生成优化建议"""
        suggestions: List[Dict[str, Any]] = []
        summary = self.tracker.get_summary(site_name, days)

        if summary.get("total_records", 0) == 0:
            suggestions.append({
                "type": "info",
                "message": f"暂无 {site_name} 最近 {days} 天的质量数据，请先运行评估",
            })
            return suggestions

        # 获取最新记录
        latest = self._get_latest_record(site_name)
        if not latest:
            suggestions.append({
                "type": "info",
                "message": f"无法获取 {site_name} 的最新质量数据",
            })
            return suggestions

        # 分析各维度
        for dimension, thresholds in self.THRESHOLDS.items():
            value = latest.get(dimension)
            if isinstance(value, dict):
                score = value.get("score", 100)
            else:
                score = float(value or 100)

            if score < thresholds["critical"]:
                severity = "critical"
            elif score < thresholds["warning"]:
                severity = "warning"
            else:
                continue

            # 获取建议
            suggestion_list = self.SUGGESTIONS.get(dimension, {}).get(severity, [])
            for suggestion in suggestion_list[:2]:  # 每个维度最多2条建议
                suggestions.append({
                    "type": severity,
                    "dimension": dimension,
                    "score": round(score, 1),
                    "message": suggestion,
                })

        # 检查趋势
        trend = self._check_trend(site_name)
        if trend:
            suggestions.append(trend)

        return suggestions

    def _get_latest_record(self, site_name: str) -> Optional[Dict[str, Any]]:
        """获取最新记录"""
        records = [
            r for r in self.tracker._history
            if r.get("site_name") == site_name
        ]
        if not records:
            return None
        return max(records, key=lambda r: r.get("timestamp", ""))

    def _check_trend(self, site_name: str) -> Optional[Dict[str, Any]]:
        """检查趋势"""
        trend_data = self.tracker.get_trend(site_name, "overall_score", days=14)
        if len(trend_data) < 3:
            return None

        recent = trend_data[-3:]
        older = trend_data[:-3]

        avg_recent = sum(d["value"] for d in recent) / len(recent)
        avg_older = sum(d["value"] for d in older) / len(older)

        change = avg_recent - avg_older

        if change < -10:
            return {
                "type": "critical",
                "dimension": "trend",
                "message": f"质量趋势明显下降 ({change:.1f}分)，建议立即检查",
            }
        elif change < -5:
            return {
                "type": "warning",
                "dimension": "trend",
                "message": f"质量趋势略有下降 ({change:.1f}分)，建议关注",
            }
        elif change > 10:
            return {
                "type": "info",
                "dimension": "trend",
                "message": f"质量趋势明显改善 ({change:.1f}分)，继续保持",
            }

        return None

    def generate_report(self, site_name: str, days: int = 7) -> str:
        """生成优化报告"""
        suggestions = self.generate_suggestions(site_name, days)

        lines = [f"# {site_name} 优化建议报告", f"\n**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", f"**数据范围**: 最近 {days} 天", ""]

        # 按严重程度分组
        critical = [s for s in suggestions if s["type"] == "critical"]
        warning = [s for s in suggestions if s["type"] == "warning"]
        info = [s for s in suggestions if s["type"] == "info"]

        if critical:
            lines.append("## 严重问题")
            lines.append("")
            for s in critical:
                lines.append(f"- 🔴 {s['message']}")
            lines.append("")

        if warning:
            lines.append("## 需要关注")
            lines.append("")
            for s in warning:
                lines.append(f"- 🟡 {s['message']}")
            lines.append("")

        if info:
            lines.append("## 提示信息")
            lines.append("")
            for s in info:
                lines.append(f"- ℹ️ {s['message']}")
            lines.append("")

        if not suggestions:
            lines.append("✅ 所有指标正常，无需优化")

        return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="优化建议生成器")
    parser.add_argument("--site", "-s", help="站点名称")
    parser.add_argument("--all", "-a", action="store_true", help="处理所有站点")
    parser.add_argument("--days", type=int, default=7, help="查询天数")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    tracker = DataQualityTracker()
    suggester = OptimizationSuggester(tracker)

    if args.site:
        report = suggester.generate_report(args.site, days=args.days)
        print(report)
    elif args.all:
        from scripts.eval_config import WEBSITE_CONFIGS
        for config in WEBSITE_CONFIGS[:5]:  # 只显示前5个
            report = suggester.generate_report(config.name, days=args.days)
            print(f"\n{report}\n")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
