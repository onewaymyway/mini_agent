"""新方案每章各阶段的状态（界面与测试共用，纯函数）。

状态取值：
  ❌ 失败      最近一次该阶段运行失败（原因写在 detail）；
  ⬜ 未生成    还没有产物；
  ⚠️ 已过期    产物存在，但依赖的上游内容已变化，需要重新生成；
  🟡 待确认    已生成，等待确认；
  ✅ 已确认    已确认（或合成完成）。
失败记录保存在 deck/errors.json：{阶段: {章ID: 原因}}；该章该阶段运行成功后清除。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from ..project_store import read_json, write_json_atomic
from .script import script_status
from .store import deck_home, deck_status
from .voice import voice_status

STAGES = ("plan", "script", "voice", "compose")
LABEL = {"plan": "幻灯片规划", "script": "讲稿", "voice": "配音", "compose": "合成"}


def _errors_path(pdir: Path) -> Path:
    return deck_home(Path(pdir)) / "errors.json"


def load_errors(pdir: Path) -> Dict[str, Dict[str, str]]:
    p = _errors_path(Path(pdir))
    return read_json(p) if p.is_file() else {}


def record_error(pdir: Path, stage: str, part_id: str, message: Optional[str]) -> None:
    """记录（message 为原因）或清除（message 为 None）某章某阶段的失败。"""
    pdir = Path(pdir)
    errs = load_errors(pdir)
    bucket = errs.setdefault(stage, {})
    if message:
        bucket[part_id] = message
    else:
        bucket.pop(part_id, None)
    deck_home(pdir).mkdir(parents=True, exist_ok=True)
    write_json_atomic(_errors_path(pdir), errs)


def _compose_state(pdir: Path, part_id: str) -> Dict[str, Any]:
    from .compose import deck_part_ready
    info = deck_part_ready(pdir, part_id)
    if info is None:
        return {"exists": False, "stale": False, "confirmed": False}
    deck = deck_status(pdir, part_id)
    stale = bool(deck["exists"] and deck["stale"]) or info.get("deck_sha") != _deck_sha(pdir, part_id)
    return {"exists": True, "stale": stale, "confirmed": not stale}


def _deck_sha(pdir: Path, part_id: str) -> Optional[str]:
    from .store import load_deck
    d = load_deck(Path(pdir), part_id)
    return d.get("input_sha") if d else None


def part_state(pdir: Path, stage: str, part_id: str) -> Dict[str, str]:
    """某章某阶段的状态：{label, detail}。"""
    pdir = Path(pdir)
    err = load_errors(pdir).get(stage, {}).get(part_id)
    if stage == "plan":
        s = deck_status(pdir, part_id)
        base = {"exists": s["exists"], "stale": s["stale"], "confirmed": s["confirmed"]}
    elif stage == "script":
        s = script_status(pdir, part_id)
        base = {"exists": s["exists"], "stale": s["stale"], "confirmed": s["confirmed"]}
    elif stage == "voice":
        s = voice_status(pdir, part_id)
        base = {"exists": s["exists"], "stale": s["stale"], "confirmed": s["exists"] and not s["stale"]}
    else:
        base = _compose_state(pdir, part_id)
    if err and not (base["exists"] and not base["stale"] and base.get("confirmed")):
        return {"label": "❌ 失败", "detail": err}
    if not base["exists"]:
        return {"label": "⬜ 未生成", "detail": ""}
    if base["stale"]:
        return {"label": "⚠️ 已过期", "detail": "上游内容已变化，请重新生成"}
    if base["confirmed"]:
        return {"label": "✅ 已确认" if stage != "compose" else "✅ 已合成", "detail": ""}
    return {"label": "🟡 待确认", "detail": ""}


def stage_summary(pdir: Path, stage: str, part_ids) -> str:
    done = sum(1 for pid in part_ids if part_state(pdir, stage, pid)["label"] in ("✅ 已确认", "✅ 已合成"))
    return f"{done}/{len(list(part_ids))} 章完成"
