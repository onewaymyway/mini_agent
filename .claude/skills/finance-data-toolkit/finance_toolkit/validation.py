# -*- coding: utf-8 -*-
"""
数据质量校验模块

提供 K 线、行情、财务等数据的完整性检查和异常值检测。

使用示例：
    from finance_toolkit.validation import (
        validate_kline_data,
        validate_quote_data,
        DataQualityValidator,
        QualityReport,
    )
    
    # 验证 K 线数据
    report = validate_kline_data(df)
    if not report.is_valid:
        print(f"发现问题：{report.issues}")
        print(f"建议：{report.recommendations}")
    
    # 使用验证器类
    validator = DataQualityValidator()
    clean_df, report = validator.validate_and_clean(kline_df)
"""

import pandas as pd
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from enum import Enum


class SeverityLevel(Enum):
    """问题严重程度"""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class QualityIssue:
    """数据质量问题"""
    level: SeverityLevel
    field: str
    message: str
    details: Optional[Dict] = None
    
    def to_dict(self) -> Dict:
        return {
            'level': self.level.value,
            'field': self.field,
            'message': self.message,
            'details': self.details
        }


@dataclass
class QualityReport:
    """数据质量报告"""
    is_valid: bool
    total_issues: int
    issues: List[QualityIssue] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    recommendations: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict:
        return {
            'is_valid': self.is_valid,
            'total_issues': self.total_issues,
            'issues': [i.to_dict() for i in self.issues],
            'metrics': self.metrics,
            'recommendations': self.recommendations
        }
    
    def __str__(self) -> str:
        status = "✓ 通过" if self.is_valid else "✗ 失败"
        lines = [
            f"数据质量报告 [{status}]",
            f"  总问题数：{self.total_issues}",
        ]
        
        by_level = {}
        for issue in self.issues:
            level = issue.level.value
            by_level[level] = by_level.get(level, 0) + 1
        
        for level in ['critical', 'error', 'warning', 'info']:
            if level in by_level:
                lines.append(f"    {level.upper()}: {by_level[level]}")
        
        if self.recommendations:
            lines.append("  建议:")
            for rec in self.recommendations[:3]:
                lines.append(f"    - {rec}")
        
        return "\n".join(lines)


# ============== K 线数据验证 ==============

