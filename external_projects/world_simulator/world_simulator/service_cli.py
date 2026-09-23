#!/usr/bin/env python
"""world_simulator/service_cli.py — 面向外部调用方的通用命令行入口。

用法：
    python -m world_simulator.service_cli <子命令> [参数...]
    python -m world_simulator.service_cli --help

**和 `entrypoints/*.py` 的区别**：`entrypoints/` 下每个脚本对应
`project.yaml` 里的一个调度入口，输出是给人看的文本（`sim_id=...`/
`summary=...` 这种一行一个字段的格式），服务对象是 mini_agent 框架
自带的 daemon 调度器；这里是给"完全独立的外部服务"场景准备的通用
命令行接口——每个子命令 1:1 对应 `tool_api.py` 的一个函数，**统一
输出单行 JSON 到 stdout**（`--pretty` 可选缩进，方便人读），成功
`exit 0`、失败 `exit 1`，方便脚本 `subprocess` 调用或者接
`jq`/`json.loads(subprocess_output)` 解析——这是主 agent（或任何
外部脚本）在"没有走 HTTP、直接拉起子进程调用"场景下的调用方式；
HTTP 场景见 `server.py`，两者共用同一个 `tool_api.py`，行为完全
一致。

不在这里重新实现任何业务逻辑——所有子命令的实现都是"解析命令行参数
→ 调 `tool_api.py` 对应函数 → 把返回的 dict 序列化成 JSON 打印"，
一个子命令的函数体一般不超过 10 行。
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, Optional

from world_simulator import tool_api


def _print_result(result: Dict[str, Any], *, pretty: bool) -> int:
    """把 `tool_api.py` 统一的 `{"ok": ..., ...}` 结构打到 stdout，
    `ok=True` 返回 0，`ok=False` 返回 1——外部调用方（尤其是
    `subprocess` 场景）不需要解析 JSON 内容就能先靠退出码判断这次
    调用有没有成功，JSON 本体仍然完整打印，供需要细节的调用方解析。
    """
    indent = 2 if pretty else None
    print(json.dumps(result, ensure_ascii=False, indent=indent))
    return 0 if result.get("ok") else 1


def _parse_json_arg(raw: Optional[str], *, arg_name: str) -> Any:
    """`--settings`/`--updates`/`--autopilot` 这类接受一段 JSON 字符串
    的参数的统一解析：`None` 原样返回；解析失败时不悄悄吞掉，直接
    让整个命令失败并给出清晰的报错位置（哪个参数、原始内容是什么），
    而不是把一个错误的 `dict` 传给 `tool_api.py` 造成更难定位的下游
    报错。"""
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "type": "validation_error",
                        "message": f"--{arg_name} 不是合法 JSON（{exc}）：{raw!r}",
                    },
                },
                ensure_ascii=False,
            )
        )
        raise SystemExit(1)


def _cmd_create_simulation(args: argparse.Namespace) -> Dict[str, Any]:
    settings = _parse_json_arg(args.settings, arg_name="settings")
    return tool_api.create_simulation(template=args.template, intent=args.intent, settings=settings)


def _cmd_advance(args: argparse.Namespace) -> Dict[str, Any]:
    custom_option = None
    if args.custom_option_label or args.custom_option_description:
        custom_option = {
            "label": args.custom_option_label or "",
            "description": args.custom_option_description or "",
        }
    return tool_api.advance_simulation(
        args.sim_id,
        choice_option_id=args.choice,
        custom_option=custom_option,
        decision_context=args.decision_context or "",
        chosen_by=args.chosen_by,
        allow_custom_options=args.allow_custom_options,
    )


def _cmd_fast_forward(args: argparse.Namespace) -> Dict[str, Any]:
    return tool_api.fast_forward_simulation(
        args.sim_id,
        max_steps=args.max_steps,
        stop_on_major_decision=not args.no_stop_on_major_decision,
        stop_on_options=not args.no_stop_on_options,
        decision_context=args.decision_context or "",
        chosen_by=args.chosen_by,
        allow_custom_options=args.allow_custom_options,
    )


def _cmd_get(args: argparse.Namespace) -> Dict[str, Any]:
    return tool_api.get_simulation(
        args.sim_id, include_history=args.include_history, history_limit=args.history_limit
    )


def _cmd_list(args: argparse.Namespace) -> Dict[str, Any]:
    return tool_api.list_simulations()


def _cmd_set_status(args: argparse.Namespace) -> Dict[str, Any]:
    return tool_api.set_status(args.sim_id, args.status)


def _cmd_set_pilot(args: argparse.Namespace) -> Dict[str, Any]:
    autopilot = _parse_json_arg(args.autopilot, arg_name="autopilot")
    return tool_api.set_pilot_config(args.sim_id, pilot_mode=args.pilot_mode, autopilot=autopilot)


def _cmd_update_settings(args: argparse.Namespace) -> Dict[str, Any]:
    updates = _parse_json_arg(args.updates, arg_name="updates") or {}
    return tool_api.update_settings(args.sim_id, updates)


def _cmd_rename(args: argparse.Namespace) -> Dict[str, Any]:
    return tool_api.rename_simulation(args.sim_id, args.title)


def _cmd_delete(args: argparse.Namespace) -> Dict[str, Any]:
    if not args.yes:
        return {
            "ok": False,
            "error": {
                "type": "validation_error",
                "message": "删除是不可逆操作，需要加 --yes 显式确认才会真正执行。",
            },
        }
    return tool_api.delete_simulation(args.sim_id)


def _cmd_apply_structural_change(args: argparse.Namespace) -> Dict[str, Any]:
    return tool_api.apply_structural_change_at_step(args.sim_id, args.step, branch=args.branch)


def _cmd_accept_causal_line(args: argparse.Namespace) -> Dict[str, Any]:
    return tool_api.accept_causal_line_suggestion(args.sim_id, args.suggestion_id)


def _cmd_reject_causal_line(args: argparse.Namespace) -> Dict[str, Any]:
    return tool_api.reject_causal_line_suggestion(args.sim_id, args.suggestion_id)


def _cmd_export_html(args: argparse.Namespace) -> Dict[str, Any]:
    result = tool_api.export_html(args.sim_id, branch=args.branch)
    if not result.get("ok"):
        return result
    if args.output:
        from pathlib import Path

        Path(args.output).write_text(result["data"]["html"], encoding="utf-8")
        # 写文件成功后，JSON 结果里就不重复带一份完整 html 正文了
        # （可能有几十 KB，命令行场景下已经落盘，没必要再打一遍到
        # stdout），只保留文件路径这个更有用的信息。
        return {
            "ok": True,
            "data": {"sim_id": args.sim_id, "branch": args.branch, "output_file": args.output},
        }
    return result


_SUBCOMMANDS = {
    "create-simulation": (_cmd_create_simulation, "创建一个新的模拟实例"),
    "advance": (_cmd_advance, "推进一个模拟实例一步"),
    "fast-forward": (_cmd_fast_forward, "连续推进多步，直到命中停止信号或达到步数上限"),
    "get": (_cmd_get, "查询一个实例的 manifest + 当前状态（可选历史）"),
    "list": (_cmd_list, "列出所有模拟实例的精简摘要"),
    "set-status": (_cmd_set_status, "设置实例状态：active | paused | ended"),
    "set-pilot": (_cmd_set_pilot, "设置手动挡/自动挡"),
    "update-settings": (_cmd_update_settings, "更新实例的 settings（合并式更新）"),
    "rename": (_cmd_rename, "重命名实例"),
    "delete": (_cmd_delete, "删除实例（不可逆，需要 --yes 确认）"),
    "apply-structural-change": (_cmd_apply_structural_change, "采纳某一步报告的结构性变化"),
    "accept-causal-line": (_cmd_accept_causal_line, "接受一条因果线建议"),
    "reject-causal-line": (_cmd_reject_causal_line, "拒绝一条因果线建议"),
    "export-html": (_cmd_export_html, "导出某条分支的完整时间线网页"),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="world_simulator.service_cli",
        description="世界模拟器——面向外部服务/主 agent 的命令行接口（统一输出 JSON）",
    )
    parser.add_argument("--pretty", action="store_true", help="JSON 输出加缩进，方便人读")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create-simulation", help=_SUBCOMMANDS["create-simulation"][1])
    p.add_argument("--template", default="life_sim", help="场景模板名，默认 life_sim")
    p.add_argument("--intent", required=True, help="一句话模拟意图描述")
    p.add_argument("--settings", default=None, help="可选，JSON 格式的 settings 覆盖项")

    p = sub.add_parser("advance", help=_SUBCOMMANDS["advance"][1])
    p.add_argument("--sim-id", required=True)
    p.add_argument("--choice", default=None, help="候选选项 id，缺省由引擎给出默认走向")
    p.add_argument("--custom-option-label", default=None)
    p.add_argument("--custom-option-description", default=None)
    p.add_argument("--decision-context", default=None, help="给这一步决策的额外背景说明")
    p.add_argument("--chosen-by", default="agent")
    p.add_argument("--allow-custom-options", action="store_true")

    p = sub.add_parser("fast-forward", help=_SUBCOMMANDS["fast-forward"][1])
    p.add_argument("--sim-id", required=True)
    p.add_argument("--max-steps", type=int, default=10)
    p.add_argument("--no-stop-on-major-decision", action="store_true")
    p.add_argument("--no-stop-on-options", action="store_true")
    p.add_argument("--decision-context", default=None)
    p.add_argument("--chosen-by", default="agent")
    p.add_argument("--allow-custom-options", action="store_true")

    p = sub.add_parser("get", help=_SUBCOMMANDS["get"][1])
    p.add_argument("--sim-id", required=True)
    p.add_argument("--include-history", action="store_true")
    p.add_argument("--history-limit", type=int, default=20)

    sub.add_parser("list", help=_SUBCOMMANDS["list"][1])

    p = sub.add_parser("set-status", help=_SUBCOMMANDS["set-status"][1])
    p.add_argument("--sim-id", required=True)
    p.add_argument("--status", required=True, choices=["active", "paused", "ended"])

    p = sub.add_parser("set-pilot", help=_SUBCOMMANDS["set-pilot"][1])
    p.add_argument("--sim-id", required=True)
    p.add_argument("--pilot-mode", required=True, choices=["manual", "autopilot"])
    p.add_argument("--autopilot", default=None, help="可选，JSON 格式的自动挡配置")

    p = sub.add_parser("update-settings", help=_SUBCOMMANDS["update-settings"][1])
    p.add_argument("--sim-id", required=True)
    p.add_argument("--updates", required=True, help="JSON 格式的 settings 更新项（合并式更新）")

    p = sub.add_parser("rename", help=_SUBCOMMANDS["rename"][1])
    p.add_argument("--sim-id", required=True)
    p.add_argument("--title", required=True)

    p = sub.add_parser("delete", help=_SUBCOMMANDS["delete"][1])
    p.add_argument("--sim-id", required=True)
    p.add_argument("--yes", action="store_true", help="确认执行这次不可逆删除")

    p = sub.add_parser("apply-structural-change", help=_SUBCOMMANDS["apply-structural-change"][1])
    p.add_argument("--sim-id", required=True)
    p.add_argument("--step", type=int, required=True)
    p.add_argument("--branch", default=None)

    p = sub.add_parser("accept-causal-line", help=_SUBCOMMANDS["accept-causal-line"][1])
    p.add_argument("--sim-id", required=True)
    p.add_argument("--suggestion-id", required=True)

    p = sub.add_parser("reject-causal-line", help=_SUBCOMMANDS["reject-causal-line"][1])
    p.add_argument("--sim-id", required=True)
    p.add_argument("--suggestion-id", required=True)

    p = sub.add_parser("export-html", help=_SUBCOMMANDS["export-html"][1])
    p.add_argument("--sim-id", required=True)
    p.add_argument("--branch", default="main")
    p.add_argument("--output", default=None, help="写入的文件路径；不传则把 html 正文放进 JSON 输出")

    return parser


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler, _ = _SUBCOMMANDS[args.command]
    result = handler(args)
    return _print_result(result, pretty=args.pretty)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
