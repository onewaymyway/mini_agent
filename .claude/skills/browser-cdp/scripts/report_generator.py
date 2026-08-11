"""
评估报告生成器 - 生成结构化的兼容性评估报告

功能：
1. 单次评估报告生成
2. 批量评估报告生成
3. 对比分析报告生成
4. 趋势分析报告生成
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ReportGenerator:
    """评估报告生成器"""

    def __init__(self, output_dir: Path = None):
        self.output_dir = output_dir or Path(__file__).parent.parent / "output" / "eval_reports"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_single_report(self, result: Dict[str, Any], report_type: str = "evaluation") -> Path:
        """
        生成单次评估报告

        Args:
            result: 评估结果
            report_type: 报告类型 (evaluation/compatibility)

        Returns:
            报告文件路径
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        website_name = result.get("website_name", "unknown")

        # 生成 Markdown 报告
        md_content = self._generate_markdown_report(result, report_type)
        md_path = self.output_dir / f"{report_type}_{website_name}_{timestamp}.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        # 生成 JSON 报告
        json_path = self.output_dir / f"{report_type}_{website_name}_{timestamp}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        logger.info(f"报告已生成: {md_path}")
        return md_path

    def generate_batch_report(self, results: List[Dict[str, Any]], report_type: str = "evaluation") -> Path:
        """
        生成批量评估报告

        Args:
            results: 评估结果列表
            report_type: 报告类型

        Returns:
            报告文件路径
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_name = f"{report_type}_batch_{timestamp}"

        # 生成 Markdown 报告
        md_content = self._generate_batch_markdown_report(results, report_type)
        md_path = self.output_dir / f"{report_name}.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        # 生成 JSON 报告
        json_path = self.output_dir / f"{report_name}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        logger.info(f"批量报告已生成: {md_path}")
        return md_path

    def generate_comparison_report(self, results: List[Dict[str, Any]], comparison_type: str = "websites") -> Path:
        """
        生成对比分析报告

        Args:
            results: 评估结果列表
            comparison_type: 对比类型 (websites/dimensions)

        Returns:
            报告文件路径
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_name = f"comparison_{comparison_type}_{timestamp}"

        # 生成 Markdown 报告
        md_content = self._generate_comparison_markdown_report(results, comparison_type)
        md_path = self.output_dir / f"{report_name}.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        logger.info(f"对比报告已生成: {md_path}")
        return md_path

    def _generate_markdown_report(self, result: Dict[str, Any], report_type: str = "evaluation") -> str:
        """生成 Markdown 报告内容"""
        if report_type == "evaluation":
            return self._generate_evaluation_markdown(result)
        elif report_type == "compatibility":
            return self._generate_compatibility_markdown(result)
        else:
            return self._generate_evaluation_markdown(result)

    def _generate_evaluation_markdown(self, result: Dict[str, Any]) -> str:
        """生成评估报告 Markdown"""
        lines = [
            f"# 网站操作能力评估报告\n",
            f"**评估网站**: {result['website_name']} ({result['website_url']})\n",
            f"**评估日期**: {result.get('eval_time', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}\n",
            f"**综合评分**: {result.get('overall_score', 0):.1f}/100 ({result.get('grade', 'N/A')})\n",
            f"**评估耗时**: {result.get('duration_seconds', 0):.1f}秒\n",
            "\n",
        ]

        # 各维度得分
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
        for name, dim in result.get("dimensions", {}).items():
            weight = weights.get(name, 0)
            weighted = dim.get("score", 0) * weight
            lines.append(f"| {name} | {dim.get('score', 0):.1f} | {weight:.0%} | {weighted:.1f} |\n")
        lines.append("\n")

        # 场景执行结果
        if result.get("scenarios"):
            lines.append("## 场景执行结果\n")
            lines.append("| 场景 ID | 成功 | 耗时 (s) | 错误 |\n")
            lines.append("|---------|------|----------|------|\n")
            for s in result["scenarios"]:
                status = "✓" if s.get("success") else "✗"
                error = (s.get("error") or "")[:30]
                lines.append(f"| {s.get('id', '')} | {status} | {s.get('duration', 0):.2f} | {error} |\n")
            lines.append("\n")

        # 关键发现
        if result.get("findings"):
            lines.append("## 关键发现\n")
            for finding in result["findings"]:
                lines.append(f"- {finding}\n")
            lines.append("\n")

        # 改进建议
        if result.get("recommendations"):
            lines.append("## 改进建议\n")
            for rec in result["recommendations"]:
                lines.append(f"- {rec}\n")
            lines.append("\n")

        # 错误信息
        if result.get("errors"):
            lines.append("## 执行错误\n")
            for err in result["errors"][:5]:
                lines.append(f"- {err}\n")
            lines.append("\n")

        return "".join(lines)

    def _generate_compatibility_markdown(self, result: Dict[str, Any]) -> str:
        """生成兼容性报告 Markdown"""
        lines = [
            f"# 网站兼容性检测报告\n",
            f"**检测网站**: {result['website_name']} ({result['website_url']})\n",
            f"**检测时间**: {result.get('check_time', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}\n",
            f"**兼容性得分**: {result.get('compatibility_score', 0):.1f}/100 ({result.get('compatibility_level', 'N/A')})\n",
            f"**检测耗时**: {result.get('duration_seconds', 0):.1f}秒\n",
            "\n",
        ]

        # 可访问性
        lines.append("## 可访问性检测\n")
        acc = result.get("accessibility", {})
        lines.append(f"- 可访问性: {'✓' if acc.get('reachable') else '✗'}\n")
        lines.append(f"- HTTP 状态: {acc.get('http_status', 'N/A')}\n")
        lines.append(f"- 加载时间: {acc.get('load_time', 'N/A')}s\n")
        lines.append(f"- HTTPS: {'✓' if acc.get('ssl_enabled') else '✗'}\n")
        lines.append(f"- 移动端友好: {'✓' if acc.get('mobile_friendly') else '✗'}\n")
        lines.append(f"- 可访问性得分: {acc.get('accessibility_score', 0):.1f}\n\n")

        # 反爬机制
        lines.append("## 反爬机制检测\n")
        anti = result.get("anti_crawl", {})
        lines.append(f"- 存在反爬: {'✓' if anti.get('has_anti_crawl') else '✗'}\n")
        if anti.get("anti_crawl_types"):
            lines.append(f"- 反爬类型: {', '.join(anti['anti_crawl_types'])}\n")
        lines.append(f"- 反爬难度: {anti.get('anti_crawl_difficulty', 'N/A')}\n")
        lines.append(f"- 反爬得分: {anti.get('anti_crawl_score', 0):.1f}\n\n")

        # 技术栈
        lines.append("## 技术栈识别\n")
        tech = result.get("tech_stack", {})
        lines.append(f"- 前端框架: {tech.get('framework', 'N/A')}\n")
        lines.append(f"- CMS: {tech.get('cms', 'N/A')}\n")
        lines.append(f"- SPA: {'✓' if tech.get('spa') else '✗'}\n")
        lines.append(f"- API 类型: {tech.get('api_type', 'N/A')}\n\n")

        # 建议
        if result.get("recommendations"):
            lines.append("## 改进建议\n")
            for rec in result["recommendations"]:
                lines.append(f"- {rec}\n")
            lines.append("\n")

        return "".join(lines)

    def _generate_batch_markdown_report(self, results: List[Dict[str, Any]], report_type: str = "evaluation") -> str:
        """生成批量报告 Markdown"""
        lines = ["# 网站操作能力评估汇总报告\n", f"\n**评估时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n", f"**评估网站数**: {len(results)}\n\n"]

        lines.append("## 评估结果概览\n")
        lines.append("| 网站 | 综合评分 | 等级 | 场景成功率 |\n")
        lines.append("|------|----------|------|-----------|\n")
        for r in results:
            scenarios = r.get("scenarios", [])
            success_count = sum(1 for s in scenarios if s.get("success"))
            success_rate = f"{success_count}/{len(scenarios)}" if scenarios else "N/A"
            lines.append(f"| {r.get('website_name', 'N/A')} | {r.get('overall_score', 0):.1f} | {r.get('grade', 'N/A')} | {success_rate} |\n")
        lines.append("\n")

        # 各维度平均得分
        lines.append("## 各维度平均得分\n")
        lines.append("| 维度 | 平均得分 |\n")
        lines.append("|------|----------|\n")
        dimension_scores = {}
        for r in results:
            for name, dim in r.get("dimensions", {}).items():
                if name not in dimension_scores:
                    dimension_scores[name] = []
                dimension_scores[name].append(dim.get("score", 0))
        for name, scores in dimension_scores.items():
            avg = sum(scores) / len(scores) if scores else 0
            lines.append(f"| {name} | {avg:.1f} |\n")
        lines.append("\n")

        return "".join(lines)

    def _generate_comparison_markdown_report(self, results: List[Dict[str, Any]], comparison_type: str = "websites") -> str:
        """生成对比报告 Markdown"""
        lines = ["# 网站操作能力对比分析报告\n", f"\n**对比时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n", f"**对比网站数**: {len(results)}\n\n"]

        if comparison_type == "websites":
            lines.append("## 网站对比\n")
            lines.append("| 网站 | 综合评分 | 页面加载 | 元素定位 | 数据提取 | 反检测 | 稳定性 |\n")
            lines.append("|------|----------|----------|----------|----------|--------|--------|\n")
            for r in results:
                dims = r.get("dimensions", {})
                lines.append(f"| {r.get('website_name', 'N/A')} | {r.get('overall_score', 0):.1f} | {dims.get('页面加载能力', {}).get('score', 0):.1f} | {dims.get('元素定位能力', {}).get('score', 0):.1f} | {dims.get('数据提取能力', {}).get('score', 0):.1f} | {dims.get('反检测能力', {}).get('score', 0):.1f} | {dims.get('稳定性与恢复', {}).get('score', 0):.1f} |\n")
            lines.append("\n")

        elif comparison_type == "dimensions":
            lines.append("## 维度对比\n")
            dimension_scores = {}
            for r in results:
                for name, dim in r.get("dimensions", {}).items():
                    if name not in dimension_scores:
                        dimension_scores[name] = []
                    dimension_scores[name].append(dim.get("score", 0))

            lines.append("| 维度 | 最高分 | 最低分 | 平均分 | 标准差 |\n")
            lines.append("|------|--------|--------|--------|--------|\n")
            for name, scores in dimension_scores.items():
                if scores:
                    import statistics
                    lines.append(f"| {name} | {max(scores):.1f} | {min(scores):.1f} | {sum(scores)/len(scores):.1f} | {statistics.stdev(scores):.1f if len(scores) > 1 else 0:.1f} |\n")
            lines.append("\n")

        return "".join(lines)


