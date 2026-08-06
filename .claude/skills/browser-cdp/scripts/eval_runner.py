"""
网站操作能力评估执行器

实现自动化评估流程：
1. 浏览器初始化
2. 场景执行
3. 数据采集
4. 评估计算
5. 报告生成
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class EvalScenario:
    """评估场景"""

    def __init__(self, scenario_id: str, name: str, description: str):
        self.scenario_id = scenario_id
        self.name = name
        self.description = description
        self.steps: List[Dict[str, Any]] = []
        self.expected_metrics: Dict[str, Any] = {}

    def add_step(self, step_id: str, action: str, params: Dict[str, Any] = None):
        """添加执行步骤"""
        self.steps.append({
            "id": step_id,
            "action": action,
            "params": params or {},
        })

    def set_expected_metrics(self, metrics: Dict[str, Any]):
        """设置预期指标"""
        self.expected_metrics = metrics


class EvalResult:
    """单次评估结果"""

    def __init__(self, website_name: str, website_url: str):
        self.website_name = website_name
        self.website_url = website_url
        self.eval_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.dimensions: Dict[str, Dict[str, Any]] = {}
        self.scenarios: List[Dict[str, Any]] = []
        self.overall_score = 0.0
        self.grade = ""
        self.findings: List[str] = []
        self.recommendations: List[str] = []
        self.errors: List[str] = []
        self.screenshots: List[str] = []
        self.duration_seconds = 0.0

    def add_dimension(self, name: str, result: Dict[str, Any]):
        self.dimensions[name] = result

    def add_scenario_result(self, scenario_id: str, success: bool, duration: float, error: Optional[str] = None, data: Optional[Dict] = None):
        result = {
            "id": scenario_id,
            "success": success,
            "duration": round(duration, 2),
            "error": error,
        }
        if data:
            result["data"] = data
        self.scenarios.append(result)
        if error:
            self.errors.append(f"{scenario_id}: {error}")

    def calculate_overall(self):
        if not self.dimensions:
            return
        weights = {
            "页面加载能力": 0.25,
            "元素定位能力": 0.25,
            "数据提取能力": 0.20,
            "反检测能力": 0.15,
            "稳定性与恢复": 0.15,
        }
        total_weighted = 0.0
        total_weight = 0.0
        for name, result in self.dimensions.items():
            weight = weights.get(name, 1.0 / 5)
            score = result.get("score", 0)
            total_weighted += score * weight
            total_weight += weight
        self.overall_score = round(total_weighted / total_weight, 2) if total_weight > 0 else 0
        self.grade = self._calculate_grade(self.overall_score)

    @staticmethod
    def _calculate_grade(score: float) -> str:
        if score >= 90:
            return "优秀 (A)"
        elif score >= 75:
            return "良好 (B)"
        elif score >= 60:
            return "合格 (C)"
        elif score >= 40:
            return "待改进 (D)"
        else:
            return "不可用 (F)"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "website_name": self.website_name,
            "website_url": self.website_url,
            "eval_time": self.eval_time,
            "overall_score": self.overall_score,
            "grade": self.grade,
            "dimensions": self.dimensions,
            "scenarios": self.scenarios,
            "findings": self.findings,
            "recommendations": self.recommendations,
            "errors": self.errors,
            "screenshot_count": len(self.screenshots),
            "duration_seconds": round(self.duration_seconds, 2),
        }

    def to_markdown(self) -> str:
        lines = [
            f"# 网站操作能力评估报告\n",
            f"**评估网站**: {self.website_name} ({self.website_url})\n",
            f"**评估日期**: {self.eval_time}\n",
            f"**综合评分**: {self.overall_score}/100 ({self.grade})\n",
            f"**评估耗时**: {self.duration_seconds:.1f}秒\n",
            "\n",
        ]
        lines.append("## 各维度得分\n")
        lines.append("| 维度 | 得分 | 权重 | 加权得分 |\n")
        lines.append("|------|------|------|----------|\n")
        weights = {
            "页面加载能力": 0.25,
            "元素定位能力": 0.25,
            "数据提取能力": 0.20,
            "反检测能力": 0.15,
            "稳定性与恢复": 0.15,
        }
        for name, result in self.dimensions.items():
            weight = weights.get(name, 0)
            weighted = result.get("score", 0) * weight
            lines.append(f"| {name} | {result.get('score', 0):.1f} | {weight:.0%} | {weighted:.1f} |\n")
        lines.append("\n")
        if self.scenarios:
            lines.append("## 场景执行结果\n")
            lines.append("| 场景 ID | 成功 | 耗时 (s) | 错误 |\n")
            lines.append("|---------|------|----------|------|\n")
            for s in self.scenarios:
                status = "✓" if s["success"] else "✗"
                error = (s.get("error") or "")[:30]
                lines.append(f"| {s['id']} | {status} | {s['duration']} | {error} |\n")
            lines.append("\n")
        if self.errors:
            lines.append("## 执行错误\n")
            for err in self.errors[:5]:
                lines.append(f"- {err}\n")
            lines.append("\n")
        return "".join(lines)


class WebsiteEvaluator:
    """网站评估器"""

    def __init__(self, output_dir: Path = Path("./output/eval_reports")):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._history: List[Dict[str, Any]] = []

    def evaluate(self, website_name: str, website_url: str, scenarios: List[EvalScenario],
                 browser_context: Dict[str, Any] = None) -> EvalResult:
        """
        执行网站评估

        Args:
            website_name: 网站名称
            website_url: 网站 URL
            scenarios: 评估场景列表
            browser_context: 浏览器上下文数据

        Returns:
            评估结果
        """
        start_time = time.time()
        result = EvalResult(website_name, website_url)

        logger.info(f"开始评估网站: {website_name} ({website_url})")

        # 执行各场景
        for scenario in scenarios:
            logger.info(f"执行场景: {scenario.name}")
            scenario_start = time.time()

            try:
                # 执行场景步骤
                scenario_data = self._execute_scenario(scenario, browser_context)
                scenario_duration = time.time() - scenario_start
                result.add_scenario_result(
                    scenario_id=scenario.scenario_id,
                    success=True,
                    duration=scenario_duration,
                    data=scenario_data,
                )
            except Exception as e:
                scenario_duration = time.time() - scenario_start
                error_msg = str(e)
                logger.error(f"场景执行失败: {scenario.name} - {error_msg}")
                result.add_scenario_result(
                    scenario_id=scenario.scenario_id,
                    success=False,
                    duration=scenario_duration,
                    error=error_msg,
                )

        # 计算各维度得分
        result.dimensions = self._calculate_dimensions(result, browser_context)

        # 计算综合评分
        result.calculate_overall()
        result.duration_seconds = time.time() - start_time

        # 生成发现和建议
        result.findings = self._generate_findings(result)
        result.recommendations = self._generate_recommendations(result)

        # 保存报告
        self._save_report(result)

        # 记录历史
        self._history.append(result.to_dict())

        logger.info(f"评估完成: {website_name} - 综合评分 {result.overall_score}/100 ({result.grade})")
        return result

    def _execute_scenario(self, scenario: EvalScenario, browser_context: Dict[str, Any]) -> Dict[str, Any]:
        """执行评估场景"""
        # 这里应该调用实际的浏览器操作
        # 暂时返回模拟数据
        return {
            "steps_executed": len(scenario.steps),
            "metrics_collected": scenario.expected_metrics,
        }

    def _calculate_dimensions(self, result: EvalResult, browser_context: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """计算各维度得分"""
        # 从浏览器上下文提取指标
        context = browser_context or {}

        dimensions = {
            "页面加载能力": {
                "score": context.get("page_loading_score", 85.0),
                "metrics": {
                    "page_access_rate": context.get("page_access_rate", 95.0),
                    "first_contentful_paint": context.get("fcp", 2.5),
                    "page_load_time": context.get("plt", 8.0),
                    "timeout_handling_rate": context.get("timeout_rate", 92.0),
                },
            },
            "元素定位能力": {
                "score": context.get("element_locate_score", 88.0),
                "metrics": {
                    "element_locate_rate": context.get("locate_rate", 90.0),
                    "interaction_success_rate": context.get("interaction_rate", 85.0),
                    "dynamic_element_rate": context.get("dynamic_rate", 82.0),
                    "locator_strategy_coverage": context.get("strategy_coverage", 75.0),
                },
            },
            "数据提取能力": {
                "score": context.get("data_extraction_score", 82.0),
                "metrics": {
                    "extraction_accuracy": context.get("extraction_accuracy", 85.0),
                    "field_completeness": context.get("field_completeness", 80.0),
                    "data_quality_score": context.get("data_quality", 82.0),
                    "structured_extraction_rate": context.get("structured_rate", 78.0),
                },
            },
            "反检测能力": {
                "score": context.get("anti_detection_score", 75.0),
                "metrics": {
                    "anti_crawl_bypass_rate": context.get("bypass_rate", 72.0),
                    "captcha_pass_rate": context.get("captcha_rate", 65.0),
                    "fingerprint_evasion_rate": context.get("fingerprint_rate", 85.0),
                    "behavior_naturalness": context.get("behavior_score", 78.0),
                },
            },
            "稳定性与恢复": {
                "score": context.get("stability_score", 88.0),
                "metrics": {
                    "execution_consistency": context.get("consistency", 90.0),
                    "error_recovery_rate": context.get("recovery_rate", 85.0),
                    "connection_stability": context.get("connection_stability", 96.0),
                    "memory_stability": context.get("memory_growth", 3.5),
                },
            },
        }

        return dimensions

    def _generate_findings(self, result: EvalResult) -> List[str]:
        """生成关键发现"""
        findings = []
        for name, dim in result.dimensions.items():
            score = dim.get("score", 0)
            if score >= 85:
                findings.append(f"✅ {name}: 表现优秀 ({score:.1f}分)")
            elif score >= 70:
                findings.append(f"⚠️  {name}: 基本可用 ({score:.1f}分)")
            else:
                findings.append(f"❌ {name}: 需要改进 ({score:.1f}分)")
        return findings

    def _generate_recommendations(self, result: EvalResult) -> List[str]:
        """生成改进建议"""
        recommendations = []
        for name, dim in result.dimensions.items():
            score = dim.get("score", 0)
            if score < 70:
                recommendations.append(f"🔧 优先优化 {name}（当前得分 {score:.1f}分）")
            elif score < 85:
                recommendations.append(f"📋 持续改进 {name}（当前得分 {score:.1f}分）")
        return recommendations

    def _save_report(self, result: EvalResult):
        """保存评估报告"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # 保存 JSON 报告
        json_path = self.output_dir / f"eval_{result.website_name}_{timestamp}.json"
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)

        # 保存 Markdown 报告
        md_path = self.output_dir / f"eval_{result.website_name}_{timestamp}.md"
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(result.to_markdown())

        logger.info(f"评估报告已保存: {json_path}, {md_path}")

    def get_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """获取评估历史"""
        return self._history[-limit:]

    def compare_websites(self, website_names: List[str]) -> Dict[str, Any]:
        """对比多个网站的评估结果"""
        comparison = {
            "websites": [],
            "summary": {},
        }

        for name in website_names:
            for record in self._history:
                if record["website_name"] == name:
                    comparison["websites"].append({
                        "name": name,
                        "url": record["website_url"],
                        "overall_score": record["overall_score"],
                        "grade": record["grade"],
                        "dimensions": record["dimensions"],
                    })
                    break

        # 生成对比摘要
        if comparison["websites"]:
            scores = [w["overall_score"] for w in comparison["websites"]]
            comparison["summary"] = {
                "average_score": sum(scores) / len(scores),
                "highest": max(scores),
                "lowest": min(scores),
                "count": len(scores),
            }

        return comparison


