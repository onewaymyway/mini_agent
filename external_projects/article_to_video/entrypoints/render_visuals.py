"""entrypoints/render_visuals.py — 渲染章的画面图层（P4）。

用法：python entrypoints/render_visuals.py --id <项目目录名> [--part P01 | 全部]
      [--provider auto|agnes|stub|none]
前提：分镜已确认（C2）且时间轴已生成（P3）。
provider：auto 使用项目设置中的生图 provider；agnes 需要环境变量 AGNES_API_KEYS 或 AGNES_API_KEY；
          none 关闭生图，所有插图按卡片绘制。未配置 key 时同样降级，不会中断渲染。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _common import force_utf8_stdio  # noqa: E402

from article_to_video import cancel  # noqa: E402
from article_to_video.paths import projects_root  # noqa: E402
from article_to_video.planning.errors import PlanError  # noqa: E402
from article_to_video.planning.runner import load_spine  # noqa: E402
from article_to_video.providers.image import make_image_provider  # noqa: E402
from article_to_video.render.compose_part import render_part  # noqa: E402
from article_to_video.visual.config import load_visual_config  # noqa: E402


def main(argv=None) -> int:
    force_utf8_stdio()
    p = argparse.ArgumentParser(description="渲染画面图层（按章）")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--id", help="项目目录名（位于项目集合目录下）")
    g.add_argument("--project", help="项目目录路径")
    p.add_argument("--part", default="all", help="章 ID（如 P01），默认全部")
    p.add_argument("--provider", default="auto", choices=["auto", "agnes", "stub", "none"])
    args = p.parse_args(argv)

    pdir = Path(args.project) if args.project else projects_root() / args.id
    cancel.bind(cancel.begin(pdir))
    from article_to_video.providers.image import log_image_diagnosis, image_diagnosis
    cfg = load_visual_config(pdir)
    if args.provider == "none" or not cfg["images"]["enabled"]:
        log_image_diagnosis({"provider": "none", "ok": False, "keys": 0, "source": "", "searched": []},
                            enabled=False)
        provider = None
    else:
        name = cfg["images"]["provider"] if args.provider == "auto" else args.provider
        diag = image_diagnosis(name)
        log_image_diagnosis(diag, enabled=True)
        provider = make_image_provider(name) if diag["ok"] else None
    try:
        spine = load_spine(pdir)
        if spine is None:
            raise PlanError("还没有大纲")
        ids = [x["id"] for x in spine["parts"]] if args.part == "all" else [args.part]
        out = {}
        for pid in ids:
            m = render_part(pdir, pid, provider=provider)
            out[pid] = m["counts"] | {"warnings": m["warnings"][:5]}
    except (PlanError, cancel.Cancelled) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, "parts": out, "provider": getattr(provider, "name", None)},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
