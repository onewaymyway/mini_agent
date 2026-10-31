"""步骤边界守卫：防止 agent 越过当前步骤去处理后面的步骤。

三层约束（见 runner / entrypoints/check_step.py）：
1. 绑定：执行 skill 步骤期间，`_studio/current_step.json` 记录当前步骤 id。
   自检入口只允许检查这个步骤（或已完成的步骤做复查）。
2. 轮次：未完成的步骤必须是流水线的 frontier（第一个未完成步骤），否则拒绝自检。
3. 越界检测 + 自动还原：步骤执行前备份「后续章节步骤的产出文件」的原始内容，
   执行后若被本步骤改动或新建，由框架直接还原（改回原内容 / 删除新建文件）。
   不能只靠提示让 agent 自己还原：agent 手里没有改前的内容，只会反复失败。
   可用设置 `step_guard_auto_restore=false` 关闭，退回「只报告、交给 agent 处理」。
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


# 共享存储：所有章节（过去与未来）都读取的全局状态。封存的章节也不会锁死它们，
# 否则幕末的人物更新、伏笔同步等正常工作会被误挡。封存只保护章节私有产物。
_SHARED_OUTPUT_KEYS = ("characters_dir",)


def _sealed_forbidden(ws: Any, plan: Sequence[Any], step: Any) -> Set[str]:
    """全局步骤（幕末 / 规划 / 设计）对已封存章节私有产物的只读约束。"""
    sealed = st.sealed_upto(plan, st.load_state(ws)["steps"])
    if sealed <= 0:
        return set()
    from novel_writer.steps import rel as _rel

    shared = tuple(_rel(k) for k in _SHARED_OUTPUT_KEYS)
    protected = [
        _pattern(x)
        for s in plan
        if 1 <= s.chapter <= sealed
        for x in s.outputs
        if not _pattern(x).startswith(shared)
    ]
    own = [_pattern(x) for x in getattr(step, "outputs", ())]
    candidates = _existing(ws, protected)
    return {rel for rel in candidates if not _matches(rel, own)}


def forbidden_rels(ws: Any, plan: Sequence[Any], step: Any) -> Set[str]:
    """本步骤不应改动的文件（当前在盘上存在的部分）。

    规则：
      * 章节步骤：属于「更后章节」步骤产出的路径，且不匹配任何不晚于本章步骤的产出路径。
      * 全局步骤（幕末 / 规划 / 设计，chapter=0）：已封存章节的私有产物（见 _sealed_forbidden）。
    按模式匹配而不是按已有文件枚举，所以本步骤新建的后续章节文件也能被识别。
    """
    if not getattr(step, "chapter", 0):
        return _sealed_forbidden(ws, plan, step)
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


def backup(ws: Any, rels: Iterable[str]) -> Dict[str, Optional[bytes]]:
    """备份受保护文件的原始内容（不存在记为 None）。受保护文件只有后续章节正文等，体积很小。"""
    out: Dict[str, Optional[bytes]] = {}
    for rel in rels:
        path = ws.root / rel
        try:
            out[rel] = path.read_bytes() if path.is_file() else None
        except OSError:
            out[rel] = None
    return out


def restore(ws: Any, violations: Iterable[str], saved: Dict[str, Optional[bytes]]) -> List[str]:
    """把越界改动还原：有备份则写回原内容，原本不存在则删除。返回仍未能还原的相对路径。

    只处理 `saved` 里有记录的文件；`violations` 里没有备份的（步骤中途新出现的后续章节文件，
    其备份为 None 即「原本不存在」）也按「删除」处理，因为基线里它们就是 MISSING。
    """
    failed: List[str] = []
    for rel in violations:
        path = ws.root / rel
        original = saved.get(rel)
        try:
            if original is None:
                if path.is_file():
                    path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_name(path.name + ".sgtmp")
                tmp.write_bytes(original)
                tmp.replace(path)
        except OSError:
            failed.append(rel)
    return failed


def protected_listing(rels: Iterable[str], limit: int = 12) -> str:
    """写进 prompt 的受保护文件清单（一行一个），超出部分只给数量。"""
    items = sorted(rels)
    lines = [f"- `{r}`" for r in items[:limit]]
    if len(items) > limit:
        lines.append(f"- …另有 {len(items) - limit} 个后续章节文件")
    return "\n".join(lines)
