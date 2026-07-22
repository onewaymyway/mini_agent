"""
workflow/api_helpers.py — workflow 控制逻辑的"纯函数"抽取层

[workflow机制改进计划（P7）一、1.2] 背景：`workflow/tools.py` 里的
run_workflow / resume_workflow_run / list_workflow_runs / ... 这批 @tool
函数，"真正做事"的逻辑和"包装成给 LLM 看的 Markdown 字符串"这两件事混在
一起，导致 REST API（看板用）想复用同一份状态机逻辑时无从下手，只能
重新写一遍——本模块把前者抽出来，返回结构化的 dict / dataclass，
`workflow/tools.py` 和 `api/routes.py` 两边各自套上不同的展示层
（一个转 Markdown 字符串给 LLM，一个转 JSON 给前端），核心逻辑只维护一份。

本模块不依赖 `@tool` 装饰器、不 import mini_agent.tools，可以被
routes.py（FastAPI 层）安全导入。
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.config import AppConfig


class WorkflowApiError(Exception):
    """结构化错误：code 用于 REST 层映射 HTTP 状态码，message 是给人看的原因。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ── 工作流定义 ────────────────────────────────────────────────────────────

def load_store(cfg: "AppConfig"):
    from mini_agent.workflow.store import WorkflowStore
    return WorkflowStore(Path(cfg.project_root))


def list_workflows(cfg: "AppConfig") -> list[dict]:
    """对应 list_workflows 工具。"""
    return load_store(cfg).list_all()


def get_workflow_yaml(cfg: "AppConfig", name: str) -> str:
    """对应 show_workflow 工具。找不到抛 WorkflowApiError(code='not_found')。"""
    yaml_str = load_store(cfg).export_yaml(name)
    if yaml_str is None:
        raise WorkflowApiError("not_found", f"找不到工作流 {name!r}")
    return yaml_str


# ── 启动执行 ──────────────────────────────────────────────────────────────

def start_workflow_run(
    cfg: "AppConfig",
    name: str,
    inputs: Optional[dict] = None,
    background: Optional[bool] = None,
) -> dict:
    """
    对应 run_workflow 工具的核心逻辑。

    返回 dict：
      {"mode": "sync", "result": WorkflowRunResult}                  # 前台同步执行完毕
      {"mode": "async", "workflow_session_id": str, "output_dir": str,
       "has_approval_step": bool}                                     # 已丢进后台线程

    找不到工作流时抛 WorkflowApiError(code='not_found')。
    """
    from mini_agent.workflow.store import WorkflowStore
    from mini_agent.workflow.runner import WorkflowRunner, step_requires_approval

    store = WorkflowStore(Path(cfg.project_root))
    wf = store.load(name)
    if wf is None:
        available = [w["name"] for w in store.list_all()]
        raise WorkflowApiError("not_found", f"找不到工作流 {name!r}，可用工作流：{available or '（无）'}")

    parsed_inputs = dict(inputs or {})

    run_in_background = background
    if run_in_background is None:
        run_in_background = bool(getattr(getattr(cfg, "workflow", None), "background_execution_default", False))

    has_approval_step = any(
        step_requires_approval(s, getattr(cfg, "workflow", None)) for s in wf.steps
    )
    if has_approval_step and not run_in_background:
        run_in_background = True  # 强制后台，否则审批门必然超时判拒绝

    runner = WorkflowRunner(cfg)

    if not run_in_background:
        result = runner.run(wf, parsed_inputs)
        return {"mode": "sync", "result": result}

    from mini_agent.storage.paths import AgentPaths
    wf_session_id = f"wfs_{uuid.uuid4().hex[:12]}"
    wf_output_dir = AgentPaths(project_root=cfg.project_root).ensure_workflow_session_output_dir(wf_session_id)

    def _bg_run():
        try:
            runner.run(wf, parsed_inputs, workflow_session_id=wf_session_id)
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where="mini_agent.workflow.api_helpers.start_workflow_run._bg_run")

    t = threading.Thread(target=_bg_run, daemon=True, name=f"wf-run-{wf_session_id}")
    t.start()

    return {
        "mode": "async",
        "workflow_session_id": wf_session_id,
        "output_dir": str(wf_output_dir),
        "has_approval_step": has_approval_step,
    }


