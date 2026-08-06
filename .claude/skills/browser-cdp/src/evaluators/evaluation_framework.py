"""
网站操作能力评估框架

统一评估标准权重，实现完整的评估流程。
"""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# 评估维度权重（与 evaluation-standards-v2.md 一致）
DIMENSION_WEIGHTS = {
    "页面加载能力": 0.25,
    "元素定位能力": 0.25,
    "数据提取能力": 0.20,
    "反检测能力": 0.15,
    "稳定性与恢复": 0.15,
}


@dataclass
class MetricResult:
    """单个指标的计算结果"""
    name: str
    value: float
    unit: str = ""
    target: Optional[float] = None
    weight: float = 1.0
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def weighted_score(self) -> float:
        """计算加权得分"""
        return self.value * self.weight

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        result = {
            "name": self.name,
            "value": round(self.value, 2),
            "unit": self.unit,
            "weight": self.weight,
            "weighted_score": round(self.weighted_score, 2),
        }
        if self.target is not None:
            result["target"] = self.target
        if self.details:
            result["details"] = self.details
        return result


@dataclass
class DimensionResult:
    """维度评估结果"""
    name: str
    score: float
    weight: float
    metrics: List[MetricResult] = field(default_factory=list)
    observations: List[str] = field(default_factory=list)

    @property
    def weighted_score(self) -> float:
        """计算加权得分"""
        return self.score * self.weight

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        result = {
            "name": self.name,
            "score": round(self.score, 2),
            "weight": self.weight,
            "weighted_score": round(self.weighted_score, 2),
            "metrics": [m.to_dict() for m in self.metrics],
        }
        if self.observations:
            result["observations"] = self.observations
        return result


class BaseEvaluator:
    """评估器基类"""

    def __init__(self, name: str, weight: float):
        self.name = name
        self.weight = weight
        self._results: List[MetricResult] = []
        self._observations: List[str] = []

    def evaluate(self, context: Dict[str, Any]) -> DimensionResult:
        """执行评估，返回维度结果（子类实现）"""
        raise NotImplementedError

    def add_metric(self, metric: MetricResult):
        """添加指标结果"""
        self._results.append(metric)
        logger.debug(f"添加指标: {metric.name} = {metric.value}{metric.unit}")

    def add_observation(self, observation: str):
        """添加观察记录"""
        self._observations.append(observation)
        logger.info(f"观察记录: {observation}")

    def calculate_score(self, metrics: List[MetricResult]) -> float:
        """计算维度得分（加权平均）"""
        if not metrics:
            return 0.0
        total_weight = sum(m.weight for m in metrics)
        if total_weight == 0:
            return 0.0
        weighted_sum = sum(m.value * m.weight for m in metrics)
        return weighted_sum / total_weight

    def get_result(self) -> DimensionResult:
        """获取评估结果"""
        score = self.calculate_score(self._results)
        return DimensionResult(
            name=self.name,
            score=score,
            weight=self.weight,
            metrics=self._results,
            observations=self._observations,
        )

    def reset(self):
        """重置评估器状态"""
        self._results = []
        self._observations = []


