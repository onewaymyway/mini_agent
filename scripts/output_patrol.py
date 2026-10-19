#!/usr/bin/env python3
"""scripts/output_patrol.py — 产出项目巡检命令行入口（P4.5 采集 / P5 报告）。

对应 next_doc/management_output_project_model_plan.md 第 8.6 节与第 9 章 P4.5、P5。

用法（PYTHONPATH=src）：
    python scripts/output_patrol.py --project <项目根> collect          # P4.5：只采集，不告警
    python scripts/output_patrol.py --project <项目根> report           # P5：生成巡检报告
    python scripts/output_patrol.py --project <项目根> report --stale-days 60 --version-gap-days 45
    python scripts/output_patrol.py --project <项目根> candidates      # P6：列出未登记目录（只读）

只读：不修改产出项目、注册表或 goals.json。只写 .agent/output_registry/ 下。
阈值默认值是未校准的，报告中会标注 calibration=uncalibrated。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from mini_agent.output_registry.adoption import list_unregistered  # noqa: E402
from mini_agent.output_registry.patrol import run_collect, run_report  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="产出项目巡检（P4.5 / P5）")
    parser.add_argument("--project", default=".", help="项目根目录")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("collect")
    sub.add_parser("candidates")
    rp = sub.add_parser("report")
    rp.add_argument("--stale-days", type=float, default=None)
    rp.add_argument("--version-gap-days", type=float, default=None)
    args = parser.parse_args(argv)

    project = Path(args.project).resolve()
    if args.cmd == "collect":
        print(json.dumps(run_collect(project), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "candidates":
        print(json.dumps(list_unregistered(project), ensure_ascii=False, indent=2))
        return 0
    overrides = {}
    if args.stale_days is not None:
        overrides["stale_days"] = args.stale_days
    if args.version_gap_days is not None:
        overrides["version_gap_days"] = args.version_gap_days
    report = run_report(project, thresholds=overrides or None)
    summary = {k: report[k] for k in ("generated_at", "projects_scanned", "findings_total")}
    summary["calibration"] = report["thresholds"]["calibration"]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
