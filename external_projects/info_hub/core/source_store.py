"""信息源的校验、预览、写入与草稿管理（P7）。

流程：
  表单 / LLM 草稿 → validate（格式、分类、适配器、查重）→ preview（小样本抓取）→ confirm（写入 sources/）

草稿存放在 data/drafts/<id>/：
  draft.json  草稿内容与状态（纯文本 json）
  script.py   可选，LLM 生成的脚本（仅草稿阶段，确认后复制到 sources/<id>/）
"""
from __future__ import annotations

import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import yaml

from adapters.base import FetchContext, FetchResult, SourceSpec
from adapters.registry import get_adapter, list_adapters
from core.fingerprint import find_similar
from core.settings import Settings
from core.sources import VALID_KINDS, build_spec, load_sources
from core.store import atomic_write_json, atomic_write_text, read_json

ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
PREVIEW_LIMIT = 10


class SourceError(ValueError):
    """信息源配置或写入失败。"""


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def existing_specs(settings: Settings) -> dict[str, SourceSpec]:
    return load_sources(settings).specs


def _id_taken(settings: Settings, sid: str) -> bool:
    index = read_json(settings.paths.source_index, default={"sources": []}) or {"sources": []}
    if any(e.get("id") == sid for e in index.get("sources", [])):
        return True
    return (settings.paths.sources_dir / sid).exists()


def validate_candidate(
    settings: Settings,
    source: dict[str, Any],
    script_dir: Optional[Path] = None,
) -> tuple[Optional[SourceSpec], list[str], list[str]]:
    """返回 (spec, errors, duplicate_ids)。errors 非空表示不能写入。

    script_dir：需要脚本的源，指向包含 script.py 的目录（草稿目录）。
    """
    errors: list[str] = []
    sid = str(source.get("id", ""))
    if not ID_RE.match(sid):
        errors.append("id 只能包含小写字母、数字与下划线，以字母开头，长度 2–40")
    if sid and _id_taken(settings, sid) and (script_dir is None or script_dir.name != sid):
        errors.append(f"id {sid} 已存在")
    if source.get("category") not in settings.category_ids:
        errors.append(f"分类 {source.get('category')} 未在 config/categories.yaml 中定义")
    if source.get("kind") not in VALID_KINDS:
        errors.append(f"kind 必须是 {sorted(VALID_KINDS)} 之一")
    adapter_name = source.get("adapter")
    adapter = get_adapter(str(adapter_name)) if adapter_name else None
    if adapter is None:
        errors.append(f"未知适配器 {adapter_name}，可用：{', '.join(list_adapters())}")
    if not source.get("name"):
        errors.append("name 不能为空")

    spec = None
    duplicates: list[str] = []
    base_dir = script_dir if script_dir is not None else settings.paths.sources_dir / (sid or "_")
    try:
        spec = build_spec(base_dir, dict(source, id=sid))
    except ValueError as exc:
        errors.append(str(exc))
    if spec is not None and adapter is not None:
        errors.extend(adapter.validate(spec))
        duplicates = find_similar(existing_specs(settings), source)
    return spec, errors, duplicates


def write_source(settings: Settings, source: dict[str, Any], script_text: Optional[str] = None) -> Path:
    sid = source["id"]
    target = settings.paths.sources_dir / sid
    if target.exists():
        raise SourceError(f"目录 {target.name} 已存在")
    target.mkdir(parents=True)
    atomic_write_text(target / "source.yaml", yaml.safe_dump(source, allow_unicode=True, sort_keys=False))
    if script_text is not None:
        atomic_write_text(target / "script.py", script_text)
    index = read_json(settings.paths.source_index, default={"version": 1, "sources": []}) or {"sources": []}
    index.setdefault("sources", []).append({"id": sid, "enabled": bool(source.get("enabled", True))})
    atomic_write_json(settings.paths.source_index, index)
    return target


def preview_spec(spec: SourceSpec, ctx: FetchContext) -> dict[str, Any]:
    adapter = get_adapter(spec.adapter)
    if adapter is None:
        return {"ok": False, "error": f"未知适配器 {spec.adapter}", "items": []}
    res: FetchResult = adapter.fetch(spec, ctx)
    return {
        "ok": res.ok,
        "error": res.error,
        "total": len(res.items),
        "items": [{"title": i.title, "url": i.url, "heat": i.heat, "rank": i.rank}
                  for i in res.items[:PREVIEW_LIMIT]],
    }


