"""steps/chapter_stills.py — C-8a 场景图（动态漫模式）。

每个镜头生成一张场景图，作为审核对象；动效片段（C-8b）与章节合成在 S4 实现。

- 提示词：沿用 C-7 的 prompt_en（已通过一致性检查）。
- 参考图：沿用视频模式的规则（角色最多 2 张、地点 1 张，有余量才加道具），由接口再截断到 3 张。
- 输出：chapters/NNN/stills/<镜头>_vNNN.png。版本号递增，旧图保留；镜头的 still_file 指向当前版本。
- 缓存键 still_key = hash(prompt, 参考图, 画幅, 尺寸)；任一变化才重新生成。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

from .. import cancel, joblog
from .. import video_format
from ..chapter_io import chapter_dir, load_entities, load_shots, mark_stage, save_shots, sha12
from ..providers.image import ImageProvider
from .chapter_assets import missing_portraits
from .chapter_clips import _reference_images

STILL_SIZE = "1K"          # 文档推荐档位；配合画幅 9:16 得到 736×1312，之后缩放到成片尺寸
STILL_KEY_VERSION = "still-v1"
_VER_RE = re.compile(r"^(?P<sid>.+)_v(?P<n>\d{3})\.png$")


def _next_version(sdir: Path, sid: str) -> int:
    nums = [0]
    if sdir.is_dir():
        for p in sdir.iterdir():
            m = _VER_RE.match(p.name)
            if m and m.group("sid") == sid:
                nums.append(int(m.group("n")))
    return max(nums) + 1


def run_stills(project_dir: Path, no: int, image: ImageProvider) -> Dict[str, Any]:
    blocked = missing_portraits(project_dir, no)
    if blocked:
        mark_stage(project_dir, no, "stills", "waiting", "缺少定妆图：" + "、".join(blocked))
        return {"blocked": blocked, "generated": [], "failed": []}

    shots = load_shots(project_dir, no)
    ents = load_entities(project_dir)
    chars = {c["id"]: c for c in ents["characters"]["characters"]}
    locs = {l["id"]: l for l in ents["locations"]["locations"]}
    props = {p["id"]: p for p in ents["props"]["props"]}
    fmt = video_format.load(project_dir)
    cdir = chapter_dir(project_dir, no)
    sdir = cdir / "stills"
    sdir.mkdir(parents=True, exist_ok=True)

    generated: List[str] = []
    failed: List[str] = []
    for sh in shots["shots"]:
        cancel.check()   # 用户请求停止：不再为后续镜头提交生图
        if sh.get("error") or not sh.get("prompt_en") or sh.get("consistency") != "pass":
            failed.append(sh["id"])
            continue
        refs = _reference_images(project_dir, sh, chars, locs, props)
        ref_key = sha12(*[f"{p.name}:{p.stat().st_size}" for p in refs])
        still_key = sha12(sh["prompt_hash"], ref_key, fmt.aspect_ratio, STILL_SIZE, STILL_KEY_VERSION)
        current = (cdir / sh["still_file"]) if sh.get("still_file") else None
        if current is not None and current.is_file() and sh.get("still_key") == still_key:
            continue

        rel = f"stills/{sh['id']}_v{_next_version(sdir, sh['id']):03d}.png"
        out = cdir / rel
        label = f"第 {no} 章 C-8a 镜头 {sh['id']}"
        joblog.log(project_dir, "chapter", f"{label}：提交场景图生成（参考图 {len(refs)} 张）")
        with joblog.task_label(label), joblog.heartbeat(project_dir, "chapter", f"{label} 场景图生成"):
            res = image.generate(sh["prompt_en"], out, ratio=fmt.aspect_ratio, size=STILL_SIZE,
                                 reference_images=refs)
        if res.dropped_refs:
            joblog.log(project_dir, "chapter",
                       f"{label}：参考图超过 3 张，已丢弃 {len(res.dropped_refs)} 张：" + "、".join(Path(p).name for p in res.dropped_refs),
                       level="warn")
        if not res.ok:
            joblog.log(project_dir, "chapter", f"{label} 失败：{res.error}", level="error")
            sh["still_error"] = res.error
            failed.append(sh["id"])
            if res.non_retryable:
                save_shots(project_dir, no, shots)
                mark_stage(project_dir, no, "stills", "failed", res.error)
                return {"blocked": [], "generated": generated, "failed": failed, "stopped": res.error}
            continue
        sh["still_file"] = rel
        sh["still_key"] = still_key
        sh["still_error"] = None
        generated.append(sh["id"])
        joblog.log(project_dir, "chapter", f"{label} 完成：{rel}", level="ok")

    save_shots(project_dir, no, shots)
    status = "failed" if failed else "done"
    mark_stage(project_dir, no, "stills", status, f"失败镜头：{', '.join(failed)}" if failed else None)
    return {"blocked": [], "generated": generated, "failed": failed}
