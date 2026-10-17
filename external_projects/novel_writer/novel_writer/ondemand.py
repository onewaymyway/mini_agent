"""novel_writer/ondemand.py — 按需请求：修订（L1–L4）与灵感评估（M4，方案 5.1 / 5.2）。

请求以文件形式保存在 `_studio/requests/<id>.json`，流水线展开时每个请求对应一个**可选的手动步骤**
（id 为 `rev.<id>` 或 `idea.<id>`）。它们不会阻塞主流程，只有你在总控台或审校页点 ▶ 时才运行。
状态（待运行/已通过/失败）由步骤状态机记录；请求文件里只保存输入与结果。
"""

from __future__ import annotations

import datetime as _dt
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from novel_writer import steps as S
from novel_writer.fsutil import atomic_write_json, read_json
from novel_writer.workspace import Workspace

REVISION_LEVELS: Dict[str, str] = {
    "L1": "文字润色：只改句段与用词，不改情节与设定",
    "L2": "章内情节调整：本章内容变化，本章概述同步更新",
    "L3": "章节级改动：可能影响人物状态与伏笔，需同步下游状态",
    "L4": "蓝图级改动：影响结构或主线，需作者确认后，受影响步骤标为需重跑",
}
KINDS = ("revision", "idea")


class OnDemandError(ValueError):
    pass


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def requests_dir(ws: Workspace) -> Path:
    return ws.path("studio_requests")


def create_request(ws: Workspace, kind: str, **fields: Any) -> Dict[str, Any]:
    if kind not in KINDS:
        raise OnDemandError(f"未知请求类型 {kind!r}")
    rid = f"{uuid.uuid4().hex[:8]}"
    data = {"id": rid, "kind": kind, "created_at": _now(), **fields}
    atomic_write_json(requests_dir(ws) / f"{kind}_{rid}.json", data)
    return data


def list_requests(ws: Workspace, kind: Optional[str] = None) -> List[Dict[str, Any]]:
    out = []
    d = requests_dir(ws)
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json")):
        data = read_json(p, default=None)
        if isinstance(data, dict) and (kind is None or data.get("kind") == kind):
            out.append(data)
    return out


def load_request(ws: Workspace, kind: str, rid: str) -> Dict[str, Any]:
    data = read_json(requests_dir(ws) / f"{kind}_{rid}.json", default=None)
    if not isinstance(data, dict):
        raise OnDemandError(f"找不到请求 {kind}_{rid}")
    return data


def save_request(ws: Workspace, data: Dict[str, Any]) -> None:
    atomic_write_json(requests_dir(ws) / f"{data['kind']}_{data['id']}.json", data)


def ondemand_steps(ws: Workspace) -> List[S.StepDef]:
    steps: List[S.StepDef] = []
    for req in list_requests(ws):
        if req.get("kind") == "revision":
            n = int(req.get("chapter") or 0)
            steps.append(S.StepDef(
                id=f"rev.{req['id']}", title=f"修订 第{n}章 {req.get('level')}：{req.get('instruction', '')[:20]}",
                stage="ondemand", category=S.CAT_REVISE, executor=S.EXECUTOR_PYTHON, handler="revision",
                chapter=0, extra={"optional": True, "request": f"revision_{req['id']}", "kind": "revision"},
            ))
        elif req.get("kind") == "idea":
            steps.append(S.StepDef(
                id=f"idea.{req['id']}", title=f"评估灵感：{str(req.get('text', ''))[:20]}",
                stage="ondemand", category=S.CAT_REVISE, executor=S.EXECUTOR_PYTHON, handler="idea",
                chapter=0, extra={"optional": True, "request": f"idea_{req['id']}", "kind": "idea"},
            ))
    return steps


# ── 处理器 ─────────────────────────────────────────────────────────────────


