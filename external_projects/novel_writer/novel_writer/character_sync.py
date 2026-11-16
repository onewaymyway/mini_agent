"""人物档案的一致性：人设卡、角色档案（角色/{id}.md）、角色索引三处必须对得上。

依赖人物的步骤（章节简述、写作等）开始前调用 ensure()：
* 角色索引与档案对不上（只是索引过期）→ 确定性刷新索引，无需人工。
* 人设卡里有、档案里没有 → 无法自动建档（需要模型按人设卡写出结构化字段），直接报错并点名缺少的角色与要运行的步骤。
  这类错误带 no_retry，流水线不会自动重跑（重跑只会得到同样的结果）。
* 档案文件本身损坏 → 同样报错并点名文件。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from novel_writer import character_store as CST

CARD_DIR_REL = "大纲规划/人物设定/人设卡"
BUILD_STEP = "build_characters"          # 「建立人物档案」步骤：人设卡 → 角色档案


class CharacterSyncError(RuntimeError):
    no_retry = True                      # 确定性错误：worker 不自动重跑


def _card_names(ws) -> List[str]:
    d = Path(ws.root) / CARD_DIR_REL
    return sorted({p.stem for p in d.glob("*.md")}) if d.is_dir() else []


def _index_ids(index_path: Path) -> set:
    """索引表格里列出的角色 id（第一列）。"""
    if not index_path.is_file():
        return set()
    ids = set()
    for line in index_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("| ") and not line.startswith("| id") and not line.startswith("|---"):
            cell = line.strip("|").split("|")[0].strip()
            if cell:
                ids.add(cell)
    return ids


def audit(ws) -> Dict[str, Any]:
    """只检查，不修改。返回三处的数量与不一致的明细。"""
    base, index = Path(ws.path("characters_dir")), Path(ws.path("characters"))
    chars, problems = CST.load_records(base)
    cards = _card_names(ws)
    store_names = {str(r.get("name") or "").strip() for r in chars.values()} - {""}
    return {
        "cards": cards,
        "store_count": len(chars),
        "index_rows": len(_index_ids(index)),
        "missing_in_store": [c for c in cards if c not in store_names],
        "orphan_in_store": sorted(n for n in store_names if cards and n not in cards),
        "stale_index": _index_ids(index) != set(chars.keys()),
        "problems": problems,
    }


def ensure(ws) -> Dict[str, Any]:
    """依赖人物的步骤开始前调用。能确定性修复的直接修复；不能修复的抛 CharacterSyncError。"""
    rep = audit(ws)
    if rep["problems"]:
        raise CharacterSyncError("人物档案有损坏的文件，无法自动修复：" + "；".join(rep["problems"][:6])
                                 + "。请修复或删除这些文件后，再运行「建立人物档案」。")
    if rep["missing_in_store"]:
        raise CharacterSyncError(
            f"人物档案未同步：人设卡 {len(rep['cards'])} 个，角色档案 {rep['store_count']} 个，"
            f"缺少：{'、'.join(rep['missing_in_store'][:12])}"
            f"{'…' if len(rep['missing_in_store']) > 12 else ''}。"
            f"处理：先运行「{BUILD_STEP}」（建立人物档案）步骤把全部人设卡建档，再重跑本步骤。")
    if rep["stale_index"]:
        CST.refresh(Path(ws.path("characters_dir")), Path(ws.path("characters")))   # 确定性：只重建索引
        rep = audit(ws)
        rep["refreshed_index"] = True
    return rep


def names(ws) -> List[str]:
    """人物姓名（以档案中的 name 为准；没有档案时退回人设卡名字）。"""
    chars, _ = CST.load_records(Path(ws.path("characters_dir")))
    out = sorted({str(r.get("name") or "").strip() for r in chars.values()} - {""})
    return out or _card_names(ws)


def id_to_name(ws) -> Dict[str, str]:
    """角色 id → 姓名，用于把模型写出的 id（如 char_002）还原为姓名。"""
    chars, _ = CST.load_records(Path(ws.path("characters_dir")))
    return {str(k): str(r.get("name") or "").strip() for k, r in chars.items() if str(r.get("name") or "").strip()}
