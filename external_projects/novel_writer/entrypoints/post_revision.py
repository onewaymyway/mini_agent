#!/usr/bin/env python
"""entrypoints/post_revision.py — 完稿后修改流程的命令行入口（独立于写作流水线）。

用法（--novel 为小说工作目录）：
    python entrypoints/post_revision.py --novel <dir> baseline                 # P0 创建 v001（需终审通过）
    python entrypoints/post_revision.py --novel <dir> status
    python entrypoints/post_revision.py --novel <dir> settings [--set k=v ...]  # 查看/修改上限
    python entrypoints/post_revision.py --novel <dir> change-add --json f.json  # P1 新建变更
    python entrypoints/post_revision.py --novel <dir> preview --base v001 --changes chg_xxx [--review]
    python entrypoints/post_revision.py --novel <dir> exclude --preview prv_xxx --chapter 3 [--include]
    python entrypoints/post_revision.py --novel <dir> confirm --preview prv_xxx
    python entrypoints/post_revision.py --novel <dir> run --preview prv_xxx     # P2 执行（会调用 LLM）
    python entrypoints/post_revision.py --novel <dir> resume --run run_xxx
    python entrypoints/post_revision.py --novel <dir> triage --base v001 --chapter 12 --feedback "…"  # P3
    python entrypoints/post_revision.py --novel <dir> accept --triage tri_xxx [--mode retcon|from_chapter --from N]
    python entrypoints/post_revision.py --novel <dir> diff --a v001 --b v002    # P4
    python entrypoints/post_revision.py --novel <dir> export --version v002     # P4
    python entrypoints/post_revision.py --novel <dir> discard --version v003    # P5 撤销
    python entrypoints/post_revision.py --novel <dir> verify --version v001
    python entrypoints/post_revision.py --novel <dir> review-estimate [--version v00N]   # R0 评估成本估算（不调用 LLM）
    python entrypoints/post_revision.py --novel <dir> review-cards [--version v00N] [--chapters 1 2] [--force]  # R0 生成事实卡（调用 LLM）
    python entrypoints/post_revision.py --novel <dir> review-checks [--version v00N]     # R1 确定性检查，更新问题池与报告（不调用 LLM）
    python entrypoints/post_revision.py --novel <dir> review-findings [--version v00N] [--set f_xxx --status rejected]  # R1 查看/标记问题
    python entrypoints/post_revision.py --novel <dir> review-llm [--version v00N]   # R2 LLM 复核：候选仲裁、衔接审阅、全书维度审阅（调用 LLM，可续跑）
    python entrypoints/post_revision.py --novel <dir> review-options --finding f_xxx [--version v00N]   # R3 查看某问题可选的处理方式
    python entrypoints/post_revision.py --novel <dir> adopt --finding f_xxx [--option 0|1] [--instruction "…"] [--chapters 2 3] [--allow-unverified]  # R3 采纳为变更
    python entrypoints/post_revision.py --novel <dir> adopt-batch --findings f_a f_b [--choose f_x=1]   # R3 批量采纳（跳过矛盾未选方向、证据未核实）
    python entrypoints/post_revision.py --novel <dir> review-increment   # R3 增量复评（基于 head，对照父版本；调用 LLM）
    python entrypoints/post_revision.py --novel <dir> outline-candidate [--version v00N]   # R4 反推实际大纲，生成候选（调用 LLM，不进入版本）
    python entrypoints/post_revision.py --novel <dir> outline-list                         # R4 列出候选
    python entrypoints/post_revision.py --novel <dir> outline-diff --id ol_xxx             # R4 候选与当前蓝图的差异
    python entrypoints/post_revision.py --novel <dir> outline-adopt --id ol_xxx [--chapters 1 3] [--set 3:function=...] [--no-structure]  # R4 采纳为仅大纲的新版本
    python entrypoints/post_revision.py --novel <dir> outline-discard --id ol_xxx          # R4 放弃候选
    python entrypoints/post_revision.py --novel <dir> outline-check [--version v00N] [--llm]   # R5 以大纲为准的偏离检查（--llm 调用模型；否则只做确定性比对）
    python entrypoints/post_revision.py --novel <dir> review-export [--version v00N]        # R6 导出评估报告（Markdown，写入 _revision/review/rev_<版本>/export.md）
    python entrypoints/post_revision.py --novel <dir> stop --run run_xxx                   # 请求停止改写运行（当前章结束后生效）
"""

