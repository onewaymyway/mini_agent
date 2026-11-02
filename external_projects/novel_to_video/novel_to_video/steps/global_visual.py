"""steps/global_visual.py — 全书级素材：G-2 视觉与声音补全、G-3 定妆图生成。

设计要点：
  * 逐实体处理，每个实体成功/失败后立即落盘，中断后重跑只处理未完成的部分；
  * 单个实体失败不影响其它实体，失败原因写入 review.json 的 last_error；
  * 所有 LLM/图像调用写入 logs/api_calls.jsonl（不含 key）；
  * 产出一律标记为 generated 或 rules 来源，需用户逐条审核后才视为 approved；
  * 人工编辑的字段由 update_entity() 写入，并把依赖它的定妆图标记为 stale。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import prompts
from ..call_log import log_call
from .. import joblog
from .. import llm_trace
from .. import runtime as _rt
from ..project_store import read_json, write_json_atomic
from ..providers.image import ImageProvider
from ..providers.text import TextLLM
from ..review import entity_key, get_item, load_review, save_review, update_item

from ..project_store import DEFAULT_ART_STYLE
from .. import autogate
from .. import video_format
CHAR_RATIO = "3:4"
# 地点定妆图的比例跟随项目的视频格式（横屏 16:9 / 竖屏 9:16），见 video_format.py
VALID_GENDER = {"male", "female", "unknown"}


class StepError(RuntimeError):
    pass


# ---------------------------------------------------------------- 文件读写

def save_entities_ids(project_dir: Path, doc: Dict[str, Any]) -> None:
    """整体写回角色库（用于 C-5 补建角色）。"""
    write_json_atomic(project_dir / "global" / "characters.json", doc)


# 素材类型 → (文件名, 列表键)。道具 prop 为阶段 3 新增，旧项目没有 props.json 时按空库处理。
KINDS = ("character", "location", "prop")
_KIND_FILE = {"character": ("characters.json", "characters"), "location": ("locations.json", "locations"),
              "prop": ("props.json", "props")}
PROP_RATIO = "1:1"


def _load_entities(project_dir: Path, kind: str) -> Dict[str, Any]:
    fname, key = _KIND_FILE[kind]
    path = project_dir / "global" / fname
    if kind == "prop" and not path.is_file():
        return {"props": []}
    data = read_json(path)
    if kind == "prop":
        data.setdefault("props", [])
    return data


def _save_entities(project_dir: Path, kind: str, data: Dict[str, Any]) -> None:
    write_json_atomic(project_dir / "global" / _KIND_FILE[kind][0], data)


def _entity_list(data: Dict[str, Any], kind: str) -> List[Dict[str, Any]]:
    return data[_KIND_FILE[kind][1]]


def _art_style(project_dir: Path) -> str:
    proj = read_json(project_dir / "project.json")
    return proj.get("art_style") or DEFAULT_ART_STYLE


def _persona_excerpt(project_dir: Path, char: Dict[str, Any]) -> str:
    """人设卡路径相对于 novel_writer 导出目录（记录在 source/manifest.json 中）。"""
    rel = char.get("persona_card")
    if not rel:
        return ""
    from ..project_store import working_source_dir
    src_dir = working_source_dir(project_dir)
    real = Path(src_dir) / rel
    return real.read_text(encoding="utf-8")[:2000] if real.is_file() else ""


def parse_json_object(text: str) -> Dict[str, Any]:
    """从 LLM 输出中取出第一个 JSON 对象；失败抛 StepError。"""
    if not text:
        raise StepError("LLM 返回为空")
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", cleaned, re.S)
    if not m:
        raise StepError(f"LLM 输出中没有 JSON 对象：{text[:120]}")
    body = m.group(0)
    try:
        obj = json.loads(body)
    except json.JSONDecodeError as exc:
        # 严格解析失败（常见：少逗号、多余引号、截断）时，用 json_repair 修复后再取值。
        # 修复只改语法，不改内容；修复后的对白、字数等仍由后续校验把关。
        obj = _repair_json(body)
        if obj is None:
            raise StepError(f"LLM 输出 JSON 解析失败（json_repair 也无法修复）：{exc}") from exc
    if not isinstance(obj, dict):
        raise StepError("LLM 输出不是 JSON 对象")
    return obj


def _repair_json(body: str) -> Optional[Dict[str, Any]]:
    try:
        import json_repair
    except ImportError:
        return None
    try:
        obj = json_repair.loads(body)
    except Exception:  # noqa: BLE001 - 修复失败就按不可解析处理
        return None
    return obj if isinstance(obj, dict) else None


# ---------------------------------------------------------------- G-2

def visual_text_pass(project_dir: Path, text_llm: TextLLM, *, only_missing: bool = True,
                     ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """为角色/地点补全 visual_anchor_en、voice_profile（角色）与 gender（角色，仅缺失时）。"""
    summary = {"done": [], "failed": [], "skipped": [], "stopped": None}
    review = load_review(project_dir)
    joblog.log(project_dir, "assets", f"G-2 文本设定补全开始（{'仅缺失项' if only_missing else '全部'}）")

    for kind in KINDS:
        data = _load_entities(project_dir, kind)
        for ent in _entity_list(data, kind):
            eid = ent["id"]
            if ids and eid not in ids:
                continue
            if _rt.stop_requested(project_dir):
                summary["stopped"] = "用户请求停止"
                joblog.log(project_dir, "assets", "G-2 已停止（用户请求）", level="warn")
                return summary
            key = entity_key(kind, eid)
            name = (ent.get("names") or [ent.get("name", eid)])[0] if kind == "character" else ent.get("name", eid)
            # 性别不属于 G-2 的补全范围（只能经人工确认），因此不计入 needs，避免反复调用 LLM
            needs = not ent.get("visual_anchor_en") or (
                kind == "character" and not ent.get("voice_profile"))
            if only_missing and not needs:
                summary["skipped"].append(eid)
                joblog.log(project_dir, "assets", f"跳过 {name}（设定已完整）")
                continue
            joblog.log(project_dir, "assets", f"G-2 补全：{name}（{eid}）")
            try:
                if kind == "character":
                    prompt = prompts.character_prompt(ent, _persona_excerpt(project_dir, ent))
                elif kind == "prop":
                    prompt = prompts.prop_prompt(ent)
                else:
                    prompt = prompts.location_prompt(ent)
                raw = llm_trace.ask_traced(project_dir, text_llm, prompt, system=prompts.SYSTEM_VISUAL,
                                           scope="assets", label=f"G-2 补全：{name}（{eid}）", step="G-2")
                obj = parse_json_object(raw)
                anchor = str(obj.get("visual_anchor_en") or "").strip()
                if not anchor:
                    raise StepError("visual_anchor_en 为空")
                if len(anchor) > 400:
                    raise StepError("visual_anchor_en 超过 400 字符")
                ent["visual_anchor_en"] = anchor
                ent["visual_anchor_source"] = text_llm.name
                if kind == "character":
                    voice = str(obj.get("voice_profile") or "").strip()
                    if not voice:
                        raise StepError("voice_profile 为空")
                    ent["voice_profile"] = voice
                    ent["voice_profile_source"] = text_llm.name
                    # 性别不再由 LLM 直接写入：只能经用户确认（见 gender_review.confirm）。
                    # LLM 返回的 gender 仅作为参考，记入建议文件中，不改动角色。
                _save_entities(project_dir, kind, data)
                item = get_item(review, key)
                old_text = item.get("text_status")
                item.update(text_status="pending", last_error=None)
                if old_text == "approved" and item.get("portrait_status") == "approved":
                    item["portrait_status"] = "stale"
                save_review(project_dir, review)
                log_call(project_dir, {"kind": "text", "provider": text_llm.name, "entity": key, "ok": True})
                summary["done"].append(eid)
                joblog.log(project_dir, "assets", f"G-2 完成：{name}", level="ok")
            except Exception as exc:
                joblog.log(project_dir, "assets", f"G-2 失败：{name}：{exc}", level="error")
                review = load_review(project_dir)
                get_item(review, key)["last_error"] = f"G-2 失败：{exc}"
                save_review(project_dir, review)
                log_call(project_dir, {"kind": "text", "provider": text_llm.name, "entity": key,
                                       "ok": False, "error": str(exc)[:300]})
                summary["failed"].append({"id": eid, "error": str(exc)})
    return summary


# ---------------------------------------------------------------- G-3

def _portrait_rel(kind: str, eid: str, version: int) -> str:
    suffix = "" if version <= 1 else f"_v{version}"
    return f"global/assets/{kind}_{eid}{suffix}.png"


def portrait_pass(project_dir: Path, image: ImageProvider, *, only_missing: bool = True,
                  ids: Optional[List[str]] = None, regenerate: bool = False) -> Dict[str, Any]:
    """为已有 visual_anchor_en 的角色/地点生成定妆图。

    only_missing=True：跳过 portrait_status 已为 pending/approved 且文件存在的实体；
    regenerate=True：对 ids 指定的实体强制生成新版本（version+1）。
    """
    summary = {"done": [], "failed": [], "skipped": [], "stopped": None}
    style = _art_style(project_dir)
    review = load_review(project_dir)
    joblog.log(project_dir, "assets", f"G-3 定妆图生成开始（{'重新生成所选' if regenerate else '仅缺失项'}）")

    for kind in KINDS:
        data = _load_entities(project_dir, kind)
        for ent in _entity_list(data, kind):
            eid = ent["id"]
            if ids and eid not in ids:
                continue
            if _rt.stop_requested(project_dir):
                summary["stopped"] = "用户请求停止"
                joblog.log(project_dir, "assets", "G-3 已停止（用户请求）", level="warn")
                return summary
            key = entity_key(kind, eid)
            item = get_item(review, key)
            name = (ent.get("names") or [eid])[0] if kind == "character" else ent.get("name", eid)
            if not ent.get("visual_anchor_en"):
                summary["skipped"].append(eid)
                joblog.log(project_dir, "assets", f"跳过 {name}：尚未补全视觉设定", level="warn")
                continue
            asset_exists = bool(ent.get("asset_path")) and (project_dir / ent["asset_path"]).is_file()
            if only_missing and not regenerate and asset_exists and item.get("portrait_status") in (
                    "pending", "approved"):
                summary["skipped"].append(eid)
                joblog.log(project_dir, "assets", f"跳过 {name}（定妆图已存在）")
                continue
            joblog.log(project_dir, "assets", f"G-3 生成定妆图：{name}（{kind}，{eid}）")

            version = int(item.get("portrait_version") or 0)
            if regenerate or not asset_exists or item.get("portrait_status") == "stale":
                version += 1
            rel = _portrait_rel(kind, eid, version)
            prompt = prompts.portrait_prompt(style, ent["visual_anchor_en"], kind=kind,
                                             name=ent.get("name", ""))
            ratio = (CHAR_RATIO if kind == "character" else PROP_RATIO if kind == "prop"
                     else video_format.load(project_dir).aspect_ratio)
            result = image.generate(prompt, project_dir / rel, ratio=ratio)
            log_call(project_dir, {"kind": "image", "provider": image.name, "entity": key,
                                   "ok": result.ok, "error": (result.error or "")[:300] or None})
            review = load_review(project_dir)
            item = get_item(review, key)
            if result.ok:
                ent["asset_path"] = rel
                _save_entities(project_dir, kind, data)
                item.update(portrait_status="approved" if autogate.is_auto(project_dir, "asset_portrait") else "pending",
                            portrait_version=version, last_error=None)
                summary["done"].append(eid)
                joblog.log(project_dir, "assets", f"G-3 完成：{name} → {rel}", level="ok")
            else:
                joblog.log(project_dir, "assets", f"G-3 失败：{name}：{result.error}", level="error")
                item.update(portrait_status="failed", last_error=f"G-3 失败：{result.error}")
                summary["failed"].append({"id": eid, "error": result.error})
                save_review(project_dir, review)
                if result.non_retryable:
                    summary["stopped"] = f"{eid}：{result.error}（参数/鉴权错误，已停止，请修正配置后重跑）"
                    return summary
                continue
            save_review(project_dir, review)
    return summary


# ---------------------------------------------------------------- 审核与人工编辑

def approve(project_dir: Path, kind: str, eid: str, what: str) -> Dict[str, Any]:
    """what: text | portrait。"""
    key = entity_key(kind, eid)
    if what == "text":
        return update_item(project_dir, key, text_status="approved")
    if what == "portrait":
        return update_item(project_dir, key, portrait_status="approved")
    raise StepError(f"未知审核对象：{what}")


def reject_portrait(project_dir: Path, kind: str, eid: str) -> Dict[str, Any]:
    return update_item(project_dir, entity_key(kind, eid), portrait_status="pending")


EDITABLE = {
    "character": {"visual_anchor_en", "voice_profile", "gender", "description_zh"},
    "location": {"visual_anchor_en", "description_zh", "location_type"},
    "prop": {"visual_anchor_en", "description_zh"},
}


def update_entity(project_dir: Path, kind: str, eid: str, fields: Dict[str, Any],
                  note: str = "人工编辑") -> Dict[str, Any]:
    """人工修改实体字段。改动视觉相关字段会把依赖它的定妆图标记为 stale，并重置文本审核。"""
    bad = set(fields) - EDITABLE[kind]
    if bad:
        raise StepError(f"不允许编辑的字段：{', '.join(sorted(bad))}")
    data = _load_entities(project_dir, kind)
    ent = next((e for e in _entity_list(data, kind) if e["id"] == eid), None)
    if ent is None:
        raise StepError(f"实体不存在：{eid}")
    visual_changed = any(ent.get(k) != v for k, v in fields.items()
                         if k in ("visual_anchor_en", "gender", "description_zh", "location_type"))
    for k, v in fields.items():
        if k == "gender" and v not in VALID_GENDER:
            raise StepError("gender 只能是 male / female / unknown")
        ent[k] = v
        # 手动编辑性别等同于人工确认，来源记为 user（门禁只认 user / persona）
        ent[f"{k}_source"] = "user" if k == "gender" else "manual"
    _save_entities(project_dir, kind, data)

    key = entity_key(kind, eid)
    review = load_review(project_dir)
    item = get_item(review, key)
    item.update(text_status="pending" if fields else item.get("text_status"), last_error=None)
    if visual_changed and item.get("portrait_status") in ("pending", "approved"):
        item["portrait_status"] = "stale"
    item["last_note"] = note
    save_review(project_dir, review)
    return item


# ---------------------------------------------------------------- 状态汇总

def visual_status(project_dir: Path) -> Dict[str, Any]:
    review = load_review(project_dir)
    rows = []
    for kind in KINDS:
        data = _load_entities(project_dir, kind)
        for ent in _entity_list(data, kind):
            key = entity_key(kind, ent["id"])
            item = get_item(review, key)
            missing = []
            if not ent.get("visual_anchor_en"):
                missing.append("visual_anchor_en")
            if kind == "character":
                if not ent.get("voice_profile"):
                    missing.append("voice_profile")
                if ent.get("gender_source") not in ("user", "persona") or not ent.get("gender"):
                    missing.append("gender（待人工确认）")
            rows.append({
                "key": key, "kind": kind, "id": ent["id"],
                "name": (ent.get("names") or [ent.get("name", "")])[0] if kind == "character" else ent.get("name"),
                "missing_fields": missing,
                "text_status": item["text_status"],
                "portrait_status": item["portrait_status"],
                "portrait_version": item["portrait_version"],
                "asset_path": ent.get("asset_path"),
                "last_error": item.get("last_error"),
            })
    ready = bool(rows) and all(
        not r["missing_fields"] and r["text_status"] == "approved" and r["portrait_status"] == "approved"
        for r in rows)
    return {"ready": ready, "rows": rows,
            "counts": {"total": len(rows),
                       "approved": sum(1 for r in rows if r["portrait_status"] == "approved"
                                       and r["text_status"] == "approved")}}
