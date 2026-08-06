"""
评估报告生成器

生成结构化的评估报告，包括：
- 综合评分
- 各维度得分详情
- 关键发现
- 改进建议
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ReportGenerator:
    """评估报告生成器"""

    def __init__(self):
        self._dimensions: Dict[str, Dict[str, Any]] = {}
        self._metadata: Dict[str, Any] = {}

    def set_metadata(self, **kwargs):
        """设置报告元数据"""
        self._metadata.update(kwargs)

    def add_dimension(self, name: str, result: Dict[str, Any]):
        """添加维度评估结果"""
        self._dimensions[name] = result

    def calculate_overall_score(self) -> float:
        """计算综合评分"""
        if not self._dimensions:
            return 0.0

        total_weighted_score = 0.0
        total_weight = 0.0

        for name, result in self._dimensions.items():
            weight = result.get("weight", 1.0)
            score = result.get("score", 0.0)
            total_weighted_score += score * weight
            total_weight += weight

        if total_weight == 0:
            return 0.0

        return total_weighted_score / total_weight

    def generate_findings(self) -> List[str]:
        """生成关键发现"""
        findings = []

        for name, result in self._dimensions.items():
            score = result.get("score", 0.0)
            observations = result.get("observations", [])

            # 识别优势
            if score >= 85:
                findings.append(f"✅ **{name}** 表现优秀 (得分: {score:.1f})")
            elif score >= 70:
                findings.append(f"⚠️  **{name}** 表现良好 (得分: {score:.1f})")
            else:
                findings.append(f"❌ **{name}** 需要改进 (得分: {score:.1f})")

            # 添加具体观察
            for obs in observations[:2]:  # 最多显示2条观察
                findings.append(f"   - {obs}")

        return findings

    def generate_recommendations(self) -> List[str]:
        """生成改进建议"""
        recommendations = []

        for name, result in self._dimensions.items():
            score = result.get("score", 0.0)

            if score < 70:
                if name == "反检测能力":
                    recommendations.append("- [ ] 优化 stealth.py 反检测模块")
                    recommendations.append("- [ ] 增强 captcha_handler.py 验证码处理能力")
                    recommendations.append("- [ ] 添加更多代理池节点")
                elif name == "抓取成功率":
                    recommendations.append("- [ ] 优化元素选择器策略")
                    recommendations.append("- [ ] 增强动态内容等待机制")
                elif name == "页面加载性能":
                    recommendations.append("- [ ] 优化网络请求策略")
                    recommendations.append("- [ ] 减少不必要的页面等待")
                elif name == "元素定位准确率":
                    recommendations.append("- [ ] 增加定位策略类型")
                    recommendations.append("- [ ] 优化动态元素识别逻辑")
                elif name == "稳定性":
                    recommendations.append("- [ ] 检查内存泄漏问题")
                    recommendations.append("- [ ] 优化 CDP 连接管理")
                elif name == "错误恢复能力":
                    recommendations.append("- [ ] 增强错误分类逻辑")
                    recommendations.append("- [ ] 优化重试策略")

        return recommendations

    def generate_report(self) -> Dict[str, Any]:
        """生成完整评估报告"""
        overall_score = self.calculate_overall_score()

        report = {
            "report_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "overall_score": round(overall_score, 2),
            "grade": self._calculate_grade(overall_score),
            "dimensions": self._dimensions,
            "findings": self.generate_findings(),
            "recommendations": self.generate_recommendations(),
        }

        # 添加元数据
        report.update(self._metadata)

        return report

    def generate_markdown_report(self) -> str:
        """生成 Markdown 格式报告"""
        report = self.generate_report()

        lines = [
            "# 网站操作能力评估报告\n",
            f"**评估日期**: {report['report_date']}\n",
            f"**综合评分**: {report['overall_score']}/100 ({report['grade']})\n",
            "\n"
        ]

        # 各维度得分表
        lines.append("## 各维度得分\n")
        lines.append("| 维度 | 得分 | 权重 | 加权得分 |\n")
        lines.append("|------|------|------|----------|\n")

        for name, result in report['dimensions'].items():
            weighted = result.get('weighted_score', 0)
            lines.append(f"| {name} | {result['score']:.1f} | {result['weight']:.0%} | {weighted:.1f} |\n")

        lines.append("\n")

        # 关键发现
        if report['findings']:
            lines.append("## 关键发现\n")
            for finding in report['findings']:
                lines.append(finding + "\n")
            lines.append("\n")

        # 改进建议
        if report['recommendations']:
            lines.append("## 改进建议\n")
            for rec in report['recommendations']:
                lines.append(rec + "\n")
            lines.append("\n")

        return "".join(lines)

    def save_report(self, filepath: str, format: str = "json"):
        """保存报告到文件"""
        if format == "json":
            report = self.generate_report()
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
        elif format == "markdown":
            markdown = self.generate_markdown_report()
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(markdown)
        else:
            raise ValueError(f"不支持的格式: {format}")

        logger.info(f"报告已保存到: {filepath}")

    @staticmethod
    def _calculate_grade(score: float) -> str:
        """根据分数计算等级"""
        if score >= 90:
            return "优秀 (A)"
        elif score >= 80:
            return "良好 (B)"
        elif score >= 70:
            return "中等 (C)"
        elif score >= 60:
            return "及格 (D)"
        else:
            return "不及格 (F)"
