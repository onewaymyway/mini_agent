"""novel_writer/pipeline.py — novel-director 的代码化：阶段展开 + 当前前沿（方案 4.2 / 4.3）。

流水线在 M1 是**线性**的：按顺序执行，前沿 = 第一个未完成的步骤。
（方案中的并行/分支（如人设设计）仍按串行执行，见方案 4.2 表格。）

展开依赖蓝图：
  * 主要角色 = 在 `chapters[].characters_present` 中出现 ≥ 3 章的角色（planner 的交接协议）；
  * 章节数 = `structure.total_chapters`，缺失时退回 `workspace_config.total_chapters_planned`。
蓝图不存在时只展开 P0/P1 的确定性部分，章节循环为空。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from novel_writer import gates as G
from novel_writer import steps as S
from novel_writer.fsutil import read_json
from novel_writer.state import FINISHED_STATUSES, ST_PENDING
from novel_writer.workspace import Workspace

MAIN_CHARACTER_MIN_CHAPTERS = 3

_NAME_SUFFIX_RE = re.compile(r"[（(].*$")


def _clean_name(raw: Any) -> str:
    return _NAME_SUFFIX_RE.sub("", str(raw or "")).strip()


def main_characters(blueprint: Optional[Dict[str, Any]]) -> List[str]:
    """出现在 ≥ 3 章 `characters_present` 中的角色名，按首次出现顺序。"""
    if not isinstance(blueprint, dict):
        return []
    counts: Dict[str, int] = {}
    order: List[str] = []
    for chapter in blueprint.get("chapters") or []:
        if not isinstance(chapter, dict):
            continue
        seen_in_chapter = set()
        for raw in chapter.get("characters_present") or []:
            name = _clean_name(raw)
            if not name or name in seen_in_chapter:
                continue
            seen_in_chapter.add(name)
            if name not in counts:
                order.append(name)
            counts[name] = counts.get(name, 0) + 1
    return [n for n in order if counts[n] >= MAIN_CHARACTER_MIN_CHAPTERS]


def total_chapters(ws: Workspace, blueprint: Optional[Dict[str, Any]]) -> int:
    if isinstance(blueprint, dict):
        n = (blueprint.get("structure") or {}).get("total_chapters")
        if isinstance(n, int) and n > 0:
            return n
    cfg = ws.workspace_config()
    n = cfg.get("total_chapters_planned")
    return int(n) if isinstance(n, int) and n > 0 else 0


def build_plan(ws: Workspace) -> List[S.StepDef]:
    blueprint = read_json(ws.path("blueprint"), default=None)
    roles = main_characters(blueprint)
    total = total_chapters(ws, blueprint)

    plan: List[S.StepDef] = [
        S.init_workspace_step(),
        S.plan_blueprint_step(),
        S.design_theme_step(),
        S.design_pacing_step(),
    ]
    plan += [S.design_character_step(name) for name in roles]
    plan += [
        S.build_characters_step(roles),
        S.build_world_step(),
        S.register_plot_step(),
        S.plot_import_step(),
        S.init_word_count_step(),
    ]
    simulate = S.simulate_blueprint_step()
    plan.append(_optional(simulate))
    plan.append(S.design_opening_step())

    quality = ws.settings().get("quality", "standard")
    last = plan[-1].id
    act_ends = _act_ends(blueprint, total)
    ending_at = _ending_chapter(blueprint, total)
    for n in range(1, total + 1):
        if n == ending_at:
            # 结局设计（M6）：进入末幕前执行（方案 4.2）
            ending = _with_deps(S.ending_design_step(), (last,))
            plan.append(ending)
            last = ending.id
        chapter = S.chapter_steps(n, "", total=total, prev_last=last, quality=quality)
        plan += chapter
        last = chapter[-1].id
        if n == S.OPENING_CHAPTERS and total >= S.OPENING_CHAPTERS:
            confirm = S.confirm_first3_step()
            confirm = _with_deps(confirm, (last,))
            plan.append(confirm)
            style = S.init_style_b_step()
            plan.append(style)
            last = style.id
        if n in act_ends:
            acts = S.act_steps(act_ends[n])
            plan += acts
            last = acts[-1].id
        if n % SNAPSHOT_INTERVAL == 0:
            plan.append(S.snapshot_step(n))
        if quality == S.PREMIUM and n % S.PATROL_INTERVAL == 0:
            plan.append(_with_deps(S.patrol_step(n), (last,)))
            last = plan[-1].id
    if total:
        # 全书终审（M6，方案 4.2 收尾）：所有章节与幕末之后
        plan.append(_with_deps(S.final_review_step(), (last,)))
    from novel_writer import ondemand

    plan += ondemand.ondemand_steps(ws)
    return plan


SNAPSHOT_INTERVAL = 10


def _act_ends(blueprint: Optional[Dict[str, Any]], total: int) -> Dict[int, int]:
    """幕结束章号 -> 幕号（取自 structure.acts[].chapter_range）。"""
    out: Dict[int, int] = {}
    acts = ((blueprint or {}).get("structure") or {}).get("acts") or []
    for a in acts:
        if not isinstance(a, dict):
            continue
        rng = a.get("chapter_range") or []
        act_no = a.get("act")
        if isinstance(act_no, int) and len(rng) == 2 and isinstance(rng[1], int) and 1 <= rng[1] <= total:
            out[rng[1]] = act_no
    return out


def _ending_chapter(blueprint: Optional[Dict[str, Any]], total: int) -> int:
    """结局设计插入的位置：最后一幕的第一章；没有幕结构时为最后一章。"""
    if total <= 0:
        return 0
    acts = [a for a in (((blueprint or {}).get("structure") or {}).get("acts") or []) if isinstance(a, dict)]
    starts = []
    for a in acts:
        rng = a.get("chapter_range") or []
        if isinstance(a.get("act"), int) and len(rng) == 2 and isinstance(rng[0], int) and 1 <= rng[0] <= total:
            starts.append((a["act"], rng[0]))
    if not starts:
        return total
    return max(starts)[1]


def _optional(step: S.StepDef) -> S.StepDef:
    return _replace_extra(step, optional=True)


def _with_deps(step: S.StepDef, deps) -> S.StepDef:
    from dataclasses import replace

    return replace(step, deps=tuple(deps))


def _replace_extra(step: S.StepDef, **extra: Any) -> S.StepDef:
    from dataclasses import replace

    return replace(step, extra={**step.extra, **extra})


def plan_index(plan: List[S.StepDef]) -> Dict[str, S.StepDef]:
    return {s.id: s for s in plan}


def frontier(plan: List[S.StepDef], statuses: Dict[str, str], settings: Dict[str, Any]) -> Optional[S.StepDef]:
    """第一个未完成的步骤。可选步骤（如蓝图快进推演）若仍是手动挡，直接跳过，不阻塞流水线。"""
    for step in plan:
        status = statuses.get(step.id, ST_PENDING)
        if status in FINISHED_STATUSES:
            continue
        if step.extra.get("optional") and G.resolve_gate(step, settings).gate == G.GATE_MANUAL:
            continue
        return step
    return None


def is_chapter_start(step: S.StepDef) -> bool:
    return step.chapter > 0 and step.id.endswith(".word_quota")


def describe_plan(plan: List[S.StepDef], statuses: Dict[str, str], settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for step in plan:
        decision = G.resolve_gate(step, settings)
        rows.append({
            "id": step.id,
            "title": step.title,
            "stage": step.stage,
            "category": step.category,
            "executor": step.executor,
            "skill": step.skill or None,
            "chapter": step.chapter or None,
            "status": statuses.get(step.id, ST_PENDING),
            "gate": decision.gate,
            "gate_source": decision.source,
            "deps": list(step.deps),
            "outputs": list(step.outputs),
            "optional": bool(step.extra.get("optional")),
        })
    return rows


BLOCKED_HINT = "执行器尚未实现（章节写作相关 python 步骤在 M3 实现）"
