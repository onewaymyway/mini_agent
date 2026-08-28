"""
hybrid_exec/cli.py — `mini-agent hybrid-exec ...` 独立命令行子命令

对应 next_doc/hybrid_exec_design_plan.md 的后续需求："task 应该能直接用命令行
简单方式启动"。写法与 cli/commands/workflow_cmd.py 的独立 CLI 短路方式完全
一致（见该文件头部说明）：不进入 build_parser() 主流程、不需要先构造一整个
交互式 Agent，只 load_config() 即可，适合脚本/cron/systemd 里直接触发。

子命令：
  mini-agent hybrid-exec run <task_id> [input_json] [--input-file PATH]
                                        [--field key=value ...] [--desc TEXT]
                                        [--allow-tiers script,llm,agent]
                                        [--force-reexplore] [--fs-write]
      执行一次任务（已有 active 脚本会优先复用，不会重新探索）。input_data
      的来源按优先级 --input-file > --field(可重复) > 位置参数 input_json
      > stdin 管道，都不传则为 `{}`，详见 _resolve_input_data() 的说明——
      主要是为了绕开 Windows PowerShell 传递含双引号 JSON 字符串给原生 exe
      时常见的引号转义丢失问题（症状：`unrecognized arguments: xxx}`）。
  mini-agent hybrid-exec list
      列举 .agent/hybrid_exec/scripts/ 下已归档的所有 task_id 及其当前状态。
  mini-agent hybrid-exec show <task_id>
      查看某个 task_id 的仓库元信息（meta.json）与当前 active 脚本源码。
  mini-agent hybrid-exec scaffold <task_id> --carrier {script,workflow-step,entrypoint}
                                             [--desc TEXT] [--output PATH] [--force]
      生成一段"把 TaskSpec 接进某个可运行入口"的执行载体样板代码，对应
      next_doc/hybrid_exec_improvement_directions.md B1，具体见 scaffold.py。

独立 CLI 独有参数：`--project`/`-p <path>` 指定项目根目录（默认当前目录），
与 workflow/daemon/user/self 子命令一致，由 cli/app.py::_extract_project_root
统一解析（在短路分支里完成，不在本文件重复实现）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional


def _load_cfg(project_root: Path):
    from mini_agent.config import load_config

    return load_config(project_root=project_root)


def _resolve_input_data(args: argparse.Namespace) -> "tuple[Optional[dict], Optional[str]]":
    """按优先级解析本次调用的 input_data，返回 (input_data, error_message)。

    Windows（尤其 PowerShell）在把包含双引号的 JSON 字符串当作单个命令行
    参数传给原生 exe 时，存在广为人知的引号转义丢失问题（内层 `"` 可能被
    吞掉），导致 JSON 字符串被空格拆成多个 argv token，argparse 报
    "unrecognized arguments"。为此提供三条不依赖行内 JSON 引号的替代
    路径，任选其一即可，优先级：
      1. --input-file <path>   从文件读 JSON 文本（最稳妥，任何平台都不会
                                有 shell 转义问题）
      2. --field key=value     重复传多次，拼成一个扁平 dict，每个 value
                                只需要最外层一层引号（不含嵌套双引号），
                                Windows 下最好写
      3. 位置参数 input_json   直接传 JSON 字符串（Linux/macOS 下最省事，
                                Windows 下建议改用 1/2，或用管道见下）
      4. stdin                 不传上述任何一种、且检测到 stdin 有管道输入
                                时，从 stdin 读取整段 JSON 文本（Windows
                                PowerShell 下用管道可以完全绕开引号转义：
                                `'{"text": "hello world"}' | mini-agent
                                hybrid-exec run word_count_v1`）
    都不传则视为 `{}`。
    """
    if args.input_file:
        try:
            text = Path(args.input_file).read_text(encoding="utf-8")
        except OSError as e:
            return None, f"--input-file 读取失败：{e}"
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            return None, f"--input-file 内容不是合法 JSON：{e}"
        if not isinstance(data, dict):
            return None, "--input-file 内容解析后必须是一个 JSON object（dict）"
        return data, None

    if args.field:
        data: dict = {}
        for pair in args.field:
            if "=" not in pair:
                return None, f"--field 格式错误（应为 key=value）：{pair!r}"
            key, _, raw_value = pair.partition("=")
            key = key.strip()
            try:
                value = json.loads(raw_value)  # 支持数字/布尔/null/嵌套 JSON
            except json.JSONDecodeError:
                value = raw_value  # 解析失败则原样按字符串处理，覆盖最常见的 --field text=hi 场景
            data[key] = value
        return data, None

    if args.input_json is not None:
        try:
            data = json.loads(args.input_json)
        except json.JSONDecodeError as e:
            return None, (
                f"input_json 不是合法 JSON：{e}\n"
                "提示：Windows PowerShell 传递含双引号的 JSON 容易被转义丢失导致解析错误，"
                "建议改用 --input-file <path>、多个 --field key=value，"
                "或用管道 `'...' | mini-agent hybrid-exec run <task_id>` 传入。"
            )
        if not isinstance(data, dict):
            return None, "input_json 解析后必须是一个 JSON object（dict）"
        return data, None

    if not sys.stdin.isatty():
        stdin_text = sys.stdin.read().strip()
        if stdin_text:
            try:
                data = json.loads(stdin_text)
            except json.JSONDecodeError as e:
                return None, f"stdin 内容不是合法 JSON：{e}"
            if not isinstance(data, dict):
                return None, "stdin 内容解析后必须是一个 JSON object（dict）"
            return data, None

    return {}, None


def _cmd_run(args: argparse.Namespace, project_root: Path) -> None:
    from mini_agent.hybrid_exec import ExecutionTier, TaskSpec, default_executor

    input_data, err = _resolve_input_data(args)
    if err:
        print(f"[hybrid-exec] {err}")
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


def _cmd_scaffold(args: argparse.Namespace, project_root: Path) -> None:
    from .scaffold import CARRIER_CHOICES, default_output_filename, render_scaffold

    if args.carrier not in CARRIER_CHOICES:
        print(f"[hybrid-exec] --carrier 取值非法（可选 {', '.join(CARRIER_CHOICES)}）：{args.carrier!r}")
        return

    desc = args.desc or f"待完善描述：{args.task_id}"
    code = render_scaffold(args.carrier, args.task_id, desc)

    output_path = Path(args.output) if args.output else Path(default_output_filename(args.carrier, args.task_id))
    if output_path.exists() and not args.force:
        print(f"[hybrid-exec] 目标文件已存在，未覆盖（加 --force 允许覆盖）：{output_path}")
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(code, encoding="utf-8")
    print(f"[hybrid-exec] 已生成 {args.carrier} 样板：{output_path}")

    # [B3] 生成后做一次轻量自检：python/entrypoint 载体校验语法能否被编译，
    # workflow-step 载体校验能否被解析成合法 YAML。只验证"样板本身没写
    # 坏"（语法正确、能被 import/解析），不代表 TODO 里的业务逻辑已经补
    # 完整——那部分仍需要调用方自己跑一遍 `hybrid-exec run` 验证。
    if args.carrier in ("script", "entrypoint"):
        try:
            compile(code, str(output_path), "exec")
        except SyntaxError as e:
            print(f"[hybrid-exec] 警告：生成的样板存在语法错误，请检查：{e}")
        else:
            print("[hybrid-exec] 自检通过：样板语法正确（TODO 部分仍需手工补全）")
        print(
            f"[hybrid-exec] 提示：填完 TODO 后，建议先跑一次 "
            f"`mini-agent hybrid-exec run {args.task_id} --project <path> -v` "
            "看 attempts 决策轨迹是否符合预期。"
        )
    else:
        try:
            import yaml  # type: ignore

            yaml.safe_load(code)
        except ImportError:
            print("[hybrid-exec] 提示：未安装 pyyaml，跳过 YAML 语法自检（不影响生成结果）")
        except Exception as e:  # noqa: BLE001 — 自检失败只提示，不影响已生成的文件
            print(f"[hybrid-exec] 警告：生成的样板 YAML 解析失败，请检查：{e}")
        else:
            print("[hybrid-exec] 自检通过：样板 YAML 语法正确（TODO 部分仍需手工补全）")
        print(
            "[hybrid-exec] 提示：把这段片段粘进目标 workflow 的 steps 列表、填完 TODO 后，"
            "建议用 `mini-agent workflow run <workflow_name> --project <path>` 跑一次验证。"
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mini-agent hybrid-exec", add_help=True)
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_run = sub.add_parser("run", help="执行一次 hybrid_exec 任务")
    p_run.add_argument("task_id", help="任务标识，对应脚本仓库 key")
    p_run.add_argument("input_json", nargs="?", default=None,
                        help="JSON 字符串形式的输入，如 '{\"text\": \"hi\"}'。"
                             "Windows PowerShell 下容易有引号转义问题，建议改用 "
                             "--input-file / --field，或用管道传 stdin（见 --help 顶部说明）")
    p_run.add_argument("--input-file", default=None, help="从文件读取 JSON 输入（object），不受 shell 引号转义影响")
    p_run.add_argument("--field", action="append", default=None,
                        help="key=value，可重复传多次拼成一个扁平 dict 输入，"
                             "如 --field text=\"hello world\"（Windows 下最省心，只需最外层一层引号）")
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

    p_scaffold = sub.add_parser(
        "scaffold", help="生成一段接入 hybrid_exec 的执行载体样板代码（script/workflow-step/entrypoint）"
    )
    p_scaffold.add_argument("task_id", help="任务标识，会作为 TaskSpec.task_id 写进生成的样板里")
    p_scaffold.add_argument(
        "--carrier",
        required=True,
        choices=("script", "workflow-step", "entrypoint"),
        help="生成哪一类载体：script=独立可运行脚本，"
             "workflow-step=可粘进 workflow yaml 的 hybrid_step 节点片段，"
             "entrypoint=贴合 external_projects 结构、含降级包装的 entrypoint 样板",
    )
    p_scaffold.add_argument("--desc", default=None, help="任务的一句话描述，写进生成样板的 TaskSpec.description")
    p_scaffold.add_argument("--output", default=None, help="输出文件路径，不传则用 <task_id> 派生的默认文件名")
    p_scaffold.add_argument("--force", action="store_true", help="目标文件已存在时允许覆盖")
    p_scaffold.set_defaults(func=_cmd_scaffold)

    return parser


def run_hybrid_exec_cli(argv: list, project_root: Path) -> Optional[int]:
    """`mini-agent hybrid-exec ...` 独立命令行入口，cli/app.py 里短路调用。"""
    parser = _build_parser()
    args = parser.parse_args(argv)
    args.func(args, project_root)
    return 0