def h_revision(ws: Workspace, step: S.StepDef, settings: Dict[str, Any], invoker=None) -> Dict[str, Any]:
    from dataclasses import replace

    from novel_writer import chapter_ops as C
    from novel_writer import state as st
    from novel_writer.runner import NeedsUser, _answers_file, run_skill_step
    from novel_writer.pipeline import build_plan

    if invoker is None:
        raise OnDemandError("修订需要执行器（invoker）")
    req = load_request(ws, "revision", step.extra["request"].split("_", 1)[1])
    n, level = int(req["chapter"]), req["level"]
    instruction = str(req.get("instruction") or "")

    if level == "L4":
        answers_path = _answers_file(ws, step.id)
        answers = (read_json(answers_path, default=None) or {}).get("answers") or {}
        decision = str(answers.get("decision") or "")
        if answers_path.exists():
            answers_path.unlink()
        if decision.startswith("取消"):
            req["result"] = {"outcome": "已取消"}
            save_request(ws, req)
            return {"summary": "作者取消了蓝图级修订"}
        if not decision.startswith("确认"):
            raise NeedsUser(
                "蓝图级修订需要你确认：本次改动会使第 {} 章及之后的步骤标为「需重跑」，不会自动改写蓝图。".format(n),
                [{"id": "decision", "question": f"修订请求：{instruction}", "options": ["确认，标记受影响步骤", "取消"]}],
            )
        forced = force_stale_from(ws, n)
        req["result"] = {"outcome": "已确认，受影响步骤已标记", "stale": forced}
        save_request(ws, req)
        return {"summary": f"已标记 {len(forced)} 个受影响步骤为需重跑"}

    diag = S.StepDef(
        id=f"{step.id}.diagnose", title="修订诊断", stage="ondemand", category=S.CAT_REVISE,
        executor=S.EXECUTOR_SKILL, skill="revision-editor", chapter=n,
        instructions=f"诊断第 {n} 章的修订请求（级别 {level}：{REVISION_LEVELS[level]}）。作者要求：{instruction}。"
                     "请列出需要改动的句段与理由，并判断是否真的属于该级别。",
        outputs=(S.rel("revision_diag", nn=n),),
    )
    run_skill_step(ws, diag, invoker=invoker, settings=settings, run_no=1)
    write = S.StepDef(
        id=f"{step.id}.write", title="按诊断修改", stage="ondemand", category=S.CAT_REVISE,
        executor=S.EXECUTOR_SKILL, skill="chapter-writer", chapter=n,
        instructions=(f"按修订诊断（{S.rel('revision_diag', nn=n)}）修改第 {n} 章，级别 {level}。"
                      f"作者要求：{instruction}。只做诊断中列出的改动，其它内容保持不变。"),
        inputs=(S.rel("revision_diag", nn=n),), outputs=(S.glob_of("chapter_text", nn=n),),
    )
    run_skill_step(ws, write, invoker=invoker, settings=settings, run_no=1)
    review = replace(C._review_step(n), id=f"{step.id}.review")
    run_skill_step(ws, review, invoker=invoker, settings=settings, run_no=1)
    C.write_machine_report(ws, n, settings)

    if level in ("L2", "L3"):
        archive = replace(C._review_step(n), id=f"{step.id}.archive", skill="chapter-review",
                          instructions=f"更新第 {n} 章的章节概述（修订后）。",
                          outputs=(S.rel("chapter_summary", nn=n),))
        run_skill_step(ws, archive, invoker=invoker, settings=settings, run_no=1)
    if level == "L3":
        sync = S.StepDef(id=f"{step.id}.sync", title="人物状态同步", stage="ondemand", category=S.CAT_REVISE,
                         executor=S.EXECUTOR_SKILL, skill="character-manager", chapter=n,
                         instructions=f"根据第 {n} 章修订后的概述同步人物状态。",
                         inputs=(S.rel("chapter_summary", nn=n),), outputs=(S.rel("characters"),))
        sync_plot = replace(sync, id=f"{step.id}.sync_plot", skill="plot-tracker",
                            instructions=f"根据第 {n} 章修订后的概述更新伏笔状态。",
                            outputs=(S.rel("plot_tracker"),))
        run_skill_step(ws, sync, invoker=invoker, settings=settings, run_no=1)
        run_skill_step(ws, sync_plot, invoker=invoker, settings=settings, run_no=1)

    stale = st.mark_stale(ws, build_plan(ws))
    req["result"] = {"outcome": "修订完成", "stale": stale}
    save_request(ws, req)
    C._log(ws, n, f"修订 {level}（{instruction[:30]}）完成；标为需重跑的步骤 {len(stale)} 个")
    return {"summary": f"{level} 修订完成；下游需重跑 {len(stale)} 步"}


def force_stale_from(ws: Workspace, chapter: int) -> List[str]:
    """L4：把第 chapter 章及之后、已通过的章节步骤标为 stale（不自动执行）。"""
    from novel_writer import state as st
    from novel_writer.pipeline import build_plan

    marked = []
    state = st.load_state(ws)["steps"]
    for step in build_plan(ws):
        if step.chapter >= chapter and state.get(step.id, {}).get("status") == st.ST_PASSED:
            st.update_step(ws, step.id, status=st.ST_STALE, note=f"L4 修订影响（自第 {chapter} 章起）")
            marked.append(step.id)
    return marked


def h_idea(ws: Workspace, step: S.StepDef, settings: Dict[str, Any], invoker=None) -> Dict[str, Any]:
    from novel_writer.runner import run_skill_step

    if invoker is None:
        raise OnDemandError("灵感评估需要执行器（invoker）")
    req = load_request(ws, "idea", step.extra["request"].split("_", 1)[1])
    text = str(req.get("text") or "")
    evaluate = S.StepDef(
        id=f"{step.id}.evaluate", title="灵感评估", stage="ondemand", category=S.CAT_REVISE,
        executor=S.EXECUTOR_SKILL, skill="idea-catcher",
        instructions=(f"评估这条灵感：「{text}」。判断：整合（写进某一章）、暂存（留待幕末复查）或丢弃。"
                      "回执 JSON 中另须包含字段 decision（只能是 整合 / 暂存 / 丢弃 之一）"
                      "与 target_chapter（整合时的章号，否则为 null）。"),
        outputs=(),
    )
    receipt = run_skill_step(ws, evaluate, invoker=invoker, settings=settings, run_no=1)
    decision = str(receipt.get("decision") or "暂存")
    if decision not in ("整合", "暂存", "丢弃"):
        decision = "暂存"
    req["result"] = {"decision": decision, "target_chapter": receipt.get("target_chapter"),
                     "summary": receipt.get("summary") or ""}
    save_request(ws, req)
    return {"summary": f"评估结果：{decision}"}


HANDLERS = {"revision": h_revision, "idea": h_idea}
