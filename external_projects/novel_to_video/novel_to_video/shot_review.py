"""shot_review.py — 小场景（镜头）逐条审核。

审核记录在 chapters/NNN/review.json：{"items": {sid: {status, approved_key, note}}}。
状态（shot_states 计算）：
  missing   尚未生成视频（包括被驳回后、等待重新生成的镜头）
  pending   已生成，等待审核（包括驳回后重新生成的新版本）
  approved  已通过，且通过时的 clip_key 与当前一致
  stale     已通过，但之后镜头内容/音频/提示词变了，需要重新审核
  failed    生成或规划失败
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from .chapter_io import chapter_dir, load_shots, save_shots, norm, source_text
from .project_store import read_json, write_json_atomic


def _review_path(project_dir: Path, no: int) -> Path:
    return chapter_dir(project_dir, no) / "review.json"


def load_shot_review(project_dir: Path, no: int) -> Dict[str, Any]:
    p = _review_path(project_dir, no)
    return read_json(p) if p.is_file() else {"items": {}}


def _save(project_dir: Path, no: int, data: Dict[str, Any]) -> None:
    write_json_atomic(_review_path(project_dir, no), data)


def shot_states(project_dir: Path, no: int) -> Dict[str, str]:
    shots = load_shots(project_dir, no)
    review = load_shot_review(project_dir, no)["items"]
    out: Dict[str, str] = {}
    for sh in shots.get("shots", []):
        sid = sh["id"]
        item = review.get(sid, {})
        if sh.get("error") or sh.get("clip_error"):
            out[sid] = "failed"
        elif not sh.get("clip_key") or not (chapter_dir(project_dir, no) / "clips" / f"{sid}.mp4").is_file():
            out[sid] = "missing"
        elif item.get("status") == "approved":
            out[sid] = "approved" if item.get("approved_key") == sh["clip_key"] else "stale"
        else:
            out[sid] = "pending"
    return out


def approve(project_dir: Path, no: int, sid: str) -> Dict[str, Any]:
    shots = load_shots(project_dir, no)
    sh = next((s for s in shots["shots"] if s["id"] == sid), None)
    if sh is None:
        raise KeyError(f"镜头不存在：{sid}")
    if not sh.get("clip_key"):
        raise ValueError("该镜头还没有生成视频，不能通过")
    data = load_shot_review(project_dir, no)
    data["items"][sid] = {"status": "approved", "approved_key": sh["clip_key"],
                          "note": data["items"].get(sid, {}).get("note"),
                          "updated_at": datetime.now().isoformat(timespec="seconds")}
    _save(project_dir, no, data)
    return data["items"][sid]


def reject(project_dir: Path, no: int, sid: str, note: str) -> Dict[str, Any]:
    """驳回：删除旧视频并清空 clip_key，下次运行 C-8 时重新生成（可先修改提示词或台词）。"""
    if not note.strip():
        raise ValueError("驳回需要填写修改意见")
    shots = load_shots(project_dir, no)
    sh = next((s for s in shots["shots"] if s["id"] == sid), None)
    if sh is None:
        raise KeyError(f"镜头不存在：{sid}")
    clip = chapter_dir(project_dir, no) / "clips" / f"{sid}.mp4"
    clip.unlink(missing_ok=True)
    sh["clip_key"] = None
    save_shots(project_dir, no, shots)
    data = load_shot_review(project_dir, no)
    data["items"][sid] = {"status": "rejected", "approved_key": None, "note": note.strip(),
                          "updated_at": datetime.now().isoformat(timespec="seconds")}
    _save(project_dir, no, data)
    return data["items"][sid]


def edit(project_dir: Path, no: int, sid: str, *, visual_hint_en: str | None = None,
         visual_hint_zh: str | None = None, block_texts: Dict[int, str] | None = None,
         character_ids: list | None = None) -> Dict[str, Any]:
    """修改镜头内容。对白必须仍能在剧本中逐字找到。修改后下次运行会自动重算音频/提示词/视频，
    已通过的审核会因 clip_key 变化而变为 stale。"""
    shots = load_shots(project_dir, no)
    sh = next((s for s in shots["shots"] if s["id"] == sid), None)
    if sh is None:
        raise KeyError(f"镜头不存在：{sid}")
    script = (chapter_dir(project_dir, no) / "script.md").read_text(encoding="utf-8")
    if visual_hint_en is not None:
        if not visual_hint_en.strip():
            raise ValueError("英文画面描述不能为空")
        sh["visual_hint_en"] = visual_hint_en.strip()
    if visual_hint_zh is not None:
        sh["visual_hint_zh"] = visual_hint_zh.strip()
    for idx, text in (block_texts or {}).items():
        b = next((x for x in sh["blocks"] if x["idx"] == idx), None)
        if b is None:
            raise KeyError(f"口播块不存在：{idx}")
        text = text.strip()
        if not text:
            raise ValueError("口播块不能为空")
        if b["type"] == "dialogue" and norm(text) not in norm(script):
            raise ValueError(f"对白必须逐字来自剧本：{text[:30]}")
        b["text"] = text
    if character_ids is not None:
        from .chapter_io import load_entities
        known = {c["id"] for c in load_entities(project_dir)["characters"]["characters"]}
        cast: list = []
        for cid in character_ids:
            if cid not in known:
                raise ValueError(f"角色不存在：{cid}")
            if cid not in cast:
                cast.append(cid)
        speakers = {b["speaker_id"] for b in sh["blocks"] if b.get("speaker_id")}
        if speakers - set(cast):
            raise ValueError("有台词的说话人必须保留在出镜角色中：" + "、".join(sorted(speakers - set(cast))))
        sh["character_ids"] = cast
    save_shots(project_dir, no, shots)
    return {"id": sid, "edited": True}
