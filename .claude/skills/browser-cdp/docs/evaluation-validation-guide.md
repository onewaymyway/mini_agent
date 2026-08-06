# 评估结果验证机制

## 概述

本文档定义 browser-cdp skill 评估结果的验证流程，确保评估结果的一致性和可靠性。

## 验证目标

1. **一致性**: 同一网站多次评估结果应保持一致
2. **可靠性**: 评估结果应反映真实能力水平
3. **可复现性**: 评估流程应可重复执行
4. **可比性**: 不同网站评估结果应可横向对比

## 验证流程

### 1. 重复测试验证

#### 1.1 测试策略

对同一网站进行至少3次独立评估，验证结果一致性。

```bash
# 第1次评估
python scripts/run_test_cases.py -u https://www.baidu.com -n baidu -o output/eval_results

# 第2次评估
python scripts/run_test_cases.py -u https://www.baidu.com -n baidu -o output/eval_results

# 第3次评估
python scripts/run_test_cases.py -u https://www.baidu.com -n baidu -o output/eval_results
```

#### 1.2 一致性判定标准

| 指标 | 一致性阈值 | 判定方法 |
|------|------------|----------|
| 综合评分 | 偏差 ≤ 5% | 计算标准差 |
| 通过率 | 偏差 ≤ 5% | 计算标准差 |
| 维度得分 | 偏差 ≤ 10% | 计算标准差 |
| 用例通过率 | 100%一致 | 逐用例对比 |

#### 1.3 一致性计算公式

```
标准差 σ = sqrt(Σ(xi - μ)² / n)
变异系数 CV = σ / μ × 100%

判定标准：
- CV ≤ 5%: 高度一致
- 5% < CV ≤ 10%: 基本一致
- CV > 10%: 不一致，需重新评估
```

### 2. 交叉验证

#### 2.1 多工具验证

使用不同评估工具对同一网站进行评估，验证结果一致性。

```bash
# 使用测试用例脚本
python scripts/run_test_cases.py -u https://www.baidu.com -n baidu

# 使用演示脚本
python scripts/run_eval_demo.py --website baidu
```

#### 2.2 人工验证

对自动化评估结果进行人工抽查验证：

1. 随机抽取10%的测试用例
2. 人工执行并记录结果
3. 对比自动化评估结果
4. 计算人工-自动一致性率

### 3. 回归测试验证

#### 3.1 基线建立

首次评估结果作为基线，后续评估与基线对比。

```python
# 基线数据示例
baseline = {
    "website": "baidu",
    "overall_score": 153.09,
    "pass_rate": 82.2,
    "dimensions": {
        "页面访问": 100.0,
        "元素定位": 85.7,
        "数据提取": 100.0,
        "交互功能": 75.0,
        "反检测": 66.7,
        "稳定性": 75.0
    }
}
```

#### 3.2 回归检测

每次评估后自动与基线对比，检测显著变化：

```python
# 回归检测逻辑
def check_regression(current, baseline, threshold=10%):
    score_change = abs(current['overall_score'] - baseline['overall_score'])
    pass_rate_change = abs(current['pass_rate'] - baseline['pass_rate'])
    
    if score_change > threshold or pass_rate_change > threshold:
        return "回归检测失败，能力下降"
    return "回归检测通过"
```

### 4. 异常检测

#### 4.1 异常判定标准

| 异常类型 | 判定条件 | 处理措施 |
|----------|----------|----------|
| 评分骤降 | 单次评估评分下降 > 20% | 立即告警，重新评估 |
| 通过率异常 | 通过率波动 > 15% | 检查测试环境 |
| 用例失败集中 | 同一维度失败率 > 50% | 专项优化 |
| 执行时间异常 | 执行时间 > 基线2倍 | 检查性能问题 |

#### 4.2 异常处理流程

```
异常检测 → 记录日志 → 发送告警 → 重新评估 → 分析原因 → 修复问题
```

## 验证报告模板

### 验证报告结构