def resume_workflow_run(
    cfg: "AppConfig",
    workflow_session_id: str,
    background: Optional[bool] = None,
    force_rerun_from: Optional[str] = None,
) -> dict:
    """
    对应 resume_workflow_run 工具的核心逻辑。

    force_rerun_from：[P7 二、3.3 单步编辑续跑] 若传入，重跑时该 step_id
    之后（不含自身）的所有下游 step 视为未完成重新执行；force_rerun_from
    自身的 output 沿用当前落盘值（通常是刚被 override_step_output 改过的）。

    返回同 start_workflow_run。找不到执行记录/定义快照抛
    WorkflowApiError(code='not_found' / 'bad_snapshot')。
    """
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.workflow.session import WorkflowSession
    from mini_agent.workflow.runner import WorkflowRunner
    from mini_agent.workflow.generator import WorkflowGenerator

    paths = AgentPaths(project_root=cfg.project_root)
    wf_session = WorkflowSession.load(paths, workflow_session_id)
    if wf_session is None:
        raise WorkflowApiError("not_found", f"找不到执行记录 {workflow_session_id!r}")

    snap_path = paths.workflow_session_def_snapshot(workflow_session_id)
    if not snap_path.exists():
        raise WorkflowApiError("bad_snapshot", f"执行 {workflow_session_id!r} 缺少工作流定义快照，无法续跑")

    generator = WorkflowGenerator(cfg)
    try:
        wf = generator.parse_yaml(snap_path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise WorkflowApiError("bad_snapshot", f"定义快照解析失败：{e}")

    if force_rerun_from:
        _mark_downstream_for_rerun(wf, wf_session, force_rerun_from)
        wf_session.save(paths)

    run_in_background = background
    if run_in_background is None:
        run_in_background = bool(getattr(getattr(cfg, "workflow", None), "background_execution_default", False))

    runner = WorkflowRunner(cfg)
    if not run_in_background:
        result = runner.run(wf, wf_session.inputs, workflow_session_id=workflow_session_id)
        return {"mode": "sync", "result": result}

    def _bg_resume():
        try:
            runner.run(wf, wf_session.inputs, workflow_session_id=workflow_session_id)
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where="mini_agent.workflow.api_helpers.resume_workflow_run._bg_resume")

    t = threading.Thread(target=_bg_resume, daemon=True, name=f"wf-resume-{workflow_session_id}")
    t.start()
    return {"mode": "async", "workflow_session_id": workflow_session_id}


def _mark_downstream_for_rerun(wf, wf_session, force_rerun_from: str) -> None:
    """[P7 二、3.3] 计算 force_rerun_from 的下游 step 集合，从 session 里摘掉它们
    的 step_results，使 runner 把它们当成"未完成"重新执行；force_rerun_from
    自身保留（沿用人工编辑后的 output）。"""
    step_ids = {s.id for s in wf.steps}
    if force_rerun_from not in step_ids:
        raise WorkflowApiError("bad_step", f"step_id {force_rerun_from!r} 不在该工作流定义中")

    dependents: dict[str, list[str]] = {s.id: [] for s in wf.steps}
    for s in wf.steps:
        for dep in s.depends_on:
            if dep in dependents:
                dependents[dep].append(s.id)

    downstream: set[str] = set()
    frontier = [force_rerun_from]
    while frontier:
        cur = frontier.pop()
        for nxt in dependents.get(cur, []):
            if nxt not in downstream:
                downstream.add(nxt)
                frontier.append(nxt)

    for step_id in downstream:
        wf_session.step_results.pop(step_id, None)


# ── 执行记录查询 ──────────────────────────────────────────────────────────