# ---------- 草稿 ----------
def drafts_root(settings: Settings) -> Path:
    return settings.paths.data_dir / "drafts"


def draft_dir(settings: Settings, draft_id: str) -> Path:
    if not ID_RE.match(draft_id):
        raise SourceError("非法的草稿 id")
    return drafts_root(settings) / draft_id


def save_draft(settings: Settings, draft: dict[str, Any]) -> None:
    draft["updated_at"] = now_iso()
    atomic_write_json(draft_dir(settings, draft["id"]) / "draft.json", draft)


def load_draft(settings: Settings, draft_id: str) -> dict[str, Any]:
    data = read_json(draft_dir(settings, draft_id) / "draft.json", default=None)
    if data is None:
        raise FileNotFoundError(f"草稿 {draft_id} 不存在")
    return data


def list_drafts(settings: Settings) -> list[dict[str, Any]]:
    root = drafts_root(settings)
    if not root.exists():
        return []
    out = []
    for path in sorted(root.glob("*/draft.json"), reverse=True):
        d = read_json(path, default=None)
        if d:
            out.append({k: d.get(k) for k in ("id", "name", "url", "status", "updated_at", "adapter", "revisions_count")})
    return out


def discard_draft(settings: Settings, draft_id: str) -> None:
    import shutil
    target = draft_dir(settings, draft_id)
    if target.exists():
        shutil.rmtree(target)


def draft_script_path(settings: Settings, draft_id: str) -> Path:
    return draft_dir(settings, draft_id) / "script.py"


def unique_id(settings: Settings, base: str, reserved: set[str]) -> str:
    base = re.sub(r"[^a-z0-9_]", "_", base.lower()).strip("_") or "source"
    if not base[0].isalpha():
        base = "src_" + base
    if len(base) < 2:
        base = "src_" + base
    base = base[:36]
    candidate, n = base, 2
    while _id_taken(settings, candidate) or candidate in reserved or (drafts_root(settings) / candidate).exists():
        candidate = f"{base}_{n}"
        n += 1
    return candidate

# 表单管理的字段；其余字段（请求头、date_pattern、url_template、time_format 等）编辑时原样保留
FORM_FIELD_KEYS = ("title", "url")


def merge_source(existing: dict[str, Any], form: dict[str, Any]) -> dict[str, Any]:
    """以现有 source.yaml 为底，覆盖表单管理的字段，保留表单未涉及的配置。"""
    import copy
    out = copy.deepcopy(existing)
    for key in ("name", "category", "kind", "adapter", "tags", "enabled"):
        if key in form:
            out[key] = form[key]
    out["request"] = {**(existing.get("request") or {}), "url": form["request"]["url"]}
    out["policy"] = {**(existing.get("policy") or {}), "max_items": form["policy"]["max_items"]}
    out["schedule"] = {**(existing.get("schedule") or {}), "every": form["schedule"]["every"]}

    if "parse" in form:
        same_adapter = existing.get("adapter") == form.get("adapter")
        base = dict(existing.get("parse") or {}) if same_adapter else {}
        parse = {k: v for k, v in base.items() if k not in ("list_selector", "items_path", "fields")}
        # 选择器只保留当前适配器对应的键
        for key in ("list_selector", "items_path"):
            if key in form["parse"]:
                parse[key] = form["parse"][key]
        fields = dict(base.get("fields") or {})
        for key in FORM_FIELD_KEYS:
            value = (form["parse"].get("fields") or {}).get(key, "")
            if value:
                fields[key] = value
            else:
                fields.pop(key, None)
        parse["fields"] = fields
        out["parse"] = parse

    if form.get("browser"):
        out["browser"] = {**(existing.get("browser") or {}), **form["browser"]}
    else:
        out.pop("browser", None)
    return out


def update_source_file(settings: Settings, sid: str, source: dict[str, Any]) -> Path:
    """覆盖写入 source.yaml，并同步索引中的启用状态。"""
    target = settings.paths.sources_dir / sid / "source.yaml"
    if not target.exists():
        raise SourceError(f"未找到信息源 {sid}")
    atomic_write_text(target, yaml.safe_dump(source, allow_unicode=True, sort_keys=False))
    index = read_json(settings.paths.source_index, default={"version": 1, "sources": []}) or {"sources": []}
    for entry in index.get("sources", []):
        if entry.get("id") == sid:
            entry["enabled"] = bool(source.get("enabled", True))
    atomic_write_json(settings.paths.source_index, index)
    return target
