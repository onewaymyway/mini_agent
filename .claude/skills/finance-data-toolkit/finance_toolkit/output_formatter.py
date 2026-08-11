# -*- coding: utf-8 -*-
"""
结果输出格式化模块

提供合规检查结果的多种格式化输出方式：
1. JSON 格式 - 用于程序处理
2. 文本格式 - 用于日志记录
3. 表格格式 - 用于报告展示
4. 摘要格式 - 用于快速概览

使用示例：
    from finance_toolkit.output_formatter import (
        ComplianceOutputFormatter,
        format_compliance_result,
    )
    
    formatter = ComplianceOutputFormatter()
    json_output = formatter.to_json(result)
    text_output = formatter.to_text(result)
    table_output = formatter.to_table(result)
"""

import json
from typing import Any, Dict, List, Optional
from datetime import datetime


class ComplianceOutputFormatter:
    """
    合规检查结果输出格式化器
    
    支持多种输出格式：
    - JSON: 结构化数据，便于程序处理
    - Text: 纯文本，便于日志记录
    - Table: 表格形式，便于报告展示
    - Summary: 摘要形式，便于快速概览
    """
    
    # 违规级别颜色标记（ANSI 转义码）
    LEVEL_COLORS = {
        'critical': '\033[91m',  # 红色
        'error': '\033[93m',     # 黄色
        'warning': '\033[94m',   # 蓝色
        'info': '\033[90m',      # 灰色
    }
    RESET_COLOR = '\033[0m'
    
    # 合规状态标记
    STATUS_MARKERS = {
        'compliant': '✓',
        'non_compliant': '✗',
        'needs_review': '⚠'
    }
    
    def __init__(self, colorize: bool = True, indent: int = 2):
        """
        初始化格式化器
        
        Args:
            colorize: 是否启用颜色标记（仅对文本格式有效）
            indent: JSON 缩进空格数
        """
        self.colorize = colorize
        self.indent = indent
    
    def to_json(self, result: Any, pretty: bool = True) -> str:
        """
        输出为 JSON 格式
        
        Args:
            result: ComplianceResult 或字典
            pretty: 是否格式化输出
        
        Returns:
            JSON 字符串
        """
        if hasattr(result, 'to_dict'):
            data = result.to_dict()
        elif isinstance(result, dict):
            data = result
        else:
            data = {'error': f'Unsupported result type: {type(result)}'}
        
        if pretty:
            return json.dumps(data, ensure_ascii=False, indent=self.indent, default=str)
        else:
            return json.dumps(data, ensure_ascii=False, default=str)
    
    def to_text(self, result: Any, include_details: bool = True) -> str:
        """
        输出为纯文本格式
        
        Args:
            result: ComplianceResult 或字典
            include_details: 是否包含详细违规信息
        
        Returns:
            文本字符串
        """
        if hasattr(result, 'to_dict'):
            data = result.to_dict()
        elif isinstance(result, dict):
            data = result
        else:
            return f"无法格式化 {type(result)} 类型"
        
        lines = []
        
        # 标题
        status = data.get('status', 'unknown')
        marker = self.STATUS_MARKERS.get(status, '?')
        score = data.get('score', 0.0)
        
        lines.append(f"{'=' * 60}")
        lines.append(f"合规检查结果 [{marker} {status.upper()}]")
        lines.append(f"{'=' * 60}")
        lines.append(f"合规评分：{score:.2%}")
        lines.append(f"检查项数：{len(data.get('checks_performed', []))}")
        lines.append(f"违规数量：{len(data.get('violations', []))}")
        
        if include_details and data.get('violations'):
            lines.append("")
            lines.append("违规详情：")
            lines.append("-" * 60)
            
            for i, v in enumerate(data.get('violations', []), 1):
                level = v.get('level', 'info')
                color = self._get_color(level) if self.colorize else ''
                reset = self.RESET_COLOR if self.colorize else ''
                
                lines.append(f"{color}[{i}] [{level.upper()}]{reset}")
                lines.append(f"    规则：{v.get('rule_name', 'N/A')}")
                lines.append(f"    字段：{v.get('field', 'N/A')}")
                lines.append(f"    值：{v.get('value', 'N/A')}")
                lines.append(f"    描述：{v.get('message', 'N/A')}")
                if v.get('suggestion'):
                    lines.append(f"    建议：{v.get('suggestion')}")
                lines.append("")
        
        return "\n".join(lines)
    
    def to_table(self, result: Any) -> str:
        """
        输出为表格格式
        
        Args:
            result: ComplianceResult 或字典
        
        Returns:
            表格字符串
        """
        if hasattr(result, 'to_dict'):
            data = result.to_dict()
        elif isinstance(result, dict):
            data = result
        else:
            return f"无法格式化 {type(result)} 类型"
        
        violations = data.get('violations', [])
        
        if not violations:
            return "无违规记录"
        
        # 计算列宽
        col_widths = {
            'id': 4,
            'level': 8,
            'rule': 20,
            'field': 25,
            'message': 40
        }
        
        for v in violations:
            col_widths['level'] = max(col_widths['level'], len(v.get('level', '')))
            col_widths['rule'] = max(col_widths['rule'], len(v.get('rule_name', '')))
            col_widths['field'] = max(col_widths['field'], len(v.get('field', '')))
            col_widths['message'] = max(col_widths['message'], len(v.get('message', '')[:40]))
        
        # 生成表头
        header = f"{'ID':<{col_widths['id']}} | {'级别':<{col_widths['level']}} | {'规则':<{col_widths['rule']}} | {'字段':<{col_widths['field']}} | {'描述':<{col_widths['message']}}"
        separator = "-" * len(header)
        
        lines = [header, separator]
        
        # 生成数据行
        for i, v in enumerate(violations, 1):
            row = f"{i:<{col_widths['id']}} | {v.get('level', '').upper():<{col_widths['level']}} | {v.get('rule_name', 'N/A')[:20]:<{col_widths['rule']}} | {v.get('field', 'N/A')[:25]:<{col_widths['field']}} | {v.get('message', 'N/A')[:40]:<{col_widths['message']}}"
            lines.append(row)
        
        return "\n".join(lines)
    
    def to_summary(self, result: Any) -> str:
        """
        输出为摘要格式
        
        Args:
            result: ComplianceResult 或字典
        
        Returns:
            摘要字符串
        """
        if hasattr(result, 'to_dict'):
            data = result.to_dict()
        elif isinstance(result, dict):
            data = result
        else:
            return f"无法格式化 {type(result)} 类型"
        
        status = data.get('status', 'unknown')
        score = data.get('score', 0.0)
        violation_count = len(data.get('violations', []))
        
        # 根据评分给出建议
        if score >= 0.9:
            suggestion = "数据质量优秀，可直接使用"
        elif score >= 0.7:
            suggestion = "数据质量良好，建议修复警告项"
        elif score >= 0.5:
            suggestion = "数据质量一般，建议人工审核"
        else:
            suggestion = "数据质量较差，需要重新获取或清洗"
        
        return f"合规评分: {score:.1%} | 状态: {status} | 违规: {violation_count} | 建议: {suggestion}"
    
    def _get_color(self, level: str) -> str:
        """获取级别对应的颜色代码"""
        return self.LEVEL_COLORS.get(level, '')
    
    def format_batch(self, results: List[Any], format_type: str = 'text') -> str:
        """
        批量格式化结果
        
        Args:
            results: 结果列表
            format_type: 输出格式 ('json', 'text', 'table', 'summary')
        
        Returns:
            格式化后的字符串
        """
        if format_type == 'json':
            data = []
            for r in results:
                if hasattr(r, 'to_dict'):
                    data.append(r.to_dict())
                elif isinstance(r, dict):
                    data.append(r)
            return json.dumps(data, ensure_ascii=False, indent=self.indent, default=str)
        
        elif format_type == 'summary':
            summaries = [self.to_summary(r) for r in results]
            return "\n".join(summaries)
        
        else:  # text or table
            parts = []
            for r in results:
                if format_type == 'table':
                    parts.append(self.to_table(r))
                else:
                    parts.append(self.to_text(r))
            return "\n" + "=" * 60 + "\n".join(parts)


