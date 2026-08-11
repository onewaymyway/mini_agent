#!/usr/bin/env python3
"""
评估编排器 - 批量自动化评估流程

功能：
1. 读取网站支持列表
2. 按优先级顺序执行评估
3. 生成汇总报告
4. 支持断点续评
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from auto_evaluator import AutoEvaluator, EvalResult
from compatibility_checker import CompatibilityChecker

logger = logging.getLogger(__name__)


class EvalOrchestrator:
    """评估编排器"""

    def __init__(self, data_dir: str = "data", output_dir: str = "output/eval_reports"):
        self.data_dir = Path(data_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.evaluator = AutoEvaluator(data_dir=data_dir, output_dir=output_dir)
        self.checker = CompatibilityChecker(output_dir=str(self.output_dir / "check_results"))
        
        self._load_website_list()
        self._load_checkpoint()

    def _load_website_list(self):
        """加载网站支持列表"""
        support_file = self.data_dir / "website_support_list.json"
        if support_file.exists():
            with open(support_file, "r", encoding="utf-8") as f:
                self.websites = json.load(f)
            logger.info(f"已加载 {len(self.websites)} 个网站")
        else:
            self.websites = {}
            logger.warning("未找到网站支持列表")

    def _load_checkpoint(self):
        """加载断点续评记录"""
        checkpoint_file = self.data_dir / "evaluation_checkpoint.json"
        if checkpoint_file.exists():
            with open(checkpoint_file, "r", encoding="utf-8") as f:
                self.checkpoint = json.load(f)
            logger.info(f"已加载断点记录: {len(self.checkpoint.get('completed', []))} 个网站已完成")
        else:
            self.checkpoint = {"completed": [], "started_at": None, "last_updated": None}

    def _save_checkpoint(self):
        """保存断点记录"""
        self.checkpoint["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        checkpoint_file = self.data_dir / "evaluation_checkpoint.json"
        with open(checkpoint_file, "w", encoding="utf-8") as f:
            json.dump(self.checkpoint, f, ensure_ascii=False, indent=2)

    def get_pending_websites(self, priorities: List[str] = None) -> List[Dict]:
        """获取待评估网站列表"""
        pending = []
        for name, info in self.websites.items():
            if name in self.checkpoint.get("completed", []):
                continue
            if priorities and info.get("priority") not in priorities:
                continue
            pending.append({"name": name, **info})
        
        # 按优先级排序
        priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
        pending.sort(key=lambda x: priority_order.get(x.get("priority", "P3"), 3))
        
        return pending

    def evaluate_single(self, website_name: str, website_url: str) -> EvalResult:
        """评估单个网站"""
        logger.info(f"开始评估: {website_name} ({website_url})")
        
        # 先执行兼容性检测
        check_result = asyncio.run(self.checker.full_check(website_url))
        self.checker.save_result(check_result)
        
        # 构建浏览器上下文
        browser_context = {
            "page_loading_score": check_result.page_load.get("score", 0),
            "element_locate_score": check_result.element_locate.get("score", 0),
            "data_extraction_score": check_result.data_extract.get("score", 0),
            "anti_detection_score": check_result.anti_detection.get("score", 0),
            "stability_score": check_result.stability.get("score", 0),
        }
        
        # 执行评估
        result = self.evaluator.evaluate_website(website_name, website_url, browser_context)
        
        # 更新断点
        if "completed" not in self.checkpoint:
            self.checkpoint["completed"] = []
        self.checkpoint["completed"].append(website_name)
        self._save_checkpoint()
        
        return result

    def batch_evaluate(self, priorities: List[str] = None, max_websites: int = None) -> List[EvalResult]:
        """
        批量评估网站
        
        Args:
            priorities: 优先级列表，如 ["P0", "P1"]
            max_websites: 最大评估数量，None 表示全部
        
        Returns:
            评估结果列表
        """
        pending = self.get_pending_websites(priorities)
        
        if max_websites:
            pending = pending[:max_websites]
        
        if not pending:
            logger.info("没有待评估的网站")
            return []
        
        logger.info(f"开始批量评估，共 {len(pending)} 个网站")
        
        if self.checkpoint.get("started_at") is None:
            self.checkpoint["started_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        results = []
        for i, website in enumerate(pending):
            logger.info(f"[{i+1}/{len(pending)}] 评估 {website['name']}")
            try:
                result = self.evaluate_single(website["name"], website["url"])
                results.append(result)
            except Exception as e:
                logger.error(f"评估 {website['name']} 失败: {e}")
                # 创建失败结果
                fail_result = EvalResult(website["name"], website["url"])
                fail_result.errors.append(f"评估失败: {e}")
                results.append(fail_result)
        
        return results

    def generate_summary_report(self, results: List[EvalResult]) -> str:
        """生成汇总报告"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # 统计
        total = len(results)
        passed = sum(1 for r in results if r.overall_score >= 75)
        partial = sum(1 for r in results if 50 <= r.overall_score < 75)
        failed = sum(1 for r in results if r.overall_score < 50)
        avg_score = sum(r.overall_score for r in results) / total if total > 0 else 0
        
        # 按领域统计
        by_category = {}
        for r in results:
            # 从网站列表获取领域
            if r.website_name in self.websites:
                category = self.websites[r.website_name].get("category", "unknown")
                if category not in by_category:
                    by_category[category] = {"total": 0, "passed": 0, "scores": []}
                by_category[category]["total"] += 1
                by_category[category]["scores"].append(r.overall_score)
                if r.overall_score >= 75:
                    by_category[category]["passed"] += 1
        
        report = f"""
# 网站兼容性评估汇总报告

**生成时间**: {timestamp}

## 总体统计

| 指标 | 数值 |
|------|------|
| 评估总数 | {total} |
| 通过 (≥75分) | {passed} |
| 部分支持 (50-74分) | {partial} |
| 不支持 (<50分) | {failed} |
| 平均得分 | {avg_score:.1f} |
| 通过率 | {passed/total*100:.1f}% |

## 按领域统计

| 领域 | 总数 | 通过 | 平均得分 |
|------|------|------|----------|
"""
        
        for category, stats in sorted(by_category.items(), key=lambda x: -x[1]["passed"]):
            avg = sum(stats["scores"]) / len(stats["scores"]) if stats["scores"] else 0
            report += f"| {category} | {stats['total']} | {stats['passed']} | {avg:.1f} |\n"
        
        report += "\n## 详细结果\n\n"
        
        # 按优先级分组
        for priority in ["P0", "P1", "P2", "P3"]:
            priority_results = [r for r in results if 
                               r.website_name in self.websites and 
                               self.websites[r.website_name].get("priority") == priority]
            
            if not priority_results:
                continue
            
            report += f"\n### {priority} 优先级\n\n"
            report += "| 网站 | URL | 得分 | 等级 | 状态 |\n"
            report += "|------|-----|------|------|------|\n"
            
            for r in sorted(priority_results, key=lambda x: -x.overall_score):
                url = self.websites.get(r.website_name, {}).get("url", r.website_url)
                status = "✓" if r.overall_score >= 75 else ("△" if r.overall_score >= 50 else "✗")
                report += f"| {r.website_name} | {url} | {r.overall_score:.1f} | {r.grade} | {status} |\n"
        
        return report

    def run_full_evaluation(self, priorities: List[str] = None) -> str:
        """
        执行完整评估流程
        
        Args:
            priorities: 优先级列表
        
        Returns:
            汇总报告
        """
        logger.info("开始完整评估流程")
        start_time = time.time()
        
        # 批量评估
        results = self.batch_evaluate(priorities=priorities)
        
        # 生成报告
        report = self.generate_summary_report(results)
        
        # 保存报告
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = self.output_dir / f"summary_report_{timestamp}.md"
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report)
        
        elapsed = time.time() - start_time
        logger.info(f"评估流程完成，耗时 {elapsed:.1f}秒")
        
        return report


if __name__ == "__main__":
    import asyncio
    import sys
    
    logging.basicConfig(level=logging.INFO)
    
    orchestrator = EvalOrchestrator()
    
    # 获取命令行参数
    priorities = None
    if len(sys.argv) > 1:
        priorities = sys.argv[1:]
    
    print("开始批量评估...")
    report = orchestrator.run_full_evaluation(priorities=priorities)
    print("\n" + report)
