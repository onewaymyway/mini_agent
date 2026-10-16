"""novel_writer/runner.py — 单步执行（方案 4.1 / 4.3 / 5.1 的 runner）。

流程：`run_step()` → 按执行器分派
  * skill：`build_prompt()` → `invoker(...)`（真实实现为 `FrameworkInvoker`，内部走
    `mini_agent.workflow.WorkflowRunner` 的 `skill_agent` 步骤；测试注入桩）
    → 读取 receipt.json → `validators` 校验 → 必要时在**新的独立 agent** 上重试
  * python：`PY_HANDLERS[step.handler]`（找不到处理器 = blocked，不假装成功）

结束后按闸门决定状态：needs_user → waiting_user；review → waiting_review；否则 passed。
本模块不做闸门判定之外的流程控制（前沿、预算、暂停都在 worker.py）。
"""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from novel_writer import config
from novel_writer import gates as G
from novel_writer import state as st
from novel_writer import steps as S
from novel_writer import validators as V
from novel_writer.fsutil import atomic_write_json, read_json
from novel_writer.workspace import Workspace

WORKFLOW_NAME = "skill_step"
RECEIPT_FILE = "receipt.json"
MAX_RECEIPT_ATTEMPTS = 2           # 同一次执行内，回执/产出不合格时最多再让 agent 补一次
DEFAULT_MAX_TURNS = 50
DEFAULT_TIMEOUT_SEC = 900


class StepFailed(RuntimeError):
    pass


@dataclass
class InvokeResult:
    ok: bool
    error: str = ""


Invoker = Callable[..., InvokeResult]


# ── prompt ─────────────────────────────────────────────────────────────────

_PREAMBLE = """【运行环境】你在后台无人值守地执行本步骤，**不能等待或向作者提问**。
遇到需要作者决定的事项，不要停下来提问，改为在回执的 needs_user 中列出（每项含 id、question，可选 options）。

【统一路径】下表是本项目唯一认可的文件路径。skill 原文中即使写着其它路径（如 project/summaries/、novel_blueprint.json、裸 simulations/），
一律以下表为准：
{paths}

【本步任务】{title}
{instructions}

【输入文件（先读取）】
{inputs}

【作者指导】{author_notes}

【作者对待确认事项的答复】{answers}
{hint}
【回执】全部工作完成后，把回执 JSON 写入结果文件 receipt.json（路径由系统指定），格式为：
{receipt_example}
最终回复只需简短说明完成情况；回执文件才是结果。
"""

_RECEIPT_EXAMPLE = (
    '{"status": "done 或 needs_user", "summary": "一句话", '
    '"outputs": ["相对路径"], "needs_user": [{"id": "q1", "question": "...", "options": []}]}'
)


def _safe_id(step_id: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]', "_", step_id)


def _note_file(ws: Workspace, kind: str, step_id: str) -> Path:
    return ws.studio / kind / f"{_safe_id(step_id)}.md"


def _answers_file(ws: Workspace, step_id: str) -> Path:
    return ws.studio / "answers" / f"{_safe_id(step_id)}.json"


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _input_listing(ws: Workspace, step: S.StepDef, limit: int = 40) -> str:
    files = st.expand_inputs(ws, step.inputs)
    lines = []
    for p in files[:limit]:
        rel_path = p.relative_to(ws.root).as_posix() if p.is_absolute() else str(p)
        lines.append(f"- `{rel_path}`" + ("" if p.is_file() else "（尚不存在）"))
    if len(files) > limit:
        lines.append(f"- …其余 {len(files) - limit} 个文件略")
    return "\n".join(lines) or "（无）"


def build_prompt(ws: Workspace, step: S.StepDef, *, hint: str = "") -> str:
    note = _read_text(_note_file(ws, "author_notes", step.id))
    answers_raw = read_json(_answers_file(ws, step.id), default=None)
    answers = json.dumps(answers_raw, ensure_ascii=False, indent=2) if answers_raw else "（无）"
    hint_block = f"\n【上次执行的问题，必须修正】{hint}\n" if hint else ""
    return _PREAMBLE.format(
        paths=ws.paths_table_markdown(),
        title=step.title,
        instructions=step.instructions or "（按 skill 说明执行）",
        inputs=_input_listing(ws, step),
        author_notes=note or "（无）",
        answers=answers,
        hint=hint_block,
        receipt_example=_RECEIPT_EXAMPLE,
    )


# ── skill 执行 ─────────────────────────────────────────────────────────────


