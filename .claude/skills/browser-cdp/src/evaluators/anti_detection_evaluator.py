"""
反检测能力评估器

评估指标：
- 反爬绕过率
- 验证码通过率
- 指纹伪装有效性
- 行为模拟自然度
"""

import logging
from typing import Any, Dict

from .base_evaluator import BaseEvaluator, MetricResult

logger = logging.getLogger(__name__)


class AntiDetectionEvaluator(BaseEvaluator):
    """反检测能力评估器"""

    def __init__(self):
        super().__init__(name="反检测能力", weight=0.15)

    def evaluate(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        评估反检测能力

        context 参数:
            - anti_crawl_triggered: 触发反爬机制次数
            - anti_crawl_bypassed: 成功绕过反爬次数
            - captcha_triggered: 触发验证码次数
            - captcha_passed: 成功通过验证码次数
            - fingerprint_detected: 被识别为机器人的次数
            - total_checks: 总检测次数
            - human_like_score: 行为自然度评分（0-100）
        """
        anti_crawl_triggered = context.get("anti_crawl_triggered", 0)
        anti_crawl_bypassed = context.get("anti_crawl_bypassed", 0)
        captcha_triggered = context.get("captcha_triggered", 0)
        captcha_passed = context.get("captcha_passed", 0)
        fingerprint_detected = context.get("fingerprint_detected", 0)
        total_checks = context.get("total_checks", 0)
        human_like_score = context.get("human_like_score", 0.0)

        # 计算各项指标
        anti_crawl_bypass_rate = self._safe_divide(
            anti_crawl_bypassed, anti_crawl_triggered, 0.0
        ) * 100

        captcha_pass_rate = self._safe_divide(
            captcha_passed, captcha_triggered, 0.0
        ) * 100

        fingerprint_evasion = self._safe_divide(
            total_checks - fingerprint_detected, total_checks, 0.0
        ) * 100 if total_checks > 0 else 0.0

        # 添加指标
        self.add_metric(MetricResult(
            name="反爬绕过率",
            value=anti_crawl_bypass_rate,
            unit="%",
            target=70.0,
            weight=0.35,
            details={"bypassed": anti_crawl_bypassed, "triggered": anti_crawl_triggered}
        ))

        self.add_metric(MetricResult(
            name="验证码通过率",
            value=captcha_pass_rate,
            unit="%",
            target=60.0,
            weight=0.25,
            details={"passed": captcha_passed, "triggered": captcha_triggered}
        ))

        self.add_metric(MetricResult(
            name="指纹伪装有效性",
            value=fingerprint_evasion,
            unit="%",
            target=80.0,
            weight=0.20,
            details={"detected": fingerprint_detected, "total": total_checks}
        ))

        self.add_metric(MetricResult(
            name="行为模拟自然度",
            value=human_like_score,
            unit="分",
            target=75.0,
            weight=0.20,
            details={}
        ))

        # 计算综合得分
        comprehensive_score = (
            anti_crawl_bypass_rate * 0.35 +
            captcha_pass_rate * 0.25 +
            fingerprint_evasion * 0.20 +
            human_like_score * 0.20
        )

        self.add_metric(MetricResult(
            name="综合反检测得分",
            value=comprehensive_score,
            unit="分",
            target=75.0,
            weight=1.0,
            details={
                "bypass_rate": round(anti_crawl_bypass_rate, 2),
                "captcha_rate": round(captcha_pass_rate, 2),
                "fingerprint_evasion": round(fingerprint_evasion, 2),
                "human_like": round(human_like_score, 2),
            }
        ))

        # 添加观察记录
        if anti_crawl_bypass_rate < 70:
            self.add_observation(f"反爬绕过率较低 ({anti_crawl_bypass_rate:.1f}%)，需增强反检测策略")
        if captcha_pass_rate < 60:
            self.add_observation(f"验证码通过率较低 ({captcha_pass_rate:.1f}%)，建议集成第三方验证码服务")
        if fingerprint_evasion < 80:
            self.add_observation(f"指纹伪装有效性较低 ({fingerprint_evasion:.1f}%)，需优化 stealth 模块")

        return self.get_result().to_dict()

    @staticmethod
    def _safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
        """安全除法，避免除零错误"""
        if denominator == 0:
            return default
        return numerator / denominator