from __future__ import annotations

import argparse
import json
import sys

import _common  # noqa: F401

from novel_writer.revision_flow import changes as CH
from novel_writer.revision_flow import config as CFG
from novel_writer.revision_flow import derived, executor, feedback, outline_check, outline_regen, review_adopt, review_cards, review_checks, review_llm, review_report
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
    sp.add_argument("--review", action="store_true", help="做 LLM 语义判定（会调用 LLM）")
    sp = sub.add_parser("exclude")
    sp.add_argument("--preview", required=True)
    sp.add_argument("--chapter", type=int, required=True)
    sp.add_argument("--include", action="store_true", help="加回被排除的章节")
    sp = sub.add_parser("confirm")
    sp.add_argument("--preview", required=True)
    sp = sub.add_parser("run")
    sp.add_argument("--preview", required=True)
    sp = sub.add_parser("resume")
    sp.add_argument("--run", required=True)
    sp = sub.add_parser("stop")
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
    sp = sub.add_parser("review-estimate")
    sp.add_argument("--version", default=None)
    sp = sub.add_parser("review-cards")
    sp.add_argument("--version", default=None)
    sp.add_argument("--chapters", nargs="+", type=int, default=None)
    sp.add_argument("--force", action="store_true", help="忽略缓存重新生成")
    sp = sub.add_parser("review-checks")
    sp.add_argument("--version", default=None)
    sp = sub.add_parser("review-llm")
    sp.add_argument("--version", default=None)
    sp = sub.add_parser("review-options")
    sp.add_argument("--finding", required=True)
    sp.add_argument("--version", default=None)
    sp = sub.add_parser("adopt")
    sp.add_argument("--finding", required=True)
    sp.add_argument("--version", default=None)
    sp.add_argument("--option", type=int, default=None, help="矛盾类必填：0 或 1")
    sp.add_argument("--instruction", default=None)
    sp.add_argument("--chapters", nargs="+", type=int, default=None)
    sp.add_argument("--allow-unverified", action="store_true")
    sp = sub.add_parser("adopt-batch")
    sp.add_argument("--findings", nargs="+", required=True)
    sp.add_argument("--version", default=None)
    sp.add_argument("--choose", action="append", default=[], help="f_xxx=序号，矛盾类选定方向")
    sp = sub.add_parser("review-increment")
    sp.add_argument("--version", default=None)
    sp = sub.add_parser("outline-candidate")
    sp.add_argument("--version", default=None)
    sub.add_parser("outline-list")
    sp = sub.add_parser("outline-diff")
    sp.add_argument("--id", required=True)
    sp = sub.add_parser("outline-adopt")
    sp.add_argument("--id", required=True)
    sp.add_argument("--chapters", nargs="+", type=int, default=None)
    sp.add_argument("--set", action="append", default=[], help="章号:字段=值（如 3:function=新功能）")
    sp.add_argument("--no-structure", action="store_true", help="不采用候选的幕划分")
    sp = sub.add_parser("outline-check")
    sp.add_argument("--version", default=None)
    sp.add_argument("--llm", action="store_true", help="调用模型做 LLM 偏离判断")
    sp = sub.add_parser("review-export")
    sp.add_argument("--version", default=None)
    sp = sub.add_parser("outline-discard")
    sp.add_argument("--id", required=True)
    sp = sub.add_parser("review-findings")
    sp.add_argument("--version", default=None)
    sp.add_argument("--set", dest="fid", default=None, help="要标记的问题编号")
    sp.add_argument("--status", default=None, choices=review_checks.STATUSES)
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
            inv = _invoker() if args.review else None
            return _out(CH.build_preview(ws, args.base, args.changes, invoker=inv))
        if args.cmd == "exclude":
            return _out(CH.set_excluded(ws, args.preview, args.chapter, not args.include))
        if args.cmd == "confirm":
            return _out(CH.confirm_preview(ws, args.preview))
        if args.cmd == "run":
            return _out(executor.start(ws, args.preview, _invoker()))
        if args.cmd == "stop":
            executor.request_stop(ws, args.run)
            return _out({"stop_requested": args.run})
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
        if args.cmd == "review-estimate":
            return _out(review_cards.estimate(ws, args.version) | {"llm_review": review_llm.estimate_llm(ws, args.version)})
        if args.cmd == "review-cards":
            return _out(review_cards.generate_cards(ws, args.version, _invoker(), progress=print,
                                                    chapters=args.chapters, force=args.force))
        if args.cmd == "review-checks":
            res = review_checks.run_checks(ws, args.version)
            return _out({k: res[k] for k in ("review_id", "version", "counts", "missing")}
                        | {"report": f"_revision/review/{res['review_id']}/report.md"})
        if args.cmd == "review-llm":
            res = review_llm.run_review(ws, args.version, _invoker(), progress=print)
            return _out({k: res[k] for k in ("review_id", "version", "calls", "paused", "failed", "counts", "missing")}
                        | {"report": f"_revision/review/{res['review_id']}/report.md"})
        if args.cmd == "review-options":
            vid = review_cards.resolve_version(ws, args.version)
            f = next((x for x in review_checks.load_findings(ws, vid)["findings"] if x["id"] == args.finding), None)
            if f is None:
                raise FlowError(f"问题不存在：{args.finding}")
            return _out(review_adopt.options(f))
        if args.cmd == "adopt":
            return _out(review_adopt.adopt(ws, args.version, args.finding, option_index=args.option,
                                           instruction=args.instruction, target_chapters=args.chapters,
                                           allow_unverified=args.allow_unverified))
        if args.cmd == "adopt-batch":
            choices = {}
            for item in args.choose:
                k, _, v = item.partition("=")
                choices[k.strip()] = int(v)
            return _out(review_adopt.adopt_batch(ws, args.version, args.findings, choices))
        if args.cmd == "review-increment":
            return _out(review_adopt.increment(ws, _invoker(), progress=print))
        if args.cmd == "outline-candidate":
            return _out(outline_regen.generate_candidate(ws, args.version, _invoker(), progress=print))
        if args.cmd == "outline-list":
            return _out(outline_regen.list_candidates(ws))
        if args.cmd == "outline-diff":
            cand = outline_regen.load_candidate(ws, args.id)
            base = outline_regen.current_blueprint(ws, cand["base_version"])
            return _out(outline_regen.diff_blueprints(base, cand["candidate"]))
        if args.cmd == "outline-adopt":
            edits: dict = {}
            for item in args.set:
                head_, _, value = item.partition("=")
                num, _, field = head_.partition(":")
                edits.setdefault(int(num), {})[field.strip()] = json.loads(value) if field.strip() == "tension_level" \
                    or field.strip() == "word_budget" else value
            return _out(outline_regen.adopt(ws, args.id, chapters=args.chapters, edits=edits,
                                            take_structure=not args.no_structure))
        if args.cmd == "outline-check":
            res = outline_check.run_check(ws, args.version, _invoker() if args.llm else None, progress=print)
            return _out({k: res[k] for k in ("version", "counts", "calls", "failed", "llm")})
        if args.cmd == "review-export":
            res = review_report.export(ws, args.version)
            return _out({k: res[k] for k in ("version", "path", "findings")})
        if args.cmd == "outline-discard":
            return _out(outline_regen.discard_candidate(ws, args.id))
        if args.cmd == "review-findings":
            vid = review_cards.resolve_version(ws, args.version)
            if args.fid:
                if not args.status:
                    raise FlowError("请用 --status 指定新状态")
                return _out(review_checks.set_status(ws, vid, args.fid, args.status))
            return _out(review_checks.load_findings(ws, vid))
    except FlowError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
