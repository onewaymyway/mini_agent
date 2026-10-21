#!/usr/bin/env python
"""entrypoints/check_step.py — 让 agent 在**同一次会话内**自检本步骤的产出。

用法：python entrypoints/check_step.py --workspace <工作目录> --step <步骤 id>
输出 OK（退出码 0）表示产出通过全部内容校验；否则逐条列出问题（退出码 1），agent 据此就地修改后再运行。

与步骤结束后的正式校验使用同一套校验函数（validators.content_problems），并先做同样的确定性修复
（如角色文件缺文件头 → 能确定的直接改正）。区别：自检**不会**把文件还原成备份——agent 还在工作，
应该让它自己改好，而不是被还原掉。这样格式问题在同一会话里（上下文还在）就能解决，
不必等整步结束后再开一次新会话从头重做。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="自检某个步骤的产出")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--step", required=True)
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    from novel_writer import pipeline as P
    from novel_writer import validators as V
    from novel_writer.workspace import Workspace

    ws = Workspace(args.workspace)
    if not ws.is_initialized():
        print(f"不是已初始化的工作目录：{ws.root}", file=sys.stderr)
        return 2
    step = next((s for s in P.build_plan(ws) if s.id == args.step), None)
    if step is None:
        print(f"找不到步骤 {args.step}", file=sys.stderr)
        return 2
    V.set_restore_enabled(False)
    problems = V.content_problems(ws, step) + [f"缺少产出 {m}" for m in V.missing_outputs(ws, step.outputs)]
    if not problems:
        print("OK")
        return 0
    print("发现以下问题，请逐条修改后再运行本命令，直到输出 OK：")
    for p in problems:
        print(f"- {p}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
