#!/usr/bin/env python
"""entrypoints/recover_state.py — 清除误判的「需重跑」标记，让流水线回到正确的位置继续。

用法（先不加参数，只看现状）：
    python entrypoints/recover_state.py --workspace <小说目录>
    python entrypoints/recover_state.py --workspace <小说目录> --accept-stale   # 作者确认这些产物无误
    python entrypoints/recover_state.py --workspace <小说目录> --accept-stale --apply

默认是**预览**（dry-run），不改任何东西。加 --apply 才会写盘。
每次 --apply 之前都会先把 pipeline_state.json、worker 状态、控制文件备份到
<小说目录>/studio/recovery_backup_<时间>/，可以手动还原。

做的事（按顺序）：
  1. 拒绝在 worker 仍存活时操作（先停止运行）。
  2. 清除残留的修改自动重跑标记（revision_autorun）。
  3. 清除心跳超时的残留运行状态。
  4. --accept-stale：把「上游产物已变化，需要重跑」的步骤以当前文件状态重新基线为「已通过」。
     只有你确认这些文件内容是对的时才加这个参数；它不会运行任何步骤，也不会改写文件内容。
  5. 打印「下一步」（与界面「运行下一步」按钮用的是同一个定义）。

注意：它不会还原已经被重跑覆盖的文件内容（例如被重新生成的蓝图）。如果那些文件内容不对，
请用备份还原，或用 write_history 查看是哪一步改写的（python -m novel_writer.service_cli … 或
界面「写入历史」）。
"""

from __future__ import annotations

import argparse
import sys

import _common  # noqa: F401

from novel_writer import recovery as REC
from novel_writer.workspace import Workspace


def _show(info: dict) -> None:
    print(f"修改自动重跑标记: {info['revision_autorun']} (章 {info['revision_autorun_chapter']})")
    print(f"worker: state={info['worker_state']} alive={info['worker_alive']}")
    print(f"需重跑步骤: {len(info['stale'])} 个")
    for row in info["stale"][:40]:
        print(f"  - {row['id']:32s} {row['updated_at']}  {row['note'][:60]}")
    if len(info["stale"]) > 40:
        print(f"  … 另有 {len(info['stale']) - 40} 个")
    print(f"下一步: {info['next_step']}  {info['next_title'] or ''}  → action={info['next_action']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="清除误判的需重跑标记，回到正确位置")
    parser.add_argument("--workspace", required=True, help="小说工作目录")
    parser.add_argument("--accept-stale", action="store_true",
                        help="作者确认：把「上游产物已变化」的需重跑步骤以当前文件重新基线为已通过")
    parser.add_argument("--apply", action="store_true", help="真正写盘（默认只预览）")
    args = parser.parse_args()

    ws = Workspace(args.workspace)
    print("── 现状 ──")
    _show(REC.preview(ws))
    if not args.apply:
        print("\n（预览模式，未修改任何内容。确认后加 --apply 执行。）")
        return 0
    try:
        res = REC.apply(ws, accept_stale=args.accept_stale)
    except REC.RecoveryError as exc:
        print(f"✖ {exc}")
        return 1
    print(f"\n已备份到 {res['backup']}")
    print("✔ 已清除修改自动重跑标记")
    if res["cleared_worker"]:
        print("✔ 已清除残留运行状态")
    if args.accept_stale:
        print(f"✔ 已重新基线 {len(res['rebaselined'])} 个需重跑步骤")
    print("\n── 结果 ──")
    _show(REC.preview(ws))
    return 0


if __name__ == "__main__":
    raise SystemExit(_common.run_entrypoint("recover_state", main))