# 便捷函数
def generate_evaluation_report(result: Dict[str, Any], output_dir: Path = None) -> Path:
    """便捷函数：生成评估报告"""
    generator = ReportGenerator(output_dir=output_dir)
    return generator.generate_single_report(result, "evaluation")


def generate_compatibility_report(result: Dict[str, Any], output_dir: Path = None) -> Path:
    """便捷函数：生成兼容性报告"""
    generator = ReportGenerator(output_dir=output_dir)
    return generator.generate_single_report(result, "compatibility")


def generate_batch_report(results: List[Dict[str, Any]], output_dir: Path = None) -> Path:
    """便捷函数：生成批量报告"""
    generator = ReportGenerator(output_dir=output_dir)
    return generator.generate_batch_report(results, "evaluation")


def generate_comparison_report(results: List[Dict[str, Any]], comparison_type: str = "websites", output_dir: Path = None) -> Path:
    """便捷函数：生成对比报告"""
    generator = ReportGenerator(output_dir=output_dir)
    return generator.generate_comparison_report(results, comparison_type)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    # 测试示例
    test_result = {
        "website_name": "百度",
        "website_url": "https://www.baidu.com",
        "eval_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "overall_score": 85.5,
        "grade": "良好 (B)",
        "dimensions": {
            "页面加载能力": {"score": 88.0, "metrics": {}},
            "元素定位能力": {"score": 90.0, "metrics": {}},
            "数据提取能力": {"score": 82.0, "metrics": {}},
            "反检测能力": {"score": 75.0, "metrics": {}},
            "稳定性与恢复": {"score": 88.0, "metrics": {}},
        },
        "scenarios": [
            {"id": "navigate", "success": True, "duration": 2.5},
            {"id": "search", "success": True, "duration": 3.0},
        ],
        "findings": ["✅ 页面加载能力: 表现优秀 (88.0分)"],
        "recommendations": ["📋 持续改进 反检测能力（当前得分 75.0分）"],
        "errors": [],
        "duration_seconds": 10.5,
    }

    path = generate_evaluation_report(test_result)
    print(f"报告已生成: {path}")
