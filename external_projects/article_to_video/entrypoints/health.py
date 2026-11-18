"""entrypoints/health.py — 环境自检（JSON）。required=False 的项只提示，不影响 ok。"""

from __future__ import annotations

import json
import sys
from importlib.util import find_spec

from _common import force_utf8_stdio  # noqa: E402

from article_to_video.media import binaries  # noqa: E402
from article_to_video.paths import projects_root  # noqa: E402


def _bin_check(name: str, finder, detail: str) -> dict:
    try:
        return {"name": name, "ok": True, "required": False, "detail": f"{detail}（{finder()}）"}
    except binaries.BinaryNotFound as exc:
        return {"name": name, "ok": False, "required": False, "detail": f"{detail}；{exc}"}


def main() -> int:
    force_utf8_stdio()
    checks = [
        {"name": "python>=3.9", "ok": sys.version_info >= (3, 9), "detail": sys.version.split()[0]},
        {"name": "pillow", "ok": find_spec("PIL") is not None, "required": False,
         "detail": "P4 视觉渲染需要"},
        _bin_check("ffmpeg", binaries.find_ffmpeg, "P3 配音（edge-tts 转码）与 P5 合成需要"),
        _bin_check("ffprobe", binaries.find_ffprobe, "读取非 WAV 音频时长需要"),
        {"name": "edge-tts", "ok": find_spec("edge_tts") is not None, "required": False,
         "detail": "在线配音引擎：pip install edge-tts"},
    ]
    root = projects_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".health_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks.append({"name": "projects_root_writable", "ok": True, "detail": str(root)})
    except OSError as exc:
        checks.append({"name": "projects_root_writable", "ok": False, "detail": f"{root}: {exc}"})

    ok = all(c["ok"] for c in checks if c.get("required", True))
    print(json.dumps({"ok": ok, "checks": checks}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