class PageLoadingEvaluator(BaseEvaluator):
    """页面加载能力评估器（权重 25%）"""

    def __init__(self):
        super().__init__(name="页面加载能力", weight=0.25)

    def evaluate(self, context: Dict[str, Any]) -> DimensionResult:
        """
        评估页面加载能力

        context 参数:
            - page_access_rate: 页面访问成功率 (%)
            - first_contentful_paint: 首屏加载时间 (s)
            - page_load_time: 页面完全加载时间 (s)
            - timeout_handling_rate: 超时处理成功率 (%)
        """
        page_access_rate = context.get("page_access_rate", 0.0)
        fcp = context.get("first_contentful_paint", 0.0)
        plt = context.get("page_load_time", 0.0)
        timeout_rate = context.get("timeout_handling_rate", 0.0)

        # 计算得分（时间越短得分越高）
        fcp_score = max(0, 100 - (fcp / 3.0) * 100)
        plt_score = max(0, 100 - (plt / 10.0) * 100)

        # 添加指标
        self.add_metric(MetricResult(
            name="页面访问成功率",
            value=page_access_rate,
            unit="%",
            target=95.0,
            weight=0.40,
        ))
        self.add_metric(MetricResult(
            name="首屏加载时间",
            value=fcp,
            unit="s",
            target=3.0,
            weight=0.25,
            details={"score": round(fcp_score, 2)},
        ))
        self.add_metric(MetricResult(
            name="页面完全加载时间",
            value=plt,
            unit="s",
            target=10.0,
            weight=0.20,
            details={"score": round(plt_score, 2)},
        ))
        self.add_metric(MetricResult(
            name="超时处理成功率",
            value=timeout_rate,
            unit="%",
            target=90.0,
            weight=0.15,
        ))

        # 添加观察记录
        if page_access_rate < 95:
            self.add_observation(f"页面访问成功率较低 ({page_access_rate:.1f}%)，需优化网络处理")
        if fcp > 3.0:
            self.add_observation(f"首屏加载时间较长 ({fcp:.2f}s)，需优化资源加载")
        if plt > 10.0:
            self.add_observation(f"页面完全加载时间过长 ({plt:.2f}s)，需优化渲染性能")

        return self.get_result()


class ElementLocateEvaluator(BaseEvaluator):
    """元素定位能力评估器（权重 25%）"""

    def __init__(self):
        super().__init__(name="元素定位能力", weight=0.25)

    def evaluate(self, context: Dict[str, Any]) -> DimensionResult:
        """
        评估元素定位能力

        context 参数:
            - element_locate_rate: 元素定位成功率 (%)
            - interaction_success_rate: 交互成功率 (%)
            - dynamic_element_rate: 动态元素识别率 (%)
            - locator_strategy_coverage: 定位策略覆盖率 (%)
        """
        locate_rate = context.get("element_locate_rate", 0.0)
        interaction_rate = context.get("interaction_success_rate", 0.0)
        dynamic_rate = context.get("dynamic_element_rate", 0.0)
        strategy_coverage = context.get("locator_strategy_coverage", 0.0)

        # 添加指标
        self.add_metric(MetricResult(
            name="元素定位成功率",
            value=locate_rate,
            unit="%",
            target=90.0,
            weight=0.35,
        ))
        self.add_metric(MetricResult(
            name="交互成功率",
            value=interaction_rate,
            unit="%",
            target=85.0,
            weight=0.30,
        ))
        self.add_metric(MetricResult(
            name="动态元素识别率",
            value=dynamic_rate,
            unit="%",
            target=80.0,
            weight=0.20,
        ))
        self.add_metric(MetricResult(
            name="定位策略覆盖率",
            value=strategy_coverage,
            unit="%",
            target=70.0,
            weight=0.15,
        ))

        # 添加观察记录
        if locate_rate < 90:
            self.add_observation(f"元素定位成功率较低 ({locate_rate:.1f}%)，需优化选择器策略")
        if interaction_rate < 85:
            self.add_observation(f"交互成功率较低 ({interaction_rate:.1f}%)，可能存在时序问题")
        if dynamic_rate < 80:
            self.add_observation(f"动态元素识别率较低 ({dynamic_rate:.1f}%)，需增强等待策略")

        return self.get_result()


