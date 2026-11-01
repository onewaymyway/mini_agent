"""steps/chapter_compose.py — C-9 章节合成：所有镜头都通过审核后，按顺序拼接为 chapter_NNN.mp4。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from ..chapter_io import chapter_dir, load_shots, mark_stage
from ..media import ffmpeg as mf
from ..shot_review import shot_states


def run_compose(project_dir: Path, no: int) -> Dict[str, Any]:
    shots = load_shots(project_dir, no)
    states = shot_states(project_dir, no)
    not_ready = [sid for sid, st in states.items() if st != "approved"]
    if not_ready:
        mark_stage(project_dir, no, "compose", "waiting", "等待审核：" + "、".join(not_ready))
        return {"waiting": not_ready}
    cdir = chapter_dir(project_dir, no)
    clips = [cdir / "clips" / f"{sh['id']}.mp4" for sh in shots["shots"]]
    missing = [str(p.name) for p in clips if not p.is_file()]
    if missing:
        raise RuntimeError(f"缺少小场景视频：{', '.join(missing)}")
    out = cdir / f"chapter_{no:03d}.mp4"
    mf.concat(clips, out)
    mark_stage(project_dir, no, "compose", "done")
    return {"waiting": [], "output": str(out), "duration": round(mf.probe_duration(out), 2)}