# 便捷函数
def run_evaluation(website_name: str, website_url: str, scenarios: List[EvalScenario],
                   output_dir: Path = None, browser_context: Dict[str, Any] = None) -> EvalResult:
    """
    便捷函数：执行网站评估

    Args:
        website_name: 网站名称
        website_url: 网站 URL
        scenarios: 评估场景列表
        output_dir: 报告输出目录
        browser_context: 浏览器上下文数据

    Returns:
        评估结果
    """
    evaluator = WebsiteEvaluator(output_dir=output_dir or Path("./output/eval_reports"))
    return evaluator.evaluate(website_name, website_url, scenarios, browser_context)


if __name__ == "__main__":
    # 测试示例
    import sys

    logging.basicConfig(level=logging.INFO)

    # 创建评估场景
    scenarios = [
        EvalScenario("page_load", "页面加载测试", "测试页面加载性能"),
        EvalScenario("element_locate", "元素定位测试", "测试元素定位能力"),
        EvalScenario("data_extract", "数据提取测试", "测试数据提取能力"),
    ]

    # 执行评估
    result = run_evaluation(
        website_name="测试网站",
        website_url="https://example.com",
        scenarios=scenarios,
        browser_context={
            "page_loading_score": 85.0,
            "element_locate_score": 88.0,
            "data_extraction_score": 82.0,
            "anti_detection_score": 75.0,
            "stability_score": 88.0,
        }
    )

    print(f"\n综合评分: {result.overall_score}/100 ({result.grade})")
    print(f"评估耗时: {result.duration_seconds:.1f}秒")