class DataExtractionEvaluator(BaseEvaluator):
    """数据提取能力评估器（权重 20%）"""

    def __init__(self):
        super().__init__(name="数据提取能力", weight=0.20)

    def evaluate(self, context: Dict[str, Any]) -> DimensionResult:
        """
        评估数据提取能力

        context 参数:
            - extraction_accuracy: 数据提取准确率 (%)
            - field_completeness: 字段完整率 (%)
            - data_quality_score: 数据质量得分 (0-100)
            - structured_extraction_rate: 结构化提取成功率 (%)
        """
        accuracy = context.get("extraction_accuracy", 0.0)
        completeness = context.get("field_completeness", 0.0)
        quality_score = context.get("data_quality_score", 0.0)
        structured_rate = context.get("structured_extraction_rate", 0.0)

        # 计算数据质量得分
        calculated_quality = accuracy * 0.6 + completeness * 0.4

        # 添加指标
        self.add_metric(MetricResult(
            name="数据提取准确率",
            value=accuracy,
            unit="%",
            target=85.0,
            weight=0.40,
        ))
        self.add_metric(MetricResult(
            name="字段完整率",
            value=completeness,
            unit="%",
            target=80.0,
            weight=0.30,
        ))
        self.add_metric(MetricResult(
            name="数据质量得分",
            value=calculated_quality,
            unit="分",
            target=80.0,
            weight=0.20,
        ))
        self.add_metric(MetricResult(
            name="结构化提取成功率",
            value=structured_rate,
            unit="%",
            target=75.0,
            weight=0.10,
        ))

        # 添加观察记录
        if accuracy < 85:
            self.add_observation(f"数据提取准确率较低 ({accuracy:.1f}%)，需优化提取逻辑")
        if completeness < 80:
            self.add_observation(f"字段完整率较低 ({completeness:.1f}%)，部分字段提取失败")

        return self.get_result()


class AntiDetectionEvaluator(BaseEvaluator):
    """反检测能力评估器（权重 15%）"""

    def __init__(self):
        super().__init__(name="反检测能力", weight=0.15)

    def evaluate(self, context: Dict[str, Any]) -> DimensionResult:
        """
        评估反检测能力

        context 参数:
            - anti_crawl_bypass_rate: 反爬绕过率 (%)
            - captcha_pass_rate: 验证码通过率 (%)
            - fingerprint_evasion_rate: 指纹伪装有效性 (%)
            - behavior_naturalness: 行为模拟自然度 (%)
        """
        bypass_rate = context.get("anti_crawl_bypass_rate", 0.0)
        captcha_rate = context.get("captcha_pass_rate", 0.0)
        fingerprint_rate = context.get("fingerprint_evasion_rate", 0.0)
        behavior_score = context.get("behavior_naturalness", 0.0)

        # 添加指标
        self.add_metric(MetricResult(
            name="反爬绕过率",
            value=bypass_rate,
            unit="%",
            target=70.0,
            weight=0.40,
        ))
        self.add_metric(MetricResult(
            name="验证码通过率",
            value=captcha_rate,
            unit="%",
            target=60.0,
            weight=0.30,
        ))
        self.add_metric(MetricResult(
            name="指纹伪装有效性",
            value=fingerprint_rate,
            unit="%",
            target=80.0,
            weight=0.20,
        ))
        self.add_metric(MetricResult(
            name="行为模拟自然度",
            value=behavior_score,
            unit="%",
            target=75.0,
            weight=0.10,
        ))

        # 添加观察记录
        if bypass_rate < 70:
            self.add_observation(f"反爬绕过率较低 ({bypass_rate:.1f}%)，需增强反检测策略")
        if captcha_rate < 60:
            self.add_observation(f"验证码通过率较低 ({captcha_rate:.1f}%)，建议集成第三方验证码服务")
        if fingerprint_rate < 80:
            self.add_observation(f"指纹伪装有效性较低 ({fingerprint_rate:.1f}%)，需优化 stealth 模块")

        return self.get_result()