def run_skill_step(
    ws: Workspace, step: S.StepDef, *, invoker: Invoker, settings: Dict[str, Any], run_no: int,
) -> Dict[str, Any]:
    """执行一个 skill 步骤，返回通过校验的回执。失败抛 StepFailed。"""
    run_dir = ws.step_dir(step.id, run_no)
    run_dir.mkdir(parents=True, exist_ok=True)
    max_turns = int((settings.get("max_turns_overrides") or {}).get(step.id, DEFAULT_MAX_TURNS))
    hint = ""
    for _ in range(MAX_RECEIPT_ATTEMPTS):
        prompt = build_prompt(ws, step, hint=hint)
        result = invoker(
            skill=step.skill, prompt=prompt, export_dir=run_dir, step_id=step.id,
            max_turns=max_turns, timeout=DEFAULT_TIMEOUT_SEC,
        )
        if not result.ok:
            raise StepFailed(f"workflow 执行未成功：{result.error or '未知原因'}")
        receipt = read_json(run_dir / RECEIPT_FILE, default=None)
        problems = V.receipt_problems(receipt) if receipt is not None else [f"缺少回执 {RECEIPT_FILE}"]
        if not problems and receipt.get("status") == "done":
            problems = [f"缺少产出 {m}" for m in V.missing_outputs(ws, step.outputs)]
        if not problems:
            return receipt
        hint = "；".join(problems)
    raise StepFailed(f"回执或产出校验失败：{hint}")


class FrameworkInvoker:
    """真实执行器：通过 mini_agent 的 `WorkflowRunner` 运行 `workflows/skill_step.yaml`。

    每次调用运行时改写 skill_name / 结果文件契约（与 world_simulator 的做法一致），
    prompt 经 inputs 传入（不放进 yaml，避免 prompt 里的花括号被占位符解析器误处理）。
    """

    def __init__(self, cfg: Any = None) -> None:
        self._cfg = cfg

    def __call__(
        self, *, skill: str, prompt: str, export_dir: Path, step_id: str,
        max_turns: Optional[int] = None, timeout: Optional[int] = None,
    ) -> InvokeResult:
        _ensure_framework_on_path()
        try:
            from mini_agent.workflow.runner import WorkflowRunner
            from mini_agent.workflow.store import WorkflowStore
        except ImportError as exc:
            return InvokeResult(False, f"未检测到 mini_agent 框架（{exc}），无法执行 skill")

        cfg = self._cfg or config.load_llm_cfg()
        wf = WorkflowStore(config.PROJECT_ROOT).load(WORKFLOW_NAME)
        if wf is None or not getattr(wf, "steps", None):
            return InvokeResult(False, f"找不到工作流定义 workflows/{WORKFLOW_NAME}.yaml")
        step = wf.steps[0]
        step.id = "skill_step"
        step.skill_name = skill
        step.prompt = "{task_prompt}"
        step.result_file = RECEIPT_FILE
        step.result_file_required_keys = ["status"]
        if max_turns:
            setattr(step, "max_turns", max_turns)
        if timeout:
            setattr(step, "timeout", timeout)
        export_dir.mkdir(parents=True, exist_ok=True)
        result = WorkflowRunner(cfg).run(wf, {"task_prompt": prompt}, output_export_dir=str(export_dir))
        if result.status == "done":
            return InvokeResult(True)
        errors = [str(getattr(sr, "error", "") or "") for sr in (result.step_results or [])]
        detail = result.error or "; ".join(e for e in errors if e) or result.status
        return InvokeResult(False, str(detail))


def _ensure_framework_on_path() -> None:
    """本项目挂在主项目 external_projects/ 下时，把主项目根加入 sys.path（与 config 的兜底一致）。"""
    detected = config._detect_in_place_main_project_root()
    if detected is not None and str(detected) not in sys.path:
        sys.path.insert(0, str(detected))


# ── python 步骤处理器 ──────────────────────────────────────────────────────

PyHandler = Callable[[Workspace, S.StepDef, Dict[str, Any]], Optional[Dict[str, Any]]]


def _h_init_workspace(ws: Workspace, step: S.StepDef, settings: Dict[str, Any]) -> Dict[str, Any]:
    if not ws.is_initialized():
        raise StepFailed("工作目录未初始化（缺少 _studio/manifest.json）")
    return {"summary": "工作目录已就绪"}


