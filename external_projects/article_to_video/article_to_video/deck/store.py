"""幻灯片规划的存储与状态：parts/<章>/deck.json；输入指纹覆盖本章大纲条目与本章文章块。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import joblog
from ..planning.errors import PlanError
from ..planning.runner import load_spine
from ..project_store import now_iso, read_json, write_json_atomic
from .plan import DECK_VERSION, RETRY_CAPS, plan_slides
from .validate import validate_deck


def deck_home(pdir: Path) -> Path:
    """新方案的根目录：项目下的 deck/，与现有方案的 parts/、exports/ 平级，两者没有包含关系。"""
    return Path(pdir) / "deck"


def deck_root(pdir: Path, part_id: str) -> Path:
    """新方案某章的过程数据：deck/parts/<章>/（规划、讲稿、时间线、配音、视频）。"""
    return deck_home(pdir) / "parts" / part_id


def deck_path(pdir: Path, part_id: str) -> Path:
    return deck_root(pdir, part_id) / "deck.json"


def load_deck(pdir: Path, part_id: str) -> Optional[Dict[str, Any]]:
    p = deck_path(pdir, part_id)
    return read_json(p) if p.is_file() else None


def _part_blocks(pdir: Path, part: Dict[str, Any]) -> List[Dict[str, Any]]:
    src = Path(pdir) / "source" / "blocks.json"
    if not src.is_file():
        raise PlanError("还没有文章结构（源文件未导入）")
    secs = set(part.get("section_ids") or [])
    return [b for b in read_json(src)["blocks"] if b.get("section") in secs]


def _input_sha(part: Dict[str, Any], blocks: List[Dict[str, Any]]) -> str:
    payload = {"version": DECK_VERSION, "part": {k: part.get(k) for k in ("id", "title", "section_ids",
                                                                           "budget_minutes", "one_line")},
               "blocks": blocks}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _part(pdir: Path, part_id: str) -> Dict[str, Any]:
    spine = load_spine(Path(pdir))
    if spine is None or not spine.get("confirmed"):
        raise PlanError("请先确认大纲（C0），再生成幻灯片规划")
    for p in spine["parts"]:
        if p["id"] == part_id:
            return p
    raise PlanError(f"大纲中没有章 {part_id}")


def _plan_with_retry(part: Dict[str, Any], blocks: List[Dict[str, Any]], part_id: str) -> Dict[str, Any]:
    """规划；不合格时自动改用更细的分页重试（RETRY_CAPS），每次失败写入日志。全部失败才抛出 PlanError。"""
    last: List[str] = []
    for i, (pc, ec) in enumerate(RETRY_CAPS, 1):
        deck = plan_slides(part, blocks, part_id, page_cap=pc, element_cap=ec)
        v = validate_deck(deck, blocks, page_cap=pc, element_cap=ec)
        if not v["errors"]:
            deck["attempts"] = i
            return deck
        last = v["errors"]
        nxt = RETRY_CAPS[i][0] if i < len(RETRY_CAPS) else None
        joblog.bound_log(f"{part_id} 幻灯片规划第 {i} 次不合格（每页最多 {pc} 条）：{last[0]}"
                         + (f"；改为每页最多 {nxt} 条，更多分页重试" if nxt else ""), level="warn")
    raise PlanError(f"{part_id} 幻灯片规划经 {len(RETRY_CAPS)} 次分页仍不合格：" + "；".join(last[:4]))


def plan_deck(pdir: Path, part_id: str, *, force: bool = False, now: Optional[datetime] = None) -> Dict[str, Any]:
    """生成本章的幻灯片规划（确定性规则）。输入未变时复用已有结果。校验有错误则不写入。"""
    pdir = Path(pdir)
    part = _part(pdir, part_id)
    blocks = _part_blocks(pdir, part)
    sha = _input_sha(part, blocks)
    existing = load_deck(pdir, part_id)
    if existing and existing.get("input_sha") == sha and not force:
        joblog.bound_log(f"{part_id}：幻灯片规划输入未变，复用已有结果")
        return existing
    deck = _plan_with_retry(part, blocks, part_id)
    v = validate_deck(deck, blocks, page_cap=deck["page_cap"], element_cap=deck["element_cap"])
    deck.update(input_sha=sha, confirmed=False, confirmed_at=None, generated_at=now_iso(now),
                warnings=v["warnings"], errors=[])
    write_json_atomic(deck_path(pdir, part_id), deck)
    joblog.bound_log(f"{part_id}：幻灯片规划已生成（{len(deck['slides'])} 页，约 {deck['total_est_sec'] / 60:.1f} 分钟）")
    return deck


def deck_status(pdir: Path, part_id: str) -> Dict[str, Any]:
    pdir = Path(pdir)
    deck = load_deck(pdir, part_id)
    if deck is None:
        return {"exists": False, "stale": False, "confirmed": False, "slides": 0, "warnings": []}
    try:
        part = _part(pdir, part_id)
        current = _input_sha(part, _part_blocks(pdir, part))
    except PlanError:
        current = None
    return {"exists": True, "stale": current != deck.get("input_sha"), "confirmed": bool(deck.get("confirmed")),
            "slides": len(deck.get("slides") or []), "total_est_sec": deck.get("total_est_sec", 0),
            "warnings": deck.get("warnings") or []}


def confirm_deck(pdir: Path, part_id: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    deck = load_deck(Path(pdir), part_id)
    if deck is None:
        raise PlanError(f"{part_id} 还没有幻灯片规划")
    if deck.get("errors"):
        raise PlanError(f"{part_id} 幻灯片规划有错误，不能确认")
    deck["confirmed"] = True
    deck["confirmed_at"] = now_iso(now)
    write_json_atomic(deck_path(Path(pdir), part_id), deck)
    return deck
