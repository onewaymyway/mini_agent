# -*- coding: utf-8 -*-
"""
Finance Data Toolkit - 数据质量监控模块

提供数据完整性检查、异常检测、质量报告功能。

使用示例：
    from finance_toolkit.quality_monitor import DataQualityMonitor
    
    monitor = DataQualityMonitor(data_dir='data/')
    report = monitor.check_quality()
    print(report.summary())
"""

import os
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field, asdict
from pathlib import Path
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclass
class QualityIssue:
    """质量问题"""
    issue_id: str
    severity: str  # critical, warning, info
    category: str  # completeness, accuracy, timeliness, consistency
    source: str
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    detected_at: float = field(default_factory=lambda: datetime.now().timestamp())

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DataQualityReport:
    """数据质量报告"""
    report_id: str
    generated_at: str
    data_source: str
    total_records: int
    issues: List[QualityIssue] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)

    @property
    def critical_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == 'critical')

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == 'warning')

    @property
    def info_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == 'info')

    @property
    def health_score(self) -> float:
        if self.total_records == 0:
            return 0.0
        penalty = (
            self.critical_count * 10 +
            self.warning_count * 3 +
            self.info_count * 1
        )
        return max(0.0, min(100.0, 100.0 - penalty))

    def summary(self) -> str:
        lines = [
            f"数据质量报告",
            f"生成时间: {self.generated_at}",
            f"数据源: {self.data_source}",
            f"总记录数: {self.total_records}",
            f"健康评分: {self.health_score:.1f}/100",
            f"问题统计: 严重={self.critical_count}, 警告={self.warning_count}, 提示={self.info_count}",
        ]
        if self.issues:
            lines.append("\n问题详情:")
            for issue in self.issues[:10]:
                lines.append(f"  [{issue.severity.upper()}] {issue.message}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return asdict(self)


class DataQualityMonitor:
    """
    数据质量监控器

    检查数据完整性、准确性、时效性和一致性。
    """

    def __init__(
        self,
        data_dir: str = None,
        report_dir: str = None,
        thresholds: Dict[str, Any] = None,
    ):
        self.data_dir = data_dir or str(Path(__file__).parent.parent / 'data')
        self.report_dir = report_dir or str(Path(__file__).parent.parent / 'reports' / 'quality')
        self.thresholds = thresholds or {
            'min_completeness': 0.8,
            'max_staleness_hours': 24,
            'min_record_count': 10,
            'max_duplicate_rate': 0.05,
        }
        os.makedirs(self.report_dir, exist_ok=True)

    def check_completeness(self, data: Dict[str, Any]) -> List[QualityIssue]:
        """检查数据完整性"""
        issues = []
        source = data.get('source', 'unknown')

        # 检查必填字段
        required_fields = data.get('required_fields', [])
        actual_fields = set(data.get('fields', {}).keys())
        missing = set(required_fields) - actual_fields

        if missing:
            issues.append(QualityIssue(
                issue_id=f"missing_fields_{source}",
                severity='critical',
                category='completeness',
                source=source,
                message=f"缺少必填字段: {missing}",
                details={'missing_fields': list(missing)}
            ))

        # 检查记录数量
        record_count = data.get('record_count', 0)
        if record_count < self.thresholds['min_record_count']:
            issues.append(QualityIssue(
                issue_id=f"low_records_{source}",
                severity='warning',
                category='completeness',
                source=source,
                message=f"记录数过低: {record_count} (阈值: {self.thresholds['min_record_count']})",
                details={'record_count': record_count, 'threshold': self.thresholds['min_record_count']}
            ))

        # 检查字段完整性比例
        if actual_fields:
            completeness = len(actual_fields & set(required_fields)) / len(required_fields) if required_fields else 1.0
            if completeness < self.thresholds['min_completeness']:
                issues.append(QualityIssue(
                    issue_id=f"low_completeness_{source}",
                    severity='warning',
                    category='completeness',
                    source=source,
                    message=f"字段完整率过低: {completeness:.1%}",
                    details={'completeness': completeness, 'threshold': self.thresholds['min_completeness']}
                ))

        return issues

    def check_timeliness(self, data: Dict[str, Any]) -> List[QualityIssue]:
        """检查数据时效性"""
        issues = []
        source = data.get('source', 'unknown')
        timestamp = data.get('timestamp', 0)

        if timestamp:
            staleness = datetime.now().timestamp() - timestamp
            staleness_hours = staleness / 3600

            if staleness_hours > self.thresholds['max_staleness_hours']:
                issues.append(QualityIssue(
                    issue_id=f"stale_data_{source}",
                    severity='critical',
                    category='timeliness',
                    source=source,
                    message=f"数据过时: {staleness_hours:.1f} 小时前",
                    details={'staleness_hours': staleness_hours, 'threshold': self.thresholds['max_staleness_hours']}
                ))
            elif staleness_hours > self.thresholds['max_staleness_hours'] * 0.5:
                issues.append(QualityIssue(
                    issue_id=f"near_stale_{source}",
                    severity='info',
                    category='timeliness',
                    source=source,
                    message=f"数据接近过期: {staleness_hours:.1f} 小时前",
                    details={'staleness_hours': staleness_hours}
                ))

        return issues

    def check_consistency(self, data: Dict[str, Any]) -> List[QualityIssue]:
        """检查数据一致性"""
        issues = []
        source = data.get('source', 'unknown')
        records = data.get('records', [])

        if not records:
            return issues

        # 检查重复记录
        record_keys = [json.dumps(r, sort_keys=True) for r in records[:1000]]
        unique_keys = set(record_keys)
        duplicate_rate = 1 - len(unique_keys) / len(record_keys) if record_keys else 0

        if duplicate_rate > self.thresholds['max_duplicate_rate']:
            issues.append(QualityIssue(
                issue_id=f"high_duplicates_{source}",
                severity='warning',
                category='consistency',
                source=source,
                message=f"重复率过高: {duplicate_rate:.1%}",
                details={'duplicate_rate': duplicate_rate, 'threshold': self.thresholds['max_duplicate_rate']}
            ))

        # 检查数值范围
        numeric_fields = data.get('numeric_fields', [])
        for field_name in numeric_fields:
            values = [r.get(field_name) for r in records if r.get(field_name) is not None]
            if values:
                min_val = min(values)
                max_val = max(values)
                # 检查异常值（超出3倍标准差）
                if len(values) > 10:
                    mean = sum(values) / len(values)
                    variance = sum((v - mean) ** 2 for v in values) / len(values)
                    std = variance ** 0.5
                    if std > 0:
                        outliers = [v for v in values if abs(v - mean) > 3 * std]
                        if outliers:
                            issues.append(QualityIssue(
                                issue_id=f"outliers_{source}_{field_name}",
                                severity='info',
                                category='consistency',
                                source=source,
                                message=f"字段 {field_name} 存在异常值: {len(outliers)} 个",
                                details={'field': field_name, 'outlier_count': len(outliers), 'mean': mean, 'std': std}
                            ))

        return issues

    def check_accuracy(self, data: Dict[str, Any]) -> List[QualityIssue]:
        """检查数据准确性"""
        issues = []
        source = data.get('source', 'unknown')
        records = data.get('records', [])

        if not records:
            return issues

        # 检查空值
        for field_name in data.get('required_fields', []):
            null_count = sum(1 for r in records if r.get(field_name) is None or r.get(field_name) == '')
            null_rate = null_count / len(records) if records else 0

            if null_rate > 0.1:
                issues.append(QualityIssue(
                    issue_id=f"high_nulls_{source}_{field_name}",
                    severity='warning',
                    category='accuracy',
                    source=source,
                    message=f"字段 {field_name} 空值率过高: {null_rate:.1%}",
                    details={'field': field_name, 'null_rate': null_rate, 'null_count': null_count}
                ))

        return issues

    def check_directory(self, dir_path: str) -> DataQualityReport:
        """检查目录中的数据质量"""
        issues = []
        total_records = 0
        metrics = {}

        if not os.path.exists(dir_path):
            return DataQualityReport(
                report_id=f"dir_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                generated_at=datetime.now().isoformat(),
                data_source=dir_path,
                total_records=0,
                issues=[QualityIssue(
                    issue_id="dir_missing",
                    severity='critical',
                    category='completeness',
                    source=dir_path,
                    message=f"数据目录不存在: {dir_path}"
                )]
            )

        # 扫描 JSON 文件
        json_files = [f for f in os.listdir(dir_path) if f.endswith('.json')]
        metrics['total_files'] = len(json_files)

        for filename in json_files[:50]:  # 限制检查数量
            filepath = os.path.join(dir_path, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                # 提取源信息
                source = data.get('source', filename)
                data['source'] = source

                # 运行各项检查
                issues.extend(self.check_completeness(data))
                issues.extend(self.check_timeliness(data))
                issues.extend(self.check_consistency(data))
                issues.extend(self.check_accuracy(data))

                total_records += data.get('record_count', len(data.get('records', [])))

            except Exception as e:
                issues.append(QualityIssue(
                    issue_id=f"parse_error_{filename}",
                    severity='critical',
                    category='accuracy',
                    source=dir_path,
                    message=f"解析文件失败: {filename}: {e}"
                ))

        # 去重问题
        seen = set()
        unique_issues = []
        for issue in issues:
            key = f"{issue.source}_{issue.category}_{issue.message[:50]}"
            if key not in seen:
                seen.add(key)
                unique_issues.append(issue)

        return DataQualityReport(
            report_id=f"dir_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            generated_at=datetime.now().isoformat(),
            data_source=dir_path,
            total_records=total_records,
            issues=unique_issues,
            metrics=metrics
        )

    def check_all(self, data_sources: Optional[List[Dict]] = None) -> Dict[str, DataQualityReport]:
        """检查所有数据源"""
        reports = {}

        if data_sources:
            for source in data_sources:
                source_dir = source.get('dir', os.path.join(self.data_dir, source.get('name', '')))
                reports[source.get('name', source_dir)] = self.check_directory(source_dir)
        else:
            # 自动扫描数据目录
            for subdir in os.listdir(self.data_dir):
                subdir_path = os.path.join(self.data_dir, subdir)
                if os.path.isdir(subdir_path):
                    reports[subdir] = self.check_directory(subdir_path)

        return reports

    def save_report(self, report: DataQualityReport) -> str:
        """保存质量报告"""
        filename = f"quality_report_{report.report_id}.json"
        filepath = os.path.join(self.report_dir, filename)

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)

        # 同时保存摘要
        summary_path = filepath.replace('.json', '.md')
        with open(summary_path, 'w', encoding='utf-8') as f:
            f.write(report.summary())

        logger.info(f"质量报告已保存: {filepath}")
        return filepath

    def get_alerts(self, reports: Dict[str, DataQualityReport]) -> List[Dict]:
        """获取告警信息"""
        alerts = []
        for source, report in reports.items():
            for issue in report.issues:
                if issue.severity in ('critical', 'warning'):
                    alerts.append({
                        'source': source,
                        'severity': issue.severity,
                        'category': issue.category,
                        'message': issue.message,
                        'timestamp': datetime.fromtimestamp(issue.detected_at).isoformat(),
                    })
        return alerts

    def generate_dashboard(self, reports: Dict[str, DataQualityReport]) -> str:
        """生成质量仪表盘"""
        lines = [
            "# 数据质量仪表盘",
            f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "## 总体概况",
            "",
            "| 数据源 | 记录数 | 健康评分 | 严重问题 | 警告 | 提示 |",
            "|--------|--------|----------|----------|------|------|",
        ]

        for source, report in reports.items():
            lines.append(
                f"| {source} | {report.total_records} | {report.health_score:.0f} | "
                f"{report.critical_count} | {report.warning_count} | {report.info_count} |"
            )

        lines.append("")
        lines.append("## 详细报告")
        lines.append("")

        for source, report in reports.items():
            lines.append(f"### {source}")
            lines.append(f"健康评分: {report.health_score:.0f}/100")
            lines.append("")
            if report.issues:
                lines.append("| 严重程度 | 类别 | 问题 |")
                lines.append("|----------|------|------|")
                for issue in report.issues:
                    lines.append(f"| {issue.severity} | {issue.category} | {issue.message} |")
            lines.append("")

        return "\n".join(lines)


# 便捷函数
def create_default_monitor(data_dir: str = None) -> DataQualityMonitor:
    """创建默认监控器"""
    base_dir = Path(__file__).parent.parent
    return DataQualityMonitor(
        data_dir=data_dir or str(base_dir / 'data'),
        report_dir=str(base_dir / 'reports' / 'quality')
    )


def quick_check(data_dir: str, verbose: bool = False) -> DataQualityReport:
    """快速检查数据质量"""
    monitor = DataQualityMonitor(data_dir=data_dir)
    report = monitor.check_directory(data_dir)
    if verbose:
        print(report.summary())
    return report