class StabilityEvaluator(BaseEvaluator):
    """稳定性与恢复能力评估器（权重 15%）"""

    def __init__(self):
        super().__init__(name="稳定性与恢复", weight=0.15)

    def evaluate(self, context: Dict[str, Any]) -> DimensionResult:
        """
        评估稳定性与恢复能力

        context 参数:
            - execution_consistency: 重复执行一致性 (%)
            - error_recovery_rate: 异常恢复率 (%)
            - connection_stability: 连接稳定性 (%)
            - memory_stability: 内存稳定性 (MB/h)
        """
        consistency = context.get("execution_consistency", 0.0)
        recovery_rate = context.get("error_recovery_rate", 0.0)
        connection_stability = context.get("connection_stability", 0.0)
        memory_growth = context.get("memory_stability", 0.0)

        # 计算内存稳定性得分（增长越慢得分越高）
        memory_score = max(0, 100 - memory_growth * 5)  # 每 MB/h 扣 5 分

        # 添加指标
        self.add_metric(MetricResult(
            name="重复执行一致性",
            value=consistency,
            unit="%",
            target=90.0,
            weight=0.35,
        ))
        self.add_metric(MetricResult(
            name="异常恢复率",
            value=recovery_rate,
            unit="%",
            target=80.0,
            weight=0.30,
        ))
        self.add_metric(MetricResult(
            name="连接稳定性",
            value=connection_stability,
            unit="%",
            target=95.0,
            weight=0.20,
        ))
        self.add_metric(MetricResult(
            name="内存稳定性",
            value=memory_score,
            unit="分",
            target=90.0,
            weight=0.15,
            details={"growth_mb_per_hour": memory_growth},
        ))

        # 添加观察记录
        if consistency < 90:
            self.add_observation(f"重复执行一致性较低 ({consistency:.1f}%)，存在不稳定因素")
        if recovery_rate < 80:
            self.add_observation(f"异常恢复率较低 ({recovery_rate:.1f}%)，需增强容错机制")
        if memory_growth > 5:
            self.add_observation(f"内存增长较快 ({memory_growth:.1f} MB/h)，可能存在内存泄漏")

        return self.get_result()


