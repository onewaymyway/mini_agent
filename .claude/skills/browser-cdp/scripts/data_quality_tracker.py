"""
数据质量追踪器

定期记录抓取质量指标，生成趋势报告，支持自动告警。

用法：
  python data_quality_tracker.py --site baidu --record
  python data_quality_tracker.py --site baidu --report
  python data_quality_tracker.py --all --record
  python data_quality_tracker.py --alerts
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

from scripts.eval_config import WEBSITE_CONFIGS, get_website_by_name, ensure_output_dirs

logger = logging.getLogger(__name__)


class DataQualityTracker:
    """数据质量追踪器"""

    def __init__(self, data_dir: str = None):
        self.data_dir = Path(data_dir) if data_dir else ensure_output_dirs()[1]
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.history_file = self.data_dir / "quality_history.json"
        self.alerts_file = self.data_dir / "quality_alerts.json"
        self._history: List[Dict[str, Any]] = []
        self._alerts: List[Dict[str, Any]] = []
        self._load_history()
        self._load_alerts()

    def _load_history(self):
        """加载历史记录"""
        if self.history_file.exists():
            try:
                with open(self.history_file, "r", encoding="utf-8") as f:
                    self._history = json.load(f)
            except Exception as e:
                logger.warning(f"加载历史记录失败: {e}")
                self._history = []

    def _save_history(self):
        """保存历史记录"""
        with open(self.history_file, "w", encoding="utf-8") as f:
            json.dump(self._history, f, ensure_ascii=False, indent=2)

    def _load_alerts(self):
        """加载告警记录"""
        if self.alerts_file.exists():
            try:
                with open(self.alerts_file, "r", encoding="utf-8") as f:
                    self._alerts = json.load(f)
            except Exception as e:
                logger.warning(f"加载告警记录失败: {e}")
                self._alerts = []

    def _save_alerts(self):
        """保存告警记录"""
        with open(self.alerts_file, "w", encoding="utf-8") as f:
            json.dump(self._alerts, f, ensure_ascii=False, indent=2)

    def record_quality(self, site_name: str, quality_data: Dict[str, Any]):
        """记录数据质量"""
        record = {
            "site_name": site_name,
            "timestamp": datetime.now().isoformat(),
            **quality_data
        }
        self._history.append(record)
        self._check_alerts(site_name, record)
        self._save_history()
        self._save_alerts()
        logger.info(f"已记录 {site_name} 数据质量: 综合得分={quality_data.get('overall_score', 'N/A')}")

    def _check_alerts(self, site_name: str, record: Dict[str, Any]):
        """检查是否需要触发告警"""
        score = record.get("overall_score", 100)
        scraping_score = record.get("scraping_success", {}).get("score", 100)
        stability_score = record.get("stability", {}).get("score", 100)

        if score < 60:
            self._alerts.append({
                "site_name": site_name,
                "type": "low_overall_score",
                "severity": "high",
                "message": f"综合得分过低 ({score:.1f}分)",
                "timestamp": record["timestamp"]
            })

        if scraping_score < 70:
            self._alerts.append({
                "site_name": site_name,
                "type": "low_scraping_success",
                "severity": "high",
                "message": f"抓取成功率过低 ({scraping_score:.1f}%)",
                "timestamp": record["timestamp"]
            })

        if stability_score < 70:
            self._alerts.append({
                "site_name": site_name,
                "type": "low_stability",
                "severity": "medium",
                "message": f"稳定性过低 ({stability_score:.1f}%)",
                "timestamp": record["timestamp"]
            })

    def get_trend(self, site_name: str, metric: str, days: int = 7) -> List[Dict[str, Any]]:
        """获取指标趋势"""
        cutoff = datetime.now() - timedelta(days=days)
        records = [
            r for r in self._history
            if r.get("site_name") == site_name
            and datetime.fromisoformat(r["timestamp"]) > cutoff
        ]
        return [
            {"timestamp": r["timestamp"], "value": r.get(metric)}
            for r in records
        ]

    def get_summary(self, site_name: Optional[str] = None, days: int = 7) -> Dict[str, Any]:
        """获取质量摘要"""
        cutoff = datetime.now() - timedelta(days=days)
        records = [
            r for r in self._history
            if datetime.fromisoformat(r["timestamp"]) > cutoff
            and (site_name is None or r.get("site_name") == site_name)
        ]

        if not records:
            return {"total_records": 0, "avg_score": 0, "sites": []}

        site_scores: Dict[str, List[float]] = {}
        for r in records:
            name = r.get("site_name", "unknown")
            score = r.get("overall_score", 0)
            if name not in site_scores:
                site_scores[name] = []
            site_scores[name].append(score)

        return {
            "total_records": len(records),
            "avg_score": sum(r.get("overall_score", 0) for r in records) / len(records),
            "sites": {
                name: {
                    "avg_score": sum(scores) / len(scores),
                    "record_count": len(scores)
                }
                for name, scores in site_scores.items()
            }
        }

    def get_alerts(self, hours: int = 24) -> List[Dict[str, Any]]:
        """获取最近 N 小时的告警"""
        cutoff = datetime.now() - timedelta(hours=hours)
        return [
            a for a in self._alerts
            if datetime.fromisoformat(a["timestamp"]) > cutoff
        ]

    def generate_trend_report(self, site_name: str) -> str:
        """生成趋势报告"""
        trend_data = self.get_trend(site_name, "overall_score", days=14)
        if not trend_data:
            return f"暂无 {site_name} 的趋势数据"

        lines = [f"# {site_name} 数据质量趋势报告", ""]
        lines.append("| 时间 | 综合得分 |\n|------|---------|\n")
        for d in trend_data[-10:]:  # 最近10条
            ts = d["timestamp"][:19].replace("T", " ")
            lines.append(f"| {ts} | {d['value']:.1f} |\n")

        # 计算趋势
        if len(trend_data) >= 2:
            recent = trend_data[-5:] if len(trend_data) >= 5 else trend_data
            avg_recent = sum(d["value"] for d in recent) / len(recent)
            avg_older = sum(d["value"] for d in trend_data[:-5]) / (len(trend_data) - 5) if len(trend_data) > 5 else avg_recent
            trend = "上升" if avg_recent > avg_older else "下降" if avg_recent < avg_older else "稳定"
            lines.append(f"\n**趋势**: {trend} (近期平均: {avg_recent:.1f}, 前期平均: {avg_older:.1f})")

        return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="数据质量追踪器")
    parser.add_argument("--site", "-s", help="站点名称")
    parser.add_argument("--all", "-a", action="store_true", help="处理所有站点")
    parser.add_argument("--record", "-r", action="store_true", help="记录质量数据")
    parser.add_argument("--report", action="store_true", help="生成趋势报告")
    parser.add_argument("--alerts", action="store_true", help="查看告警")
    parser.add_argument("--days", type=int, default=7, help="查询天数")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    tracker = DataQualityTracker()

    if args.record:
        if args.site:
            config = get_website_by_name(args.site)
            if not config:
                logger.error(f"站点未找到: {args.site}")
                sys.exit(1)
            # 模拟质量数据（实际应从评估结果读取）
            quality_data = {
                "overall_score": 85.0,
                "scraping_success": {"score": 90.0},
                "performance": {"score": 80.0},
                "stability": {"score": 85.0},
            }
            tracker.record_quality(args.site, quality_data)
        elif args.all:
            for config in WEBSITE_CONFIGS:
                quality_data = {
                    "overall_score": 80.0,
                    "scraping_success": {"score": 85.0},
                    "performance": {"score": 75.0},
                    "stability": {"score": 80.0},
                }
                tracker.record_quality(config.name, quality_data)
        else:
            logger.error("请指定 --site 或 --all")
            sys.exit(1)

    elif args.report:
        if args.site:
            report = tracker.generate_trend_report(args.site)
            print(report)
        elif args.all:
            for config in WEBSITE_CONFIGS[:5]:  # 只显示前5个
                report = tracker.generate_trend_report(config.name)
                print(f"\n{report}\n")
        else:
            summary = tracker.get_summary(days=args.days)
            print(f"# 数据质量摘要 (最近 {args.days} 天)")
            print(f"总记录数: {summary['total_records']}")
            print(f"平均得分: {summary['avg_score']:.1f}")
            print("\n各站点:")
            for name, stats in summary.get("sites", {}).items():
                print(f"  - {name}: 平均 {stats['avg_score']:.1f}分 ({stats['record_count']}次)")

    elif args.alerts:
        alerts = tracker.get_alerts(hours=24)
        if alerts:
            print("# 最近24小时告警")
            for a in alerts:
                print(f"[{a['severity']}] {a['site_name']}: {a['message']}")
        else:
            print("最近24小时无告警")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
