"""步骤边界守卫：防止 agent 越过当前步骤去处理后面的步骤。

三层约束（见 runner / entrypoints/check_step.py）：
1. 绑定：执行 skill 步骤期间，`_studio/current_step.json` 记录当前步骤 id。
   自检入口只允许检查这个步骤（或已完成的步骤做复查）。
2. 轮次：未完成的步骤必须是流水线的 frontier（第一个未完成步骤），否则拒绝自检。
3. 越界检测：步骤执行前后对比「后续章节步骤的产出文件」的内容指纹，
   若被本步骤改动或新建，则作为问题交给 agent 还原（不自动覆盖，避免误伤）。
   与其它步骤（包括本章更早的步骤、全局步骤）共享的产出不计入越界，
   例如 `characters/*.md` 这类跨章共享的 glob。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from novel_writer import state as st
from novel_writer.fsutil import atomic_write_text, read_json

BIND_FILE = "current_step.json"
MISSING = "MISSING"


def _bind_path(ws: Any) -> Path:
    return ws.studio / BIND_FILE


def bind_current_step(ws: Any, step_id: str, run_no: int) -> None:
    """执行 skill 步骤前调用：告诉自检入口当前只允许处理 step_id。"""
    path = _bind_path(ws)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps({"step_id": step_id, "run": run_no}, ensure_ascii=False))


def unbind_current_step(ws: Any) -> None:
    try:
        _bind_path(ws).unlink()
    except FileNotFoundError:
        pass


def bound_step_id(ws: Any) -> Optional[str]:
    data = read_json(_bind_path(ws), default=None)
    if isinstance(data, dict) and isinstance(data.get("step_id"), str):
        return data["step_id"]
    return None


def _pattern(spec: str) -> str:
    return spec[len("glob:"):] if spec.startswith("glob:") else spec


def _matches(rel: str, patterns: Iterable[str]) -> bool:
    path = Path(rel)
    return any(path.match(p) for p in patterns)


def _existing(ws: Any, patterns: Iterable[str]) -> Set[str]:
    out: Set[str] = set()
    for pat in patterns:
        if "*" in pat:
            matches = ws.root.glob(pat)
        else:
            matches = [ws.root / pat]
        for path in matches:
            if path.is_file():
                out.add(path.relative_to(ws.root).as_posix())
    return out


def forbidden_rels(ws: Any, plan: Sequence[Any], step: Any) -> Set[str]:
    """本步骤不应改动的文件（当前在盘上存在的部分）。

    规则：属于「更后章节」步骤产出的路径，且不匹配任何不晚于本章步骤的产出路径。
    按模式匹配而不是按已有文件枚举，所以本步骤新建的后续章节文件也能被识别。
    全局步骤（chapter=0）不参与越界检测。
    """
    if not getattr(step, "chapter", 0):
        return set()
    later = [_pattern(x) for s in plan if s.chapter > step.chapter for x in s.outputs]
    shared = [_pattern(x) for s in plan if s.chapter <= step.chapter for x in s.outputs]
    if not later:
        return set()
    candidates = _existing(ws, later)
    return {rel for rel in candidates if not _matches(rel, shared)}


def snapshot(ws: Any, rels: Iterable[str]) -> Dict[str, str]:
    return {r: st._file_digest(ws.root / r) for r in rels}


def boundary_violations(ws: Any, plan: Sequence[Any], step: Any, before: Dict[str, str]) -> List[str]:
    """对比执行前的快照，返回被本步骤改动或新建的后续章节文件（相对路径，已排序）。"""
    after_rels = forbidden_rels(ws, plan, step)
    changed: Set[str] = set()
    for rel in after_rels | set(before):
        if st._file_digest(ws.root / rel) != before.get(rel, MISSING):
            changed.add(rel)
    return sorted(changed)


def step_guard_problem(ws: Any, plan: Sequence[Any], statuses: Dict[str, str], step_id: str) -> Optional[str]:
    """自检入口用：执行 skill 步骤期间，只允许检查当前绑定的步骤（已完成步骤可复查）。

    没有绑定（人工在命令行自检）时不做限制——守卫针对的是 agent 在会话里越过当前步骤。
    """
    from novel_writer.state import FINISHED_STATUSES

    bound = bound_step_id(ws)
    if bound is None or bound == step_id:
        return None
    if statuses.get(step_id) in FINISHED_STATUSES:
        return None
    return (f"当前正在执行的是「{bound}」，不能检查或处理「{step_id}」。"
            "请只完成当前步骤，不要去做其它步骤。")
