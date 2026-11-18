"""visual/sharing.py — 一图多用：插图按组分配，同组镜头共用一张生成图，只用不同取景区分。

规则（纯函数，确定性）：
  * 章内插图场景按顺序编号 j = 0..n-1，分为 G = ceil(n / shots) 组，组号 g = j mod G（轮转）。
    轮转分组使同一组的镜头在章内相隔约 G 段，避免相邻两段看起来像同一张图。
  * 组内第一个镜头为主图（leader）：只有主图触发生图；其余镜头复用主图文件。
  * 取景：组内 k 个镜头按 FOCUS[k] 取水平位置（左、中、右），奇数组反向，使相邻组的运镜方向不同。
  * 生图次数 = 组数 G ≤ 本章额度，与原来“每个插图场景一张图”相比，额度可多出 shots 倍镜头。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List

FOCUS = {1: [0.5], 2: [0.2, 0.8], 3: [0.12, 0.5, 0.88]}
MAX_SHOTS = 3


def _focus_for(k: int, group_index: int) -> List[float]:
    base = FOCUS.get(k) or [round(0.1 + 0.8 * i / max(1, k - 1), 3) for i in range(k)]
    return list(reversed(base)) if group_index % 2 == 1 else base


def allocate_shared_images(scenes: List[Dict[str, Any]], shots_per_image: int) -> List[str]:
    """原地为插图场景写入 image.group / image.leader / image.focus_x。返回组号列表（按出现顺序）。"""
    shots = max(1, min(MAX_SHOTS, int(shots_per_image)))
    idx = [i for i, s in enumerate(scenes) if s.get("type") == "illustration"]
    for i in idx:
        scenes[i].setdefault("image", {"subject": "", "focus_x": 0.5, "version": 0, "file": None})
        scenes[i]["image"]["group"] = None
        scenes[i]["image"]["leader"] = False
    if not idx:
        return []
    n = len(idx)
    groups = math.ceil(n / shots)
    members: Dict[int, List[int]] = {g: [] for g in range(groups)}
    for j, i in enumerate(idx):
        members[j % groups].append(i)
    for g in range(groups):
        gid = f"G{g + 1:02d}"
        ms = members[g]
        pos = _focus_for(len(ms), g)
        for m, i in enumerate(ms):
            img = scenes[i]["image"]
            img["group"] = gid
            img["leader"] = m == 0
            img["focus_x"] = pos[m]
    return [f"G{g + 1:02d}" for g in range(groups)]


def count_groups(scenes: List[Dict[str, Any]]) -> int:
    """真正需要生成的图片数：有组号的按组计；无组号的插图每个算一张。"""
    keys = set()
    for s in scenes:
        if s.get("type") == "illustration":
            keys.add((s.get("image") or {}).get("group") or s["id"])
    return len(keys)


def leader_subjects(scenes: List[Dict[str, Any]]) -> Dict[str, str]:
    """组号 → 主图的画面主题（用于缓存指纹：主图主题变化，组内镜头都要重画）。"""
    out: Dict[str, str] = {}
    for s in scenes:
        img = s.get("image") or {}
        if s.get("type") == "illustration" and img.get("group") and img.get("leader"):
            out[img["group"]] = str(img.get("subject") or "")
    return out
