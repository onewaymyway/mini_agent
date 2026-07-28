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


def patch_workflow_step(cfg: "AppConfig", name: str, step_id: str, patch_dict: dict) -> dict:
    """[workflow_mechanism_improvement_proposal.md §4.2] 单步编辑的纯函数版本。

    与 `tools.py::patch_workflow_step` 是同一段逻辑的"提取复用"（本函数落地后
    tools.py 改为调用本函数），供 REST 层（看板"✏️ 修改此步骤定义"入口）
    复用，不重新实现一遍字段校验/保存逻辑。

    返回 {"changed": [...], "path": str}；失败时抛 WorkflowApiError。
    """
    from mini_agent.workflow.store import WorkflowStore

    store = WorkflowStore(Path(cfg.project_root))
    wf = store.load(name)
    if wf is None:
        raise WorkflowApiError("not_found", f"找不到工作流 {name!r}")

    target = next((s for s in wf.steps if s.id == step_id), None)
    if target is None:
        raise WorkflowApiError("not_found", f"工作流 {name!r} 中不存在 step_id={step_id!r}")

    unknown_fields = [k for k in patch_dict if not hasattr(target, k)]
    if unknown_fields:
        raise WorkflowApiError("invalid_patch", f"patch 中包含未知字段：{unknown_fields}")

    changed = []
    for k, v in patch_dict.items():
        setattr(target, k, v)
        changed.append(k)

    errors = wf.validate()
    if errors:
        raise WorkflowApiError("validation_failed", "修改后的工作流定义校验失败，未保存：\n" + "\n".join(f"- {e}" for e in errors))

    try:
        path = store.save(wf, cfg=cfg)
    except ValueError as e:
        raise WorkflowApiError("save_failed", f"保存失败：{e}")

    return {"changed": changed, "path": str(path)}


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
    force_serial: Optional[bool] = None,
    require_all_inputs_upfront: bool = False,
    output_export_dir: Optional[str] = None,
) -> dict:
    """
    对应 run_workflow 工具的核心逻辑。

    output_export_dir: 可选的外部导出目录。不设置则不做任何复制；设置时，
        workflow 到达终态后会把 output/ 目录下的所有文件复制过去（见
        `WorkflowSession.export_output_files`）。

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

    # [改进方案 §1] require_all_inputs_upfront=True 时，启动前一次性扫描
    # 所有 human_input step：既没有 input_key、也没能从 parsed_inputs 里
    # 解析到对应值的，直接判为启动失败并把缺失字段列清楚，把"运行到一半
    # 才发现缺参数"提前到"启动前一次性检查"。
    if require_all_inputs_upfront:
        missing = []
        for s in wf.steps:
            if s.effective_type != "human_input":
                continue
            key = s.input_key
            if not key or key not in parsed_inputs:
                missing.append(f"{s.id}（input_key={key!r}）")
        if missing:
            raise WorkflowApiError(
                "missing_inputs",
                f"require_all_inputs_upfront=True，但以下 human_input 步骤缺少可解析的输入："
                f"{', '.join(missing)}；请在 inputs 中补全对应 input_key 的值",
            )

    run_in_background = background
    if run_in_background is None:
        run_in_background = bool(getattr(getattr(cfg, "workflow", None), "background_execution_default", False))

    has_approval_step = any(
        step_requires_approval(s, getattr(cfg, "workflow", None)) for s in wf.steps
    )
    if has_approval_step and not run_in_background:
        run_in_background = True  # 强制后台，否则审批门必然超时判拒绝

    # [改进方案 §1] 同理：存在没有 input_key 兜底的 human_input 步骤时，
    # 前台同步执行没有其他线程能调用 provide_workflow_step_input，也应
    # 强制转后台，否则必然阻塞到 human_input_wait_timeout_seconds 超时。
    has_blocking_human_input = any(
        s.effective_type == "human_input" and not (s.input_key and s.input_key in parsed_inputs)
        for s in wf.steps
    )
    if has_blocking_human_input and not run_in_background:
        run_in_background = True

    runner = WorkflowRunner(cfg)

    if not run_in_background:
        result = runner.run(wf, parsed_inputs, force_serial=force_serial, output_export_dir=output_export_dir)
        return {"mode": "sync", "result": result}

    from mini_agent.storage.paths import AgentPaths
    wf_session_id = f"wfs_{uuid.uuid4().hex[:12]}"
    wf_output_dir = AgentPaths(project_root=cfg.project_root).ensure_workflow_session_output_dir(wf_session_id)

    def _bg_run():
        try:
            runner.run(wf, parsed_inputs, workflow_session_id=wf_session_id,
                       force_serial=force_serial, output_export_dir=output_export_dir)
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
    step_overrides: Optional[dict] = None,
) -> dict:
    """
    对应 resume_workflow_run 工具的核心逻辑。

    force_rerun_from：[P7 二、3.3 单步编辑续跑] 若传入，重跑时该 step_id
    之后（不含自身）的所有下游 step 视为未完成重新执行；force_rerun_from
    自身的 output 沿用当前落盘值（通常是刚被 override_step_output 改过的）。

    step_overrides：[workflow_mechanism_improvement_plan_p10.md §2] 形如
    {"step_id": {"timeout": 120}} 的一次性执行参数覆盖，只影响本次 resume
    执行，不写回 WorkflowStore 持久化的定义——在加载 WorkflowDef 之后、
    真正执行之前，对内存中的这份 WorkflowDef 副本做字段覆盖。字段名必须
    在 RUNTIME_OVERRIDABLE_FIELDS 白名单内（timeout/retry_on_error/
    allow_parallel/model/escalate_after_n_same_failures），命中白名单外
    字段（如 prompt/condition/tool_name）直接拒绝并报错，不静默忽略——
    这类改动本质上是"改逻辑"，应该走 patch_workflow_step 留痕。

    返回同 start_workflow_run。找不到执行记录/定义快照抛
    WorkflowApiError(code='not_found' / 'bad_snapshot')；step_overrides
    引用了不存在的 step_id 或非法字段抛 WorkflowApiError(code='bad_override')。
    """
    import dataclasses
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.workflow.session import WorkflowSession
    from mini_agent.workflow.runner import WorkflowRunner
    from mini_agent.workflow.generator import WorkflowGenerator
    from mini_agent.workflow.schema import RUNTIME_OVERRIDABLE_FIELDS

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

    # [bugfix] wf.source_dir 是纯运行时字段，不参与 to_dict()/快照序列化，
    # generator.parse_yaml() 重建出来的 WorkflowDef 因此丢失了目录模式
    # workflow 的 source_dir，导致 python_step 的 ctx.workflow_dir 为 None、
    # load_prompt_file() 报"workflow_dir 未设置"；同理 prompt_file/
    # script_path 在快照里也只存了相对路径本身（没存展开后的正文），
    # 需要重新按 store 加载时同一套逻辑解析一遍。这里按工作流名字重新定位
    # 一次原始入口文件（目录模式 or 单文件模式），复用 WorkflowStore 的
    # 静态解析方法，行为与 WorkflowStore._load_path() 保持一致。
    from mini_agent.workflow.store import WorkflowStore
    _store = WorkflowStore(Path(cfg.project_root))
    _entry_path = _store._resolve_path(wf.name)
    if _entry_path is not None:
        if _entry_path.name == "workflow.yaml" and _entry_path.parent != _store._dir:
            wf.source_dir = _entry_path.parent
        _store._resolve_prompt_files(wf, _entry_path)
        _store._resolve_script_paths(wf, _entry_path)

    if force_rerun_from:
        _mark_downstream_for_rerun(wf, wf_session, force_rerun_from)
        wf_session.save(paths)

    if step_overrides:
        step_by_id = {s.id: s for s in wf.steps}
        for step_id, fields_ in step_overrides.items():
            if step_id not in step_by_id:
                raise WorkflowApiError("bad_override", f"step_overrides 引用了不存在的 step_id：{step_id!r}")
            if not isinstance(fields_, dict):
                raise WorkflowApiError("bad_override", f"step_overrides[{step_id!r}] 必须是一个字段字典")
            illegal = [k for k in fields_ if k not in RUNTIME_OVERRIDABLE_FIELDS]
            if illegal:
                raise WorkflowApiError(
                    "bad_override",
                    f"step_overrides[{step_id!r}] 包含不允许一次性覆盖的字段：{illegal}"
                    f"（只允许 {sorted(RUNTIME_OVERRIDABLE_FIELDS)}；改逻辑类字段请用 patch_workflow_step）",
                )
        # [内存态覆盖] 只替换 wf.steps 里被引用到的 WorkflowStep 对象，用
        # dataclasses.replace() 生成新对象，不 mutate 原对象、不调用
        # WorkflowStore.save()，确保持久化的 YAML/目录定义完全不受影响。
        new_steps = []
        for s in wf.steps:
            if s.id in step_overrides:
                new_steps.append(dataclasses.replace(s, **step_overrides[s.id]))
            else:
                new_steps.append(s)
        wf.steps = new_steps
        wf_session.last_step_overrides = dict(step_overrides)
        wf_session.save(paths)
    elif wf_session.last_step_overrides:
        # 这次 resume 没有传 step_overrides，清空上一次遗留的标注，避免
        # get_workflow_run_status 展示"过期"的覆盖信息。
        wf_session.last_step_overrides = {}
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


# ── 单 step 沙箱测试（workflow_mechanism_improvement_plan_p10.md §1）──────

def test_workflow_step(
    cfg: "AppConfig",
    name: str,
    step_id: str,
    mock_step_results: Optional[dict] = None,
    mock_inputs: Optional[dict] = None,
    timeout_override: Optional[float] = None,
) -> dict:
    """
    对应 test_workflow_step 工具的核心逻辑：只执行某个已保存 workflow 里的
    一个 step，用手工提供的 mock 上游数据代替真实依赖，不落盘进
    workflow_runs 历史、不创建 WorkflowSession、不启动 watchdog、不触发
    hooks/system_events——用完即弃，用于验证 patch_workflow_step 改动是否
    符合预期，而不必接入完整 DAG 重跑一次。

    与 resume_workflow_run(force_rerun_from=...) 的本质区别：那是接入真实
    DAG 的一次正式执行，会落盘、计入统计、影响下游 step；这里只是拿目标
    step 的定义单独跑一次，跑完直接把结果返回，不留下任何痕迹。

    返回 dict：{"skipped": bool, "reason": str}（human_input/require_approval
    类型跳过时）或 StepResult.to_dict()（正常执行完毕，无论成功与否）。

    找不到工作流/step_id 抛 WorkflowApiError(code='not_found' / 'bad_step')。
    """
    import dataclasses
    from mini_agent.workflow.store import WorkflowStore
    from mini_agent.workflow.runner import WorkflowRunner, step_requires_approval
    from mini_agent.workflow.executors import build_mock_step_results

    store = WorkflowStore(Path(cfg.project_root))
    wf = store.load(name)
    if wf is None:
        raise WorkflowApiError("not_found", f"找不到工作流 {name!r}")

    target = next((s for s in wf.steps if s.id == step_id), None)
    if target is None:
        raise WorkflowApiError("bad_step", f"工作流 {name!r} 中不存在 step_id={step_id!r}")

    wf_cfg = getattr(cfg, "workflow", None)
    if target.effective_type == "human_input" or step_requires_approval(target, wf_cfg):
        return {
            "skipped": True,
            "reason": (
                f"该类型不支持沙箱测试（type={target.effective_type!r}"
                f"{'，require_approval=True' if step_requires_approval(target, wf_cfg) else ''}），"
                "这类 step 本身没有'输出对不对'的验证意义，请用 "
                "resume_workflow_run(force_rerun_from=...) 实际验证"
            ),
        }

    # timeout_override 只作用于本次沙箱执行，用 dataclasses.replace() 生成
    # 一份临时 step 对象，不 mutate 原定义、不写回持久化。
    if timeout_override is not None:
        target = dataclasses.replace(target, timeout=timeout_override)

    mock_srs = build_mock_step_results(mock_step_results)
    inputs = dict(mock_inputs or {})

    runner = WorkflowRunner(cfg)
    # 供 _resolve_prompt/_eval_condition 之外的执行路径（sub_workflow 递归
    # 深度保护、skill_agent 本地资源包查找等）读取——这些 getattr 都有 None
    # 兜底，不设置也不会报错，设置了则行为与正式执行完全一致。
    runner._current_wf = wf
    runner._current_wf_steps = wf.steps
    runner._current_inputs = inputs
    try:
        from mini_agent.workflow.resource_bundle import build_resource_bundle
        runner._current_resource_bundle = build_resource_bundle(cfg, wf)
    except Exception:
        runner._current_resource_bundle = None

    try:
        resolved_prompt = runner._resolve_prompt(target.prompt, mock_srs, inputs)
    except KeyError as e:
        raise WorkflowApiError(
            "bad_mock_data",
            f"prompt 占位符解析失败：{e}（请检查 mock_step_results/mock_inputs 是否覆盖了 "
            f"prompt 里引用到的所有 {{step_id.output}}/{{variable}}）",
        )

    sr = runner._execute_step(target, resolved_prompt, mock_srs)
    d = sr.to_dict()
    d["skipped"] = False
    d["resolved_prompt_preview"] = resolved_prompt[:500] + ("...(截断)" if len(resolved_prompt) > 500 else "")
    return d


# ── 执行记录查询 ──────────────────────────────────────────────────────────

def list_workflow_runs(cfg: "AppConfig", name: Optional[str] = None) -> list[dict]:
    """对应 list_workflow_runs 工具，返回每次执行的 to_dict() + summary_line()。

    [孤儿运行修复] session.json 里落盘的 RUNNING/PAUSED/AWAITING_APPROVAL
    只代表"上次写盘时是这个状态"，如果 daemon 进程在那之后崩溃/重启，
    没有任何东西会把它改回终态——registry.py 里的进程内控制表
    （pause/cancel 等信号真正生效的地方）在重启后是空的，磁盘状态却会
    一直显示"运行中"，看板据此判断"当前有 workflow 在跑"就会显示假信息。
    这里额外计算一个 is_stale 字段：状态处于"进行中"三态之一、但
    registry 里找不到对应的活跃控制时，标记为 True，交给调用方（看板）
    决定如何展示/提示清理，不改动磁盘上的原始状态。
    """
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.workflow.session import WorkflowSession, WorkflowRunStatus
    from mini_agent.workflow import registry as wf_registry

    in_flight = {
        WorkflowRunStatus.RUNNING, WorkflowRunStatus.PAUSED, WorkflowRunStatus.AWAITING_APPROVAL,
    }
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
        d["is_stale"] = s.status in in_flight and wf_registry.get(wf_session_id) is None
        out.append(d)
    return out


def get_workflow_stats(cfg: "AppConfig", name: str) -> dict:
    """
    [P9-1a workflow_system_next_directions.md §1.2a] 对某个 workflow 的历史
    执行记录做汇总统计视图。纯粹是对 list_workflow_runs() 已经落盘的
    WorkflowSession 数据做一层聚合，不涉及执行逻辑改动，也不产生新的落盘
    数据（每次调用都是即时重新计算）。

    返回结构：
      {
        "workflow_name": str,
        "total_runs": int,
        "success_rate": float,        # DONE 状态的执行次数占比
        "step_stats": {
          step_id: {
            "total": int,             # 该 step 在多少次执行里出现过结果
            "done": int,
            "fail_rate": float,       # 1 - done/total（含 FAILED/GATE_FAILED/
                                       # TIMEOUT/CANCELLED/REJECTED/SKIPPED 等）
            "avg_duration": float,    # 秒
            "avg_score": float | None,  # 0-100，只有 evaluator 类角色产生过
                                         # 分数时才非 None
            "avg_retries_used": float,
          },
          ...
        },
        "condition_stats": {
          step_id: {                 # 只包含定义了 condition 的 step
            "total": int,
            "true_rate": float,      # 该 step 实际被执行（非 SKIPPED）的比例，
                                      # 用来近似 condition 判 True 的比例——
                                      # 注意 SKIPPED 也可能来自"依赖失败"而非
                                      # "condition 为 False"，这里不做区分，
                                      # 只是给用户一个"这个分支基本没走过/
                                      # 走的很频繁"的粗粒度信号。
          },
          ...
        },
      }

    找不到该 workflow 的任何执行记录时 total_runs=0，其余聚合字段为空 dict，
    不抛异常（"从没跑过"是正常状态，不是错误）。
    """
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.workflow.session import WorkflowSession, WorkflowRunStatus
    from mini_agent.workflow.schema import StepStatus
    from mini_agent.workflow.store import WorkflowStore

    paths = AgentPaths(project_root=cfg.project_root)
    sessions = []
    for wf_session_id in sorted(paths.list_workflow_session_ids()):
        s = WorkflowSession.load(paths, wf_session_id)
        if s is None or s.workflow_name != name:
            continue
        sessions.append(s)

    total_runs = len(sessions)
    if total_runs == 0:
        return {
            "workflow_name": name,
            "total_runs": 0,
            "success_rate": 0.0,
            "step_stats": {},
            "condition_stats": {},
        }

    success_runs = sum(1 for s in sessions if s.status == WorkflowRunStatus.DONE)

    # 只有定义了 condition 的 step 才计入 condition_stats（读取当前工作流
    # 定义；如果工作流已被删除/改名导致读不到，退化为不输出 condition_stats，
    # 不影响 step_stats 的计算）。
    condition_step_ids: set[str] = set()
    try:
        wf = WorkflowStore(Path(cfg.project_root)).load(name)
        if wf is not None:
            condition_step_ids = {s.id for s in wf.steps if s.condition}
    except Exception:
        pass

    step_agg: dict[str, dict] = {}
    cond_agg: dict[str, dict] = {}

    for s in sessions:
        for step_id, sr in s.step_results.items():
            agg = step_agg.setdefault(step_id, {
                "total": 0, "done": 0, "duration_sum": 0.0,
                "score_sum": 0.0, "score_count": 0, "retries_sum": 0,
            })
            agg["total"] += 1
            if sr.status == StepStatus.DONE:
                agg["done"] += 1
            agg["duration_sum"] += sr.duration_seconds or 0.0
            if sr.score is not None:
                agg["score_sum"] += sr.score
                agg["score_count"] += 1
            agg["retries_sum"] += sr.retries_used or 0

            if step_id in condition_step_ids:
                cagg = cond_agg.setdefault(step_id, {"total": 0, "executed": 0})
                cagg["total"] += 1
                if sr.status != StepStatus.SKIPPED:
                    cagg["executed"] += 1

    step_stats: dict[str, dict] = {}
    for step_id, agg in step_agg.items():
        step_stats[step_id] = {
            "total": agg["total"],
            "done": agg["done"],
            "fail_rate": round(1 - agg["done"] / agg["total"], 4) if agg["total"] else 0.0,
            "avg_duration": round(agg["duration_sum"] / agg["total"], 3) if agg["total"] else 0.0,
            # StepResult.score 内部按 0-1 存储（见 runner._eval_condition /
            # _resolve_prompt 里 int(score*100) 的换算），这里统一换算成
            # 0-100 展示，跟其它地方看到的评分口径一致。
            "avg_score": (
                round(agg["score_sum"] / agg["score_count"] * 100, 2)
                if agg["score_count"] else None
            ),
            "avg_retries_used": round(agg["retries_sum"] / agg["total"], 3) if agg["total"] else 0.0,
        }

    condition_stats: dict[str, dict] = {}
    for step_id, cagg in cond_agg.items():
        condition_stats[step_id] = {
            "total": cagg["total"],
            "true_rate": round(cagg["executed"] / cagg["total"], 4) if cagg["total"] else 0.0,
        }

    return {
        "workflow_name": name,
        "total_runs": total_runs,
        "success_rate": round(success_runs / total_runs, 4) if total_runs else 0.0,
        "step_stats": step_stats,
        "condition_stats": condition_stats,
    }


def get_workflow_run_detail(cfg: "AppConfig", workflow_session_id: str) -> dict:
    """对应 get_workflow_run_status 工具，附加 output_dir。
    找不到抛 WorkflowApiError(code='not_found')。"""
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.workflow.session import WorkflowSession, WorkflowRunStatus
    from mini_agent.workflow import registry as wf_registry

    paths = AgentPaths(project_root=cfg.project_root)
    s = WorkflowSession.load(paths, workflow_session_id)
    if s is None:
        raise WorkflowApiError("not_found", f"找不到执行记录 {workflow_session_id!r}")
    d = s.to_dict()
    d["summary_line"] = s.summary_line()
    d["output_dir"] = str(paths.workflow_session_output_dir(workflow_session_id))
    in_flight = {
        WorkflowRunStatus.RUNNING, WorkflowRunStatus.PAUSED, WorkflowRunStatus.AWAITING_APPROVAL,
    }
    d["is_stale"] = s.status in in_flight and wf_registry.get(workflow_session_id) is None
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


def mark_workflow_run_interrupted(cfg: "AppConfig", workflow_session_id: str) -> dict:
    """[孤儿运行修复] 把一条"看起来还在跑，但进程内已经没有活跃控制"的
    执行记录直接改判为 CANCELLED 并落盘。

    与 cancel_workflow_run 不同：cancel_workflow_run 依赖
    registry.get() 找到进程内的 ControlState 才能发信号，daemon 重启后
    这个内存态是空的，会直接抛 not_active——用户此时既不能续跑（其实
    也没意义，早就没有线程在处理了），也没有任何办法把这条记录标记完结，
    只能眼睁睁看着它一直显示"运行中"。这里绕开 registry，直接读盘改状态，
    仅在确认状态确实处于"进行中三态"且 registry 里没有对应控制时才允许
    操作，避免误伤真正还在跑的执行。
    """
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.workflow.session import WorkflowSession, WorkflowRunStatus
    from mini_agent.workflow import registry as wf_registry

    paths = AgentPaths(project_root=cfg.project_root)
    s = WorkflowSession.load(paths, workflow_session_id)
    if s is None:
        raise WorkflowApiError("not_found", f"找不到执行记录 {workflow_session_id!r}")

    in_flight = {
        WorkflowRunStatus.RUNNING, WorkflowRunStatus.PAUSED, WorkflowRunStatus.AWAITING_APPROVAL,
    }
    if s.status not in in_flight:
        raise WorkflowApiError(
            "not_in_flight", f"执行 {workflow_session_id!r} 当前状态为 {s.status.value}，不是进行中，无需标记中断",
        )
    if wf_registry.get(workflow_session_id) is not None:
        raise WorkflowApiError(
            "still_active", f"执行 {workflow_session_id!r} 在进程内仍有活跃控制，不是孤儿记录，请用暂停/取消",
        )

    s.status = WorkflowRunStatus.CANCELLED
    s.updated_at = time.time()
    s.save(paths)
    d = s.to_dict()
    d["summary_line"] = s.summary_line()
    return d


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
    纯计算预览：不调用任何 Agent/工具。找不到工作流抛
    WorkflowApiError(code='not_found')。字段含义见 preview_workflow_def()。
    """
    from mini_agent.workflow.store import WorkflowStore

    store = WorkflowStore(Path(cfg.project_root))
    wf = store.load(name)
    if wf is None:
        raise WorkflowApiError("not_found", f"找不到工作流 {name!r}")
    return preview_workflow_def(cfg, wf, inputs)