def list_workflow_runs(cfg: "AppConfig", name: Optional[str] = None) -> list[dict]:
    """对应 list_workflow_runs 工具，返回每次执行的 to_dict() + summary_line()。"""
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.workflow.session import WorkflowSession

    paths = AgentPaths(project_root=cfg.project_root)
    out = []
    for wf_session_id in sorted(paths.list_workflow_session_ids()):
        s = WorkflowSession.load(paths, wf_session_id)
        if s is None:
            continue
        if name and s.workflow_name != name:
            continue
        d = s.to_dict()
        d["summary_line"] = s.summary_line()
        out.append(d)
    return out


def get_workflow_run_detail(cfg: "AppConfig", workflow_session_id: str) -> dict:
    """对应 get_workflow_run_status 工具，附加 output_dir。
    找不到抛 WorkflowApiError(code='not_found')。"""
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.workflow.session import WorkflowSession

    paths = AgentPaths(project_root=cfg.project_root)
    s = WorkflowSession.load(paths, workflow_session_id)
    if s is None:
        raise WorkflowApiError("not_found", f"找不到执行记录 {workflow_session_id!r}")
    d = s.to_dict()
    d["summary_line"] = s.summary_line()
    d["output_dir"] = str(paths.workflow_session_output_dir(workflow_session_id))
    return d


def read_workflow_run_events(cfg: "AppConfig", workflow_session_id: str, since_line: int = 0) -> dict:
    """
    [P7 一、1.2] events.jsonl 增量拉取，前端用 next_line 做下一次轮询的
    since_line，避免每次全量重读整个文件。找不到文件时返回空列表（工作流
    可能还没跑到第一条事件，不算错误）。
    """
    from mini_agent.storage.paths import AgentPaths

    paths = AgentPaths(project_root=cfg.project_root)
    p = paths.workflow_session_events(workflow_session_id)
    if not p.exists():
        return {"events": [], "next_line": since_line}

    events: list[dict] = []
    line_no = 0
    try:
        with open(p, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                if line_no <= since_line:
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where="mini_agent.workflow.api_helpers.read_workflow_run_events")

    return {"events": events, "next_line": max(line_no, since_line)}


# ── 执行控制：暂停/取消/审批/人工输入 ──────────────────────────────────────

def _get_control_or_raise(workflow_session_id: str):
    from mini_agent.workflow import registry as wf_registry
    control = wf_registry.get(workflow_session_id)
    if control is None:
        raise WorkflowApiError(
            "not_active",
            f"进程内没有找到执行 {workflow_session_id!r} 的活跃控制状态（可能已结束、或所在进程已重启）",
        )
    return control


def pause_workflow_run(cfg: "AppConfig", workflow_session_id: str) -> None:
    _get_control_or_raise(workflow_session_id).request_pause()


def cancel_workflow_run(cfg: "AppConfig", workflow_session_id: str) -> None:
    _get_control_or_raise(workflow_session_id).request_cancel()


def approve_workflow_step(cfg: "AppConfig", workflow_session_id: str) -> str:
    """返回被批准的 step_id。没有待审批 step 时抛 WorkflowApiError(code='no_pending')。"""
    control = _get_control_or_raise(workflow_session_id)
    if not control.pending_approval_step:
        raise WorkflowApiError("no_pending", f"执行 {workflow_session_id!r} 当前没有正在等待审批的步骤")
    step_id = control.pending_approval_step
    control.request_approve(step_id)
    return step_id


def reject_workflow_step(cfg: "AppConfig", workflow_session_id: str, reason: str = "") -> str:
    control = _get_control_or_raise(workflow_session_id)
    if not control.pending_approval_step:
        raise WorkflowApiError("no_pending", f"执行 {workflow_session_id!r} 当前没有正在等待审批的步骤")
    step_id = control.pending_approval_step
    control.request_reject(step_id, reason)
    return step_id


def provide_workflow_step_input(cfg: "AppConfig", workflow_session_id: str, text: str) -> str:
    control = _get_control_or_raise(workflow_session_id)
    if not control.pending_input_step:
        raise WorkflowApiError("no_pending", f"执行 {workflow_session_id!r} 当前没有正在等待人工输入的步骤")
    step_id = control.pending_input_step
    control.request_provide_input(step_id, text)
    return step_id


