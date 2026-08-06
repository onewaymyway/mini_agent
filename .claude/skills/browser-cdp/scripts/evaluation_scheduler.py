#!/usr/bin/env python3
"""评估定期调度器 - 实现评估机制的持续改进流程"""

import json
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import subprocess
import sys


# 评估周期配置
EVAL_SCHEDULE = {
    "P0": {  # 核心网站
        "full_eval": "weekly",  # 每周全量评估
        "quick_check": "daily",  # 每日快速检查
        "min_runs_for_consistency": 3,
    },
    "P1": {  # 重要网站
        "full_eval": "biweekly",  # 每两周全量评估
        "quick_check": "weekly",  # 每周快速检查
        "min_runs_for_consistency": 2,
    },
    "P2": {  # 扩展网站
        "full_eval": "monthly",  # 每月全量评估
        "quick_check": "biweekly",  # 每两周快速检查
        "min_runs_for_consistency": 2,
    },
    "P3": {  # 探索网站
        "full_eval": "quarterly",  # 每季度全量评估
        "quick_check": "monthly",  # 每月快速检查
        "min_runs_for_consistency": 1,
    },
}

# 评估维度迭代周期
DIMENSION_ITERATION = {
    "页面访问成功率": {"review_cycle": "monthly", "trigger": "pass_rate_drop > 10%"},
    "元素定位成功率": {"review_cycle": "biweekly", "trigger": "cv > 15%"},
    "数据提取准确率": {"review_cycle": "monthly", "trigger": "accuracy_drop > 5%"},
    "反爬绕过率": {"review_cycle": "weekly", "trigger": "cv > 30%"},
    "稳定性": {"review_cycle": "weekly", "trigger": "consistency_drop > 10%"},
}

# 测试用例迭代周期
CASE_ITERATION = {
    "新增用例": {"cycle": "monthly", "source": "new_website_patterns"},
    "废弃用例": {"cycle": "quarterly", "source": "always_pass_or_always_fail"},
    "优化用例": {"cycle": "biweekly", "source": "high_variance_cases"},
}


