#!/usr/bin/env python3
"""评估结果验证工具 - 验证评估结果的一致性和可靠性"""

import json
import statistics
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any
import argparse


class EvaluationValidator:
    """评估验证器"""
    
    def __init__(self, website_name: str, eval_dir: Path):
        self.website_name = website_name
        self.eval_dir = eval_dir
        self.reports = []
    
    def load_reports(self, count: int = 3):
        """加载最近的评估报告（只加载相同格式）"""
        # 查找匹配的网站报告
        pattern = f"eval_{self.website_name}_*.json"
        all_reports = sorted(
            self.eval_dir.glob(pattern),
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )

        # 先读取第一份报告确定格式
        if not all_reports:
            return 0
        with open(all_reports[0], 'r', encoding='utf-8') as f:
            first = json.load(f)
        fmt = 'v2' if 'dimension_scores' in first else 'v1'

        # 只加载相同格式的报告
        for report_path in all_reports:
            if len(self.reports) >= count:
                break
            with open(report_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if ('dimension_scores' in data and fmt == 'v2') or ('dimensions' in data and fmt == 'v1'):
                self.reports.append(data)

        return len(self.reports)
    
    def _get_overall_score(self, report: Dict[str, Any]) -> float:
        """获取综合评分（兼容不同格式）"""
        if 'overall_score' in report:
            return report['overall_score']
        return 0.0

    def _get_pass_rate(self, report: Dict[str, Any]) -> float:
        """获取通过率（兼容不同格式）"""
        if 'pass_rate' in report:
            return report['pass_rate']
        # 从 dimensions 计算
        if 'dimensions' in report:
            scores = [d.get('score', 0) for d in report['dimensions'].values()]
            if scores:
                return sum(scores) / len(scores)
        return 0.0

    def validate_consistency(self) -> Dict[str, Any]:
        """验证一致性"""
        if len(self.reports) < 2:
            return {"valid": False, "reason": "报告数量不足，需要至少2份报告"}
        
        results = {
            "overall_score": self._validate_metric(
                [self._get_overall_score(r) for r in self.reports]
            ),
            "pass_rate": self._validate_metric(
                [self._get_pass_rate(r) for r in self.reports]
            ),
            "dimensions": {},
            "cases": {}
        }
        
        # 验证维度得分一致性（兼容两种格式）
        all_dim_keys = set()
        for r in self.reports:
            if 'dimension_scores' in r:
                all_dim_keys.update(r['dimension_scores'].keys())
            if 'dimensions' in r:
                all_dim_keys.update(r['dimensions'].keys())

        for dim in all_dim_keys:
            scores = []
            for r in self.reports:
                if 'dimension_scores' in r and dim in r['dimension_scores']:
                    scores.append(r['dimension_scores'][dim].get('rate', 0))
                elif 'dimensions' in r and dim in r['dimensions']:
                    scores.append(r['dimensions'][dim].get('score', 0))
            if scores:
                results['dimensions'][dim] = self._validate_metric(scores)
        
        # 验证用例通过率一致性
        if self.reports and 'test_results' in self.reports[0]:
            case_ids = set()
            for r in self.reports:
                if 'test_results' in r and r['test_results']:
                    case_ids.update(t['case_id'] for t in r['test_results'])
            
            for case_id in case_ids:
                pass_rates = []
                for r in self.reports:
                    if 'test_results' in r:
                        case_results = [t for t in r['test_results'] if t['case_id'] == case_id]
                        if case_results:
                            pass_rates.append(1 if case_results[0]['success'] else 0)
                if pass_rates:
                    results['cases'][case_id] = {
                        "pass_rate": sum(pass_rates) / len(pass_rates) * 100,
                        "consistent": len(set(pass_rates)) == 1
                    }
        
        return results
    
    def _validate_metric(self, values: List[float]) -> Dict[str, Any]:
        """验证单个指标的一致性"""
        if len(values) < 2:
            return {"valid": False, "reason": "数据不足"}
        
        mean = statistics.mean(values)
        stdev = statistics.stdev(values) if len(values) > 1 else 0
        cv = (stdev / mean * 100) if mean != 0 else 0
        
        if cv <= 5:
            consistency = "高度一致"
        elif cv <= 10:
            consistency = "基本一致"
        else:
            consistency = "不一致"
        
        return {
            "values": [round(v, 2) for v in values],
            "mean": round(mean, 2),
            "stdev": round(stdev, 2),
            "cv": round(cv, 2),
            "consistent": cv <= 10,
            "consistency_level": consistency
        }
    
    def _detect_format(self, report: Dict[str, Any]) -> str:
        """检测报告格式"""
        if 'dimension_scores' in report:
            return 'v2'
        elif 'dimensions' in report:
            return 'v1'
        return 'unknown'

    def generate_report(self) -> str:
        """生成验证报告"""
        consistency = self.validate_consistency()
        
        if not consistency.get('valid', True):
            return f"# 评估验证报告\n\n❌ 验证失败: {consistency.get('reason', '未知错误')}\n"
        
        # 检测格式一致性
        formats = [self._detect_format(r) for r in self.reports]
        format_consistent = len(set(formats)) == 1
        
        lines = [
            "# 评估验证报告\n",
            f"\n## 1. 验证概述\n",
            f"- 验证时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
            f"- 验证网站: {self.website_name}\n",
            f"- 验证次数: {len(self.reports)}次\n",
            f"- 报告格式: {'统一' if format_consistent else '混合 (' + ', '.join(set(formats)) + ')'}\n",
            "\n",
        ]
        
        # 格式警告
        if not format_consistent:
            lines.append("> ⚠️ **注意**: 检测到不同格式的评估报告，可能使用不同评分系统。\n")
            lines.append("> 建议仅比较相同格式的报告以获得准确的一致性分析。\n\n")
        
        # 综合评分一致性
        lines.append("## 2. 一致性分析\n\n### 2.1 综合评分一致性\n")
        lines.append("| 评估次数 | 综合评分 | 等级 | 偏差 |\n")
        lines.append("|----------|----------|------|------|\n")
        for i, r in enumerate(self.reports, 1):
            score = self._get_overall_score(r)
            grade = r.get('grade', 'N/A')
            if i == 1:
                lines.append(f"| 第{i}次 | {score} | {grade} | - |\n")
            else:
                prev_score = self._get_overall_score(self.reports[0])
                if prev_score > 0:
                    change = (score - prev_score) / prev_score * 100
                else:
                    change = 0
                lines.append(f"| 第{i}次 | {score} | {grade} | {change:+.2f}% |\n")
        
        if consistency['overall_score'].get('mean'):
            lines.append(f"\n- 均值: {consistency['overall_score']['mean']}\n")
            lines.append(f"- 标准差: {consistency['overall_score']['stdev']}\n")
            lines.append(f"- 变异系数: {consistency['overall_score']['cv']}%\n")
            lines.append(f"- 判定: {'✅ 高度一致' if consistency['overall_score']['cv'] <= 5 else '✅ 基本一致' if consistency['overall_score']['cv'] <= 10 else '❌ 不一致'}\n")
        
        # 通过率一致性
        lines.append("\n### 2.2 通过率一致性\n")
        lines.append("| 评估次数 | 通过率 | 偏差 |\n")
        lines.append("|----------|--------|------|\n")
        for i, r in enumerate(self.reports, 1):
            rate = self._get_pass_rate(r)
            if i == 1:
                lines.append(f"| 第{i}次 | {rate}% | - |\n")
            else:
                prev_rate = self._get_pass_rate(self.reports[0])
                change = rate - prev_rate
                lines.append(f"| 第{i}次 | {rate}% | {change:+.2f}% |\n")
        
        if consistency['pass_rate'].get('mean'):
            lines.append(f"\n- 均值: {consistency['pass_rate']['mean']}%\n")
            lines.append(f"- 标准差: {consistency['pass_rate']['stdev']}%\n")
            lines.append(f"- 变异系数: {consistency['pass_rate']['cv']}%\n")
            lines.append(f"- 判定: {'✅ 高度一致' if consistency['pass_rate']['cv'] <= 5 else '✅ 基本一致' if consistency['pass_rate']['cv'] <= 10 else '❌ 不一致'}\n")
        
        # 维度得分一致性
        lines.append("\n### 2.3 维度得分一致性\n")
        lines.append("| 维度 | 均值 | 标准差 | 变异系数 | 判定 |\n")
        lines.append("|------|------|--------|----------|------|\n")
        for dim, result in consistency.get('dimensions', {}).items():
            if result.get('mean'):
                status = '✅ 一致' if result.get('consistent', False) else '❌ 不一致'
                lines.append(f"| {dim} | {result['mean']} | {result['stdev']} | {result['cv']}% | {status} |\n")
        
        # 验证结论
        lines.append("\n## 3. 验证结论\n")
        score_ok = consistency['overall_score'].get('consistent', False)
        rate_ok = consistency['pass_rate'].get('consistent', False)
        
        if not format_consistent:
            lines.append("- **格式一致性**: ❌ 检测到不同格式报告\n")
            lines.append("- **建议**: 仅比较相同格式的报告\n")
        else:
            lines.append("- **格式一致性**: ✅ 报告格式统一\n")
            lines.append("- 综合评分一致性: " + ("✅ 通过" if score_ok else "❌ 不通过") + "\n")
            lines.append("- 通过率一致性: " + ("✅ 通过" if rate_ok else "❌ 不通过") + "\n")
            
            if score_ok and rate_ok:
                lines.append("- **总体判定: ✅ 验证通过**\n")
                lines.append("\n评估结果一致性和可靠性良好，可用于后续分析和决策。\n")
            else:
                lines.append("- **总体判定: ❌ 验证不通过**\n")
                lines.append("\n建议重新执行评估，检查测试环境稳定性。\n")
        
        return "".join(lines)


def main():
    parser = argparse.ArgumentParser(description='评估结果验证工具')
    parser.add_argument('--website', '-w', required=True, help='网站名称')
    parser.add_argument('--eval-dir', '-e', default='.claude/skills/browser-cdp/output/eval_results', help='评估报告目录')
    parser.add_argument('--count', '-c', type=int, default=3, help='验证次数')
    args = parser.parse_args()
    
    eval_dir = Path(args.eval_dir)
    if not eval_dir.exists():
        print(f"❌ 评估报告目录不存在: {eval_dir}")
        return 1
    
    validator = EvaluationValidator(args.website, eval_dir)
    count = validator.load_reports(args.count)
    
    if count < 2:
        print(f"❌ 未找到足够的评估报告（需要至少2份，找到{count}份）")
        return 1
    
    print(f"\n加载了 {count} 份评估报告")
    print(f"网站: {args.website}")
    print(f"报告目录: {eval_dir}")
    print()
    
    report = validator.generate_report()
    print(report)
    
    # 保存报告
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_path = eval_dir / f"validation_{args.website}_{timestamp}.md"
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\n验证报告已保存: {output_path}")
    
    return 0


if __name__ == "__main__":
    exit(main())
