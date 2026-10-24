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

from novel_writer.textio import read_text_utf8
from novel_writer import config
from novel_writer import gates as G
from novel_writer import state as st
from novel_writer import steps as S
from novel_writer import validators as V
from novel_writer.fsutil import atomic_write_json, read_json
from novel_writer.workspace import Workspace

WORKFLOW_NAME = "skill_step"
RECEIPT_FILE = "receipt.json"
MAX_RECEIPT_ATTEMPTS = 3           # 同一次执行内，回执/产出/内容不合格时最多让 agent 补 2 次
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

【范围约束】本次会话只负责【本步任务】。不要检查、执行或修改其它步骤（尤其是后续章节的步骤）；
不要对其它步骤 id 运行 check_step.py；不要创建或修改不属于本步产出的章节文件。
本步完成、写好回执即结束，不要继续做后面的工作。

【统一路径】下表是本项目唯一认可的文件路径。skill 原文中即使写着其它路径（如 project/summaries/、novel_blueprint.json、裸 simulations/），
一律以下表为准：
{paths}

【本步任务】{title}
{instructions}

【输入文件（先读取）】
{inputs}

【作者意图（新建向导）】{intake}

【作者指导】{author_notes}

【作者对待确认事项的答复】{answers}
{protected}{hint}{selfcheck}
【回执】全部工作完成后，把回执 JSON 写入结果文件 receipt.json（路径由系统指定），格式为：
{receipt_example}
最终回复只需简短说明完成情况；回执文件才是结果。
"""

_RECEIPT_EXAMPLE = (
    '{"status": "done", "summary": "一句话", "outputs": ["相对路径"]}\n'
    '`status` 只能是英文单词 "done"（已完成）或 "needs_user"（有事项要作者决定）这两个值之一，'
    '不要写 complete / completed / success 等其它写法。\n'
    '有待作者决定的事项时：{"status": "needs_user", "summary": "一句话", '
    '"needs_user": [{"id": "q1", "question": "...", "options": []}]}'
)


def _safe_id(step_id: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]', "_", step_id)


def _note_file(ws: Workspace, kind: str, step_id: str) -> Path:
    return ws.studio / kind / f"{_safe_id(step_id)}.md"


def _answers_file(ws: Workspace, step_id: str) -> Path:
    return ws.studio / "answers" / f"{_safe_id(step_id)}.json"


def _read_text(path: Path) -> str:
    try:
        return read_text_utf8(path).strip()
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


def _selfcheck_block(ws: Workspace, step: S.StepDef) -> str:
    """声明了产出内容校验的步骤：让 agent 写完后在本会话内运行自检命令，问题当场改掉，再写回执。"""
    if not (step.extra or {}).get("output_check"):
        return ""
    script = config.PROJECT_ROOT / "entrypoints" / "check_step.py"
    cmd = f'"{sys.executable}" "{script}" --workspace "{ws.root}" --step "{step.id}"'
    return (
        "\n【自检（写回执之前必须做）】文件写完后，先运行下面的命令检查产出格式：\n"
        f"{cmd}\n"
        "输出 OK 才算通过；若列出了问题，就在**当前会话里直接修改相应文件**，再运行同一命令，直到输出 OK，然后再写回执。"
        "只有在确实无法执行命令时才可以跳过这一步。\n")


def build_prompt(ws: Workspace, step: S.StepDef, *, hint: str = "", protected: str = "") -> str:
    note = _read_text(_note_file(ws, "author_notes", step.id))
    answers_raw = read_json(_answers_file(ws, step.id), default=None)
    answers = json.dumps(answers_raw, ensure_ascii=False, indent=2) if answers_raw else "（无）"
    hint_block = f"\n【上次执行的问题，必须修正】{hint}\n" if hint else ""
    return _PREAMBLE.format(
        selfcheck=_selfcheck_block(ws, step),
        paths=ws.paths_table_markdown(),
        title=step.title,
        instructions=step.instructions or "（按 skill 说明执行）",
        inputs=_input_listing(ws, step),
        intake=_read_text(ws.path("intake_note")) or "（无）",
        author_notes=note or "（无）",
        answers=answers,
        hint=hint_block,
        protected=(
            "\n【受保护文件（本步骤禁止读取以外的任何操作）】以下文件属于后续步骤，只可阅读，不得修改、覆盖、删除或新建同名文件；"
            "即使发现它们与本步改动不一致，也只在回执 summary 里说明，留给对应步骤处理。"
            "框架会在本步结束后把对它们的任何改动自动还原，改了也无效：\n" + protected + "\n"
        ) if protected else "",
        receipt_example=_RECEIPT_EXAMPLE,
    )


# ── skill 执行 ─────────────────────────────────────────────────────────────


def run_skill_step(
    ws: Workspace, step: S.StepDef, *, invoker: Invoker, settings: Dict[str, Any], run_no: int,
) -> Dict[str, Any]:
    """执行一个 skill 步骤，返回通过校验的回执。失败抛 StepFailed。

    agent 可能改写任何文件（不只是声明的产出）。这里对比执行前后所有受追踪文件的指纹，
    把实际发生变化的文件记到本步骤名下，供脏标记判断「这次变化是谁造成的」。
    """
    from novel_writer import pipeline as P

    tracked = sorted({
        str(p.relative_to(ws.root))
        for step_def in P.build_plan(ws)
        for p in (list(st.expand_inputs(ws, step_def.inputs)) + list(st.expand_inputs(ws, step_def.outputs)))
    })
    before = {rel: st._file_digest(ws.root / rel) for rel in tracked}
    from novel_writer import step_guard as SG
    SG.bind_current_step(ws, step.id, run_no)
    try:
        return _run_skill_step_inner(ws, step, invoker=invoker, settings=settings, run_no=run_no)
    finally:
        SG.unbind_current_step(ws)
        changed = [rel for rel in tracked if st._file_digest(ws.root / rel) != before[rel]]
        if changed:
            st.record_writer_paths(ws, changed, step.id, before={rel: before[rel] for rel in changed}, kind="agent")


def _normalize_outputs(ws: Workspace, step: S.StepDef, *, since: float) -> List[str]:
    """智能体有时会把文件起成别的名字（如把章节标题加进文件名）。

    对每个缺失的、普通路径的产出：在同一目录里找本次运行新写入、且与预期文件「前缀_…后缀」
    结构相同的文件。恰好一个则改名为规范路径并记入日志；有多个则不猜，保持缺失并列出候选。
    返回实际改名的 (旧→新) 列表。
    """
    import os

    renamed: List[str] = []
    for spec in step.outputs:
        if V._is_glob(spec) or (ws.root / spec).is_file():
            continue
        target = ws.root / spec
        name = target.name
        if "_" not in name:
            continue
        prefix = name.split("_", 1)[0]          # 第11章
        suffix = "_" + name.rsplit("_", 1)[1]    # _候选B.md
        if not target.parent.is_dir():
            continue
        candidates = [
            f for f in target.parent.iterdir()
            if f.is_file() and f.name.startswith(prefix) and f.name.endswith(suffix)
            and f.name != name and f.stat().st_mtime >= since - 1
        ]
        if len(candidates) == 1:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(candidates[0], target)
            renamed.append(f"{candidates[0].name} → {name}")
            st.log_line(ws, f"🔧 {step.id}：产出文件名不符，已归一化：{candidates[0].name} → {name}")
    return renamed


def _run_skill_step_inner(
    ws: Workspace, step: S.StepDef, *, invoker: Invoker, settings: Dict[str, Any], run_no: int,
) -> Dict[str, Any]:
    run_dir = ws.step_dir(step.id, run_no)
    run_dir.mkdir(parents=True, exist_ok=True)
    max_turns = int((settings.get("max_turns_overrides") or {}).get(step.id, DEFAULT_MAX_TURNS))
    checks = (step.extra or {}).get("output_check")
    if checks and "character_files" in ([checks] if isinstance(checks, str) else list(checks)):
        from novel_writer import character_store as CS
        try:   # 改前备份：agent 写坏文件时可还原，不留半坏的角色文件
            CS.backup_dir(ws.path("characters_dir"), ws.studio / "backup" / "characters")
        except OSError as exc:
            st.log_line(ws, f"⚠ {step.id}：角色目录备份失败（{exc}），本次不做还原保护")
    hint = ""
    started = time.time()
    from novel_writer import pipeline as P
    from novel_writer import step_guard as SG
    plan = P.build_plan(ws)
    # 越界基线：步骤开始前的后续章节文件。每次尝试后与基线比较（累计），
    # 所以 agent 按提示删除/还原越界文件后，问题即消失。
    protected_rels = SG.forbidden_rels(ws, plan, step)
    guard_before = SG.snapshot(ws, protected_rels)
    guard_saved = SG.backup(ws, protected_rels)
    auto_restore = bool(settings.get("step_guard_auto_restore", True))
    for attempt in range(MAX_RECEIPT_ATTEMPTS):
        prompt = build_prompt(ws, step, hint=hint, protected=SG.protected_listing(protected_rels))
        (run_dir / f"prompt_{attempt + 1}.md").write_text(prompt, encoding="utf-8")
        result = invoker(
            skill=step.skill, prompt=prompt, export_dir=run_dir, step_id=step.id,
            max_turns=max_turns, timeout=DEFAULT_TIMEOUT_SEC,
        )
        if not result.ok:
            raise StepFailed(f"workflow 执行未成功：{result.error or '未知原因'}")
        out_of_scope = SG.boundary_violations(ws, plan, step, guard_before)
        restored_note = ""
        if out_of_scope:
            st.log_line(ws, f"⚠ {step.id}：越界修改了后续步骤的文件：{'、'.join(out_of_scope[:8])}")
            if auto_restore:
                failed = SG.restore(ws, out_of_scope, guard_saved)
                still = [r for r in out_of_scope if r in set(SG.boundary_violations(ws, plan, step, guard_before))]
                if not failed and not still:
                    st.log_line(ws, f"🔧 {step.id}：已自动还原越界改动的 {len(out_of_scope)} 个文件")
                    restored_note = (f"上次执行越界改动了后续步骤的文件（{'、'.join(out_of_scope[:6])}），"
                                     "框架已自动还原；本步骤不要再动它们。")
                    out_of_scope = []
                else:
                    out_of_scope = sorted(set(failed) | set(still)) or out_of_scope
        receipt = read_json(run_dir / RECEIPT_FILE, default=None)
        receipt, was = V.normalize_receipt(receipt)
        if was is not None:
            st.log_line(ws, f"🔧 {step.id}：回执 status 写成了 {was!r}，已按「完成」规范为 done")
        problems = V.receipt_problems(receipt) if receipt is not None else [f"缺少回执 {RECEIPT_FILE}"]
        if not problems and receipt.get("status") == "done":
            _normalize_outputs(ws, step, since=started)
            problems = [f"缺少产出 {m}" for m in V.missing_outputs(ws, step.outputs)]
        if not problems:
            problems = V.content_problems(ws, step)   # 产出存在但内容不合格（如择优报告 winner 非法）
            if problems:
                st.log_line(ws, f"⚠ {step.id} 产出内容不合格（第 {attempt + 1} 次），要求 agent 修正：" + "；".join(problems))
        if out_of_scope:
            problems = [
                f"越界修改了不属于本步骤的文件：{'、'.join(out_of_scope[:10])}。"
                "这些文件属于后续步骤，框架自动还原失败或已关闭自动还原，请把它们恢复为修改前的内容，只完成本步骤。"
            ] + problems
        if not problems:
            return receipt
        hint = "；".join(([restored_note] if restored_note else []) + problems)
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
        # 子 agent 以 verbose 运行：工具调用与结果会打印到标准输出，worker 把它重定向到 run.log，
        # 界面由此能看到运行中的中间输出（框架本身只在步骤结束时落盘会话，无法实时读取）。
        try:
            import copy as _copy

            cfg = _copy.copy(cfg)
            cfg.verbose = True
        except Exception:  # noqa: BLE001 - 复制失败时退回原配置，只是少了实时输出
            pass
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

PyHandler = Callable[..., Optional[Dict[str, Any]]]  # (ws, step, settings, invoker=None) -> dict | None


class NeedsUser(Exception):
    """处理器内部需要作者决定（例如修改循环超过 3 轮）：步骤挂起为 waiting_user。"""

    def __init__(self, summary: str, items: List[Dict[str, Any]]) -> None:
        super().__init__(summary)
        self.summary = summary
        self.items = items


def _h_init_workspace(ws: Workspace, step: S.StepDef, settings: Dict[str, Any], invoker=None) -> Dict[str, Any]:
    if not ws.is_initialized():
        raise StepFailed("工作目录未初始化（缺少 _studio/manifest.json）")
    return {"summary": "工作目录已就绪"}


def _h_init_word_count(ws: Workspace, step: S.StepDef, settings: Dict[str, Any], invoker=None) -> Dict[str, Any]:
    from novel_writer import blueprint_store as BS
    blueprint = BS.load_for_ws(ws, strict=False)
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


def _h_checkpoint(ws: Workspace, step: S.StepDef, settings: Dict[str, Any], invoker=None) -> Optional[Dict[str, Any]]:
    # 检查点本身不执行任何工作：由 runner 直接置为 waiting_review
    return None


DEFAULT_PY_HANDLERS: Dict[str, PyHandler] = {
    "init_workspace": _h_init_workspace,
    "init_word_count": _h_init_word_count,
    "checkpoint": _h_checkpoint,
}
# 章节循环的 python 步骤（M3）。放在模块末尾导入：chapter_ops 只在函数内部反向导入本模块。
from novel_writer.chapter_ops import CHAPTER_HANDLERS as _CHAPTER_HANDLERS  # noqa: E402

DEFAULT_PY_HANDLERS.update(_CHAPTER_HANDLERS)
from novel_writer.ondemand import HANDLERS as _ONDEMAND_HANDLERS  # noqa: E402

DEFAULT_PY_HANDLERS.update(_ONDEMAND_HANDLERS)


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
    started_iso = st.now_iso()
    st.heartbeat(ws, step=step.id, step_title=step.title, step_started_at=started_iso, attempt=attempts)
    st.log_line(ws, f"▶ {step.id}　{step.title}（第 {attempts} 次）")
    if step.skill:
        st.log_line(ws, f"  调用 skill：{step.skill}")

    fields: Dict[str, Any] = {}
    try:
        if step.executor == S.EXECUTOR_PYTHON:
            status, fields = _run_python(ws, step, handlers, settings, invoker)
        else:
            status, fields = _run_skill(ws, step, invoker, settings, attempts)
    except StepFailed as exc:
        status, fields = st.ST_FAILED, {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - 单步异常不能拖垮 worker，记入状态
        status, fields = st.ST_FAILED, {"error": f"{type(exc).__name__}: {exc}"}

    duration = round(time.monotonic() - started, 2)
    final = {"status": status, "finished_at": st.now_iso(), "duration_sec": duration, **fields}
    shown = fields.get("summary") or fields.get("error") or ""
    mark = {"passed": "✔", "waiting_review": "⏸", "waiting_user": "❓"}.get(status, "✖")
    st.log_line(ws, f"{mark} {step.id} → {status}　{shown}　（{duration}s）".rstrip())
    if status in (st.ST_PASSED, st.ST_WAITING_REVIEW):
        final["input_hash"] = st.input_hash(ws, step)
        final["input_digests"] = st.input_digests(ws, step)
        st.record_writers(ws, step)
    st.update_step(ws, step.id, **final)
    st.append_usage(ws, {
        "step": step.id, "attempt": attempts, "executor": step.executor, "skill": step.skill or None,
        "status": status, "duration_sec": duration, "error": fields.get("error"),
    })
    return status


def _run_python(
    ws: Workspace, step: S.StepDef, handlers: Dict[str, PyHandler], settings: Dict[str, Any],
    invoker: Optional[Invoker] = None,
) -> Tuple[str, Dict[str, Any]]:
    if step.handler == "checkpoint":
        return st.ST_WAITING_REVIEW, {"note": "强制检查点：等待作者确认"}
    handler = handlers.get(step.handler)
    if handler is None:
        return st.ST_BLOCKED, {"error": f"{step.handler}：{_blocked_hint()}"}
    try:
        result = handler(ws, step, settings, invoker=invoker) or {}
    except NeedsUser as exc:
        return st.ST_WAITING_USER, {"summary": exc.summary, "needs_user": exc.items}
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
