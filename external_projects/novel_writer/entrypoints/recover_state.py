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
import datetime as _dt
import json
import shutil
import sys
from pathlib import Path

import _common  # noqa: F401

from novel_writer import pipeline as P
from novel_writer import state as st
from novel_writer import tool_api
from novel_writer.workspace import Workspace

_UPSTREAM = st._UPSTREAM_NOTE


def _backup(ws: Workspace) -> Path:
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = ws.studio / f"recovery_backup_{stamp}"
    dest.mkdir(parents=True, exist_ok=True)
    for key in ("studio_state", "studio_worker", "studio_control"):
        try:
            src = ws.path(key)
        except KeyError:
            continue
        if src.exists():
            shutil.copy2(src, dest / src.name)
    return dest


def _stale_rows(ws: Workspace):
    rows = []
    for sid, entry in st.load_state(ws)["steps"].items():
        if entry.get("status") == st.ST_STALE:
            rows.append((sid, str(entry.get("note") or ""), entry.get("updated_at") or ""))
    return rows


def _report(ws: Workspace) -> None:
    ctl = st.read_control(ws)
    print(f"修改自动重跑标记: {ctl.get('revision_autorun')} (章 {ctl.get('revision_autorun_chapter')})")
    worker = st.read_worker(ws)
    print(f"worker: state={worker.get('state')} alive={st.worker_alive(ws)}")
    rows = _stale_rows(ws)
    print(f"需重跑步骤: {len(rows)} 个")
    for sid, note, ts in rows[:40]:
        print(f"  - {sid:32s} {ts}  {note[:60]}")
    if len(rows) > 40:
        print(f"  … 另有 {len(rows) - 40} 个")
    nxt = tool_api.next_step(str(ws.root))
    data = nxt.get("data") or {}
    print(f"下一步: {data.get('step')}  {data.get('title') or ''}  → action={data.get('action')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="清除误判的需重跑标记，回到正确位置")
    parser.add_argument("--workspace", required=True, help="小说工作目录")
    parser.add_argument("--accept-stale", action="store_true",
                        help="作者确认：把「上游产物已变化」的需重跑步骤以当前文件重新基线为已通过")
    parser.add_argument("--apply", action="store_true", help="真正写盘（默认只预览）")
    args = parser.parse_args()

    ws = Workspace(args.workspace)
    if st.worker_alive(ws):
        print("✖ worker 仍在运行（心跳正常）。请先在界面点「停止」，等它结束后再运行本脚本。")
        return 1

    print("── 现状 ──")
    _report(ws)

    if not args.apply:
        print("\n（预览模式，未修改任何内容。确认后加 --apply 执行。）")
        return 0

    backup = _backup(ws)
    print(f"\n已备份到 {backup}")

    st.write_control(ws, revision_autorun=False, revision_autorun_chapter=None)
    print("✔ 已清除修改自动重跑标记")

    info = st.read_worker(ws)
    if info.get("state") == "running":
        res = tool_api.clear_stale_worker(str(ws.root))
        print("✔ 已清除残留运行状态" if res.get("ok") else f"✖ 清除失败: {res.get('error')}")

    if args.accept_stale:
        res = tool_api.rebaseline_stale(str(ws.root))
        done = (res.get("data") or {}).get("rebaselined") or []
        print(f"✔ 已重新基线 {len(done)} 个需重跑步骤（产物以当前文件为准）")
        remaining = [sid for sid, note, _ in _stale_rows(ws) if not note.startswith(_UPSTREAM)]
        if remaining:
            print(f"  仍有 {len(remaining)} 个需重跑步骤不是「上游产物已变化」类型，需要在界面逐个处理")

    print("\n── 结果 ──")
    _report(ws)
    return 0


if __name__ == "__main__":
    raise SystemExit(_common.run_entrypoint("recover_state", main))