class EvaluationScheduler:
    """评估调度器 - 管理定期评估和持续改进"""
    
    def __init__(self, skill_dir: Path):
        self.skill_dir = skill_dir
        self.eval_dir = skill_dir / "output" / "eval_results"
        self.config_file = skill_dir / "config" / "evaluation_schedule.json"
        self.history_file = skill_dir / "data" / "evaluation_history.json"
        
    def load_config(self) -> Dict[str, Any]:
        """加载调度配置"""
        if self.config_file.exists():
            with open(self.config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {"websites": {}, "last_run": {}, "next_scheduled": {}}
    
    def save_config(self, config: Dict[str, Any]):
        """保存调度配置"""
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_file, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    
    def load_history(self) -> List[Dict[str, Any]]:
        """加载评估历史"""
        if self.history_file.exists():
            with open(self.history_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []
    
    def save_history(self, history: List[Dict[str, Any]]):
        """保存评估历史"""
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.history_file, 'w', encoding='utf-8') as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    
    def get_website_priority(self, website: str) -> str:
        """获取网站优先级"""
        config = self.load_config()
        return config.get("websites", {}).get(website, {}).get("priority", "P2")
    
    def is_due_for_eval(self, website: str) -> bool:
        """检查是否到了评估时间"""
        config = self.load_config()
        priority = self.get_website_priority(website)
        schedule = EVAL_SCHEDULE.get(priority, EVAL_SCHEDULE["P2"])
        
        last_run = config.get("last_run", {}).get(website)
        if not last_run:
            return True  # 从未评估过
        
        last_time = datetime.fromisoformat(last_run)
        now = datetime.now()
        
        # 检查快速检查周期
        quick_interval = self._parse_interval(schedule["quick_check"])
        if (now - last_time) >= quick_interval:
            return True
        
        # 检查全量评估周期
        full_interval = self._parse_interval(schedule["full_eval"])
        if (now - last_time) >= full_interval:
            return True
        
        return False
    
    def _parse_interval(self, period: str) -> timedelta:
        """解析周期为时间间隔"""
        intervals = {
            "daily": timedelta(days=1),
            "weekly": timedelta(weeks=1),
            "biweekly": timedelta(weeks=2),
            "monthly": timedelta(weeks=4),
            "quarterly": timedelta(weeks=13),
        }
        return intervals.get(period, timedelta(weeks=1))
    
    def get_pending_evaluations(self) -> List[Dict[str, Any]]:
        """获取待评估网站列表"""
        pending = []
        
        # 从历史中获取已评估网站
        history = self.load_history()
        evaluated_sites = set(h.get("website") for h in history)
        
        # 检查每个网站
        for website in evaluated_sites:
            if self.is_due_for_eval(website):
                priority = self.get_website_priority(website)
                pending.append({
                    "website": website,
                    "priority": priority,
                    "last_eval": history[-1].get("eval_time") if history else None,
                    "reason": self._get_eval_reason(website, priority),
                })
        
        # 按优先级排序
        priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
        pending.sort(key=lambda x: priority_order.get(x["priority"], 2))
        
        return pending
    
    def _get_eval_reason(self, website: str, priority: str) -> str:
        """获取评估原因"""
        history = self.load_history()
        website_history = [h for h in history if h.get("website") == website]
        
        if not website_history:
            return "首次评估"
        
        last_eval = website_history[-1]
        reasons = []
        
        # 检查评分下降
        if last_eval.get("overall_score"):
            prev_scores = [h.get("overall_score") for h in website_history[-3:] if h.get("overall_score")]
            if prev_scores and last_eval["overall_score"] < min(prev_scores) * 0.9:
                reasons.append("评分下降超过10%")
        
        # 检查一致性波动
        if last_eval.get("consistency_cv"):
            if last_eval["consistency_cv"] > 10:
                reasons.append("一致性波动较大")
        
        # 检查周期到期
        schedule = EVAL_SCHEDULE.get(priority, EVAL_SCHEDULE["P2"])
        reasons.append(f"{schedule['quick_check']}检查周期到期")
        
        return "; ".join(reasons) if reasons else "定期评估"
    
    def record_evaluation(self, website: str, result: Dict[str, Any]):
        """记录评估结果到历史"""
        history = self.load_history()
        
        entry = {
            "website": website,
            "eval_time": datetime.now().isoformat(),
            "overall_score": result.get("overall_score"),
            "pass_rate": result.get("pass_rate"),
            "consistency_cv": result.get("consistency_cv"),
            "dimensions": result.get("dimension_scores", {}),
            "priority": self.get_website_priority(website),
        }
        
        history.append(entry)
        self.save_history(history)
        
        # 更新最后评估时间
        config = self.load_config()
        if "last_run" not in config:
            config["last_run"] = {}
        config["last_run"][website] = datetime.now().isoformat()
        self.save_config(config)
    
    def get_dimension_review_queue(self) -> List[Dict[str, Any]]:
        """获取需要审查的维度队列"""
        review_queue = []
        history = self.load_history()
        
        for website in set(h.get("website") for h in history):
            website_history = [h for h in history if h.get("website") == website]
            
            for dim_name, dim_config in DIMENSION_ITERATION.items():
                # 检查该维度在最近评估中的表现
                recent_scores = []
                for h in website_history[-3:]:
                    dim_data = h.get("dimensions", {}).get(dim_name, {})
                    if isinstance(dim_data, dict):
                        recent_scores.append(dim_data.get("rate", dim_data.get("score", 0)))
                    elif isinstance(dim_data, (int, float)):
                        recent_scores.append(dim_data)
                
                if len(recent_scores) >= 2:
                    cv = self._calc_cv(recent_scores)
                    trigger = dim_config.get("trigger", "")
                    
                    # 检查是否触发审查
                    should_review = False
                    reason = ""
                    
                    if "cv >" in trigger:
                        threshold = float(trigger.split("> ")[1].replace("%", ""))
                        if cv > threshold:
                            should_review = True
                            reason = f"变异系数{cv:.1f}%超过阈值{threshold}%"
                    
                    if "drop >" in trigger:
                        threshold = float(trigger.split("> ")[1].replace("%", ""))
                        if recent_scores[-1] < recent_scores[0] * (1 - threshold/100):
                            should_review = True
                            reason = f"评分下降超过{threshold}%"
                    
                    if should_review:
                        review_queue.append({
                            "website": website,
                            "dimension": dim_name,
                            "cv": cv,
                            "recent_scores": recent_scores,
                            "reason": reason,
                            "review_cycle": dim_config.get("review_cycle"),
                        })
        
        return review_queue
    
    def get_case_iteration_queue(self) -> List[Dict[str, Any]]:
        """获取用例迭代队列"""
        iteration_queue = []
        history = self.load_history()
        
        for website in set(h.get("website") for h in history):
            website_history = [h for h in history if h.get("website") == website]
            
            if not website_history:
                continue
            
            # 分析用例通过率
            case_stats = {}
            for h in website_history:
                for test in h.get("test_results", []):
                    case_id = test.get("case_id")
                    if case_id not in case_stats:
                        case_stats[case_id] = {"pass": 0, "fail": 0, "total": 0}
                    case_stats[case_id]["total"] += 1
                    if test.get("success"):
                        case_stats[case_id]["pass"] += 1
                    else:
                        case_stats[case_id]["fail"] += 1
            
            for case_id, stats in case_stats.items():
                pass_rate = stats["pass"] / stats["total"] * 100 if stats["total"] > 0 else 0
                
                # 识别需要迭代的用例
                if stats["total"] >= 2:
                    if pass_rate == 100:
                        iteration_queue.append({
                            "case_id": case_id,
                            "website": website,
                            "action": "废弃",  # 总是通过，考虑移除
                            "reason": f"100%通过率({stats['pass']}/{stats['total']})，可能过于简单",
                            "iteration_type": "废弃用例",
                        })
                    elif pass_rate == 0:
                        iteration_queue.append({
                            "case_id": case_id,
                            "website": website,
                            "action": "优化",  # 总是失败，需要修复
                            "reason": f"0%通过率({stats['fail']}/{stats['total']})，可能存在环境问题",
                            "iteration_type": "优化用例",
                        })
                    elif 10 < pass_rate < 90:
                        iteration_queue.append({
                            "case_id": case_id,
                            "website": website,
                            "action": "审查",  # 部分通过，需要分析
                            "reason": f"通过率{pass_rate:.1f}%，存在波动",
                            "iteration_type": "优化用例",
                        })
        
        return iteration_queue
    
    def _calc_cv(self, values: List[float]) -> float:
        """计算变异系数"""
        if len(values) < 2 or sum(values) == 0:
            return 0.0
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
        stdev = variance ** 0.5
        return (stdev / mean * 100) if mean != 0 else 0.0
    
    def generate_improvement_report(self) -> str:
        """生成持续改进报告"""
        report_lines = [
            "# 评估机制持续改进报告\n",
            f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n",
        ]
        
        # 1. 待评估网站
        pending = self.get_pending_evaluations()
        report_lines.append("## 1. 待评估网站\n\n")
        if pending:
            report_lines.append("| 网站 | 优先级 | 最后评估 | 评估原因 |\n")
            report_lines.append("|------|--------|----------|----------|\n")
            for item in pending:
                report_lines.append(f"| {item['website']} | {item['priority']} | {item.get('last_eval', '从未')} | {item['reason']} |\n")
        else:
            report_lines.append("✅ 所有网站评估周期内，无需评估\n")
        
        # 2. 维度审查队列
        dim_queue = self.get_dimension_review_queue()
        report_lines.append("\n## 2. 维度审查队列\n\n")
        if dim_queue:
            report_lines.append("| 网站 | 维度 | CV | 最近得分 | 原因 |\n")
            report_lines.append("|------|------|-----|----------|------|\n")
            for item in dim_queue[:10]:  # 只显示前10个
                scores_str = ", ".join(f"{s:.1f}" for s in item['recent_scores'])
                report_lines.append(f"| {item['website']} | {item['dimension']} | {item['cv']:.1f}% | {scores_str} | {item['reason']} |\n")
        else:
            report_lines.append("✅ 所有维度表现稳定，无需审查\n")
        
        # 3. 用例迭代队列
        case_queue = self.get_case_iteration_queue()
        report_lines.append("\n## 3. 用例迭代队列\n\n")
        if case_queue:
            report_lines.append("| 网站 | 用例ID | 操作 | 原因 |\n")
            report_lines.append("|------|--------|------|------|\n")
            for item in case_queue[:10]:
                report_lines.append(f"| {item['website']} | {item['case_id']} | {item['action']} | {item['reason']} |\n")
        else:
            report_lines.append("✅ 所有用例表现稳定，无需迭代\n")
        
        # 4. 改进建议
        report_lines.append("\n## 4. 持续改进建议\n\n")
        report_lines.append("### 4.1 评估周期建议\n")
        report_lines.append("| 优先级 | 全量评估 | 快速检查 | 说明 |\n")
        report_lines.append("|--------|----------|----------|------|\n")
        for prio, config in EVAL_SCHEDULE.items():
            report_lines.append(f"| {prio} | {config['full_eval']} | {config['quick_check']} | 核心/重要/扩展/探索网站 |\n")
        
        report_lines.append("\n### 4.2 维度迭代周期\n")
        report_lines.append("| 维度 | 审查周期 | 触发条件 |\n")
        report_lines.append("|------|----------|----------|\n")
        for dim, config in DIMENSION_ITERATION.items():
            report_lines.append(f"| {dim} | {config['review_cycle']} | {config['trigger']} |\n")
        
        report_lines.append("\n### 4.3 用例迭代策略\n")
        report_lines.append("| 迭代类型 | 周期 | 触发条件 |\n")
        report_lines.append("|----------|------|----------|\n")
        for case_type, config in CASE_ITERATION.items():
            report_lines.append(f"| {case_type} | {config['cycle']} | {config['source']} |\n")
        
        return "".join(report_lines)
    
    def run_scheduled_eval(self, website: str, priority: str = None):
        """运行 scheduled 评估"""
        if priority is None:
            priority = self.get_website_priority(website)
        
        schedule = EVAL_SCHEDULE.get(priority, EVAL_SCHEDULE["P2"])
        
        # 构建评估命令
        eval_script = self.skill_dir / "scripts" / "run_test_cases.py"
        output_dir = self.eval_dir / website
        output_dir.mkdir(parents=True, exist_ok=True)
        
        cmd = [
            sys.executable, str(eval_script),
            "-u", website,
            "-n", website,
            "-o", str(self.eval_dir),
            "--priority", priority,
        ]
        
        print(f"运行评估: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            print(f"✅ {website} 评估完成")
            # 记录评估结果
            # 这里需要解析评估输出并记录
        else:
            print(f"❌ {website} 评估失败: {result.stderr}")
        
        return result.returncode == 0


def main():
    parser = argparse.ArgumentParser(description='评估调度器 - 持续改进流程')
    parser.add_argument('--skill-dir', '-s', default='.claude/skills/browser-cdp', help='skill目录')
    parser.add_argument('--action', '-a', choices=['status', 'report', 'run', 'review'],
                       default='status', help='执行动作')
    parser.add_argument('--website', '-w', help='指定网站（用于 --action run）')
    parser.add_argument('--priority', '-p', choices=['P0', 'P1', 'P2', 'P3'], help='网站优先级')
    args = parser.parse_args()
    
    skill_dir = Path(args.skill_dir)
    scheduler = EvaluationScheduler(skill_dir)
    
    if args.action == 'status':
        pending = scheduler.get_pending_evaluations()
        print(f"待评估网站: {len(pending)}个")
        for item in pending:
            print(f"  - {item['website']} ({item['priority']}): {item['reason']}")
    
    elif args.action == 'report':
        report = scheduler.generate_improvement_report()
        print(report)
        
        # 保存报告
        output_dir = skill_dir / "output" / "eval_results"
        output_dir.mkdir(parents=True, exist_ok=True)
        report_file = output_dir / f"improvement_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write(report)
        print(f"\n报告已保存: {report_file}")
    
    elif args.action == 'run':
        if args.website:
            scheduler.run_scheduled_eval(args.website, args.priority)
        else:
            pending = scheduler.get_pending_evaluations()
            for item in pending:
                scheduler.run_scheduled_eval(item['website'], item['priority'])
    
    elif args.action == 'review':
        dim_queue = scheduler.get_dimension_review_queue()
        case_queue = scheduler.get_case_iteration_queue()
        
        print("=== 维度审查队列 ===")
        for item in dim_queue:
            print(f"  {item['website']}/{item['dimension']}: {item['reason']}")
        
        print("\n=== 用例迭代队列 ===")
        for item in case_queue:
            print(f"  {item['website']}/{item['case_id']}: {item['action']} - {item['reason']}")


if __name__ == "__main__":
    main()