```
# 评估验证报告

## 1. 验证概述
- 验证时间
- 验证网站
- 验证次数

## 2. 一致性分析
- 综合评分一致性
- 通过率一致性
- 维度得分一致性
- 用例通过率一致性

## 3. 回归检测
- 基线数据
- 当前数据
- 变化分析

## 4. 异常检测
- 发现的异常
- 处理措施
- 处理结果

## 5. 验证结论
- 一致性判定
- 可靠性判定
- 建议措施
```

### 验证报告示例

```markdown
# 评估验证报告

## 1. 验证概述
- 验证时间: 2026-08-06 20:55:00
- 验证网站: 百度 (https://www.baidu.com)
- 验证次数: 3次

## 2. 一致性分析

### 2.1 综合评分一致性
| 评估次数 | 综合评分 | 偏差 |
|----------|----------|------|
| 第1次 | 153.09 | - |
| 第2次 | 152.45 | -0.42% |
| 第3次 | 153.78 | +0.45% |

标准差: 0.41
变异系数: 0.27%
判定: ✅ 高度一致

### 2.2 通过率一致性
| 评估次数 | 通过率 | 偏差 |
|----------|--------|------|
| 第1次 | 82.2% | - |
| 第2次 | 80.0% | -2.2% |
| 第3次 | 82.2% | 0.0% |

标准差: 1.24
变异系数: 1.51%
判定: ✅ 高度一致

## 3. 回归检测
- 基线评分: 153.09
- 当前评分: 153.78
- 变化: +0.45%
- 判定: ✅ 回归检测通过

## 4. 异常检测
- 发现的异常: 无
- 处理措施: 无
- 处理结果: 无

## 5. 验证结论
- 一致性判定: ✅ 通过
- 可靠性判定: ✅ 可靠
- 建议措施: 继续监控
```

## 验证工具

### 验证脚本

创建 `scripts/validate_evaluation.py`：

```python
#!/usr/bin/env python3
"""评估结果验证工具"""

import json
import statistics
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any


class EvaluationValidator:
    """评估验证器"""
    
    def __init__(self, website_name: str, eval_dir: Path):
        self.website_name = website_name
        self.eval_dir = eval_dir
        self.reports = []
    
    def load_reports(self, count: int = 3):
        """加载最近的评估报告"""
        reports = sorted(
            self.eval_dir.glob(f"eval_{self.website_name}_*.json"),
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )[:count]
        
        for report_path in reports:
            with open(report_path, 'r', encoding='utf-8') as f:
                self.reports.append(json.load(f))
    
    def validate_consistency(self) -> Dict[str, Any]:
        """验证一致性"""
        if len(self.reports) < 2:
            return {"valid": False, "reason": "报告数量不足"}
        
        results = {
            "overall_score": self._validate_metric(
                [r['overall_score'] for r in self.reports]
            ),
            "pass_rate": self._validate_metric(
                [r['pass_rate'] for r in self.reports]
            ),
            "dimensions": {},
            "cases": {}
        }
        
        # 验证维度得分一致性
        for dim in self.reports[0].get('dimension_scores', {}):
            scores = [r['dimension_scores'][dim]['rate'] for r in self.reports]
            results['dimensions'][dim] = self._validate_metric(scores)
        
        # 验证用例通过率一致性
        case_ids = set(r['test_results'][0]['case_id'] for r in self.reports)
        for case_id in case_ids:
            pass_rates = []
            for r in self.reports:
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
        stdev = statistics.stdev(values)
        cv = (stdev / mean * 100) if mean != 0 else 0
        
        if cv <= 5:
            consistency = "高度一致"
        elif cv <= 10:
            consistency = "基本一致"
        else:
            consistency = "不一致"
        
        return {
            "values": values,
            "mean": round(mean, 2),
            "stdev": round(stdev, 2),
            "cv": round(cv, 2),
            "consistent": cv <= 10,
            "consistency_level": consistency
        }
    
    def generate_report(self) -> str:
        """生成验证报告"""
        consistency = self.validate_consistency()
        
        report = f"""# 评估验证报告

## 1. 验证概述
- 验证时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
- 验证网站: {self.website_name}
- 验证次数: {len(self.reports)}次

## 2. 一致性分析

### 2.1 综合评分一致性
- 评分列表: {[round(r['overall_score'], 2) for r in self.reports]}
- 均值: {consistency['overall_score']['mean']}
- 标准差: {consistency['overall_score']['stdev']}
- 变异系数: {consistency['overall_score']['cv']}%
- 判定: {consistency['overall_score']['consistency_level']}

### 2.2 通过率一致性
- 通过率列表: {[round(r['pass_rate'], 2) for r in self.reports]}
- 均值: {consistency['pass_rate']['mean']}
- 标准差: {consistency['pass_rate']['stdev']}
- 变异系数: {consistency['pass_rate']['cv']}%
- 判定: {consistency['pass_rate']['consistency_level']}

## 3. 验证结论
- 综合评分一致性: {'✅ 通过' if consistency['overall_score']['consistent'] else '❌ 不通过'}
- 通过率一致性: {'✅ 通过' if consistency['pass_rate']['consistent'] else '❌ 不通过'}
- 总体判定: {'✅ 验证通过' if consistency['overall_score']['consistent'] and consistency['pass_rate']['consistent'] else '❌ 验证不通过'}
"""
        
        return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='评估结果验证工具')
    parser.add_argument('--website', '-w', required=True, help='网站名称')
    parser.add_argument('--eval-dir', '-e', default='output/eval_results', help='评估报告目录')
    args = parser.parse_args()
    
    validator = EvaluationValidator(args.website, Path(args.eval_dir))
    validator.load_reports(3)
    
    report = validator.generate_report()
    print(report)
    
    # 保存报告
    output_path = Path(args.eval_dir) / f"validation_{args.website}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\n验证报告已保存: {output_path}")
```