# ============== 便捷函数 ==============

_formatter = ComplianceOutputFormatter()


def format_compliance_result(result: Any, format_type: str = 'text') -> str:
    """
    格式化合规检查结果
    
    Args:
        result: ComplianceResult 或字典
        format_type: 输出格式 ('json', 'text', 'table', 'summary')
    
    Returns:
        格式化后的字符串
    """
    return _formatter.to_text(result) if format_type == 'text' else \
           _formatter.to_json(result) if format_type == 'json' else \
           _formatter.to_table(result) if format_type == 'table' else \
           _formatter.to_summary(result)


def format_compliance_batch(results: List[Any], format_type: str = 'text') -> str:
    """
    批量格式化合规检查结果
    
    Args:
        results: 结果列表
        format_type: 输出格式
    
    Returns:
        格式化后的字符串
    """
    return _formatter.format_batch(results, format_type)


if __name__ == "__main__":
    # 测试示例
    from finance_toolkit.compliance_checker import ComplianceChecker, ViolationLevel
    
    checker = ComplianceChecker()
    
    # 创建测试数据
    test_data = {
        'source': 'akshare',
        'data_type': 'quote',
        'symbol': '600000.SH',
        'timestamp': '2024-01-15T10:30:00Z',
        'payload': {
            'open': 10.50,
            'high': 10.80,
            'low': 10.40,
            'close': 10.70,
            'volume': 1000000,
            'amount': 10500000.0
        }
    }
    
    result = checker.check(test_data)
    
    print("=" * 60)
    print("JSON 格式输出:")
    print("=" * 60)
    print(_formatter.to_json(result))
    
    print("\n" + "=" * 60)
    print("文本格式输出:")
    print("=" * 60)
    print(_formatter.to_text(result))
    
    print("\n" + "=" * 60)
    print("摘要格式输出:")
    print("=" * 60)
    print(_formatter.to_summary(result))