def validate_kline_data(
    df: pd.DataFrame,
    check_continuity: bool = True,
    check_outliers: bool = True,
    outlier_std: float = 3.0
) -> QualityReport:
    """验证 K 线数据质量"""
    issues = []
    recommendations = []
    
    required_cols = ['date', 'open', 'high', 'low', 'close', 'volume']
    missing_cols = [c for c in required_cols if c not in df.columns]
    
    if missing_cols:
        issues.append(QualityIssue(
            level=SeverityLevel.CRITICAL,
            field='columns',
            message=f"缺少必需字段：{missing_cols}"
        ))
        return QualityReport(
            is_valid=False,
            total_issues=len(issues),
            issues=issues,
            recommendations=["请确保数据包含 date/open/high/low/close/volume 字段"]
        )
    
    # 1. 空值检查
    null_counts = df[required_cols].isnull().sum()
    null_fields = {k: v for k, v in null_counts.items() if v > 0}
    
    if null_fields:
        for field, count in null_fields.items():
            pct = count / len(df) * 100
            level = SeverityLevel.ERROR if pct > 5 else SeverityLevel.WARNING
            issues.append(QualityIssue(
                level=level,
                field=field,
                message=f"存在 {count} 个空值 (占比 {pct:.2f}%)",
                details={'count': count, 'percentage': pct}
            ))
        recommendations.append("使用 MissingValueHandler 处理缺失值")
    
    # 2. 价格逻辑检查
    invalid_high_low = df[df['high'] < df['low']]
    if len(invalid_high_low) > 0:
        pct = len(invalid_high_low) / len(df) * 100
        issues.append(QualityIssue(
            level=SeverityLevel.ERROR,
            field='high/low',
            message=f"{len(invalid_high_low)} 条数据最高价 < 最低价 (占比 {pct:.2f}%)",
            details={'count': len(invalid_high_low), 'percentage': pct}
        ))
        recommendations.append("检查数据源或清洗逻辑")
    
    # 3. 负值检查
    for field in ['open', 'high', 'low', 'close', 'volume']:
        neg_count = (df[field] <= 0).sum()
        if neg_count > 0:
            issues.append(QualityIssue(
                level=SeverityLevel.ERROR,
                field=field,
                message=f"存在 {neg_count} 个非正值",
                details={'count': neg_count}
            ))
    
    # 4. 日期连续性检查
    if check_continuity and 'date' in df.columns:
        df_sorted = df.sort_values('date').reset_index(drop=True)
        try:
            dates = pd.to_datetime(df_sorted['date'])
            diffs = dates.diff().dropna()
            if len(diffs) > 0:
                median_diff = diffs.median()
                large_gaps = diffs[diffs > median_diff * 5]
                if len(large_gaps) > 0:
                    issues.append(QualityIssue(
                        level=SeverityLevel.WARNING,
                        field='date continuity',
                        message=f"发现 {len(large_gaps)} 个异常日期间隔",
                        details={'count': len(large_gaps), 'median_gap': str(median_diff)}
                    ))
        except Exception as e:
            issues.append(QualityIssue(
                level=SeverityLevel.WARNING,
                field='date parsing',
                message=f"日期解析失败：{str(e)[:100]}"
            ))
    
    # 5. 异常值检测
    if check_outliers and 'close' in df.columns:
        try:
            returns = df['close'].pct_change()
            mean_ret = returns.mean()
            std_ret = returns.std()
            if std_ret > 0:
                z_scores = (returns - mean_ret) / std_ret
                outliers = df[abs(z_scores) > outlier_std]
                if len(outliers) > 0:
                    pct = len(outliers) / len(df) * 100
                    issues.append(QualityIssue(
                        level=SeverityLevel.WARNING,
                        field='price change',
                        message=f"发现 {len(outliers)} 个异常涨跌幅点 (Z-score > {outlier_std})",
                        details={'count': len(outliers), 'percentage': pct, 'threshold': outlier_std}
                    ))
                    recommendations.append("考虑使用缩尾处理 (winsorize) 或手动核查这些点")
        except Exception:
            pass
    
    metrics = {
        'total_rows': len(df),
        'null_rate': df[required_cols].isnull().mean().mean(),
        'valid_rate': 1 - (df[required_cols].isnull().sum().sum() / (len(df) * len(required_cols))),
    }
    
    is_valid = not any(i.level in [SeverityLevel.CRITICAL, SeverityLevel.ERROR] for i in issues)
    
    return QualityReport(
        is_valid=is_valid,
        total_issues=len(issues),
        issues=issues,
        metrics=metrics,
        recommendations=recommendations
    )


# ============== 实时行情验证 ==============

