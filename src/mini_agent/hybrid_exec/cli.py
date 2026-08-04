"""
hybrid_exec/cli.py — `mini-agent hybrid-exec ...` 独立命令行子命令

对应 next_doc/hybrid_exec_design_plan.md 的后续需求："task 应该能直接用命令行
简单方式启动"。写法与 cli/commands/workflow_cmd.py 的独立 CLI 短路方式完全
一致（见该文件头部说明）：不进入 build_parser() 主流程、不需要先构造一整个
交互式 Agent，只 load_config() 即可，适合脚本/cron/systemd 里直接触发。

子命令：
  mini-agent hybrid-exec run <task_id> [input_json] [--desc TEXT]
                                        [--allow-tiers script,llm,agent]
                                        [--force-reexplore] [--fs-write]
      执行一次任务（已有 active 脚本会优先复用，不会重新探索）。
  mini-agent hybrid-exec list
      列举 .agent/hybrid_exec/scripts/ 下已归档的所有 task_id 及其当前状态。
  mini-agent hybrid-exec show <task_id>
      查看某个 task_id 的仓库元信息（meta.json）与当前 active 脚本源码。

独立 CLI 独有参数：`--project`/`-p <path>` 指定项目根目录（默认当前目录），
与 workflow/daemon/user/self 子命令一致，由 cli/app.py::_extract_project_root
统一解析（在短路分支里完成，不在本文件重复实现）。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional


def _load_cfg(project_root: Path):
    from mini_agent.config import load_config

    return load_config(project_root=project_root)


def _cmd_run(args: argparse.Namespace, project_root: Path) -> None:
    from mini_agent.hybrid_exec import ExecutionTier, TaskSpec, default_executor

    try:
        input_data = json.loads(args.input_json) if args.input_json else {}
    except json.JSONDecodeError as e:
        print(f"[hybrid-exec] input_json 不是合法 JSON：{e}")
        return
    if not isinstance(input_data, dict):
        print("[hybrid-exec] input_json 解析后必须是一个 JSON object（dict）")
        return

    allow_tiers = None
    if args.allow_tiers:
        try:
            allow_tiers = tuple(ExecutionTier(t.strip()) for t in args.allow_tiers.split(",") if t.strip())
        except ValueError as e:
            print(f"[hybrid-exec] --allow-tiers 取值非法（可选 script/llm/agent）：{e}")
            return

    cfg = None
    try:
        cfg = _load_cfg(project_root)
    except Exception:
        cfg = None  # 允许在没有完整 agent_config.json 的目录下也能跑（走默认值）

    kwargs = {}
    if cfg is not None:
        kwargs["mini_agent_config"] = cfg
    executor = default_executor(project_root, **kwargs)

    task_kwargs = dict(
        task_id=args.task_id,
        description=args.desc or f"命令行直接触发的 hybrid_exec 任务：{args.task_id}",
        input_data=input_data,
        force_reexplore=bool(args.force_reexplore),
        agent_fs_write_enabled=bool(args.fs_write),
    )
    if allow_tiers is not None:
        task_kwargs["allow_tiers"] = allow_tiers

    result = executor.run(TaskSpec(**task_kwargs))

    print(f"[hybrid-exec] task_id={args.task_id!r} ok={result.ok} tier_used={result.tier_used.value} "
          f"script_version={result.script_version} duration={result.duration:.2f}s")
    print("--- output ---")
    if isinstance(result.output, (dict, list)):
        print(json.dumps(result.output, ensure_ascii=False, indent=2))
    else:
        print(result.output)
    if args.verbose and result.attempts:
        print("--- attempts ---")
        for a in result.attempts:
            status = "OK" if a.ok else "FAIL"
            print(f"  [{status}] {a.stage} ({a.tier.value}, {a.duration:.2f}s): {a.detail}")


def _cmd_list(args: argparse.Namespace, project_root: Path) -> None:
    scripts_dir = project_root / ".agent" / "hybrid_exec" / "scripts"
    if not scripts_dir.exists():
        print("[hybrid-exec] 尚无任何已归档的任务（.agent/hybrid_exec/scripts/ 不存在）")
        return
    task_dirs = sorted(p for p in scripts_dir.iterdir() if p.is_dir())
    if not task_dirs:
        print("[hybrid-exec] 尚无任何已归档的任务")
        return
    for task_dir in task_dirs:
        meta_path = task_dir / "meta.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"  {task_dir.name}: meta.json 解析失败")
            continue
        active_version = meta.get("active_version")
        versions = meta.get("versions", {})
        active_info = versions.get(str(active_version), {})
        print(f"  {task_dir.name}: active=v{active_version} "
              f"成功={active_info.get('success_count', 0)} "
              f"失败={active_info.get('fail_count', 0)} "
              f"连续失败={active_info.get('consecutive_fail', 0)} "
              f"状态={active_info.get('status', '?')}")


def _cmd_show(args: argparse.Namespace, project_root: Path) -> None:
    task_dir = project_root / ".agent" / "hybrid_exec" / "scripts" / args.task_id
    meta_path = task_dir / "meta.json"
    if not meta_path.exists():
        print(f"[hybrid-exec] 未找到 task_id={args.task_id!r}（{meta_path} 不存在）")
        return
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    active_version = meta.get("active_version")
    if active_version is not None:
        script_path = task_dir / f"v{active_version}.py"
        if script_path.exists():
            print(f"--- v{active_version}.py ---")
            print(script_path.read_text(encoding="utf-8"))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mini-agent hybrid-exec", add_help=True)
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_run = sub.add_parser("run", help="执行一次 hybrid_exec 任务")
    p_run.add_argument("task_id", help="任务标识，对应脚本仓库 key")
    p_run.add_argument("input_json", nargs="?", default="{}", help="JSON 字符串形式的输入，如 '{\"text\": \"hi\"}'")
    p_run.add_argument("--desc", default=None, help="任务描述（探索/修复用的 prompt），不传用默认占位描述")
    p_run.add_argument("--allow-tiers", default=None, help="逗号分隔，如 script,llm,agent（默认三层都允许）")
    p_run.add_argument("--force-reexplore", action="store_true", help="忽略已有脚本，强制重新探索")
    p_run.add_argument("--fs-write", action="store_true", help="允许探索/修复用的 Agent 写文件系统（默认只读）")
    p_run.add_argument("-v", "--verbose", action="store_true", help="打印完整的决策轨迹（attempts）")
    p_run.set_defaults(func=_cmd_run)

    p_list = sub.add_parser("list", help="列举已归档的所有任务")
    p_list.set_defaults(func=_cmd_list)

    p_show = sub.add_parser("show", help="查看某个任务的仓库元信息与当前脚本")
    p_show.add_argument("task_id")
    p_show.set_defaults(func=_cmd_show)

    return parser


def run_hybrid_exec_cli(argv: list, project_root: Path) -> Optional[int]:
    """`mini-agent hybrid-exec ...` 独立命令行入口，cli/app.py 里短路调用。"""
    parser = _build_parser()
    args = parser.parse_args(argv)
    args.func(args, project_root)
    return 0
