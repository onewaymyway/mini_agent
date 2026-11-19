"""visual/normalize.py — 分镜的规范化（用户编辑保存时使用）。

保证字段齐全、类型合法、焦点在 0..1；条目变化后按镜头的句子范围重算逐步出现的时间点。
第一期起，镜头带 sentences（首末句编号）与 kind（计划中的镜头类型）；没有 sentences 的旧场景覆盖整段。
"""

from __future__ import annotations

from typing import Any, Dict, List

from .director import TEXT_LIMIT
from .shots import KIND_TO_TYPE, TYPE_TO_KIND, reveal_in_range, scene_reveal, shot_range
from .states import SCENE_TYPES


def _valid_range(raw: Any) -> bool:
    return (isinstance(raw, (list, tuple)) and len(raw) == 2
            and all(isinstance(x, str) and ".s" in x for x in raw))


def normalize_scene(raw: Dict[str, Any], sentences: List[Dict[str, Any]]) -> Dict[str, Any]:
    """sentences：该场景所在段落的全部句子（用于句子范围与逐步出现时机）。"""
    sc_type = str(raw.get("type") or "keypoint")
    sc_type = sc_type if sc_type in SCENE_TYPES else "keypoint"
    kind = str(raw.get("kind") or "")
    if KIND_TO_TYPE.get(kind) != sc_type:          # 画面类型被改动：镜头类型跟随画面类型
        kind = TYPE_TO_KIND.get(sc_type, sc_type)
    sc: Dict[str, Any] = {
        "id": str(raw.get("id") or raw.get("beat") or ""),
        "beat": str(raw.get("beat") or raw.get("id") or ""),
        "type": sc_type,
        "kind": kind,
        "sentences": [str(x) for x in raw["sentences"]] if _valid_range(raw.get("sentences")) else [],
        "refs": [str(x) for x in raw.get("refs") or []],
        "items": [str(x).strip()[: TEXT_LIMIT + 20] for x in raw.get("items") or [] if str(x).strip()],
        "emphasis": [str(x) for x in raw.get("emphasis") or [] if str(x)],
        "header": [str(x) for x in raw.get("header") or []],
        "rows": [[str(c) for c in r] for r in raw.get("rows") or [] if isinstance(r, list)],
        "loop": bool(raw.get("loop", False)),
        "formula": str(raw.get("formula") or "").strip(),
        "image": None,
        "reveal": [],
    }
    if sc["type"] == "table" and sc["rows"]:  # 表格的条目由行派生，保证行数与逐行出现一致
        sc["items"] = ["、".join(r) for r in sc["rows"]]
    if sc["type"] == "illustration":
        img = raw.get("image") if isinstance(raw.get("image"), dict) else {}
        try:
            focus = float(img.get("focus_x", 0.5))
        except (TypeError, ValueError):
            focus = 0.5
        sc["image"] = {
            "subject": str(img.get("subject") or "").strip()[:60],
            "scene": str(img.get("scene") or "").strip()[:60],
            "composition": str(img.get("composition") or "").strip()[:60],
            "focus_x": min(1.0, max(0.0, focus)),
            "version": int(img.get("version") or 0),
            "file": img.get("file"),
            "group": img.get("group"),
            "leader": bool(img.get("leader", False)),
        }
    a, b = shot_range(sc, len(sentences))
    texts = [sentences[i]["text"] for i in range(a, b + 1)] if sentences else [""]
    reveal_raw = raw.get("reveal") or []
    if isinstance(reveal_raw, list) and reveal_in_range(dict(sc, reveal=reveal_raw), a, b):
        sc["reveal"] = reveal_raw
    else:
        sc["reveal"] = scene_reveal(sc, texts, start=a)
    return sc