# ── 单步编辑续跑（P7 二、3.3）──────────────────────────────────────────────

def override_step_output(cfg: "AppConfig", workflow_session_id: str, step_id: str, new_output: str) -> None:
    """
    把已完成 step 的输出替换成人工编辑的文本，状态保持 DONE，落盘。
    找不到执行记录 / step 抛 WorkflowApiError。
    """
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.workflow.session import WorkflowSession

    paths = AgentPaths(project_root=cfg.project_root)
    s = WorkflowSession.load(paths, workflow_session_id)
    if s is None:
        raise WorkflowApiError("not_found", f"找不到执行记录 {workflow_session_id!r}")
    if step_id not in s.step_results:
        raise WorkflowApiError("bad_step", f"执行 {workflow_session_id!r} 中没有步骤 {step_id!r} 的结果")
    s.step_results[step_id].output = new_output
    s.save(paths)


# ── Dry-run 预览（P7 二、3.2）────────────────────────────────────────────

_PARAM_PLACEHOLDER_RE = re.compile(r"\{([^}.]+)\}")


def preview_workflow(cfg: "AppConfig", name: str, inputs: Optional[dict] = None) -> dict:
    """
    纯计算预览：不调用任何 Agent/工具。
      - batches: 按并发批次分层的 step 列表（每项含 id/name/type/role）
      - resolved_prompts: 能用 inputs 静态替换的 prompt 预览
        （`{step_id.output}` 这类运行时占位符原样保留，标注 runtime）
      - conditions: 每个 step 的 condition 表达式，能静态求值的给出结果，
        涉及 `{step_id.score}` 等运行期依赖的标注"运行时决定"
    找不到工作流抛 WorkflowApiError(code='not_found')。
    """
    from mini_agent.workflow.store import WorkflowStore
    from mini_agent.workflow.runner import WorkflowRunner

    store = WorkflowStore(Path(cfg.project_root))
    wf = store.load(name)
    if wf is None:
        raise WorkflowApiError("not_found", f"找不到工作流 {name!r}")

    parsed_inputs = dict(inputs or {})
    runner = WorkflowRunner(cfg)
    try:
        batches = runner._compute_parallel_batches(wf)
    except ValueError as e:
        raise WorkflowApiError("cyclic", str(e))

    batch_view = [
        [
            {
                "id": s.id,
                "name": s.name,
                "type": getattr(s, "effective_type", None) or getattr(s, "type", None) or "agent",
                "role": s.role,
                "depends_on": list(s.depends_on),
            }
            for s in batch
        ]
        for batch in batches
    ]

    resolved_prompts: dict[str, str] = {}
    for s in wf.steps:
        prompt = s.prompt or ""

        def _sub(m: re.Match) -> str:
            key = m.group(1)
            if key in parsed_inputs:
                return str(parsed_inputs[key])
            return m.group(0)  # 保留原样，标注为运行时占位符（含 '.' 的已被正则排除）

        resolved_prompts[s.id] = _PARAM_PLACEHOLDER_RE.sub(_sub, prompt)

    conditions: dict[str, str] = {}
    for s in wf.steps:
        cond = getattr(s, "condition", None)
        if not cond:
            continue
        if "." in cond or "{" in cond:
            # 涉及运行时输出/评分依赖，交给 runner 在真正执行时求值
            conditions[s.id] = f"{cond}  （运行时决定，无法预览）"
            continue
        try:
            # 仅对纯 inputs 相关、不含依赖引用的表达式做只读求值，
            # 沙箱环境只暴露 inputs，避免 eval 到任意名字。
            value = eval(cond, {"__builtins__": {}}, dict(parsed_inputs))  # noqa: S307
            conditions[s.id] = f"{cond}  → {value!r}"
        except Exception:
            conditions[s.id] = f"{cond}  （无法静态求值，运行时决定）"

    return {
        "workflow_name": wf.name,
        "batches": batch_view,
        "resolved_prompts": resolved_prompts,
        "conditions": conditions,
    }
