"""
测试报告模板

生成 HTML 和 Markdown 格式的测试报告。
"""
from typing import Dict, List, Any, Optional
from datetime import datetime
from pathlib import Path
import json


class TestReportTemplate:
    """测试报告模板"""
    
    HTML_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Browser CDP 测试报告</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 40px; }}
        h1 {{ color: #333; }}
        .summary {{ background: #f5f5f5; padding: 20px; border-radius: 5px; margin: 20px 0; }}
        .pass {{ color: green; }}
        .fail {{ color: red; }}
        .skip {{ color: orange; }}
        table {{ border-collapse: collapse; width: 100%; margin: 20px 0; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background-color: #4CAF50; color: white; }}
        .metric {{ display: inline-block; margin: 10px 20px; padding: 15px; background: #e7f3ff; border-radius: 5px; }}
        .metric-value {{ font-size: 24px; font-weight: bold; color: #2196F3; }}
        .metric-label {{ font-size: 14px; color: #666; }}
    </style>
</head>
<body>
    <h1>Browser CDP 测试报告</h1>
    <div class="summary">
        <h2>执行摘要</h2>
        <p><strong>生成时间:</strong> {generated_at}</p>
        <p><strong>执行模式:</strong> {mode}</p>
        <p><strong>退出码:</strong> {exit_code}</p>
    </div>
    
    <h2>核心指标</h2>
    <div>
        <div class="metric">
            <div class="metric-value">{pass_rate}%</div>
            <div class="metric-label">通过率</div>
        </div>
        <div class="metric">
            <div class="metric-value">{total}</div>
            <div class="metric-label">总用例数</div>
        </div>
        <div class="metric">
            <div class="metric-value">{passed}</div>
            <div class="metric-label">通过</div>
        </div>
        <div class="metric">
            <div class="metric-value">{failed}</div>
            <div class="metric-label">失败</div>
        </div>
    </div>
    
    <h2>测试详情</h2>
    <table>
        <tr>
            <th>测试用例</th>
            <th>状态</th>
            <th>耗时</th>
            <th>错误信息</th>
        </tr>
        {rows}
    </table>
    
    <h2>评级</h2>
    <p><strong>综合评级:</strong> {rating}</p>
    <p><strong>评级说明:</strong> {rating_description}</p>
</body>
</html>
    '''
    
    MARKDOWN_TEMPLATE = '''# Browser CDP 测试报告

**生成时间**: {generated_at}  
**执行模式**: {mode}  
**退出码**: {exit_code}

## 核心指标

| 指标 | 数值 |
|------|------|
| 通过率 | {pass_rate}% |
| 总用例数 | {total} |
| 通过 | {passed} |
| 失败 | {failed} |
| 跳过 | {skipped} |
| 错误 | {error} |

## 评级

**综合评级**: {rating}

{rating_description}

## 测试详情

| 测试用例 | 状态 | 耗时 | 错误信息 |
|----------|------|------|----------|
{details_rows}

---

*报告由 Browser CDP 测试框架自动生成*
    '''
    
    def generate_html_report(
        self,
        results: Dict[str, Any],
        output_path: str
    ) -> str:
        """生成 HTML 报告"""
        summary = results.get("summary", {})
        details = results.get("details", [])
        
        total = summary.get("total", 0)
        passed = summary.get("passed", 0)
        failed = summary.get("failed", 0)
        skipped = summary.get("skipped", 0)
        error = summary.get("error", 0)
        
        pass_rate = (passed / total * 100) if total > 0 else 0
        rating = self._calculate_rating(pass_rate)
        
        # 生成表格行
        rows = ""
        for item in details:
            status_class = "pass" if item.get("status") == "passed" else "fail"
            rows += f'''<tr>
                <td>{item.get("name", "")}</td>
                <td class="{status_class}">{item.get("status", "")}</td>
                <td>{item.get("duration", "")}</td>
                <td>{item.get("error", "")}</td>
            </tr>\n'''
        
        html = self.HTML_TEMPLATE.format(
            generated_at=results.get("generated_at", ""),
            mode=results.get("mode", ""),
            exit_code=results.get("exit_code", ""),
            pass_rate=round(pass_rate, 1),
            total=total,
            passed=passed,
            failed=failed,
            rating=rating["label"],
            rating_description=rating["description"],
            rows=rows
        )
        
        Path(output_path).write_text(html, encoding="utf-8")
        return output_path
    
    def generate_markdown_report(
        self,
        results: Dict[str, Any],
        output_path: str
    ) -> str:
        """生成 Markdown 报告"""
        summary = results.get("summary", {})
        details = results.get("details", [])
        
        total = summary.get("total", 0)
        passed = summary.get("passed", 0)
        failed = summary.get("failed", 0)
        skipped = summary.get("skipped", 0)
        error = summary.get("error", 0)
        
        pass_rate = (passed / total * 100) if total > 0 else 0
        rating = self._calculate_rating(pass_rate)
        
        # 生成表格行
        details_rows = ""
        for item in details:
            status = item.get("status", "")
            details_rows += f"| {item.get('name', '')} | {status} | {item.get('duration', '')} | {item.get('error', '')} |\n"
        
        md = self.MARKDOWN_TEMPLATE.format(
            generated_at=results.get("generated_at", ""),
            mode=results.get("mode", ""),
            exit_code=results.get("exit_code", ""),
            pass_rate=round(pass_rate, 1),
            total=total,
            passed=passed,
            failed=failed,
            skipped=skipped,
            error=error,
            rating=rating["label"],
            rating_description=rating["description"],
            details_rows=details_rows
        )
        
        Path(output_path).write_text(md, encoding="utf-8")
        return output_path
    
    def _calculate_rating(self, pass_rate: float) -> Dict[str, str]:
        """计算评级"""
        if pass_rate >= 95:
            return {"label": "A+", "description": "优秀，完全支持，推荐用于生产环境"}
        elif pass_rate >= 90:
            return {"label": "A", "description": "良好，基本支持，可正常使用"}
        elif pass_rate >= 80:
            return {"label": "B", "description": "中等，部分支持，需要优化"}
        elif pass_rate >= 70:
            return {"label": "C", "description": "较差，需优化，需要重构"}
        else:
            return {"label": "D", "description": "不支持，需重构，暂时不支持"}
    
    def generate_json_report(
        self,
        results: Dict[str, Any],
        output_path: str
    ) -> str:
        """生成 JSON 报告"""
        Path(output_path).write_text(
            json.dumps(results, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        return output_path


# 全局报告模板实例
_report_template = TestReportTemplate()


def get_report_template() -> TestReportTemplate:
    """获取全局报告模板实例"""
    return _report_template
