"""visual/normalize.py — 分镜的规范化（用户编辑保存时使用）。

保证字段齐全、类型合法、焦点在 0..1；条目变化后按句子均匀重算逐步出现的时间点。
"""

from __future__ import annotations

from typing import Any, Dict, List

from .director import TEXT_LIMIT
from .states import SCENE_TYPES, expected_state_count, uniform_reveal


def normalize_scene(raw: Dict[str, Any], sentences: List[Dict[str, Any]]) -> Dict[str, Any]:
    kind = str(raw.get("type") or "keypoint")
    sc = {
        "id": str(raw.get("id") or raw.get("beat") or ""),
        "beat": str(raw.get("beat") or raw.get("id") or ""),
        "type": kind if kind in SCENE_TYPES else "keypoint",
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
    if sc["type"] == "table":  # 表格的条目由行派生，不接受单独编辑，保证行数与逐行出现一致
        sc["items"] = ["、".join(r) for r in sc["rows"]]
    if sc["type"] == "illustration":
        img = raw.get("image") if isinstance(raw.get("image"), dict) else {}
        try:
            focus = float(img.get("focus_x", 0.5))
        except (TypeError, ValueError):
            focus = 0.5
        sc["image"] = {
            "subject": str(img.get("subject") or "").strip()[:60],
            "focus_x": min(1.0, max(0.0, focus)),
            "version": int(img.get("version") or 0),
            "file": img.get("file"),
        }
    reveal_raw = raw.get("reveal") or []
    sc["reveal"] = reveal_raw if _reveal_ok(reveal_raw, sc, sentences) else uniform_reveal(sc, len(sentences))
    return sc


def _reveal_ok(reveal: List[Dict[str, Any]], sc: Dict[str, Any], sentences: List[Dict[str, Any]]) -> bool:
    if len(reveal) != expected_state_count(sc):
        return False
    prev = -1
    for i, r in enumerate(reveal):
        k = r.get("at_sentence") if isinstance(r, dict) else None
        if not isinstance(k, int) or k < 0 or k >= len(sentences) or k < prev or r.get("step") != i:
            return False
        prev = k
    return True
