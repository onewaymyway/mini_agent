#!/usr/bin/env python
"""world_forge 命令行入口（daemon 与手工运行共用）。输出 JSON；失败退出码为 1。

用法示例：
  python entrypoints/cli.py health
  python entrypoints/cli.py list
  python entrypoints/cli.py init --concept "蒸汽与符文并存的海上帝国" [--title 名称]
  python entrypoints/cli.py approve --world <wid> --proposal <pid>
  python entrypoints/cli.py advance --world <wid> [--steps 1]
  python entrypoints/cli.py fill --world <wid> [--limit 5]
  python entrypoints/cli.py arc --world <wid> [--events a,b] [--from 0] [--to 30]
  python entrypoints/cli.py short --world <wid> [--arc <sid>] [--brief 文字] [--events a,b]
  python entrypoints/cli.py board --world <wid> --story <sid> [--pages 6|auto] [--reading ltr|rtl] [--color color|bw]
  python entrypoints/cli.py comic --world <wid> --story <sid> [--reading ltr|rtl] [--color color|bw] [--candidates 1-3]
  python entrypoints/cli.py limits --world <wid> [--budget 80] [--candidate-max 3]   # 查看或设置漫画上限
  python entrypoints/cli.py style --world <wid>
  python entrypoints/cli.py sheets --world <wid> --story <sid> [--overwrite]
  python entrypoints/cli.py scenes --world <wid> --story <sid> [--overwrite]
  python entrypoints/cli.py choose --world <wid> --story <sid> --page 1 --panel 1 --k 2
  python entrypoints/cli.py export --world <wid> --story <sid>
  python entrypoints/cli.py expand --world <wid> [--request "北方增加一个冰原部族"] [--count 3]
  python entrypoints/cli.py relations --world <wid> [--limit 5]  # 自动补全实体之间的关系
  python entrypoints/cli.py modify --world <wid> --request "把北港改为冬季封冻" [--targets id1,id2]
  python entrypoints/cli.py enrich --world <wid>                 # 补全缺口 + 自动扩展
  python entrypoints/cli.py overview --world <wid>               # 生成世界观背景介绍
  python entrypoints/cli.py delete --world <wid> --confirm <wid>   # 永久删除，不可恢复
"""

from __future__ import annotations

import argparse
import json
import sys

import _common  # noqa: F401
from _common import force_utf8_stdio

from world_forge import engine, proposals, repo, timeline
from world_forge.calendar import clock_minutes, format_time
from world_forge.paths import data_root, mini_agent_root
from world_forge.providers.factory import make_image_provider, make_text_llm


def _ids(text):
    return [x.strip() for x in text.split(",") if x.strip()] if text else None


def _stderr_log(wid, kind):
    from world_forge.runlog import RunLog, format_entry
    return RunLog(wid, kind, sink=lambda e: print(format_entry(e), file=sys.stderr, flush=True))


