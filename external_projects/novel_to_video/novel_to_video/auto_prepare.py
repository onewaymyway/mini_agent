"""auto_prepare.py — G6 全自动档位：替用户完成「需要人动手」的素材工作（只处理本章镜头用到的素材）。

对本章镜头引用的角色 / 地点 / 道具（含场景点），按顺序：
  1. 性别：角色缺少已确认性别时，先取人设卡明确字段，否则让模型依据本章正文二选一（来源记为 auto）；
     非人类实体不判定（保持空，不阻塞流程）。
  2. G-2：只补缺失的视觉锚点与声音设定。
  3. 通过设定：仅当设定字段齐全（视觉锚点、角色还要声音）时才自动通过；不齐全的保持待审核并计入 skipped。
  4. G-3：生成缺失的定妆图（G6 的 asset_portrait 为自动，生成成功即通过）。
所有动作写入 assets 日志；本模块只做编排，不改变各步骤自身的校验。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Set

from . import chapter_io as cio
from . import joblog
from .providers.image import ImageProvider
from .providers.text import TextLLM
from .review import entity_key, get_item, load_review
from .steps import global_visual as gv


def needed_entity_ids(project_dir: Path, no: int) -> Dict[str, Set[str]]:
    """本章镜头用到的素材 id：{"character": {...}, "location": {...}, "prop": {...}}。"""
    out: Dict[str, Set[str]] = {"character": set(), "location": set(), "prop": set()}
    try:
        shots = cio.load_shots(project_dir, no).get("shots") or []
    except (OSError, KeyError, ValueError):
        return out
    for sh in shots:
        out["character"].update(sh.get("character_ids") or [])
        if sh.get("location_id"):
            out["location"].add(sh["location_id"])
        out["prop"].update(sh.get("prop_ids") or [])
    return out


def _fill_gender(project_dir: Path, no: int, text_llm: TextLLM, char_ids: Set[str]) -> List[str]:
    from .gender_review import CONFIRMED_SOURCES
    from .steps import chapter_assets as CA
    from .project_store import write_json_atomic
    doc = gv._load_entities(project_dir, "character")
    changed: List[str] = []
    text = CA._chapter_text(project_dir, no)
    for c in gv._entity_list(doc, "character"):
        if c["id"] not in char_ids or c.get("gender") and c.get("gender_source") in CONFIRMED_SOURCES:
            continue
        name = (c.get("names") or [c["id"]])[0]
        try:
            g, basis = CA._infer_gender(project_dir, text_llm, name, [], no, text)
        except Exception as exc:  # noqa: BLE001 - 性别推断失败不应阻塞整章，保持为空即可
            joblog.log(project_dir, "assets", f"G6 性别推断失败（{name}）：{exc}", level="warn")
            continue
        if g:
            c["gender"], c["gender_source"], c["gender_basis"] = g, "auto", basis
            changed.append(c["id"])
            joblog.log(project_dir, "assets", f"G6 自动确定性别：{name} → {g}（{basis}）", level="ok")
    if changed:
        gv._save_entities(project_dir, "character", doc)
    return changed


def _approve_complete_texts(project_dir: Path, kinds_ids: Dict[str, Set[str]]) -> Dict[str, List[str]]:
    """设定字段齐全且仍是 pending/missing 的，自动通过；不齐全的记入 skipped。"""
    approved: List[str] = []
    skipped: List[str] = []
    review = load_review(project_dir)
    for kind, ids in kinds_ids.items():
        if not ids:
            continue
        doc = gv._load_entities(project_dir, kind)
        for e in gv._entity_list(doc, kind):
            if e["id"] not in ids:
                continue
            status = get_item(review, entity_key(kind, e["id"]))["text_status"]
            if status == "approved":
                continue
            complete = bool(e.get("visual_anchor_en")) and (kind != "character" or bool(e.get("voice_profile")))
            if complete:
                gv.approve(project_dir, kind, e["id"], "text")
                approved.append(e["id"])
            else:
                skipped.append(e["id"])
    return {"approved": approved, "skipped": skipped}


def prepare_chapter_assets(project_dir: Path, no: int, text_llm: TextLLM, image: ImageProvider) -> Dict[str, Any]:
    """返回 {"progress": bool, ...}：progress 表示本轮有新进展（性别、设定、定妆图任一有变化）。"""
    project_dir = Path(project_dir)
    need = needed_entity_ids(project_dir, no)
    ids: Set[str] = set().union(*need.values())
    if not ids:
        return {"progress": False, "note": "本章镜头没有引用素材"}
    joblog.log(project_dir, "assets", f"G6 第 {no} 章自动补素材：角色 {len(need['character'])}、"
               f"地点 {len(need['location'])}、道具 {len(need['prop'])}")
    genders = _fill_gender(project_dir, no, text_llm, need["character"]) if need["character"] else []
    g2 = gv.visual_text_pass(project_dir, text_llm, only_missing=True, ids=sorted(ids))
    if g2.get("stopped"):
        return {"progress": False, "stopped": g2["stopped"]}
    texts = _approve_complete_texts(project_dir, need)
    g3 = gv.portrait_pass(project_dir, image, only_missing=True, ids=sorted(ids))
    done_n = len(g2["done"]) + len(texts["approved"]) + len(g3["done"]) + len(genders)
    joblog.log(project_dir, "assets",
               f"G6 第 {no} 章自动补素材完成：性别 {len(genders)}、设定补全 {len(g2['done'])}、"
               f"设定通过 {len(texts['approved'])}、定妆图 {len(g3['done'])}，失败 {len(g2['failed']) + len(g3['failed'])}",
               level="ok" if done_n else "warn")
    return {"progress": done_n > 0, "genders": genders, "texts": g2["done"], "approved": texts["approved"],
            "portraits": g3["done"], "failed": g2["failed"] + g3["failed"],
            "skipped": texts["skipped"], "stopped": g3.get("stopped")}