class EvaluationFramework:
    """评估框架主类"""

    def __init__(self):
        self.evaluators = {
            "页面加载能力": PageLoadingEvaluator(),
            "元素定位能力": ElementLocateEvaluator(),
            "数据提取能力": DataExtractionEvaluator(),
            "反检测能力": AntiDetectionEvaluator(),
            "稳定性与恢复": StabilityEvaluator(),
        }
        self._results: Dict[str, DimensionResult] = {}
        self._start_time = 0.0
        self._end_time = 0.0

    def evaluate(self, website_name: str, website_url: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行完整评估

        Args:
            website_name: 网站名称
            website_url: 网站 URL
            context: 评估上下文数据

        Returns:
            评估结果字典
        """
        self._start_time = time.time()
        self._results = {}

        # 执行各维度评估
        for name, evaluator in self.evaluators.items():
            logger.info(f"执行评估: {name}")
            result = evaluator.evaluate(context.get(name, {}))
            self._results[name] = result

        self._end_time = time.time()

        # 计算综合评分
        overall_score = self._calculate_overall_score()
        grade = self._calculate_grade(overall_score)

        # 生成报告
        report = self._generate_report(website_name, website_url, overall_score, grade)

        return report

    def _calculate_overall_score(self) -> float:
        """计算综合评分"""
        total_weighted = 0.0
        total_weight = 0.0
        for name, result in self._results.items():
            total_weighted += result.score * result.weight
            total_weight += result.weight
        return round(total_weighted / total_weight, 2) if total_weight > 0 else 0.0

    @staticmethod
    def _calculate_grade(score: float) -> str:
        """计算等级"""
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

    def _generate_report(self, website_name: str, website_url: str, overall_score: float, grade: str) -> Dict[str, Any]:
        """生成评估报告"""
        duration = self._end_time - self._start_time

        report = {
            "website_name": website_name,
            "website_url": website_url,
            "eval_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "overall_score": overall_score,
            "grade": grade,
            "duration_seconds": round(duration, 2),
            "dimensions": {name: result.to_dict() for name, result in self._results.items()},
            "findings": self._generate_findings(),
            "recommendations": self._generate_recommendations(),
        }

        return report

    def _generate_findings(self) -> List[str]:
        """生成关键发现"""
        findings = []
        for name, result in self._results.items():
            if result.score >= 85:
                findings.append(f"✅ {name}: 表现优秀 ({result.score:.1f}分)")
            elif result.score >= 70:
                findings.append(f"⚠️  {name}: 基本可用 ({result.score:.1f}分)")
            else:
                findings.append(f"❌ {name}: 需要改进 ({result.score:.1f}分)")
        return findings

    def _generate_recommendations(self) -> List[str]:
        """生成改进建议"""
        recommendations = []
        for name, result in self._results.items():
            if result.score < 70:
                recommendations.append(f"🔧 优先优化 {name}（当前得分 {result.score:.1f}分）")
            elif result.score < 85:
                recommendations.append(f"📋 持续改进 {name}（当前得分 {result.score:.1f}分）")
        return recommendations

    def save_report(self, report: Dict[str, Any], output_dir: Path) -> Path:
        """保存评估报告"""
        output_dir.mkdir(parents=True, exist_ok=True)

        # 保存 JSON 报告
        json_path = output_dir / f"eval_{report['website_name']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        # 保存 Markdown 报告
        md_path = output_dir / f"eval_{report['website_name']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(self._report_to_markdown(report))

        logger.info(f"评估报告已保存: {json_path}, {md_path}")
        return json_path

    def _report_to_markdown(self, report: Dict[str, Any]) -> str:
        """将报告转换为 Markdown 格式"""
        lines = [
            f"# 网站操作能力评估报告\n",
            f"**评估网站**: {report['website_name']} ({report['website_url']})\n",
            f"**评估日期**: {report['eval_time']}\n",
            f"**综合评分**: {report['overall_score']}/100 ({report['grade']})\n",
            f"**评估耗时**: {report['duration_seconds']}秒\n",
            "\n",
        ]

        # 各维度得分
        lines.append("## 各维度得分\n")
        lines.append("| 维度 | 得分 | 权重 | 加权得分 |\n")
        lines.append("|------|------|------|----------|\n")
        for name, dim in report['dimensions'].items():
            lines.append(f"| {name} | {dim['score']:.1f} | {dim['weight']:.0%} | {dim['weighted_score']:.1f} |\n")
        lines.append("\n")

        # 关键发现
        if report.get('findings'):
            lines.append("## 关键发现\n")
            for finding in report['findings']:
                lines.append(f"- {finding}\n")
            lines.append("\n")

        # 改进建议
        if report.get('recommendations'):
            lines.append("## 改进建议\n")
            for rec in report['recommendations']:
                lines.append(f"- [ ] {rec}\n")
            lines.append("\n")

        return "".join(lines)


# 便捷函数
def evaluate_website(website_name: str, website_url: str, context: Dict[str, Any], output_dir: Optional[Path] = None) -> Dict[str, Any]:
    """
    便捷函数：执行网站评估

    Args:
        website_name: 网站名称
        website_url: 网站 URL
        context: 评估上下文数据
        output_dir: 报告输出目录

    Returns:
        评估结果字典
    """
    framework = EvaluationFramework()
    report = framework.evaluate(website_name, website_url, context)

    if output_dir:
        framework.save_report(report, output_dir)

    return report


if __name__ == "__main__":
    # 测试示例
    import sys
    from pathlib import Path

    logging.basicConfig(level=logging.INFO)

    # 模拟评估数据
    test_context = {
        "页面加载能力": {
            "page_access_rate": 95.0,
            "first_contentful_paint": 2.5,
            "page_load_time": 8.0,
            "timeout_handling_rate": 92.0,
        },
        "元素定位能力": {
            "element_locate_rate": 88.0,
            "interaction_success_rate": 85.0,
            "dynamic_element_rate": 82.0,
            "locator_strategy_coverage": 75.0,
        },
        "数据提取能力": {
            "extraction_accuracy": 85.0,
            "field_completeness": 80.0,
            "data_quality_score": 82.0,
            "structured_extraction_rate": 78.0,
        },
        "反检测能力": {
            "anti_crawl_bypass_rate": 72.0,
            "captcha_pass_rate": 65.0,
            "fingerprint_evasion_rate": 85.0,
            "behavior_naturalness": 78.0,
        },
        "稳定性与恢复": {
            "execution_consistency": 90.0,
            "error_recovery_rate": 85.0,
            "connection_stability": 96.0,
            "memory_stability": 3.5,
        },
    }

    # 执行评估
    report = evaluate_website(
        website_name="测试网站",
        website_url="https://example.com",
        context=test_context,
        output_dir=Path("./temp/eval_reports")
    )

    print(f"\n综合评分: {report['overall_score']}/100 ({report['grade']})")
    print(f"评估耗时: {report['duration_seconds']}秒")
