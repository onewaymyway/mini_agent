"""entrypoints/synth_audio.py — 配音（P3）：逐句合成并生成时间轴。

用法：python entrypoints/synth_audio.py --id <项目目录名> [--part P01 | 全部] [--force]
      加 --stub 只生成静音做流程验证（结果标记为 stub，不能作为正式成片）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _common import force_utf8_stdio  # noqa: E402

from article_to_video import cancel  # noqa: E402
from article_to_video.audio.synth import project_tts_config, synthesize_all, synthesize_part  # noqa: E402
from article_to_video.paths import projects_root  # noqa: E402
from article_to_video.planning.errors import PlanError  # noqa: E402
from article_to_video.providers.tts import SilenceTTS, build_chain  # noqa: E402


def main(argv=None) -> int:
    force_utf8_stdio()
    p = argparse.ArgumentParser(description="配音：逐句合成并生成时间轴")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--id")
    g.add_argument("--project")
    p.add_argument("--part", default="all")
    p.add_argument("--force", action="store_true")
    p.add_argument("--stub", action="store_true", help="仅流程验证：生成静音")
    args = p.parse_args(argv)

    pdir = Path(args.project) if args.project else projects_root() / args.id
    cancel.bind(cancel.begin(pdir))
    cfg = project_tts_config(pdir)
    tts = SilenceTTS() if args.stub else build_chain(cfg["engine_chain"], edge_proxy=cfg["edge_proxy"])
    try:
        if args.part == "all":
            done = synthesize_all(pdir, tts, force=args.force)
        else:
            synthesize_part(pdir, args.part, tts, force=args.force)
            done = [args.part]
    except (PlanError, RuntimeError, cancel.Cancelled) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, "parts": done, "engine_chain": tts.name}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
