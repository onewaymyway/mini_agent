"""
网站操作能力评估器（主入口）

整合所有评估维度，提供统一的评估接口。
"""

import logging
from typing import Any, Dict, List, Optional

from .base_evaluator import BaseEvaluator
from .success_rate_evaluator import SuccessRateEvaluator
from .performance_evaluator import PerformanceEvaluator
from .element_evaluator import ElementEvaluator
from .anti_detection_evaluator import AntiDetectionEvaluator
from .stability_evaluator import StabilityEvaluator
from .error_recovery_evaluator import ErrorRecoveryEvaluator
from .report_generator import ReportGenerator

logger = logging.getLogger(__name__)


class WebsiteEvaluator:
    """
    网站操作能力评估器

    提供完整的评估流程：
    1. 初始化评估器
    2. 执行各维度评估
    3. 生成评估报告
    """

    # 评估维度定义
    DIMENSIONS = {
        "scraping_success": {
            "class": SuccessRateEvaluator,
            "name": "抓取成功率",
            "weight": 0.30,
        },
        "performance": {
            "class": PerformanceEvaluator,
            "name": "页面加载性能",
            "weight": 0.20,
        },
        "element_accuracy": {
            "class": ElementEvaluator,
            "name": "元素定位准确率",
            "weight": 0.20,
        },
        "anti_detection": {
            "class": AntiDetectionEvaluator,
            "name": "反检测能力",
            "weight": 0.15,
        },
        "stability": {
            "class": StabilityEvaluator,
            "name": "稳定性",
            "weight": 0.10,
        },
        "error_recovery": {
            "class": ErrorRecoveryEvaluator,
            "name": "错误恢复能力",
            "weight": 0.05,
        },
    }

    def __init__(self, website_url: str, website_name: Optional[str] = None):
        """
        初始化评估器

        Args:
            website_url: 目标网站 URL
            website_name: 网站名称（可选，默认为 URL 域名）
        """
        self.website_url = website_url
        self.website_name = website_name or self._extract_domain(website_url)
        self._evaluators: Dict[str, BaseEvaluator] = {}
        self._results: Dict[str, Dict[str, Any]] = {}
        self._report_generator = ReportGenerator()

        # 初始化所有评估器
        self._init_evaluators()

    def _init_evaluators(self):
        """初始化所有评估器"""
        for key, config in self.DIMENSIONS.items():
            self._evaluators[key] = config["class"]()

    @staticmethod
    def _extract_domain(url: str) -> str:
        """从 URL 提取域名"""
        if "://" in url:
            url = url.split("://")[1]
        return url.split("/")[0].split(":")[0]

    def evaluate(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行完整评估

        Args:
            context: 评估上下文数据，包含各维度的原始数据

        Returns:
            完整的评估报告
        """
        logger.info(f"开始评估网站: {self.website_url}")

        # 执行各维度评估
        for key, evaluator in self._evaluators.items():
            logger.info(f"执行评估: {evaluator.name}")
            result = evaluator.evaluate(context.get(key, {}))
            self._results[key] = result
            self._report_generator.add_dimension(
                evaluator.name,
                {**result, "weight": self.DIMENSIONS[key]["weight"]}
            )

        # 生成报告
        report = self._report_generator.generate_report()
        report["website_url"] = self.website_url
        report["website_name"] = self.website_name

        logger.info(f"评估完成，综合得分: {report['overall_score']}")
        return report

    def evaluate_incremental(self, dimension: str, context: Dict[str, Any]):
        """
        增量评估单个维度

        Args:
            dimension: 维度名称（如 'scraping_success'）
            context: 该维度的评估数据
        """
        if dimension not in self._evaluators:
            raise ValueError(f"未知的评估维度: {dimension}")

        evaluator = self._evaluators[dimension]
        result = evaluator.evaluate(context)
        self._results[dimension] = result
        self._report_generator.add_dimension(
            evaluator.name,
            {**result, "weight": self.DIMENSIONS[dimension]["weight"]}
        )

    def get_report(self) -> Dict[str, Any]:
        """获取当前评估报告"""
        if not self._results:
            raise RuntimeError("尚未执行评估，请先调用 evaluate()")
        return self._report_generator.generate_report()

    def get_markdown_report(self) -> str:
        """获取 Markdown 格式报告"""
        if not self._results:
            raise RuntimeError("尚未执行评估，请先调用 evaluate()")
        return self._report_generator.generate_markdown_report()

    def save_report(self, filepath: str, format: str = "json"):
        """保存报告到文件"""
        if not self._results:
            raise RuntimeError("尚未执行评估，请先调用 evaluate()")
        self._report_generator.save_report(filepath, format)

    def reset(self):
        """重置评估器状态"""
        self._results = {}
        self._report_generator = ReportGenerator()
        for evaluator in self._evaluators.values():
            evaluator.reset()


def evaluate_website(website_url: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """
    便捷函数：评估单个网站

    Args:
        website_url: 目标网站 URL
        context: 评估上下文数据

    Returns:
        评估报告
    """
    evaluator = WebsiteEvaluator(website_url)
    return evaluator.evaluate(context)


def batch_evaluate(sites: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    批量评估多个网站

    Args:
        sites: 网站列表，每个元素包含 'url' 和 'context'

    Returns:
        评估报告列表
    """
    results = []
    for site in sites:
        url = site['url']
        context = site.get('context', {})
        logger.info(f"评估网站: {url}")
        result = evaluate_website(url, context)
        results.append(result)
    return results
