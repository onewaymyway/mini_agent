"""
Error Log Analyzer - 分析 ~/.agent/logs/error.jsonl

按日期统计不同错误类型的数量，生成错误趋势报告。
包含每个异常类型的详细错误信息和堆栈示例。
支持只统计最新日期的错误。
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class ErrorLogAnalyzer:
    """错误日志分析器"""

    def __init__(self, log_path: Optional[Path] = None):
        """
        初始化分析器。

        Args:
            log_path: 错误日志文件路径，默认为 ~/.agent/logs/error.jsonl
        """
        if log_path is None:
            home_override = Path.home() / ".agent"
            log_path = home_override / "logs" / "error.jsonl"
        self.log_path = Path(log_path)
        self._records = None

    def _iter_records(self):
        """逐行读取并解析 JSON 记录，生成器模式避免内存溢出"""
        if not self.log_path.exists():
            print(f"错误日志文件不存在: {self.log_path}", file=sys.stderr)
            return

        with self.log_path.open("r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    yield record
                except json.JSONDecodeError as e:
                    print(f"第 {line_num} 行 JSON 解析失败: {e}", file=sys.stderr)
                    continue

    def load_records(self, force_reload: bool = False) -> list[dict]:
        """加载所有记录到内存 (仅用于小文件或需要多次遍历时)"""
        if self._records is None or force_reload:
            self._records = list(self._iter_records())
        return self._records

    def parse_date(self, ts_str: str) -> str:
        """从 ISO 时间戳提取日期字符串 (YYYY-MM-DD)"""
        try:
            dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
            return dt.date().isoformat()
        except Exception:
            return "unknown"

    def get_latest_date(self) -> Optional[str]:
        """获取日志中最新的日期 (YYYY-MM-DD)，如果没有有效记录返回 None"""
        latest = None
        for record in self._iter_records():
            date = self.parse_date(record.get("ts", ""))
            if date != "unknown":
                if latest is None or date > latest:
                    latest = date
        return latest

    def stats_by_date(self) -> dict[str, dict[str, int]]:
        """
        按日期统计每种错误类型的数量。

        Returns:
            {date: {exc_type: count, ...}, ...}
        """
        stats = defaultdict(Counter)
        for record in self._iter_records():
            date = self.parse_date(record.get("ts", ""))
            exc_type = record.get("exc_type", "Unknown")
            stats[date][exc_type] += 1
        return {date: dict(counter) for date, counter in sorted(stats.items())}

    def stats_by_date_summary(self) -> dict[str, dict]:
        """
        按日期统计汇总，包含每种错误计数和总计。

        Returns:
            {date: {exc_type: count, ..., "total": total}, ...}
        """
        stats = self.stats_by_date()
        result = {}
        for date, counter in stats.items():
            total = sum(counter.values())
            result[date] = dict(counter)
            result[date]["total"] = total
        return result

    def stats_latest_date(self) -> dict[str, int]:
        """
        只统计最新日期的错误类型分布。

        Returns:
            {exc_type: count, ...} 如果没有数据返回空字典
        """
        latest_date = self.get_latest_date()
        if latest_date is None:
            return {}
        
        counter = Counter()
        for record in self._iter_records():
            date = self.parse_date(record.get("ts", ""))
            if date == latest_date:
                exc_type = record.get("exc_type", "Unknown")
                counter[exc_type] += 1
        return dict(counter)

    def stats_latest_date_summary(self) -> dict:
        """
        只统计最新日期的错误汇总，包含总计。

        Returns:
            {"date": str, "total": int, "errors": {exc_type: count, ...}}
        """
        latest_date = self.get_latest_date()
        if latest_date is None:
            return {"date": None, "total": 0, "errors": {}}
        
        errors = self.stats_latest_date()
        total = sum(errors.values())
        return {
            "date": latest_date,
            "total": total,
            "errors": errors
        }

    def collect_error_details_latest_date(self, max_samples_per_type: int = 3) -> dict[str, dict]:
        """
        收集最新日期每种错误类型的详细信息。

        Returns:
            {exc_type: {"count": int, "top_messages": {...}, "top_wheres": {...}, "samples": [...]}, ...}
        """
        latest_date = self.get_latest_date()
        if latest_date is None:
            return {}

        details = defaultdict(lambda: {
            "count": 0,
            "messages": Counter(),
            "wheres": Counter(),
            "samples": []
        })

        for record in self._iter_records():
            date = self.parse_date(record.get("ts", ""))
            if date != latest_date:
                continue
            exc_type = record.get("exc_type", "Unknown")
            detail = details[exc_type]
            detail["count"] += 1
            detail["messages"][record.get("message", "")] += 1
            detail["wheres"][record.get("where", "unknown")] += 1

            if len(detail["samples"]) < max_samples_per_type:
                sample_key = (record.get("where", ""), record.get("message", ""))
                existing_keys = [(s["where"], s["message"]) for s in detail["samples"]]
                if sample_key not in existing_keys:
                    detail["samples"].append({
                        "ts": record.get("ts", ""),
                        "where": record.get("where", ""),
                        "message": record.get("message", ""),
                        "traceback": record.get("traceback", "")
                    })

        result = {}
        for exc_type, detail in sorted(details.items(), key=lambda x: -x[1]["count"]):
            result[exc_type] = {
                "count": detail["count"],
                "top_messages": dict(detail["messages"].most_common(5)),
                "top_wheres": dict(detail["wheres"].most_common(5)),
                "samples": detail["samples"]
            }
        return result

    def top_errors(self, n: int = 10) -> list[tuple[str, int]]:
        """获取出现频率最高的前 N 个错误类型"""
        counter = Counter()
        for record in self._iter_records():
            exc_type = record.get("exc_type", "Unknown")
            counter[exc_type] += 1
        return counter.most_common(n)

    def top_where(self, n: int = 10) -> list[tuple[str, int]]:
        """获取出现频率最高的前 N 个报错位置"""
        counter = Counter()
        for record in self._iter_records():
            where = record.get("where", "unknown")
            counter[where] += 1
        return counter.most_common(n)

    def hourly_distribution(self) -> dict[int, int]:
        """按小时统计错误分布 (0-23)"""
        counter = Counter()
        for record in self._iter_records():
            ts = record.get("ts", "")
            try:
                dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                counter[dt.hour] += 1
            except Exception:
                pass
        return dict(sorted(counter.items()))

    def daily_totals(self) -> dict[str, int]:
        """每日错误总数"""
        counter = Counter()
        for record in self._iter_records():
            date = self.parse_date(record.get("ts", ""))
            counter[date] += 1
        return dict(sorted(counter.items()))

    def filter_by_date_range(self, start_date: str, end_date: str) -> list[dict]:
        """按日期范围筛选记录 (包含开始和结束日期)"""
        start = datetime.fromisoformat(start_date).date()
        end = datetime.fromisoformat(end_date).date()
        result = []
        for record in self._iter_records():
            date = self.parse_date(record.get("ts", ""))
            if date == "unknown":
                continue
            record_date = datetime.fromisoformat(date).date()
            if start <= record_date <= end:
                result.append(record)
        return result

    def collect_error_details(self, max_samples_per_type: int = 3) -> dict[str, dict]:
        """
        收集每种错误类型的详细信息：错误消息、堆栈示例、发生位置分布。

        Args:
            max_samples_per_type: 每种错误类型最多保存的堆栈示例数

        Returns:
            {exc_type: {
                "count": int,
                "messages": Counter,
                "wheres": Counter,
                "samples": [{"ts": str, "where": str, "message": str, "traceback": str}, ...]
            }, ...}
        """
        details = defaultdict(lambda: {
            "count": 0,
            "messages": Counter(),
            "wheres": Counter(),
            "samples": []
        })

        for record in self._iter_records():
            exc_type = record.get("exc_type", "Unknown")
            detail = details[exc_type]
            detail["count"] += 1
            detail["messages"][record.get("message", "")] += 1
            detail["wheres"][record.get("where", "unknown")] += 1

            # 保存堆栈示例 (去重：同一 where + message 只保留第一个)
            if len(detail["samples"]) < max_samples_per_type:
                sample_key = (record.get("where", ""), record.get("message", ""))
                # 检查是否已有相同的样本
                existing_keys = [(s["where"], s["message"]) for s in detail["samples"]]
                if sample_key not in existing_keys:
                    detail["samples"].append({
                        "ts": record.get("ts", ""),
                        "where": record.get("where", ""),
                        "message": record.get("message", ""),
                        "traceback": record.get("traceback", "")
                    })

        # 转换 Counter 为普通 dict 并排序
        result = {}
        for exc_type, detail in sorted(details.items(), key=lambda x: -x[1]["count"]):
            result[exc_type] = {
                "count": detail["count"],
                "top_messages": dict(detail["messages"].most_common(5)),
                "top_wheres": dict(detail["wheres"].most_common(5)),
                "samples": detail["samples"]
            }
        return result

    def collect_error_details_by_date(self, max_samples_per_type: int = 2) -> dict[str, dict[str, dict]]:
        """
        按日期收集每种错误类型的详细信息。

        Returns:
            {date: {exc_type: {"count": int, "top_messages": {...}, "top_wheres": {...}, "samples": [...]}, ...}, ...}
        """
        details = defaultdict(lambda: defaultdict(lambda: {
            "count": 0,
            "messages": Counter(),
            "wheres": Counter(),
            "samples": []
        }))

        for record in self._iter_records():
            date = self.parse_date(record.get("ts", ""))
            exc_type = record.get("exc_type", "Unknown")
            detail = details[date][exc_type]
            detail["count"] += 1
            detail["messages"][record.get("message", "")] += 1
            detail["wheres"][record.get("where", "unknown")] += 1

            if len(detail["samples"]) < max_samples_per_type:
                sample_key = (record.get("where", ""), record.get("message", ""))
                existing_keys = [(s["where"], s["message"]) for s in detail["samples"]]
                if sample_key not in existing_keys:
                    detail["samples"].append({
                        "ts": record.get("ts", ""),
                        "where": record.get("where", ""),
                        "message": record.get("message", ""),
                        "traceback": record.get("traceback", "")
                    })

        # 转换格式
        result = {}
        for date in sorted(details.keys()):
            result[date] = {}
            for exc_type in sorted(details[date].keys(), key=lambda x: -details[date][x]["count"]):
                detail = details[date][exc_type]
                result[date][exc_type] = {
                    "count": detail["count"],
                    "top_messages": dict(detail["messages"].most_common(3)),
                    "top_wheres": dict(detail["wheres"].most_common(3)),
                    "samples": detail["samples"]
                }
        return result

    def format_traceback(self, tb: str, max_lines: int = 15, indent: str = "      ") -> str:
        """格式化堆栈跟踪，限制行数并添加缩进"""
        if not tb:
            return f"{indent}(无堆栈信息)"
        lines = tb.strip().split('\n')
        if len(lines) > max_lines:
            # 保留前几行和最后几行
            head = lines[:8]
            tail = lines[-7:]
            lines = head + [f"{indent}... (省略 {len(lines) - max_lines} 行) ..."] + tail
        return '\n'.join(f"{indent}{line}" for line in lines)

    def generate_text_report(self, days: Optional[int] = None, include_details: bool = True) -> str:
        """生成文本格式的完整分析报告"""
        lines = []
        lines.append("=" * 70)
        lines.append("错误日志分析报告")
        lines.append("=" * 70)
        lines.append(f"日志文件: {self.log_path}")
        lines.append(f"分析时间: {datetime.now().isoformat()}")
        lines.append("")

        # 总体统计
        total_records = sum(1 for _ in self._iter_records())
        lines.append(f"总错误记录数: {total_records}")
        lines.append("")

        # 每日总数
        daily = self.daily_totals()
        if days:
            daily = dict(list(daily.items())[-days:])
        lines.append("--- 每日错误总数 ---")
        for date, count in daily.items():
            lines.append(f"  {date}: {count}")
        lines.append("")

        # 按日期分错误类型统计
        stats = self.stats_by_date_summary()
        if days:
            stats = dict(list(stats.items())[-days:])
        lines.append("--- 按日期分错误类型统计 ---")
        for date, counter in stats.items():
            total = counter.pop("total", 0)
            lines.append(f"  {date} (总计: {total}):")
            for exc_type, count in sorted(counter.items(), key=lambda x: -x[1]):
                lines.append(f"    {exc_type}: {count}")
            counter["total"] = total
        lines.append("")

        # 高频错误 Top 20
        top_errors = self.top_errors(20)
        lines.append("--- 高频错误 Top 20 ---")
        for i, (exc_type, count) in enumerate(top_errors, 1):
            lines.append(f"  {i:2d}. {exc_type}: {count}")
        lines.append("")

        # 高频报错位置 Top 10
        top_where = self.top_where(10)
        lines.append("--- 高频报错位置 Top 10 ---")
        for i, (where, count) in enumerate(top_where, 1):
            lines.append(f"  {i:2d}. {where}: {count}")
        lines.append("")

        # 小时分布
        hourly = self.hourly_distribution()
        lines.append("--- 小时分布 (UTC) ---")
        max_count = max(hourly.values()) if hourly else 1
        for hour in range(24):
            count = hourly.get(hour, 0)
            bar = "#" * (count // max(1, max_count // 40)) if hourly else ""
            lines.append(f"  {hour:02d}:00  {count:4d} {bar}")
        lines.append("")

        # 详细错误信息和堆栈示例
        if include_details:
            lines.append("=" * 70)
            lines.append("各异常类型详细分析 (含错误消息分布、发生位置、堆栈示例)")
            lines.append("=" * 70)
            lines.append("")

            error_details = self.collect_error_details(max_samples_per_type=3)
            for exc_type, detail in error_details.items():
                lines.append(f"🔴 {exc_type} (共 {detail['count']} 次)")
                lines.append("-" * 50)

                # Top 错误消息
                if detail["top_messages"]:
                    lines.append("  📝 高频错误消息:")
                    for msg, cnt in detail["top_messages"].items():
                        msg_short = msg[:120] + "..." if len(msg) > 120 else msg
                        lines.append(f"     [{cnt}x] {msg_short}")
                    lines.append("")

                # Top 发生位置
                if detail["top_wheres"]:
                    lines.append("  📍 高频发生位置:")
                    for where, cnt in detail["top_wheres"].items():
                        lines.append(f"     [{cnt}x] {where}")
                    lines.append("")

                # 堆栈示例
                if detail["samples"]:
                    lines.append(f"  📋 堆栈示例 (共 {len(detail['samples'])} 个):")
                    for idx, sample in enumerate(detail["samples"], 1):
                        lines.append(f"     示例 {idx} [{sample['ts']}] @ {sample['where']}:")
                        lines.append(f"        消息: {sample['message'][:200]}" if len(sample['message']) > 200 else f"        消息: {sample['message']}")
                        lines.append(self.format_traceback(sample["traceback"]))
                        lines.append("")
                lines.append("")

        return "\n".join(lines)

    def generate_latest_date_report(self, include_details: bool = True) -> str:
        """生成最新日期的错误分析报告"""
        latest_summary = self.stats_latest_date_summary()
        latest_date = latest_summary["date"]
        
        if latest_date is None:
            return "日志中没有有效的日期记录"

        lines = []
        lines.append("=" * 70)
        lines.append(f"最新日期 ({latest_date}) 错误分析报告")
        lines.append("=" * 70)
        lines.append(f"日志文件: {self.log_path}")
        lines.append(f"分析时间: {datetime.now().isoformat()}")
        lines.append("")
        lines.append(f"日期: {latest_date}")
        lines.append(f"总错误数: {latest_summary['total']}")
        lines.append("")

        # 错误类型分布
        lines.append("--- 错误类型分布 ---")
        for exc_type, count in sorted(latest_summary["errors"].items(), key=lambda x: -x[1]):
            lines.append(f"  {exc_type}: {count}")
        lines.append("")

        if include_details:
            lines.append("=" * 70)
            lines.append("详细错误信息 (含错误消息分布、发生位置、堆栈示例)")
            lines.append("=" * 70)
            lines.append("")

            error_details = self.collect_error_details_latest_date(max_samples_per_type=3)
            for exc_type, detail in error_details.items():
                lines.append(f"🔴 {exc_type} (共 {detail['count']} 次)")
                lines.append("-" * 50)

                # Top 错误消息
                if detail["top_messages"]:
                    lines.append("  📝 高频错误消息:")
                    for msg, cnt in detail["top_messages"].items():
                        msg_short = msg[:120] + "..." if len(msg) > 120 else msg
                        lines.append(f"     [{cnt}x] {msg_short}")
                    lines.append("")

                # Top 发生位置
                if detail["top_wheres"]:
                    lines.append("  📍 高频发生位置:")
                    for where, cnt in detail["top_wheres"].items():
                        lines.append(f"     [{cnt}x] {where}")
                    lines.append("")

                # 堆栈示例
                if detail["samples"]:
                    lines.append(f"  📋 堆栈示例 (共 {len(detail['samples'])} 个):")
                    for idx, sample in enumerate(detail["samples"], 1):
                        lines.append(f"     示例 {idx} [{sample['ts']}] @ {sample['where']}:")
                        lines.append(f"        消息: {sample['message'][:200]}" if len(sample['message']) > 200 else f"        消息: {sample['message']}")
                        lines.append(self.format_traceback(sample["traceback"]))
                        lines.append("")
                lines.append("")

        return "\n".join(lines)

    def print_report(self, days: Optional[int] = None, include_details: bool = True):
        """打印报告到标准输出"""
        print(self.generate_text_report(days, include_details))

    def print_latest_date_report(self, include_details: bool = True):
        """打印最新日期报告到标准输出"""
        print(self.generate_latest_date_report(include_details))



def main():
    """CLI 入口"""
    import argparse

    parser = argparse.ArgumentParser(description="错误日志统计分析工具")
    parser.add_argument("--log", type=Path, help="错误日志文件路径")
    parser.add_argument("--days", type=int, help="只分析最近 N 天")
    parser.add_argument("--by-date", action="store_true", help="按日期分组显示错误类型统计")
    parser.add_argument("--top", type=int, default=20, help="显示高频错误 Top N")
    parser.add_argument("--where", type=int, default=10, help="显示高频报错位置 Top N")
    parser.add_argument("--report", action="store_true", help="生成完整报告")
    parser.add_argument("--output", type=Path, help="报告输出文件路径")
    parser.add_argument("--hourly", action="store_true", help="显示小时分布")
    parser.add_argument("--no-details", action="store_true", help="不包含详细错误信息和堆栈示例")
    parser.add_argument("--by-date-details", action="store_true", help="按日期显示详细错误信息")
    parser.add_argument("--latest-date", action="store_true", help="只分析最新日期的错误")
    parser.add_argument("--latest-date-summary", action="store_true", help="只显示最新日期的错误统计摘要")

    args = parser.parse_args()

    analyzer = ErrorLogAnalyzer(args.log)

    if args.latest_date:
        # 只分析最新日期
        report = analyzer.generate_latest_date_report(include_details=not args.no_details)
        if args.output:
            args.output.write_text(report, encoding="utf-8")
            print(f"报告已保存到: {args.output}")
        else:
            print(report)
    elif args.latest_date_summary:
        # 只显示最新日期摘要
        summary = analyzer.stats_latest_date_summary()
        if summary["date"] is None:
            print("日志中没有有效的日期记录")
        else:
            print(f"最新日期: {summary['date']}")
            print(f"总错误数: {summary['total']}")
            print("错误类型分布:")
            for exc_type, count in sorted(summary["errors"].items(), key=lambda x: -x[1]):
                print(f"  {exc_type}: {count}")
    elif args.report or (not args.by_date and not args.top and not args.where and not args.hourly and not args.by_date_details):
        # 默认生成完整报告
        report = analyzer.generate_text_report(args.days, include_details=not args.no_details)
        if args.output:
            args.output.write_text(report, encoding="utf-8")
            print(f"报告已保存到: {args.output}")
        else:
            print(report)
    else:
        if args.by_date:
            stats = analyzer.stats_by_date_summary()
            if args.days:
                stats = dict(list(stats.items())[-args.days:])
            for date, counter in stats.items():
                total = counter.pop("total", 0)
                print(f"{date} (总计: {total}):")
                for exc_type, count in sorted(counter.items(), key=lambda x: -x[1]):
                    print(f"  {exc_type}: {count}")
                counter["total"] = total

        if args.by_date_details:
            details = analyzer.collect_error_details_by_date(max_samples_per_type=2)
            if args.days:
                details = dict(list(details.items())[-args.days:])
            for date, exc_types in details.items():
                print(f"\n=== {date} ===")
                for exc_type, detail in exc_types.items():
                    print(f"  {exc_type} ({detail['count']} 次):")
                    if detail["top_messages"]:
                        for msg, cnt in detail["top_messages"].items():
                            print(f"    消息 [{cnt}x]: {msg[:100]}")
                    if detail["top_wheres"]:
                        for where, cnt in detail["top_wheres"].items():
                            print(f"    位置 [{cnt}x]: {where}")
                    if detail["samples"]:
                        for idx, sample in enumerate(detail["samples"], 1):
                            print(f"    示例 {idx} @ {sample['where']}:")
                            print(f"      {sample['message'][:150]}")
                            tb_lines = sample["traceback"].strip().split('\n')[:10]
                            for line in tb_lines:
                                print(f"      {line}")
                            if len(sample["traceback"].strip().split('\n')) > 10:
                                print(f"      ... (省略)")

        if args.top:
            print(f"\n高频错误 Top {args.top}:")
            for i, (exc_type, count) in enumerate(analyzer.top_errors(args.top), 1):
                print(f"  {i:2d}. {exc_type}: {count}")

        if args.where:
            print(f"\n高频报错位置 Top {args.where}:")
            for i, (where, count) in enumerate(analyzer.top_where(args.where), 1):
                print(f"  {i:2d}. {where}: {count}")

        if args.hourly:
            print("\n小时分布 (UTC):")
            hourly = analyzer.hourly_distribution()
            for hour in range(24):
                count = hourly.get(hour, 0)
                print(f"  {hour:02d}:00  {count}")


if __name__ == "__main__":
    main()
