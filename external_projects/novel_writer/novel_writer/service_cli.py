"""novel_writer/service_cli.py — 命令行壳：只调用 tool_api，输出 JSON（方案第 2 节的三层结构）。

用法（在项目根目录运行）：
    python -m novel_writer.service_cli status   <工作目录>
    python -m novel_writer.service_cli run      <工作目录> [--mode single|until_gate] [--step ID] [--no-spawn]
    python -m novel_writer.service_cli pause    <工作目录>
    python -m novel_writer.service_cli stop     <工作目录>
    python -m novel_writer.service_cli approve  <工作目录> <步骤ID>
    python -m novel_writer.service_cli rerun    <工作目录> <步骤ID>
    python -m novel_writer.service_cli answer   <工作目录> <步骤ID> --json '{"q1": "..."}'
    python -m novel_writer.service_cli note     <工作目录> <步骤ID> --text "..."
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from novel_writer import tool_api


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="novel_writer", description="novel_writer 命令行")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add(name: str, *, step: bool = False) -> argparse.ArgumentParser:
        p = sub.add_parser(name)
        p.add_argument("workspace")
        if step:
            p.add_argument("step_id")
        return p

    add("status")
    run = add("run")
    run.add_argument("--mode", default="until_gate", choices=["until_gate", "single"])
    run.add_argument("--step", dest="step_id", default=None)
    run.add_argument("--no-spawn", action="store_true", help="只写控制指令，不拉起 worker（调试用）")
    add("pause")
    add("stop")
    add("approve", step=True)
    add("rerun", step=True)
    ans = add("answer", step=True)
    ans.add_argument("--json", dest="answers", required=True, help="答复 JSON 对象")
    note = add("note", step=True)
    note.add_argument("--text", default="")
    return parser


def dispatch(args: argparse.Namespace) -> dict:
    if args.cmd == "status":
        return tool_api.pipeline_status(args.workspace)
    if args.cmd == "run":
        return tool_api.run_novel(args.workspace, mode=args.mode, step_id=args.step_id, spawn=not args.no_spawn)
    if args.cmd == "pause":
        return tool_api.pause_novel(args.workspace)
    if args.cmd == "stop":
        return tool_api.stop_novel(args.workspace)
    if args.cmd == "approve":
        return tool_api.approve_step(args.workspace, args.step_id)
    if args.cmd == "rerun":
        return tool_api.rerun_step(args.workspace, args.step_id)
    if args.cmd == "answer":
        try:
            answers = json.loads(args.answers)
        except ValueError as exc:
            return {"ok": False, "error": {"type": "validation_error", "message": f"--json 不是合法 JSON：{exc}"}}
        return tool_api.answer_step(args.workspace, args.step_id, answers)
    if args.cmd == "note":
        return tool_api.write_author_note(args.workspace, args.step_id, args.text)
    raise SystemExit(f"未知命令 {args.cmd}")


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    result = dispatch(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