### 使用示例

```bash
# 验证百度评估结果一致性
python scripts/validate_evaluation.py -w baidu -e output/eval_results

# 验证所有网站
for site in baidu bing zhihu; do
    python scripts/validate_evaluation.py -w $site -e output/eval_results
done
```

## 验证检查清单

- [ ] 对每个网站进行至少3次重复评估
- [ ] 计算综合评分的一致性（标准差、变异系数）
- [ ] 计算通过率的一致性
- [ ] 验证各维度得分的一致性
- [ ] 验证用例通过率的一致性
- [ ] 生成验证报告
- [ ] 判定验证结果（通过/不通过）
- [ ] 记录异常并处理

## 实际验证结果（2026-08-06）

### 百度 (baidu) - 2次评估
| 指标 | 均值 | CV | 判定 |
|------|------|-----|------|
| 综合评分 | 155.68 | 2.35% | ✅ 高度一致 |
| 通过率 | 84.45% | 3.77% | ✅ 高度一致 |

**波动较大维度**（需关注）:
- 反爬绕过率: CV=47.2%（UA轮换策略随机性导致）
- 异常恢复率: CV=47.1%（网络异常注入结果不稳定）
- 动态元素识别率: CV=28.3%（页面动态内容变化）
- 指纹伪装有效性: CV=28.3%

**稳定维度**（CV=0%）:
- 页面访问成功率、数据提取准确率、字段完整率
- 结构化提取成功率、内存稳定性、连接稳定性

### 其他网站
- Bing: 仅1次评估，待补充
- 知乎: 仅1次评估，待补充

## 相关文件

- `docs/evaluation-standards-v2.md` - 评估标准
- `docs/evaluation-tools-guide.md` - 工具使用指南
- `docs/evaluation-tracking-guide.md` - 追踪机制
- `scripts/run_test_cases.py` - 测试执行脚本
- `scripts/validate_evaluation.py` - 验证工具脚本
- `output/eval_results/validation_*.md` - 历史验证报告
