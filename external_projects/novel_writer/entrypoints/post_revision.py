#!/usr/bin/env python
"""entrypoints/post_revision.py — 完稿后修改流程的命令行入口（独立于写作流水线）。

用法（--novel 为小说工作目录）：
    python entrypoints/post_revision.py --novel <dir> baseline                 # P0 创建 v001（需终审通过）
    python entrypoints/post_revision.py --novel <dir> status
    python entrypoints/post_revision.py --novel <dir> settings [--set k=v ...]  # 查看/修改上限
    python entrypoints/post_revision.py --novel <dir> change-add --json f.json  # P1 新建变更
    python entrypoints/post_revision.py --novel <dir> preview --base v001 --changes chg_xxx [...]
    python entrypoints/post_revision.py --novel <dir> confirm --preview prv_xxx
    python entrypoints/post_revision.py --novel <dir> run --preview prv_xxx     # P2 执行（会调用 LLM）
    python entrypoints/post_revision.py --novel <dir> resume --run run_xxx
    python entrypoints/post_revision.py --novel <dir> triage --base v001 --chapter 12 --feedback "…"  # P3
    python entrypoints/post_revision.py --novel <dir> accept --triage tri_xxx [--mode retcon|from_chapter --from N]
    python entrypoints/post_revision.py --novel <dir> diff --a v001 --b v002    # P4
    python entrypoints/post_revision.py --novel <dir> export --version v002     # P4
    python entrypoints/post_revision.py --novel <dir> discard --version v003    # P5 撤销
    python entrypoints/post_revision.py --novel <dir> verify --version v001
"""

from __future__ import annotations

import argparse
import json
import sys

import _common  # noqa: F401

from novel_writer.revision_flow import changes as CH
from novel_writer.revision_flow import config as CFG
from novel_writer.revision_flow import derived, executor, feedback
from novel_writer.revision_flow import versions as V
from novel_writer.revision_flow.common import FlowError
from novel_writer.workspace import Workspace


def _invoker():
    from novel_writer.runner import FrameworkInvoker
    return FrameworkInvoker()


def _out(data) -> int:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="完稿后修改流程")
    p.add_argument("--novel", required=True, help="小说工作目录")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("baseline")
    sub.add_parser("status")
    sp = sub.add_parser("settings")
    sp.add_argument("--set", action="append", default=[], help="key=value")
    sp = sub.add_parser("change-add")
    sp.add_argument("--json", required=True)
    sp = sub.add_parser("preview")
    sp.add_argument("--base", required=True)
    sp.add_argument("--changes", nargs="+", required=True)
    sp = sub.add_parser("confirm")
    sp.add_argument("--preview", required=True)
    sp = sub.add_parser("run")
    sp.add_argument("--preview", required=True)
    sp = sub.add_parser("resume")
    sp.add_argument("--run", required=True)
    sp = sub.add_parser("triage")
    sp.add_argument("--base", required=True)
    sp.add_argument("--chapter", type=int, required=True)
    sp.add_argument("--feedback", required=True)
    sp.add_argument("--selection", default="")
    sp = sub.add_parser("accept")
    sp.add_argument("--triage", required=True)
    sp.add_argument("--mode", choices=CH.MODES)
    sp.add_argument("--from", dest="effective_from", type=int)
    sp = sub.add_parser("diff")
    sp.add_argument("--a", required=True)
    sp.add_argument("--b", required=True)
    sp = sub.add_parser("export")
    sp.add_argument("--version", required=True)
    sp = sub.add_parser("discard")
    sp.add_argument("--version", required=True)
    sp = sub.add_parser("verify")
    sp.add_argument("--version", required=True)
    args = p.parse_args(argv)

    ws = Workspace(args.novel)
    try:
        if args.cmd == "baseline":
            return _out(V.init_baseline(ws))
        if args.cmd == "status":
            return _out({"head": V.load_index(ws)["head"], "versions": V.list_versions(ws),
                         "changes": CH.list_changes(ws), "runs": executor.list_runs(ws)})
        if args.cmd == "settings":
            if args.set:
                patch = {}
                for item in args.set:
                    k, _, v = item.partition("=")
                    try:
                        patch[k.strip()] = json.loads(v)
                    except ValueError:
                        patch[k.strip()] = v
                return _out(CFG.update(ws, patch))
            return _out(CFG.load(ws))
        if args.cmd == "change-add":
            with open(args.json, encoding="utf-8") as fh:
                return _out(CH.save_change(ws, json.load(fh)))
        if args.cmd == "preview":
            return _out(CH.build_preview(ws, args.base, args.changes))
        if args.cmd == "confirm":
            return _out(CH.confirm_preview(ws, args.preview))
        if args.cmd == "run":
            return _out(executor.start(ws, args.preview, _invoker()))
        if args.cmd == "resume":
            return _out(executor.resume(ws, args.run, _invoker()))
        if args.cmd == "triage":
            return _out(feedback.triage(ws, args.base, args.chapter, args.feedback, _invoker(), selection=args.selection))
        if args.cmd == "accept":
            return _out(feedback.accept(ws, args.triage, mode=args.mode, effective_from=args.effective_from))
        if args.cmd == "diff":
            return _out(derived.diff_versions(ws, args.a, args.b))
        if args.cmd == "export":
            return _out(derived.export_version(ws, args.version))
        if args.cmd == "discard":
            V.discard(ws, args.version)
            return _out({"discarded": args.version})
        if args.cmd == "verify":
            problems = V.verify(ws, args.version)
            return _out({"ok": not problems, "problems": problems})
    except FlowError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
