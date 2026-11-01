#!/usr/bin/env python
"""真实 API 冒烟（仅在本机、配置真实 key 后运行）。

目的：在投入整章生成前，确认图像与视频接口的请求结构、鉴权、轮询与下载都能跑通。
花费最小化：只生成一张定妆图（图像）和一个 4 秒镜头（视频），不跑剧本与配音。

用法：
  set AGNES_API_KEYS=key1,key2        （Windows）
  export AGNES_API_KEYS=key1,key2     （macOS/Linux）
  python entrypoints/smoke_real_api.py [--out smoke_out] [--skip-video]

输出 JSON 诊断。每一步给出 ok / 失败原因（不打印 key）。退出码 0 表示全部通过。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import _common  # noqa: F401
from _common import force_utf8_stdio

from novel_to_video.providers.factory import ProviderError, make_image_provider, make_video_provider
from novel_to_video.providers.skill_bridge import key_config

PROMPT_IMAGE = ("cinematic realistic, dystopian sci-fi, muted cold palette. A lone figure in a grey work jacket "
                "standing before a steel dome city at dawn. Vertical 9:16 frame, no text, no watermark.")
PROMPT_VIDEO = ("cinematic realistic, slow push-in on a lone figure in a grey work jacket before a steel "
                "dome city at dawn. Consistent character design, no text, no subtitles.")


def _step(name: str, ok: bool, detail: str = "") -> dict:
    return {"step": name, "ok": ok, "detail": detail}


def main(argv=None) -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="真实 API 冒烟")
    ap.add_argument("--out", default="smoke_out")
    ap.add_argument("--skip-video", action="store_true", help="只测图像接口")
    args = ap.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    steps = []

    cfg = key_config()
    n_keys = cfg["count"]
    steps.append(_step("key 配置", n_keys > 0,
                       f"检测到 {n_keys} 把 key，来源：{cfg['source']}（不显示内容）"))
    if n_keys == 0:
        return _finish(steps, 1)

    # 1) 图像
    try:
        img = make_image_provider("agnes")
        t0 = time.monotonic()
        res = img.generate(PROMPT_IMAGE, out_dir / "smoke_portrait.png", ratio="9:16")
        dt = round(time.monotonic() - t0, 1)
        if res.ok and (out_dir / "smoke_portrait.png").is_file():
            from PIL import Image
            with Image.open(out_dir / "smoke_portrait.png") as im:
                size = im.size
            steps.append(_step("图像生成", True, f"{dt}s，尺寸 {size[0]}x{size[1]}"))
        else:
            steps.append(_step("图像生成", False, f"{res.error}（non_retryable={res.non_retryable}）"))
    except ProviderError as exc:
        steps.append(_step("图像生成", False, str(exc)))
    except Exception as exc:  # 诊断脚本：任何异常都要记录而不是崩溃
        steps.append(_step("图像生成", False, f"异常：{type(exc).__name__}: {exc}"))

    # 2) 视频（4 秒，单镜头）
    if not args.skip_video and steps[-1]["ok"]:
        try:
            vid = make_video_provider("agnes")
            t0 = time.monotonic()
            res = vid.generate(PROMPT_VIDEO, out_dir / "smoke_clip.mp4", seconds=4,
                               reference_images=[out_dir / "smoke_portrait.png"], aspect_ratio="9:16")
            dt = round(time.monotonic() - t0, 1)
            if res.ok and (out_dir / "smoke_clip.mp4").is_file():
                from novel_to_video.media import ffmpeg as mf
                dur = round(mf.probe_duration(out_dir / "smoke_clip.mp4"), 2)
                steps.append(_step("视频生成", True, f"{dt}s，时长 {dur}s（期望约 4s）"))
            else:
                steps.append(_step("视频生成", False, f"{res.error}（non_retryable={res.non_retryable}）"))
        except ProviderError as exc:
            steps.append(_step("视频生成", False, str(exc)))
        except Exception as exc:
            steps.append(_step("视频生成", False, f"异常：{type(exc).__name__}: {exc}"))
    elif not args.skip_video:
        steps.append(_step("视频生成", False, "跳过：图像步骤失败"))

    return _finish(steps, 0 if all(s["ok"] for s in steps) else 1)


def _finish(steps, code: int) -> int:
    print(json.dumps({"ok": code == 0, "steps": steps}, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
