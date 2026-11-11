"""info_hub 命令行入口。

用法（在项目根目录执行）：
  python cli.py list                 列出信息源
  python cli.py validate             校验全部信息源配置
  python cli.py run <source_id>      立即抓取一个信息源
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from adapters.registry import list_adapters
from core.runner import run_source
from core.settings import load_settings
from core.sources import load_sources


def cmd_list(settings) -> int:
    loaded = load_sources(settings)
    if not loaded.specs and not loaded.errors:
        print("（暂无信息源）")
        return 0
    for sid, spec in sorted(loaded.specs.items()):
        flag = "启用" if spec.enabled else "停用"
        print(f"{sid:20s} {flag}  [{spec.category}] {spec.kind:10s} {spec.adapter:10s} {spec.name}")
    for sid, errs in sorted(loaded.errors.items()):
        print(f"{sid:20s} 配置错误: {'; '.join(errs)}")
    return 0


def cmd_validate(settings) -> int:
    loaded = load_sources(settings)
    print(f"已加载 {len(loaded.specs)} 个信息源，{len(loaded.errors)} 个有错误")
    print(f"可用适配器: {', '.join(list_adapters())}")
    for sid, errs in sorted(loaded.errors.items()):
        for err in errs:
            print(f"  [{sid}] {err}")
    return 1 if loaded.errors else 0


def cmd_run(settings, source_id: str) -> int:
    loaded = load_sources(settings)
    if source_id in loaded.errors:
        print(f"信息源 {source_id} 配置有误: {'; '.join(loaded.errors[source_id])}")
        return 1
    spec = loaded.specs.get(source_id)
    if spec is None:
        print(f"未找到信息源 {source_id}")
        return 1
    result = run_source(spec, settings)
    if result.ok:
        print(f"[{source_id}] 成功，{result.item_count} 条，快照: {result.raw_path}")
        return 0
    print(f"[{source_id}] 失败: {result.error}")
    return 1


def main(argv: list[str] | None = None, root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(prog="info_hub")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="列出信息源")
    sub.add_parser("validate", help="校验信息源配置")
    p_run = sub.add_parser("run", help="立即抓取一个信息源")
    p_run.add_argument("source_id")
    args = parser.parse_args(argv)

    settings = load_settings(root)
    if args.command == "list":
        return cmd_list(settings)
    if args.command == "validate":
        return cmd_validate(settings)
    if args.command == "run":
        return cmd_run(settings, args.source_id)
    return 2


if __name__ == "__main__":
    sys.exit(main())