def preview_workflow_def(cfg: "AppConfig", wf: "WorkflowDef", inputs: Optional[dict] = None) -> dict:
    """
    [P9-1b workflow_system_next_directions.md §1.2b] 从 preview_workflow()
    拆分出的、直接接受内存中 WorkflowDef 对象的版本——原来的实现强制先
    store.load(name) 按名字读盘，导致"刚生成、还没保存"的 workflow 无法
    预览；generate_workflow / build_workflow_from_summary 想在生成结果里
    自动附带一次 dry-run 预览时，只能在这里操作对象本身。

    返回：
      - batches: 按并发批次分层的 step 列表（每项含 id/name/type/role）
      - resolved_prompts: 能用 inputs 静态替换的 prompt 预览
        （`{step_id.output}` 这类运行时占位符原样保留，标注 runtime）
      - conditions: 每个 step 的 condition 表达式：
          - 只引用 inputs（或不引用任何 step/inputs，如纯字面量表达式）的，
            给出静态求值结果
          - 引用了任何 step 结果（如 `analyze.passed`）的，标注"运行时决定"
        [P9-3] 这里判断"是否只引用 inputs"复用 schema.py 的
        condition_referenced_names()，跟 validate() 的静态一致性检查
        共用同一套 ast 解析逻辑，不重复判断标准。
    """
    from mini_agent.workflow.runner import WorkflowRunner
    from mini_agent.workflow.schema import condition_referenced_names

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
    # [P11 §2a] 收集"prompt 里引用了 inputs 变量、但当前 inputs 没给对应
    # 值"的占位符——这些会被 _resolve_prompt 原样保留在最终发出去的 prompt
    # 里，之前完全没有信号，只能等运行完自己读输出发现。dry-run 阶段暴露
    # 出来，让用户在保存/运行前就能看到"这次调用如果不补 inputs.xxx，
    # prompt 里会带着大括号原样发出去"。
    unresolved_placeholders: dict[str, list[str]] = {}
    for s in wf.steps:
        prompt = s.prompt or ""
        _missing: list[str] = []

        def _sub(m: re.Match) -> str:
            key = m.group(1)
            if key in parsed_inputs:
                return str(parsed_inputs[key])
            if key not in _missing:
                _missing.append(key)
            return m.group(0)  # 保留原样，标注为运行时占位符（含 '.' 的已被正则排除）

        resolved_prompts[s.id] = _PARAM_PLACEHOLDER_RE.sub(_sub, prompt)
        if _missing:
            unresolved_placeholders[s.id] = _missing

    conditions: dict[str, str] = {}
    for s in wf.steps:
        cond = getattr(s, "condition", None)
        if not cond:
            continue
        referenced = condition_referenced_names(cond)
        if referenced - {"inputs"}:
            # 引用了至少一个 step 结果，涉及运行时输出/评分依赖，交给
            # runner 在真正执行时求值。
            conditions[s.id] = f"{cond}  （运行时决定，无法预览）"
            continue
        try:
            # 只引用 inputs（或不引用任何名字）的表达式可以静态求值：
            # 用跟 runner._eval_condition 一致的 `inputs.xxx` 命名空间
            # （而不是把 inputs 摊平进顶层命名空间），保持两处求值口径一致。
            import types
            ns = {"inputs": types.SimpleNamespace(**parsed_inputs)}
            value = eval(cond, {"__builtins__": {}}, ns)  # noqa: S307
            conditions[s.id] = f"{cond}  → {value!r}"
        except Exception:
            conditions[s.id] = f"{cond}  （无法静态求值，运行时决定）"

    return {
        "workflow_name": wf.name,
        "batches": batch_view,
        "resolved_prompts": resolved_prompts,
        "conditions": conditions,
        "unresolved_placeholders": unresolved_placeholders,
    }
