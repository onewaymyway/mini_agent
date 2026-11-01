"""merge_character.py — 把“补建角色”合并到库中已有的角色。

场景：C-3 之后 C-5 起草了一个新角色，但用户确认它其实就是库里已有的某个人（别名、称谓不同）。
合并做的事（只动补建角色与引用它的镜头，不碰其他角色）：
  1. 补建角色的名字加入目标角色的 names（作为别名）：以后各章的镜头规划按名字就能归到目标角色；
  2. 所有章节 shots.yaml 里指向补建角色的 speaker_id / character_ids 改指目标角色；
  3. 已经生成过音频、提示词或视频的镜头，按“台词变更”层级清理下游产物（换了人就换音色与形象），
     并把章节与全书成片标记为 stale；还没生成过的镜头不动；
  4. 从角色库与审核记录中删除补建角色。已生成的定妆图文件保留在磁盘上，不删除。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from . import chapter_io as cio
from . import joblog
from . import revision
from .project_store import append_progress, read_json, write_json_atomic
from .review import entity_key, load_review, save_review


class MergeError(RuntimeError):
    pass


def merge_candidates(project_dir: Path, auto_id: str) -> List[Dict[str, Any]]:
    """可作为合并目标的角色：除自己之外的全部角色，正式角色在前。"""
    chars = read_json(Path(project_dir) / "global" / "characters.json")["characters"]
    rows = [{"id": c["id"], "name": (c.get("names") or [c["id"]])[0], "source": c.get("source") or "export"}
            for c in chars if c["id"] != auto_id]
    return sorted(rows, key=lambda r: (r["source"] == "auto_added", r["id"]))


def merge_auto_added(project_dir: Path, auto_id: str, target_id: str) -> Dict[str, Any]:
    project_dir = Path(project_dir)
    doc = read_json(project_dir / "global" / "characters.json")
    chars = doc["characters"]
    src = next((c for c in chars if c["id"] == auto_id), None)
    dst = next((c for c in chars if c["id"] == target_id), None)
    if src is None:
        raise MergeError(f"角色不存在：{auto_id}")
    if src.get("source") != "auto_added":
        raise MergeError(f"只有补建角色可以合并：{auto_id} 不是补建角色")
    if dst is None:
        raise MergeError(f"合并目标不存在：{target_id}")
    if auto_id == target_id:
        raise MergeError("不能合并到自己")

    src_names = list(src.get("names") or [])
    dst_names = list(dst.get("names") or [])
    added = [n for n in src_names if n not in dst_names]
    dst["names"] = dst_names + added
    doc["characters"] = [c for c in chars if c["id"] != auto_id]
    write_json_atomic(project_dir / "global" / "characters.json", doc)

    review = load_review(project_dir)
    review.get("items", {}).pop(entity_key("character", auto_id), None)
    save_review(project_dir, review)

    touched: Dict[int, List[str]] = {}
    for row in cio.load_index(project_dir).get("chapters", []):
        no = row["no"]
        shots_doc = cio.load_shots(project_dir, no)
        if not shots_doc.get("shots"):
            continue
        hit: List[str] = []
        for sh in shots_doc["shots"]:
            changed = False
            for b in sh.get("blocks", []):
                if b.get("speaker_id") == auto_id:
                    b["speaker_id"], b["speaker_name"] = target_id, None
                    changed = True
            cast: List[str] = []
            for cid in sh.get("character_ids") or []:
                cid = target_id if cid == auto_id else cid
                if cid not in cast:
                    cast.append(cid)
            if cast != (sh.get("character_ids") or []):
                sh["character_ids"] = cast
                changed = True
            if changed:
                hit.append(sh["id"])
        old_ns = shots_doc.get("new_speakers") or []
        ns = [n for n in old_ns if n not in src_names]
        if ns != old_ns:
            shots_doc["new_speakers"] = ns
        if hit or ns != old_ns:
            cio.save_shots(project_dir, no, shots_doc)
        if hit:
            touched[no] = hit

    cleared: Dict[int, List[str]] = {}
    for no, sids in touched.items():
        shots = {s["id"]: s for s in cio.load_shots(project_dir, no)["shots"]}
        for sid in sids:
            sh = shots[sid]
            # 只有已经生成过下游产物的镜头才需要清理；全新的镜头什么都不用动
            if sh.get("audio_key") or sh.get("prompt_en") or sh.get("clip_key") or sh.get("duration_sec"):
                revision.invalidate(project_dir, no, sid, "dialogue", touch_script=False)
                cleared.setdefault(no, []).append(sid)

    msg = (f"补建角色 {auto_id}（{'、'.join(src_names)}）已合并到 {target_id}（{(dst['names'] or [''])[0]}）；"
           f"别名新增：{'、'.join(added) or '无'}；改写镜头：{sum(len(v) for v in touched.values())} 个；"
           f"清理下游产物：{sum(len(v) for v in cleared.values())} 个镜头")
    append_progress(project_dir, msg)
    joblog.log(project_dir, "chapter", msg, level="ok")
    return {"from": auto_id, "into": target_id, "aliases_added": added,
            "shots_rewritten": touched, "shots_cleared": cleared}
