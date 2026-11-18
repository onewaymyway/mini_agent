"""entrypoints/run_pipeline.py — 一键流水线（P7）：可重入，按挡位在检查点停下或自动推进。

用法：
  python entrypoints/run_pipeline.py --id <项目目录名> [--until export] [--gear G0|G1|G2]
        [--max-images N] [--max-minutes M] [--approve] [--merge-all] [--offline]
挡位：G0 四个检查点都需人工；G1 只有 C0 与终审人工；G2 全自动，必须设置 --max-images 或 --max-minutes。
--offline：语音用静音、生图用占位图，大纲改用离线规则（只用于冒烟，不产生费用）；写稿仍需要文本模型。
--outline-engine：大纲生成方式，llm（默认，需要文本模型）或 rules（离线规则）。
输出 JSON（同时写入项目的 pipeline.json）。退出码：done=0，其它=1。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _common import force_utf8_stdio  # noqa: E402

from article_to_video import cancel  # noqa: E402
from article_to_video.paths import projects_root  # noqa: E402
from article_to_video.pipeline.gates import PipelineError, STAGES  # noqa: E402
from article_to_video.pipeline.run import default_providers, run_pipeline  # noqa: E402


def main(argv=None) -> int:
    force_utf8_stdio()
    p = argparse.ArgumentParser(description="一键流水线（可重入，按挡位门控）")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--id", help="项目目录名（位于项目集合目录下）")
    g.add_argument("--project", help="项目目录路径")
    p.add_argument("--until", default="export", choices=list(STAGES))
    p.add_argument("--gear", default="G0", choices=["G0", "G1", "G2"])
    p.add_argument("--max-images", type=int, default=None, help="G2 预算：最多生图次数")
    p.add_argument("--max-minutes", type=float, default=None, help="G2 预算：最长总耗时（分钟）")
    p.add_argument("--approve", action="store_true", help="确认终审，允许导出")
    p.add_argument("--merge-all", action="store_true", help="多期时额外导出全集合并版")
    p.add_argument("--offline", action="store_true", help="语音静音、生图占位，大纲改用离线规则（冒烟，不产生费用）")
    p.add_argument("--outline-engine", default="llm", choices=["llm", "rules"], help="大纲生成方式（默认 llm）")
    args = p.parse_args(argv)

    pdir = Path(args.project) if args.project else projects_root() / args.id
    cancel.bind(cancel.begin(pdir))
    try:
        prov = default_providers(pdir, offline=args.offline)
        outline_engine = args.outline_engine if not args.offline else ("rules" if args.outline_engine == "llm" else args.outline_engine)
        status = run_pipeline(pdir, until=args.until, gear=args.gear, outline_engine=outline_engine, max_images=args.max_images,
                              max_minutes=args.max_minutes, approve=args.approve, llm=prov["llm"],
                              tts=prov["tts"], image_provider=prov["image"], merge_all=args.merge_all)
    except PipelineError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0 if status["state"] == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