def _h_init_word_count(ws: Workspace, step: S.StepDef, settings: Dict[str, Any]) -> Dict[str, Any]:
    blueprint = read_json(ws.path("blueprint"), default=None)
    if not isinstance(blueprint, dict):
        raise StepFailed("缺少蓝图，无法计算字数配额")
    meta = blueprint.get("meta") or {}
    per_chapter = {}
    for ch in blueprint.get("chapters") or []:
        if isinstance(ch, dict) and isinstance(ch.get("id"), int):
            per_chapter[str(ch["id"])] = ch.get("word_budget")
    data = {
        "schema_version": 1,
        "total_target": meta.get("target_length"),
        "per_chapter_budget": per_chapter,
        "deviation_tolerance": 0.05,
    }
    atomic_write_json(ws.path("word_count"), data)
    return {"summary": f"字数配额已写入（{len(per_chapter)} 章）"}


def _h_checkpoint(ws: Workspace, step: S.StepDef, settings: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    # 检查点本身不执行任何工作：由 runner 直接置为 waiting_review
    return None


DEFAULT_PY_HANDLERS: Dict[str, PyHandler] = {
    "init_workspace": _h_init_workspace,
    "init_word_count": _h_init_word_count,
    "checkpoint": _h_checkpoint,
}


# ── 单步总入口 ─────────────────────────────────────────────────────────────


def _gate_status(step: S.StepDef, settings: Dict[str, Any]) -> str:
    gate = G.resolve_gate(step, settings).gate
    return st.ST_WAITING_REVIEW if gate == G.GATE_REVIEW else st.ST_PASSED


def run_step(
    ws: Workspace, step: S.StepDef, *, invoker: Optional[Invoker], settings: Dict[str, Any],
    handlers: Optional[Dict[str, PyHandler]] = None,
) -> str:
    """执行一个步骤并写回状态，返回最终状态。"""
    handlers = DEFAULT_PY_HANDLERS if handlers is None else handlers
    attempts = int(st.get_step_state(ws, step.id).get("attempts", 0)) + 1
    started = time.monotonic()
    st.update_step(ws, step.id, status=st.ST_RUNNING, attempts=attempts, started_at=st.now_iso(),
                   error=None, finished_at=None, needs_user=None)
    st.heartbeat(ws, step=step.id)

    fields: Dict[str, Any] = {}
    try:
        if step.executor == S.EXECUTOR_PYTHON:
            status, fields = _run_python(ws, step, handlers, settings)
        else:
            status, fields = _run_skill(ws, step, invoker, settings, attempts)
    except StepFailed as exc:
        status, fields = st.ST_FAILED, {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - 单步异常不能拖垮 worker，记入状态
        status, fields = st.ST_FAILED, {"error": f"{type(exc).__name__}: {exc}"}

    duration = round(time.monotonic() - started, 2)
    final = {"status": status, "finished_at": st.now_iso(), "duration_sec": duration, **fields}
    if status in (st.ST_PASSED, st.ST_WAITING_REVIEW):
        final["input_hash"] = st.input_hash(ws, step)
    st.update_step(ws, step.id, **final)
    st.append_usage(ws, {
        "step": step.id, "attempt": attempts, "executor": step.executor, "skill": step.skill or None,
        "status": status, "duration_sec": duration, "error": fields.get("error"),
    })
    return status


def _run_python(
    ws: Workspace, step: S.StepDef, handlers: Dict[str, PyHandler], settings: Dict[str, Any],
) -> Tuple[str, Dict[str, Any]]:
    if step.handler == "checkpoint":
        return st.ST_WAITING_REVIEW, {"note": "强制检查点：等待作者确认"}
    handler = handlers.get(step.handler)
    if handler is None:
        return st.ST_BLOCKED, {"error": f"{step.handler}：{_blocked_hint()}"}
    result = handler(ws, step, settings) or {}
    fields = {k: v for k, v in result.items() if k == "summary"}
    return _gate_status(step, settings), fields


def _run_skill(
    ws: Workspace, step: S.StepDef, invoker: Optional[Invoker], settings: Dict[str, Any], attempts: int,
) -> Tuple[str, Dict[str, Any]]:
    if invoker is None:
        raise StepFailed("未配置执行器（invoker）")
    receipt = run_skill_step(ws, step, invoker=invoker, settings=settings, run_no=attempts)
    summary = str(receipt.get("summary") or "")
    if receipt.get("status") == "needs_user":
        return st.ST_WAITING_USER, {"needs_user": receipt.get("needs_user"), "summary": summary}
    return _gate_status(step, settings), {"summary": summary}


def _blocked_hint() -> str:
    from novel_writer.pipeline import BLOCKED_HINT

    return BLOCKED_HINT


def describe_handlers(handlers: Dict[str, PyHandler]) -> List[str]:
    return sorted(handlers)
