"""steps/character_looks.py — 角色多形象（阶段 4，默认关闭）。

背景：同一个角色在不同章节可能换装、受伤、伪装；只用一张定妆图会让视频里的外观与剧情不符。
设计：
  * 角色可选地带一个 `looks` 列表；没有 looks 的角色就是原来的“基础形象”，旧数据无需迁移；
  * 镜头只在需要时用 `character_looks` 指定形象标签，其余仍用基础形象；
  * 库里没有的标签由 C-5 起草（source=auto_added），文本与定妆图确认沿用补建角色的门槛；
  * 项目选项 `character_looks_enabled` 默认 False（保守）：关闭时提示词、镜头与流水线与之前完全一致。

审核键：`look:<角色id>:<形象id>`；定妆图：`global/assets/characters/<角色id>/<形象id>[_vN].png`。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import autogate, joblog, llm_trace, prompts
from .. import runtime as _rt
from ..call_log import log_call
from ..chapter_io import chapter_dir, load_entities, save_shots
from ..project_store import read_json, write_json_atomic
from ..providers.image import ImageProvider
from ..providers.text import TextLLM
from ..review import entity_key, get_item, load_review, save_review
from .global_visual import CHAR_RATIO, StepError, _art_style, parse_json_object

DEFAULT_LABELS = {"", "默认", "基础", "default", "base", "none", "无"}


def looks_enabled(project_dir: Path) -> bool:
    try:
        return bool(read_json(Path(project_dir) / "project.json").get("character_looks_enabled", False))
    except (OSError, ValueError):
        return False


def look_key(cid: str, lid: str) -> str:
    return f"{cid}:{lid}"


def find_look(char: Dict[str, Any], label: str) -> Optional[Dict[str, Any]]:
    """按 id / 标签 / 别名匹配角色已有的形象。"""
    label = (label or "").strip()
    for lk in char.get("looks") or []:
        if label == lk["id"] or label == lk.get("label") or label in (lk.get("aliases") or []):
            return lk
    return None


def resolve(char: Dict[str, Any], look_id: Optional[str]) -> Tuple[str, Optional[str]]:
    """镜头实际使用的 (视觉锚点, 定妆图路径)。指定的形象存在就用它（不回退到基础定妆图）；否则用基础形象。"""
    if look_id:
        for lk in char.get("looks") or []:
            if lk["id"] == look_id:
                return lk.get("visual_anchor_en") or "", lk.get("asset_path")
    return char.get("visual_anchor_en") or "", char.get("asset_path")


def _next_look_id(char: Dict[str, Any]) -> str:
    nums = [int(l["id"].split("_")[1]) for l in char.get("looks") or []
            if l["id"].startswith("look_") and l["id"].split("_")[1].isdigit()]
    return f"look_{(max(nums) + 1 if nums else 1):02d}"


def _char_name(c: Dict[str, Any]) -> str:
    return (c.get("names") or [c.get("id", "")])[0]


def _save_chars(project_dir: Path, doc: Dict[str, Any]) -> None:
    write_json_atomic(project_dir / "global" / "characters.json", doc)


# ---------------------------------------------------------------- C-5：起草缺失形象

def _context(script: str, name: str) -> List[str]:
    return [ln.strip() for ln in script.splitlines() if name in ln and ln.strip()][:6]


def draft_missing_looks(project_dir: Path, text_llm: TextLLM, shots: Dict[str, Any], no: int) -> List[str]:
    """为 shots['new_looks']（[{character_id, label}]）起草形象，写入角色库，并把镜头里的标签回填为 look_ids。"""
    wanted = [w for w in (shots.get("new_looks") or []) if w.get("character_id") and w.get("label")]
    if not wanted:
        return []
    doc = load_entities(project_dir)["characters"]
    chars = {c["id"]: c for c in doc["characters"]}
    script = (chapter_dir(project_dir, no) / "script.md").read_text(encoding="utf-8")
    review = load_review(project_dir)
    drafted: List[str] = []
    for w in wanted:
        c = chars.get(w["character_id"])
        if c is None or find_look(c, w["label"]):
            continue
        lid = _next_look_id(c)
        name = _char_name(c)
        label = f"第 {no} 章 C-5 起草「{name}」的形象「{w['label']}」"
        with joblog.timed(project_dir, "chapter", label):
            raw = llm_trace.ask_traced(project_dir, text_llm,
                                       prompts.look_prompt(name, c.get("visual_anchor_en") or "", w["label"],
                                                           _context(script, name)),
                                       system=prompts.SYSTEM_VISUAL, scope="chapter", label=label,
                                       step="C-5", chapter=no)
        obj = parse_json_object(raw)
        anchor = str(obj.get("visual_anchor_en") or "").strip()
        if not anchor:
            raise StepError(f"「{name}」的形象「{w['label']}」草稿缺少 visual_anchor_en")
        c.setdefault("looks", []).append({
            "id": lid, "label": w["label"], "aliases": [], "description_zh": str(obj.get("description_zh") or "").strip(),
            "visual_anchor_en": anchor, "visual_anchor_source": text_llm.name, "asset_path": None,
            "source": "auto_added", "auto_added_chapter": no})
        get_item(review, entity_key("look", look_key(c["id"], lid))).update(
            text_status="approved" if autogate.is_auto(project_dir, "asset_text") else "pending",
            portrait_status="missing")
        drafted.append(look_key(c["id"], lid))
    if drafted:
        _save_chars(project_dir, doc)
        save_review(project_dir, review)
    # 回填镜头：标签 → look_ids；未解决的留在 look_labels / new_looks
    for sh in shots.get("shots") or []:
        labels = dict(sh.get("look_labels") or {})
        ids = dict(sh.get("look_ids") or {})
        for cid, lab in list(labels.items()):
            c = chars.get(cid)
            hit = find_look(c, lab) if c else None
            if hit:
                ids[cid] = hit["id"]
                del labels[cid]
        if ids:
            sh["look_ids"] = ids
        if labels:
            sh["look_labels"] = labels
        else:
            sh.pop("look_labels", None)
    shots["new_looks"] = [w for w in (shots.get("new_looks") or [])
                          if not (chars.get(w.get("character_id")) and find_look(chars[w["character_id"]], w.get("label")))]
    save_shots(project_dir, no, shots)
    return drafted


def looks_waiting(project_dir: Path, no: int, image: ImageProvider) -> List[str]:
    """本章草拟的形象：文本确认 → 定妆图生成 → 定妆图确认。"""
    from .global_visual import portrait_pass
    waiting: List[str] = []

    def mine():
        out = []
        for c in load_entities(project_dir)["characters"]["characters"]:
            for lk in c.get("looks") or []:
                if lk.get("auto_added_chapter") == no:
                    out.append((c, lk))
        return out

    rows = mine()
    if not rows:
        return waiting
    review = load_review(project_dir)
    need = []
    for c, lk in rows:
        item = get_item(review, entity_key("look", look_key(c["id"], lk["id"])))
        tag = f"{_char_name(c)}·{lk['label']}"
        if item["text_status"] != "approved":
            waiting.append(f"{look_key(c['id'], lk['id'])} {tag}：等待确认形象设定")
        elif not lk.get("asset_path"):
            need.append(look_key(c["id"], lk["id"]))
    if need:
        summary = portrait_pass(project_dir, image, ids=need)
        if summary.get("stopped"):
            raise StepError(summary["stopped"])
        rows = mine()
        review = load_review(project_dir)
        for c, lk in rows:
            if look_key(c["id"], lk["id"]) in need and not lk.get("asset_path"):
                waiting.append(f"{look_key(c['id'], lk['id'])}：形象定妆图生成失败，请查看全书素材页")
    for c, lk in rows:
        item = get_item(review, entity_key("look", look_key(c["id"], lk["id"])))
        if lk.get("asset_path") and item["portrait_status"] != "approved":
            waiting.append(f"{look_key(c['id'], lk['id'])} {_char_name(c)}·{lk['label']}：等待确认形象定妆图")
    return waiting


def missing_look_portraits(project_dir: Path, shots: Dict[str, Any]) -> List[str]:
    """C-8 前检查：镜头指定的形象必须已有定妆图文件（不静默退回基础形象）。"""
    chars = {c["id"]: c for c in load_entities(project_dir)["characters"]["characters"]}
    missing = []
    for sh in shots.get("shots") or []:
        for cid, lid in (sh.get("look_ids") or {}).items():
            c = chars.get(cid)
            lk = next((l for l in (c or {}).get("looks") or [] if l["id"] == lid), None)
            if lk and (not lk.get("asset_path") or not (project_dir / lk["asset_path"]).is_file()):
                missing.append(f"形象 {look_key(cid, lid)}")
    return missing


# ---------------------------------------------------------------- G-3：形象定妆图

def _portrait_rel(cid: str, lid: str, version: int) -> str:
    suffix = "" if version <= 1 else f"_v{version}"
    return f"global/assets/characters/{cid}/{lid}{suffix}.png"


def portrait_pass_looks(project_dir: Path, image: ImageProvider, summary: Dict[str, Any], *,
                        only_missing: bool, ids: Optional[List[str]], regenerate: bool) -> None:
    """由 global_visual.portrait_pass 在角色/地点/道具之后调用，结果并入同一个 summary。"""
    if summary.get("stopped"):
        return
    style = _art_style(project_dir)
    doc = load_entities(project_dir)["characters"]
    review = load_review(project_dir)
    for c in doc["characters"]:
        for lk in c.get("looks") or []:
            key = look_key(c["id"], lk["id"])
            if ids and key not in ids:
                continue
            if _rt.stop_requested(project_dir):
                summary["stopped"] = "用户请求停止"
                return
            if not lk.get("visual_anchor_en"):
                summary["skipped"].append(key)
                continue
            rk = entity_key("look", key)
            item = get_item(review, rk)
            exists = bool(lk.get("asset_path")) and (project_dir / lk["asset_path"]).is_file()
            if only_missing and not regenerate and exists and item.get("portrait_status") in ("pending", "approved"):
                summary["skipped"].append(key)
                continue
            tag = f"{_char_name(c)}·{lk['label']}"
            joblog.log(project_dir, "assets", f"G-3 生成形象定妆图：{tag}（{key}）")
            version = int(item.get("portrait_version") or 0)
            if regenerate or not exists or item.get("portrait_status") == "stale":
                version += 1
            rel = _portrait_rel(c["id"], lk["id"], version)
            prompt = prompts.portrait_prompt(style, lk["visual_anchor_en"], kind="character", name=tag)
            result = image.generate(prompt, project_dir / rel, ratio=CHAR_RATIO)
            log_call(project_dir, {"kind": "image", "provider": image.name, "entity": rk,
                                   "ok": result.ok, "error": (result.error or "")[:300] or None})
            review = load_review(project_dir)
            item = get_item(review, rk)
            if result.ok:
                lk["asset_path"] = rel
                _save_chars(project_dir, doc)
                item.update(portrait_status="approved" if autogate.is_auto(project_dir, "asset_portrait") else "pending",
                            portrait_version=version, last_error=None)
                summary["done"].append(key)
                joblog.log(project_dir, "assets", f"G-3 完成：{tag} → {rel}", level="ok")
            else:
                joblog.log(project_dir, "assets", f"G-3 失败：{tag}：{result.error}", level="error")
                item.update(portrait_status="failed", last_error=f"G-3 失败：{result.error}")
                summary["failed"].append({"id": key, "error": result.error})
                save_review(project_dir, review)
                if result.non_retryable:
                    summary["stopped"] = f"{key}：{result.error}（参数/鉴权错误，已停止，请修正配置后重跑）"
                    return
                continue
            save_review(project_dir, review)


# ---------------------------------------------------------------- 编辑与状态汇总

LOOK_EDITABLE = {"visual_anchor_en", "description_zh"}


def _locate(project_dir: Path, key: str):
    cid, _, lid = key.partition(":")
    doc = load_entities(project_dir)["characters"]
    c = next((x for x in doc["characters"] if x["id"] == cid), None)
    lk = next((l for l in (c or {}).get("looks") or [] if l["id"] == lid), None)
    return doc, c, lk


def update_look(project_dir: Path, key: str, fields: Dict[str, Any], note: str = "人工编辑") -> Dict[str, Any]:
    bad = set(fields) - LOOK_EDITABLE
    if bad:
        raise StepError(f"不允许编辑的字段：{', '.join(sorted(bad))}")
    doc, c, lk = _locate(project_dir, key)
    if lk is None:
        raise StepError(f"形象不存在：{key}")
    visual_changed = any(lk.get(k) != v for k, v in fields.items())
    for k, v in fields.items():
        lk[k] = v
        lk[f"{k}_source"] = "manual"
    _save_chars(project_dir, doc)
    review = load_review(project_dir)
    item = get_item(review, entity_key("look", key))
    item.update(text_status="pending" if fields else item.get("text_status"), last_error=None)
    if visual_changed and item.get("portrait_status") in ("pending", "approved"):
        item["portrait_status"] = "stale"
    item["last_note"] = note
    save_review(project_dir, review)
    return item


def status_rows(project_dir: Path) -> List[Dict[str, Any]]:
    """与 global_visual.visual_status 的行格式一致，供全书素材页复用同一套卡片。"""
    review = load_review(project_dir)
    rows = []
    for c in load_entities(project_dir)["characters"]["characters"]:
        for lk in c.get("looks") or []:
            key = look_key(c["id"], lk["id"])
            item = get_item(review, entity_key("look", key))
            rows.append({
                "key": entity_key("look", key), "kind": "look", "id": key,
                "name": f"{_char_name(c)}·{lk['label']}",
                "missing_fields": [] if lk.get("visual_anchor_en") else ["visual_anchor_en"],
                "text_status": item["text_status"], "portrait_status": item["portrait_status"],
                "portrait_version": item["portrait_version"], "asset_path": lk.get("asset_path"),
                "last_error": item.get("last_error"),
            })
    return rows
