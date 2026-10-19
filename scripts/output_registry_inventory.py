#!/usr/bin/env python3
"""scripts/output_registry_inventory.py — 产出注册表 P0 只读盘点。

对应 next_doc/management_output_project_model_plan.md 第 9 章 P0。

用法：
    PYTHONPATH=src python scripts/output_registry_inventory.py --project <项目根>
    PYTHONPATH=src python scripts/output_registry_inventory.py --project <项目根> --stdout

行为：
  - 只读扫描 output_projects_root、.agent/goals.json 与六处产出位置；
  - 唯一写入：.agent/output_registry/inventory_draft.json（--stdout 时不写盘）；
  - 不修改 output_projects_root 下的任何文件。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from mini_agent.output_registry import build_inventory, write_inventory_draft  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="产出注册表 P0 只读盘点")
    parser.add_argument("--project", default=".", help="项目根目录（默认当前目录）")
    parser.add_argument("--stdout", action="store_true", help="只打印 JSON，不写草稿文件")
    args = parser.parse_args(argv)

    project_root = Path(args.project).resolve()
    inventory = build_inventory(project_root)

    if args.stdout:
        print(json.dumps(inventory, ensure_ascii=False, indent=2))
        return 0

    draft = write_inventory_draft(project_root, inventory)
    s = inventory["summary"]
    print(f"盘点完成：候选项目 {s['candidate_projects']}（已绑定目标 {s['candidate_bound']}，"
          f"游离 {s['candidate_floating']}），目标 {s['goals_total']}，异常 {s['anomalies']} 条")
    print(f"草稿已写入：{draft}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
