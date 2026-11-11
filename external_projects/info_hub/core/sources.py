"""信息源注册表加载与校验。

_index.json 只记录 id 与启用状态；每个信息源的完整配置在 sources/<id>/source.yaml。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from adapters.base import SourceSpec
from adapters.registry import get_adapter
from core.settings import Settings
from core.store import read_json

VALID_KINDS = {"hot_list", "feed", "recommend", "subscription", "custom"}


@dataclass
class LoadResult:
    specs: dict[str, SourceSpec] = field(default_factory=dict)
    errors: dict[str, list[str]] = field(default_factory=dict)


def _read_source_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError("source.yaml 顶层必须是映射")
    return data


def _as_dict(value: Any, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{name} 必须是映射")
    return value


def build_spec(source_dir: Path, raw: dict[str, Any]) -> SourceSpec:
    """从 source.yaml 内容构造 SourceSpec，缺字段时抛出 ValueError。"""
    for key in ("id", "name", "category", "kind", "adapter"):
        if not raw.get(key):
            raise ValueError(f"缺少必填字段 {key}")
    tags = raw.get("tags") or []
    if not isinstance(tags, list):
        raise ValueError("tags 必须是列表")
    return SourceSpec(
        id=str(raw["id"]),
        name=str(raw["name"]),
        category=str(raw["category"]),
        kind=str(raw["kind"]),
        adapter=str(raw["adapter"]),
        enabled=bool(raw.get("enabled", True)),
        request=_as_dict(raw.get("request"), "request"),
        parse=_as_dict(raw.get("parse"), "parse"),
        policy=_as_dict(raw.get("policy"), "policy"),
        schedule=_as_dict(raw.get("schedule"), "schedule"),
        browser=_as_dict(raw.get("browser"), "browser"),
        tags=[str(t) for t in tags],
        dir=source_dir,
        raw=raw,
    )


def check_spec(spec: SourceSpec, settings: Settings, index_id: str | None) -> list[str]:
    """语义校验：id 一致性、分类存在、kind 合法、适配器存在且自校验通过。"""
    errs: list[str] = []
    if index_id is not None and index_id != spec.id:
        errs.append(f"_index.json 中 id={index_id} 与 source.yaml 中 id={spec.id} 不一致")
    if spec.dir.name != spec.id:
        errs.append(f"目录名 {spec.dir.name} 必须与 id={spec.id} 一致")
    if spec.category not in settings.category_ids:
        errs.append(f"分类 {spec.category} 未在 config/categories.yaml 中定义")
    if spec.kind not in VALID_KINDS:
        errs.append(f"kind={spec.kind} 不合法，应为 {sorted(VALID_KINDS)} 之一")
    adapter = get_adapter(spec.adapter)
    if adapter is None:
        errs.append(f"未找到适配器 {spec.adapter}")
    else:
        errs.extend(adapter.validate(spec))
    return errs


def load_sources(settings: Settings) -> LoadResult:
    """读取注册表并逐个加载、校验。单个源出错不影响其他源。"""
    result = LoadResult()
    index = read_json(settings.paths.source_index, default={"sources": []}) or {}
    entries = index.get("sources", [])
    for entry in entries:
        sid = str(entry.get("id", ""))
        if not sid:
            continue
        source_dir = settings.paths.sources_dir / sid
        yaml_path = source_dir / "source.yaml"
        if not yaml_path.exists():
            result.errors[sid] = [f"缺少 {yaml_path.name}"]
            continue
        try:
            spec = build_spec(source_dir, _read_source_yaml(yaml_path))
        except (ValueError, yaml.YAMLError) as exc:
            result.errors[sid] = [str(exc)]
            continue
        spec.enabled = bool(entry.get("enabled", spec.enabled))
        errs = check_spec(spec, settings, index_id=sid)
        if errs:
            result.errors[sid] = errs
        else:
            result.specs[sid] = spec
    return result