def _dump(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def main(argv=None) -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="架空世界工坊 CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("health")
    sub.add_parser("list")
    p = sub.add_parser("init"); p.add_argument("--concept", required=True)
    p.add_argument("--title", default=""); p.add_argument("--genre", default=""); p.add_argument("--tone", default="")
    p = sub.add_parser("approve"); p.add_argument("--world", required=True); p.add_argument("--proposal", required=True)
    p = sub.add_parser("reject"); p.add_argument("--world", required=True); p.add_argument("--proposal", required=True)
    p = sub.add_parser("advance"); p.add_argument("--world", required=True); p.add_argument("--steps", type=int, default=1)
    p.add_argument("--amount", type=int, default=None, help="本次推进的步长数值（默认用世界设置）")
    p.add_argument("--unit", choices=["minute", "hour", "day", "week", "month", "year"], default=None,
                   help="本次推进的时间单位（默认用世界设置）")
    p = sub.add_parser("fill"); p.add_argument("--world", required=True); p.add_argument("--limit", type=int, default=5)
    p = sub.add_parser("arc"); p.add_argument("--world", required=True); p.add_argument("--events", default="")
    p.add_argument("--from", dest="year_from", type=int); p.add_argument("--to", dest="year_to", type=int)
    p = sub.add_parser("short"); p.add_argument("--world", required=True); p.add_argument("--arc", default=None)
    p.add_argument("--brief", default=""); p.add_argument("--events", default=""); p.add_argument("--form", default="novel")
    p = sub.add_parser("board"); p.add_argument("--world", required=True); p.add_argument("--story", required=True)
    p.add_argument("--pages", default=None, help="页数（整数 1–20）或 auto（按故事长度，不限制），默认 6")
    p.add_argument("--reading", default=None, choices=["ltr", "rtl"], help="阅读方向，默认 ltr")
    p.add_argument("--color", default=None, choices=["color", "bw"], help="色彩，默认 color")
    p = sub.add_parser("comic"); p.add_argument("--world", required=True); p.add_argument("--story", required=True)
    p.add_argument("--reading", default=None, choices=["ltr", "rtl"])
    p.add_argument("--color", default=None, choices=["color", "bw"])
    p.add_argument("--candidates", type=int, default=None, choices=[1, 2, 3], help="每格候选数，默认 1")
    p = sub.add_parser("limits"); p.add_argument("--world", required=True)
    p.add_argument("--budget", type=int, default=None, help="单次操作的图像调用上限（1–500）")
    p.add_argument("--candidate-max", type=int, default=None, help="每格候选数上限（1–5）")
    p = sub.add_parser("style"); p.add_argument("--world", required=True)
    p = sub.add_parser("sheets"); p.add_argument("--world", required=True); p.add_argument("--story", required=True)
    p.add_argument("--overwrite", action="store_true")
    p = sub.add_parser("scenes"); p.add_argument("--world", required=True); p.add_argument("--story", required=True)
    p.add_argument("--overwrite", action="store_true")
    p = sub.add_parser("choose"); p.add_argument("--world", required=True); p.add_argument("--story", required=True)
    p.add_argument("--page", type=int, required=True); p.add_argument("--panel", type=int, required=True)
    p.add_argument("--k", type=int, required=True)
    p = sub.add_parser("export"); p.add_argument("--world", required=True); p.add_argument("--story", required=True)
    p = sub.add_parser("expand"); p.add_argument("--world", required=True)
    p.add_argument("--request", default="", help="扩展要求；留空则自动完善")
    p.add_argument("--count", type=int, default=3)
    p = sub.add_parser("relations"); p.add_argument("--world", required=True)
    p.add_argument("--limit", type=int, default=5)
    p = sub.add_parser("modify"); p.add_argument("--world", required=True)
    p.add_argument("--request", required=True); p.add_argument("--targets", default="", help="逗号分隔的对象 id，留空则自动判断")
    p = sub.add_parser("enrich"); p.add_argument("--world", required=True)
    p = sub.add_parser("overview"); p.add_argument("--world", required=True)
    p = sub.add_parser("delete"); p.add_argument("--world", required=True)
    p.add_argument("--confirm", required=True, help="必须与 --world 的值完全相同，用于防止误删")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "health":
            llm, llm_note = make_text_llm()
            _, img_note = make_image_provider()
            _dump({"ok": True, "data_dir": str(data_root()), "data_dir_exists": data_root().is_dir(),
                   "mini_agent_root": str(mini_agent_root()) if mini_agent_root() else None,
                   "text": llm_note, "image": img_note, "worlds": len(repo.list_world_ids())})
        elif args.cmd == "list":
            _dump([{"id": w, "title": repo.load_meta(w)["title"], "time": format_time(clock_minutes(repo.load_meta(w)))}
                   for w in repo.list_world_ids()])
        elif args.cmd == "init":
            llm, _ = make_text_llm()
            _dump(engine.create_world(llm, concept=args.concept, title=args.title, genre=args.genre, tone=args.tone))
        elif args.cmd == "approve":
            _dump(proposals.approve(args.world, args.proposal))
        elif args.cmd == "reject":
            _dump(proposals.reject(args.world, args.proposal))
        elif args.cmd == "advance":
            from world_forge.runlog import RunLog, format_entry
            llm, _ = make_text_llm()
            log = RunLog(args.world, "advance",
                         sink=lambda e: print(format_entry(e), file=sys.stderr, flush=True))
            res = engine.advance(llm, args.world, args.steps, log=log, amount=args.amount, unit=args.unit)
            _dump({k: v for k, v in res.items() if k != "log"})
        elif args.cmd == "fill":
            llm, _ = make_text_llm()
            _dump(engine.fill_gaps(llm, args.world, limit=args.limit))
        elif args.cmd == "arc":
            llm, _ = make_text_llm()
            _dump({"story": engine.make_arc(llm, args.world, event_ids=_ids(args.events),
                                            year_from=args.year_from, year_to=args.year_to)})
        elif args.cmd == "short":
            llm, _ = make_text_llm()
            _dump({"story": engine.write_short(llm, args.world, arc_sid=args.arc, brief=args.brief,
                                               event_ids=_ids(args.events), form=args.form)})
        elif args.cmd == "board":
            llm, _ = make_text_llm()
            _dump(engine.make_storyboard(llm, args.world, args.story, pages=args.pages,
                                         reading=args.reading, color=args.color))
        elif args.cmd == "limits":
            if args.budget is not None or args.candidate_max is not None:
                engine.set_comic_limits(args.world, image_budget=args.budget, candidate_max=args.candidate_max)
            _dump(engine.comic_limits(args.world))
        elif args.cmd == "style":
            llm, _ = make_text_llm()
            _dump({"style_guide": engine.make_style_guide(llm, args.world)})
        elif args.cmd == "sheets":
            prov, note = make_image_provider()
            _dump({**engine.make_character_sheets(prov, args.world, args.story, overwrite=args.overwrite), "note": note})
        elif args.cmd == "scenes":
            llm, _ = make_text_llm()
            prov, note = make_image_provider()
            _dump({**engine.make_scene_images(llm, prov, args.world, args.story, overwrite=args.overwrite), "note": note})
        elif args.cmd == "choose":
            _dump(engine.choose_candidate(args.world, args.story, args.page, args.panel, args.k))
        elif args.cmd == "comic":
            prov, note = make_image_provider()
            res = engine.render_comic(prov, args.world, args.story, reading=args.reading, color=args.color,
                                      candidates=args.candidates)
            _dump({**res, "note": note})
        elif args.cmd == "expand":
            llm, _ = make_text_llm()
            _dump(engine.expand_world(llm, args.world, request=args.request, count=args.count))
        elif args.cmd == "relations":
            llm, _ = make_text_llm()
            _dump(engine.complete_relations(llm, args.world, limit=args.limit))
        elif args.cmd == "modify":
            llm, _ = make_text_llm()
            _dump(engine.modify_world(llm, args.world, args.request, _ids(args.targets)))
        elif args.cmd == "enrich":
            llm, _ = make_text_llm()
            _dump(engine.enrich_world(llm, args.world))
        elif args.cmd == "overview":
            llm, _ = make_text_llm()
            _dump(engine.generate_overview(llm, args.world))
        elif args.cmd == "delete":
            if args.confirm != args.world:
                raise ValueError("确认内容与世界 id 不一致，未删除")
            _dump(engine.delete_world(args.world))
        elif args.cmd == "export":
            _dump({"path": str(engine.export_bundle(args.world, args.story))})
        return 0
    except (engine.EngineError, engine.generate.GenerationError, repo.WorldNotFound,
            timeline.TimelineError, KeyError, ValueError) as exc:
        _dump({"ok": False, "error": str(exc)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