def validate_quote_data(
    quote: Dict[str, Any],
    symbol: Optional[str] = None
) -> QualityReport:
    """验证实时行情数据质量"""
    issues = []
    recommendations = []
    
    required_fields = ['close']
    missing = [f for f in required_fields if f not in quote or quote[f] is None]
    
    if missing:
        issues.append(QualityIssue(
            level=SeverityLevel.CRITICAL,
            field='fields',
            message=f"缺少必需字段：{missing}"
        ))
        return QualityReport(
            is_valid=False,
            total_issues=len(issues),
            issues=issues,
            recommendations=["请确保数据包含 close 字段"]
        )
    
    # 非正值检查
    price_fields = ['close', 'open', 'high', 'low', 'pre_close']
    for price_field in price_fields:
        if price_field in quote and quote[price_field] is not None:
            try:
                if float(quote[price_field]) <= 0:
                    issues.append(QualityIssue(
                        level=SeverityLevel.ERROR,
                        field=price_field,
                        message=f"{price_field} 为非正值：{quote[price_field]}"
                    ))
            except (ValueError, TypeError):
                issues.append(QualityIssue(
                    level=SeverityLevel.ERROR,
                    field=field,
                    message=f"{field} 无法转换为数字：{quote[field]}"
                ))
    
    # 价格逻辑检查
    if all(f in quote and quote[f] is not None for f in ['high', 'low']):
        try:
            if float(quote['high']) < float(quote['low']):
                issues.append(QualityIssue(
                    level=SeverityLevel.ERROR,
                    field='high/low',
                    message=f"最高价 < 最低价：high={quote['high']}, low={quote['low']}"
                ))
        except (ValueError, TypeError):
            pass
    
    # 涨跌幅合理性检查
    if all(f in quote and quote[f] is not None for f in ['close', 'pre_close']):
        try:
            change_pct = (float(quote['close']) - float(quote['pre_close'])) / float(quote['pre_close']) * 100
            if abs(change_pct) > 30:
                issues.append(QualityIssue(
                    level=SeverityLevel.WARNING,
                    field='change_pct',
                    message=f"涨跌幅异常：{change_pct:.2f}%",
                    details={'change_pct': change_pct}
                ))
                recommendations.append("核查是否为停牌复牌或数据错误")
        except (ValueError, TypeError, ZeroDivisionError):
            pass
    
    is_valid = not any(i.level in [SeverityLevel.CRITICAL, SeverityLevel.ERROR] for i in issues)
    
    return QualityReport(
        is_valid=is_valid,
        total_issues=len(issues),
        issues=issues,
        metrics={'symbol': symbol},
        recommendations=recommendations
    )


# ============== 批量验证器 ==============

class DataQualityValidator:
    """数据质量验证器"""
    
    def __init__(
        self,
        check_continuity: bool = True,
        check_outliers: bool = True,
        outlier_std: float = 3.0
    ):
        self.check_continuity = check_continuity
        self.check_outliers = check_outliers
        self.outlier_std = outlier_std
    
    def validate_kline(self, df: pd.DataFrame, return_cleaned: bool = False):
        """验证并可选清洗 K 线数据"""
        report = validate_kline_data(
            df,
            check_continuity=self.check_continuity,
            check_outliers=self.check_outliers,
            outlier_std=self.outlier_std
        )
        return report, df.copy() if return_cleaned and report.is_valid else None
    
    def validate_quote(self, quote: Dict[str, Any], symbol: Optional[str] = None) -> QualityReport:
        """验证实时行情"""
        return validate_quote_data(quote, symbol)
    
    def batch_validate_kline(self, df: pd.DataFrame, group_by: str = 'symbol') -> Dict[str, QualityReport]:
        """批量验证多只股票的 K 线数据"""
        if group_by not in df.columns:
            raise ValueError(f"DataFrame 必须包含 '{group_by}' 列")
        
        reports = {}
        for symbol, group in df.groupby(group_by):
            reports[symbol] = validate_kline_data(
                group.reset_index(drop=True),
                check_continuity=self.check_continuity,
                check_outliers=self.check_outliers,
                outlier_std=self.outlier_std
            )
        return reports


# ============== 便捷函数 ==============

def check_data_quality(data: Any, data_type: str = 'auto') -> QualityReport:
    """自动检测数据类型并验证"""
    if data_type == 'auto':
        if isinstance(data, pd.DataFrame):
            data_type = 'kline'
        elif isinstance(data, dict):
            data_type = 'quote'
        else:
            return QualityReport(
                is_valid=False,
                total_issues=1,
                issues=[QualityIssue(
                    level=SeverityLevel.CRITICAL,
                    field='type',
                    message=f"不支持的数据类型：{type(data)}"
                )],
                recommendations=[]
            )
    
    if data_type == 'kline':
        return validate_kline_data(data)
    elif data_type == 'quote':
        return validate_quote_data(data)
    else:
        raise ValueError(f"不支持的数据类型：{data_type}")
