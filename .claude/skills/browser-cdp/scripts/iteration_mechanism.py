"""
网站操作能力迭代机制

实现评估周期、触发条件和改进流程。
"""

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class IterationTrigger:
    """迭代触发条件"""

    # 触发条件定义
    TRIGGERS = {
        "score_drop": {
            "name": "评分下降",
            "threshold": 5.0,  # 评分下降超过 5 分
            "action": "立即触发专项评估",
        },
        "metric_below_target": {
            "name": "指标低于目标",
            "threshold": None,  # 任何指标低于目标值
            "action": "制定优化计划",
        },
        "website_structure_change": {
            "name": "网站结构变更",
            "threshold": None,
            "action": "重新评估适配性",
        },
        "new_anti_crawl": {
            "name": "新增反爬机制",
            "threshold": None,
            "action": "更新反检测策略",
        },
        "version_release": {
            "name": "版本发布",
            "threshold": None,
            "action": "全量回归评估",
        },
    }

    @classmethod
    def check_triggers(cls, current_result: Dict[str, Any], previous_result: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        检查触发条件

        Args:
            current_result: 当前评估结果
            previous_result: 上次评估结果

        Returns:
            触发的条件列表
        """
        triggers = []

        # 检查评分下降
        if previous_result:
            score_drop = previous_result.get("overall_score", 0) - current_result.get("overall_score", 0)
            if score_drop >= cls.TRIGGERS["score_drop"]["threshold"]:
                triggers.append({
                    "trigger": "score_drop",
                    "message": f"综合评分下降 {score_drop:.1f} 分 ({previous_result.get('overall_score', 0):.1f} → {current_result.get('overall_score', 0):.1f})",
                    "action": cls.TRIGGERS["score_drop"]["action"],
                    "priority": "high",
                })

        # 检查指标低于目标
        for dim_name, dim_result in current_result.get("dimensions", {}).items():
            dim_score = dim_result.get("score", 0)
            if dim_score < 70:  # 低于 70 分视为需要改进
                triggers.append({
                    "trigger": "metric_below_target",
                    "message": f"{dim_name} 得分较低 ({dim_score:.1f}分)，需优化",
                    "action": cls.TRIGGERS["metric_below_target"]["action"],
                    "priority": "medium",
                })

        return triggers


class IterationCycle:
    """迭代周期管理"""

    # 评估周期定义
    CYCLES = {
        "full_evaluation": {
            "name": "全量评估",
            "frequency": "weekly",  # 每周
            "scope": "所有 P0/P1 网站",
            "executor": "自动化执行",
        },
        "incremental_evaluation": {
            "name": "增量评估",
            "frequency": "daily",  # 每日
            "scope": "新增/变更网站",
            "executor": "自动化执行",
        },
        "special_evaluation": {
            "name": "专项评估",
            "frequency": "on_demand",  # 按需
            "scope": "特定场景",
            "executor": "手动+自动化",
        },
        "version_evaluation": {
            "name": "版本评估",
            "frequency": "per_release",  # 每次发布
            "scope": "全量 P0/P1 网站",
            "executor": "自动化执行",
        },
    }

    @classmethod
    def get_next_schedule(cls, last_eval_time: datetime, cycle_type: str) -> datetime:
        """
        计算下次评估时间

        Args:
            last_eval_time: 上次评估时间
            cycle_type: 评估周期类型

        Returns:
            下次评估时间
        """
        cycle = cls.CYCLES.get(cycle_type, cls.CYCLES["full_evaluation"])
        frequency = cycle["frequency"]

        if frequency == "weekly":
            return last_eval_time + timedelta(weeks=1)
        elif frequency == "daily":
            return last_eval_time + timedelta(days=1)
        elif frequency == "on_demand":
            return datetime.now()  # 立即执行
        elif frequency == "per_release":
            return datetime.now()  # 发布后立即执行
        else:
            return last_eval_time + timedelta(weeks=1)

    @classmethod
    def is_due(cls, last_eval_time: datetime, cycle_type: str) -> bool:
        """
        检查是否到了评估时间

        Args:
            last_eval_time: 上次评估时间
            cycle_type: 评估周期类型

        Returns:
            是否到期
        """
        next_time = cls.get_next_schedule(last_eval_time, cycle_type)
        return datetime.now() >= next_time


class ImprovementTracker:
    """改进跟踪器"""

    def __init__(self, tracking_file: Path = Path("./data/iteration_tracking.json")):
        self.tracking_file = tracking_file
        self._data = self._load_data()

    def _load_data(self) -> Dict[str, Any]:
        """加载跟踪数据"""
        if self.tracking_file.exists():
            with open(self.tracking_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {
            "iterations": [],
            "improvements": [],
            "status": {},
        }

    def _save_data(self):
        """保存跟踪数据"""
        self.tracking_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.tracking_file, 'w', encoding='utf-8') as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def record_iteration(self, iteration_id: str, website_name: str, action: str,
                         before_score: float, after_score: float, notes: str = ""):
        """
        记录迭代改进

        Args:
            iteration_id: 迭代 ID
            website_name: 网站名称
            action: 改进动作
            before_score: 改进前得分
            after_score: 改进后得分
            notes: 备注
        """
        iteration = {
            "id": iteration_id,
            "website_name": website_name,
            "action": action,
            "before_score": before_score,
            "after_score": after_score,
            "improvement": after_score - before_score,
            "notes": notes,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._data["iterations"].append(iteration)
        self._save_data()
        logger.info(f"记录迭代改进: {iteration_id} - {website_name} ({before_score:.1f} → {after_score:.1f})")

    def record_improvement(self, website_name: str, dimension: str, improvement: str,
                           metric_before: float, metric_after: float):
        """
        记录具体改进

        Args:
            website_name: 网站名称
            dimension: 维度名称
            improvement: 改进描述
            metric_before: 改进前指标
            metric_after: 改进后指标
        """
        improvement_record = {
            "website_name": website_name,
            "dimension": dimension,
            "improvement": improvement,
            "metric_before": metric_before,
            "metric_after": metric_after,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._data["improvements"].append(improvement_record)
        self._save_data()
        logger.info(f"记录改进: {website_name} - {dimension} ({metric_before:.1f} → {metric_after:.1f})")

    def get_status(self, website_name: str) -> Dict[str, Any]:
        """获取网站改进状态"""
        iterations = [i for i in self._data["iterations"] if i["website_name"] == website_name]
        improvements = [i for i in self._data["improvements"] if i["website_name"] == website_name]

        return {
            "website_name": website_name,
            "total_iterations": len(iterations),
            "total_improvements": len(improvements),
            "latest_score": iterations[-1]["after_score"] if iterations else None,
            "improvement_trend": self._calculate_trend(iterations),
        }

    def _calculate_trend(self, iterations: List[Dict[str, Any]]) -> str:
        """计算改进趋势"""
        if len(iterations) < 2:
            return "insufficient_data"

        scores = [i["after_score"] for i in iterations[-5:]]  # 最近 5 次
        if all(scores[i] <= scores[i + 1] for i in range(len(scores) - 1)):
            return "improving"
        elif all(scores[i] >= scores[i + 1] for i in range(len(scores) - 1)):
            return "declining"
        else:
            return "stable"

    def get_summary(self) -> Dict[str, Any]:
        """获取改进摘要"""
        return {
            "total_iterations": len(self._data["iterations"]),
            "total_improvements": len(self._data["improvements"]),
            "websites_tracked": len(set(i["website_name"] for i in self._data["iterations"])),
            "recent_iterations": self._data["iterations"][-10:],
        }


class EvaluationScheduler:
    """评估调度器"""

    def __init__(self, scheduler_file: Path = Path("./data/evaluation_scheduler.json")):
        self.scheduler_file = scheduler_file
        self._schedule = self._load_schedule()

    def _load_schedule(self) -> Dict[str, Any]:
        """加载调度配置"""
        if self.scheduler_file.exists():
            with open(self.scheduler_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {
            "last_full_evaluation": None,
            "last_incremental_evaluation": None,
            "pending_evaluations": [],
            "config": {
                "auto_run": True,
                "notification_enabled": True,
            },
        }

    def _save_schedule(self):
        """保存调度配置"""
        self.scheduler_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.scheduler_file, 'w', encoding='utf-8') as f:
            json.dump(self._schedule, f, ensure_ascii=False, indent=2)

    def schedule_evaluation(self, website_name: str, cycle_type: str = "full_evaluation",
                            priority: str = "normal"):
        """
        调度评估任务

        Args:
            website_name: 网站名称
            cycle_type: 评估周期类型
            priority: 优先级
        """
        task = {
            "website_name": website_name,
            "cycle_type": cycle_type,
            "priority": priority,
            "scheduled_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "pending",
        }
        self._schedule["pending_evaluations"].append(task)
        self._save_schedule()
        logger.info(f"调度评估任务: {website_name} ({cycle_type}, priority={priority})")

    def get_pending_tasks(self) -> List[Dict[str, Any]]:
        """获取待执行任务"""
        return [t for t in self._schedule["pending_evaluations"] if t["status"] == "pending"]

    def mark_completed(self, website_name: str, cycle_type: str):
        """标记任务完成"""
        for task in self._schedule["pending_evaluations"]:
            if task["website_name"] == website_name and task["cycle_type"] == cycle_type:
                task["status"] = "completed"
                task["completed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                break

        # 更新最后评估时间
        if cycle_type == "full_evaluation":
            self._schedule["last_full_evaluation"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        elif cycle_type == "incremental_evaluation":
            self._schedule["last_incremental_evaluation"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        self._save_schedule()
        logger.info(f"标记评估完成: {website_name} ({cycle_type})")

    def get_schedule_status(self) -> Dict[str, Any]:
        """获取调度状态"""
        return {
            "last_full_evaluation": self._schedule.get("last_full_evaluation"),
            "last_incremental_evaluation": self._schedule.get("last_incremental_evaluation"),
            "pending_count": len(self.get_pending_tasks()),
            "config": self._schedule.get("config", {}),
        }


# 便捷函数
def check_iteration_triggers(current_result: Dict[str, Any], previous_result: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """检查迭代触发条件"""
    return IterationTrigger.check_triggers(current_result, previous_result)


def record_improvement(website_name: str, dimension: str, improvement: str,
                       metric_before: float, metric_after: float,
                       tracking_file: Path = None):
    """记录改进"""
    tracker = ImprovementTracker(tracking_file=tracking_file or Path("./data/iteration_tracking.json"))
    tracker.record_improvement(website_name, dimension, improvement, metric_before, metric_after)


def schedule_evaluation(website_name: str, cycle_type: str = "full_evaluation",
                        priority: str = "normal", scheduler_file: Path = None):
    """调度评估"""
    scheduler = EvaluationScheduler(scheduler_file=scheduler_file or Path("./data/evaluation_scheduler.json"))
    scheduler.schedule_evaluation(website_name, cycle_type, priority)


if __name__ == "__main__":
    # 测试示例
    import sys

    logging.basicConfig(level=logging.INFO)

    # 测试触发条件检查
    current_result = {
        "overall_score": 72.5,
        "dimensions": {
            "页面加载能力": {"score": 85.0},
            "元素定位能力": {"score": 78.0},
            "数据提取能力": {"score": 65.0},  # 低于 70
            "反检测能力": {"score": 70.0},
            "稳定性与恢复": {"score": 88.0},
        },
    }

    previous_result = {
        "overall_score": 80.0,
    }

    triggers = check_iteration_triggers(current_result, previous_result)
    print(f"\n触发的条件: {len(triggers)} 个")
    for trigger in triggers:
        print(f"  - {trigger['message']} ({trigger['priority']})")

    # 测试改进跟踪
    tracker = ImprovementTracker()
    tracker.record_improvement(
        website_name="测试网站",
        dimension="数据提取能力",
        improvement="优化选择器策略",
        metric_before=65.0,
        metric_after=82.0,
    )

    # 测试调度器
    scheduler = EvaluationScheduler()
    scheduler.schedule_evaluation("测试网站", "full_evaluation", "high")
    print(f"\n待执行任务数: {len(scheduler.get_pending_tasks())}")
