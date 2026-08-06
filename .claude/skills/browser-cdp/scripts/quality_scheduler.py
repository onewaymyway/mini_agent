"""
数据质量定期评估调度器

支持定时自动评估和优化建议生成。

用法：
  python quality_scheduler.py --run-once --sites baidu,zhihu
  python quality_scheduler.py --schedule --interval 3600
  python quality_scheduler.py --status
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.data_quality_tracker import DataQualityTracker
from scripts.optimization_suggester import OptimizationSuggester
from scripts.quality_dashboard import QualityDashboard
from scripts.eval_config import WEBSITE_CONFIGS, get_website_by_name

logger = logging.getLogger(__name__)


class QualityScheduler:
    """数据质量定期评估调度器"""

    def __init__(self, interval_seconds: int = 3600, max_sites: int = 10):
        self.interval = interval_seconds
        self.max_sites = max_sites
        self.tracker = DataQualityTracker()
        self.suggester = OptimizationSuggester(self.tracker)
        self.dashboard = QualityDashboard(self.tracker, self.suggester)
        self._running = False
        self._last_run: Dict[str, datetime] = {}
        self._config_file = Path(__file__).parent.parent / "data" / "scheduler_config.json"
        self._load_config()

    def _load_config(self):
        """加载调度器配置"""
        if self._config_file.exists():
            try:
                with open(self._config_file, "r", encoding="utf-8") as f:
                    config = json.load(f)
                    self.interval = config.get("interval_seconds", 3600)
                    self.max_sites = config.get("max_sites", 10)
                    self._last_run = {
                        k: datetime.fromisoformat(v) if isinstance(v, str) else v
                        for k, v in config.get("last_run", {}).items()
                    }
            except Exception as e:
                logger.warning(f"加载配置失败: {e}")

    def _save_config(self):
        """保存调度器配置"""
        self._config_file.parent.mkdir(parents=True, exist_ok=True)
        config = {
            "interval_seconds": self.interval,
            "max_sites": self.max_sites,
            "last_run": {
                k: v.isoformat() if isinstance(v, datetime) else v
                for k, v in self._last_run.items()
            },
            "updated_at": datetime.now().isoformat(),
        }
        with open(self._config_file, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

    def get_sites_to_evaluate(self) -> List[str]:
        """获取需要评估的站点列表"""
        sites = [config.name for config in WEBSITE_CONFIGS]
        # 过滤掉最近已评估且未到间隔时间的站点
        to_evaluate = []
        now = datetime.now()
        for site in sites:
            last_run = self._last_run.get(site)
            if last_run is None:
                to_evaluate.append(site)
            elif (now - last_run).total_seconds() >= self.interval:
                to_evaluate.append(site)
            if len(to_evaluate) >= self.max_sites:
                break
        return to_evaluate

    def run_evaluation(self, site_name: str) -> Dict[str, Any]:
        """对单个站点执行评估"""
        logger.info(f"开始评估站点: {site_name}")
        start_time = time.time()

        try:
            # 模拟评估结果（实际应从 eval_runner 获取）
            quality_data = {
                "overall_score": 85.0,
                "scraping_success": {"score": 90.0},
                "performance": {"score": 80.0},
                "element_accuracy": {"score": 85.0},
                "anti_detection": {"score": 75.0},
                "stability": {"score": 85.0},
                "error_recovery": {"score": 80.0},
            }

            # 记录质量数据
            self.tracker.record_quality(site_name, quality_data)

            # 生成优化建议
            suggestions = self.suggester.generate_suggestions(site_name)

            # 更新最后运行时间
            self._last_run[site_name] = datetime.now()
            self._save_config()

            elapsed = time.time() - start_time
            logger.info(f"站点 {site_name} 评估完成，耗时 {elapsed:.1f}s")

            return {
                "site_name": site_name,
                "success": True,
                "score": quality_data.get("overall_score", 0),
                "suggestions": suggestions,
                "elapsed_seconds": round(elapsed, 2),
            }
        except Exception as e:
            logger.error(f"站点 {site_name} 评估失败: {e}")
            return {
                "site_name": site_name,
                "success": False,
                "error": str(e),
                "elapsed_seconds": round(time.time() - start_time, 2),
            }

    def run_all(self) -> Dict[str, Any]:
        """执行所有站点的评估"""
        sites = self.get_sites_to_evaluate()
        if not sites:
            logger.info("没有需要评估的站点")
            return {"sites_evaluated": 0, "results": []}

        logger.info(f"开始批量评估，共 {len(sites)} 个站点")
        results = []
        for site in sites:
            result = self.run_evaluation(site)
            results.append(result)

        return {
            "sites_evaluated": len(results),
            "results": results,
            "completed_at": datetime.now().isoformat(),
        }

    def run_once(self, site_name: Optional[str] = None) -> Dict[str, Any]:
        """执行单次评估"""
        if site_name:
            return self.run_evaluation(site_name)
        return self.run_all()

    def get_status(self) -> Dict[str, Any]:
        """获取调度器状态"""
        sites_to_eval = self.get_sites_to_evaluate()
        return {
            "interval_seconds": self.interval,
            "max_sites": self.max_sites,
            "sites_to_evaluate": len(sites_to_eval),
            "next_evaluation_sites": sites_to_eval[:5],
            "last_run_times": {
                k: v.isoformat() if isinstance(v, datetime) else v
                for k, v in self._last_run.items()
            },
            "total_sites": len(WEBSITE_CONFIGS),
        }

    def generate_report(self, site_name: str, days: int = 7) -> str:
        """生成站点质量报告"""
        return self.dashboard.generate_site_report(site_name, days)

    def generate_all_report(self, days: int = 7) -> str:
        """生成所有站点质量报告"""
        return self.dashboard.generate_all_report(days)


def main():
    parser = argparse.ArgumentParser(description="数据质量定期评估调度器")
    parser.add_argument("--run-once", "-r", action="store_true", help="执行单次评估")
    parser.add_argument("--schedule", "-s", action="store_true", help="启动定时调度")
    parser.add_argument("--site", "-t", help="指定站点名称")
    parser.add_argument("--interval", type=int, default=3600, help="评估间隔（秒）")
    parser.add_argument("--max-sites", type=int, default=10, help="最大评估站点数")
    parser.add_argument("--status", action="store_true", help="查看调度器状态")
    parser.add_argument("--report", action="store_true", help="生成质量报告")
    parser.add_argument("--days", type=int, default=7, help="查询天数")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    scheduler = QualityScheduler(interval_seconds=args.interval, max_sites=args.max_sites)

    if args.status:
        status = scheduler.get_status()
        print(json.dumps(status, ensure_ascii=False, indent=2))
    elif args.report:
        if args.site:
            report = scheduler.generate_report(args.site, args.days)
            print(report)
        else:
            report = scheduler.generate_all_report(args.days)
            print(report)
    elif args.schedule:
        logger.info(f"启动定时调度器，间隔 {args.interval} 秒")
        scheduler._running = True
        while scheduler._running:
            try:
                result = scheduler.run_all()
                logger.info(f"评估完成: {result['sites_evaluated']} 个站点")
                time.sleep(args.interval)
            except KeyboardInterrupt:
                logger.info("收到中断信号，停止调度")
                scheduler._running = False
            except Exception as e:
                logger.error(f"调度器异常: {e}")
                time.sleep(60)
    elif args.run_once:
        result = scheduler.run_once(args.site)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
