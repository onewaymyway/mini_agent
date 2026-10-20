"""novel_writer/tool_api.py — 唯一承载业务逻辑的一层（M0 版本）。

约定与 world_simulator/tool_api.py 相同：入参只用基本类型，返回统一的
`{"ok": True, "data": {...}}` 或 `{"ok": False, "error": {"type", "message"}}`，
**不抛异常**。entrypoints / service_cli / Streamlit 都只是它的壳。
M1 起在这里追加运行/暂停/步骤操作等接口。
"""

from __future__ import annotations

import re

import datetime as _dt
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from novel_writer import config, shelf, skills_catalog
from novel_writer import settings as settings_mod
from novel_writer.fsutil import read_json
from novel_writer.workspace import Workspace, WorkspaceError, workspace_dir_name


def _ok(data: Any) -> Dict[str, Any]:
    return {"ok": True, "data": data}


def _err(kind: str, message: str) -> Dict[str, Any]:
    return {"ok": False, "error": {"type": kind, "message": message}}


def _guard(fn: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    try:
        return fn()
    except WorkspaceError as exc:
        return _err("workspace_error", str(exc))
    except settings_mod.SettingsError as exc:
        return _err("validation_error", str(exc))
    except Exception as exc:  # noqa: BLE001 - 接口层不向外抛异常
        return _err("internal_error", f"{type(exc).__name__}: {exc}")


def create_novel(
    title: str,
    *,
    workspace: Optional[str] = None,
    gear: str = "G0",
    quality: str = "standard",
    total_chapters_planned: int = 0,
    novels_dir: Optional[str] = None,
    index_file: Optional[str] = None,
) -> Dict[str, Any]:
    """新建小说：创建工作目录并初始化。`workspace` 为空时用默认位置
    `data/novels/<书名>_<时间戳>/`；指定了其它位置则登记到书架索引。"""

    def run() -> Dict[str, Any]:
        base = Path(novels_dir) if novels_dir else config.NOVELS_DIR
        custom = bool(workspace)
        root = Path(workspace).expanduser() if custom else base / workspace_dir_name(title)
        ws = Workspace(root)
        result = ws.init(title, gear=gear, quality=quality, total_chapters_planned=total_chapters_planned)
        if custom:
            shelf.register(ws.root, index_file=Path(index_file) if index_file else None)
        return _ok(result)

    return _guard(run)


def list_novels(*, novels_dir: Optional[str] = None, index_file: Optional[str] = None) -> Dict[str, Any]:
    return _guard(
        lambda: _ok(
            {
                "novels": shelf.list_novels(
                    Path(novels_dir) if novels_dir else None, Path(index_file) if index_file else None
                )
            }
        )
    )


def get_novel(workspace: str) -> Dict[str, Any]:
    """项目恢复摘要 + 当前设置。"""

    def run() -> Dict[str, Any]:
        ws = Workspace(workspace)
        ws.touch_accessed()
        return _ok({"recover": ws.recover(), "settings": ws.settings()})

    return _guard(run)


def novel_health(workspace: str) -> Dict[str, Any]:
    return _guard(lambda: _ok(Workspace(workspace).health().to_dict()))


def update_settings(workspace: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    """合并更新设置（挡位/质量档/预算等）；非法取值返回 validation_error。"""

    def run() -> Dict[str, Any]:
        ws = Workspace(workspace)
        current = ws.settings()
        merged = dict(current)
        for key, value in (updates or {}).items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = value
        return _ok(ws.save_settings(merged))

    return _guard(run)


def list_skills() -> Dict[str, Any]:
    return _guard(lambda: _ok({"skills": skills_catalog.list_skills()}))


def project_health() -> Dict[str, Any]:
    """项目级健康检查（不依赖 LLM/网络）：skills 完整、数据目录可写。"""

    def run() -> Dict[str, Any]:
        problems = []
        rows = skills_catalog.list_skills()
        missing = [r["name"] for r in rows if not r["present"]]
        uncatalogued = [r["name"] for r in rows if r["present"] and not r["catalogued"]]
        mismatched = [r["name"] for r in rows if not r["name_matches_dir"]]
        if missing:
            problems.append(f"缺少 skill：{missing}")
        if uncatalogued:
            problems.append(f"skills/ 下有未登记的 skill：{uncatalogued}")
        if mismatched:
            problems.append(f"SKILL.md 的 name 与目录名不一致：{mismatched}")
        try:
            config.ensure_dirs()
            probe = config.DATA_DIR / ".health_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            problems.append(f"数据目录不可写：{exc}")
        return _ok({"ok": not problems, "problems": problems, "skills_total": len(rows), "checked_at": _dt.datetime.now().isoformat(timespec="seconds")})

    return _guard(run)


# ───────────────────────── M1：流水线运行 / 步骤操作 ─────────────────────────
# 以下接口只写状态与控制文件；真正的执行在后台 worker 子进程里（方案 6.5）。

import os as _os  # noqa: E402
import subprocess as _subprocess  # noqa: E402
import sys as _sys  # noqa: E402

from novel_writer import pipeline as _pipeline  # noqa: E402
from novel_writer import state as _state  # noqa: E402
from novel_writer import steps as _steps  # noqa: E402
from novel_writer.fsutil import atomic_write_json as _atomic_write_json  # noqa: E402
from novel_writer.runner import _answers_file, _note_file  # noqa: E402


def _workspace_or_raise(workspace: str) -> Workspace:
    ws = Workspace(workspace)
    if not ws.is_initialized():
        raise WorkspaceError(f"不是已初始化的 novel_writer 工作目录：{ws.root}")
    return ws


def _find_step(ws: Workspace, step_id: str) -> _steps.StepDef:
    plan = _pipeline.build_plan(ws)
    for step in plan:
        if step.id == step_id:
            return step
    raise WorkspaceError(f"流水线里没有步骤 {step_id!r}（可能是章节数未展开，或步骤 id 写错）")


def _spawn_worker(ws: Workspace, mode: str) -> int:
    entry = config.PROJECT_ROOT / "entrypoints" / "run_novel.py"
    log_path = ws.studio / "run.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    kwargs: Dict[str, Any] = {}
    if _os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    with log_path.open("a", encoding="utf-8") as log:
        proc = _subprocess.Popen(
            [_sys.executable, "-u", str(entry), "--workspace", str(ws.root), "--mode", mode],
            cwd=str(config.PROJECT_ROOT), stdout=log, stderr=_subprocess.STDOUT, close_fds=True, **kwargs,
        )
    return proc.pid


def pipeline_status(workspace: str) -> Dict[str, Any]:
    """流水线视图：每个步骤的状态/闸门/尝试次数/错误，当前前沿，worker 与控制状态。"""

    def run() -> Dict[str, Any]:
        ws = _workspace_or_raise(workspace)
        settings = ws.settings()
        plan = _pipeline.build_plan(ws)
        _state.adopt_legacy_steps(ws)  # 改版后的老数据：先认领旧结果，避免界面上出现一串「待运行」
        states = _state.load_state(ws)["steps"]
        statuses = {sid: e.get("status", _state.ST_PENDING) for sid, e in states.items()}
        rows = _pipeline.describe_plan(plan, statuses, settings)
        for row in rows:
            entry = states.get(row["id"], {})
            for key in ("attempts", "error", "summary", "needs_user", "duration_sec", "note", "finished_at"):
                row[key] = entry.get(key)
        frontier = _pipeline.frontier(plan, statuses, settings)
        counts: Dict[str, int] = {}
        for row in rows:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        return _ok({
            "workspace": str(ws.root),
            "gear": settings["gear"],
            "quality": settings["quality"],
            "steps": rows,
            "frontier": frontier.id if frontier else None,
            "counts": counts,
            "stale_count": counts.get(_state.ST_STALE, 0),
            "worker": {"alive": _state.worker_alive(ws), **_state.read_worker(ws)},
            "control": _state.read_control(ws),
        })

    return _guard(run)


def run_novel(workspace: str, *, mode: str = "until_gate", step_id: Optional[str] = None,
              spawn: bool = True) -> Dict[str, Any]:
    """请求运行：写入控制指令，并在没有存活 worker 时拉起后台 worker。

    mode: until_gate（推进到下一个需要人的地方）| single（只跑一步）。
    step_id: 指定运行某一步（手动挡下的 ▶，或重跑）；不指定则按前沿推进。
    """

    def run() -> Dict[str, Any]:
        if mode not in ("until_gate", "single"):
            raise _settings_error(f"未知运行模式 {mode!r}")
        ws = _workspace_or_raise(workspace)
        if step_id:
            _find_step(ws, step_id)
        _state.write_control(ws, command="run", mode=mode, run_step=step_id or None)
        alive = _state.worker_alive(ws)
        pid = None
        # 测试/受限环境可设 NOVEL_WRITER_NO_SPAWN=1，只写控制指令、不拉起后台进程
        allow_spawn = spawn and not _os.environ.get("NOVEL_WRITER_NO_SPAWN")
        if not alive and allow_spawn:
            pid = _spawn_worker(ws, mode)
        if alive:
            info = _state.read_worker(ws)
            state_text = (f"当前任务：{info.get('step') or '（启动中）'}" if info.get("step") else "当前任务启动中")
            message = (f"已有后台任务在运行（{state_text}）。本步已排队，但当前任务结束后不会自动执行，"
                       "请等它结束后再点一次「运行此步」。")
            outcome = "queued_behind_running"
        elif pid is not None:
            message = f"已启动后台任务（pid {pid}）。进度见下方运行状态，日志在 studio/run.log。"
            outcome = "spawned"
        elif not allow_spawn:
            message = "已写入运行指令（当前环境不拉起后台进程）。"
            outcome = "control_only"
        else:
            message = "已写入运行指令，但没有拉起后台进程。"
            outcome = "not_spawned"
        return _ok({"queued": True, "worker_alive": alive, "spawned_pid": pid, "mode": mode,
                    "run_step": step_id or None, "outcome": outcome, "message": message})

    return _guard(run)


def pause_novel(workspace: str) -> Dict[str, Any]:
    """请求暂停：当前步骤结束后 worker 退出（不打断正在执行的 LLM 调用）。"""
    return _guard(lambda: _ok({"command": _state.write_control(_workspace_or_raise(workspace), command="pause")["command"]}))


def stop_novel(workspace: str) -> Dict[str, Any]:
    """请求停止：与暂停相同的协作式生效点，但不会被视为可续跑的暂停（由用户再次运行）。"""
    return _guard(lambda: _ok({"command": _state.write_control(_workspace_or_raise(workspace), command="stop")["command"]}))


def rebaseline_stale(workspace: str) -> Dict[str, Any]:
    """作者确认：当前「上游产物已变化」的需重跑步骤产物无误，以现状重新基线（不执行任何步骤）。"""

    def run() -> Dict[str, Any]:
        from novel_writer import pipeline as P

        ws = _workspace_or_raise(workspace)
        plan = P.build_plan(ws)
        ids = [sid for sid, e in _state.load_state(ws)["steps"].items()
               if e.get("status") == _state.ST_STALE and str(e.get("note") or "").startswith("上游产物已变化")]
        done = _state.rebaseline_stale(ws, plan, ids)
        return _ok({"rebaselined": done})

    return _guard(run)


def write_history(workspace: str, *, path: Optional[str] = None, writer: Optional[str] = None,
                  kind: Optional[str] = None, chapter: Optional[int] = None, limit: int = 200) -> Dict[str, Any]:
    """写入历史（最新在前）：谁在什么时候改写了哪个文件，前后指纹各是什么。跨分段读取。"""

    def run() -> Dict[str, Any]:
        ws = _workspace_or_raise(workspace)
        return _ok({"events": _state.read_write_history(ws, path=path, writer=writer, kind=kind,
                                                       chapter=chapter, limit=limit)})

    return _guard(run)


def approve_step(workspace: str, step_id: str) -> Dict[str, Any]:
    """批准一个 waiting_review 的步骤，置为 passed。"""

    def run() -> Dict[str, Any]:
        ws = _workspace_or_raise(workspace)
        current = _state.get_step_state(ws, step_id)
        if current["status"] != _state.ST_WAITING_REVIEW:
            raise WorkspaceError(f"步骤 {step_id} 当前状态为 {current['status']}，只有 waiting_review 可以批准")
        entry = _state.update_step(ws, step_id, status=_state.ST_PASSED, approved_at=_state.now_iso())
        return _ok({"step": step_id, "status": entry["status"]})

    return _guard(run)


def rerun_step(workspace: str, step_id: str) -> Dict[str, Any]:
    """把步骤重置为 pending（保留尝试次数）；之后用 run_novel(step_id=...) 执行。"""

    def run() -> Dict[str, Any]:
        ws = _workspace_or_raise(workspace)
        _find_step(ws, step_id)
        if _state.get_step_state(ws, step_id)["status"] == _state.ST_RUNNING:
            raise WorkspaceError(f"步骤 {step_id} 正在执行，不能重跑")
        entry = _state.reset_step(ws, step_id)
        return _ok({"step": step_id, "status": entry["status"]})

    return _guard(run)


def answer_step(workspace: str, step_id: str, answers: Dict[str, Any]) -> Dict[str, Any]:
    """回答 waiting_user 步骤的 needs_user 事项，答复随下次执行的 prompt 注入。"""

    def run() -> Dict[str, Any]:
        ws = _workspace_or_raise(workspace)
        current = _state.get_step_state(ws, step_id)
        if current["status"] != _state.ST_WAITING_USER:
            raise WorkspaceError(f"步骤 {step_id} 当前不需要答复（状态 {current['status']}）")
        if not isinstance(answers, dict) or not answers:
            raise WorkspaceError("answers 必须是非空对象")
        if re.fullmatch(r"ch\d+\.review", step_id):
            from novel_writer import chapter_ops as _C

            routed = _C.route_review_answers(ws, step_id, answers, current.get("needs_user") or [])
            # 转交即排入修改循环并拉起后台 worker：修改 → 重新核查，不需要再手动点运行
            queued = run_novel(workspace, mode="single", step_id=routed["routed_to"])
            if not queued["ok"]:
                return queued
            return _ok({"step": step_id, "status": _state.ST_PASSED, **routed, "run": queued["data"]})
        _atomic_write_json(_answers_file(ws, step_id), {"answers": answers, "answered_at": _state.now_iso()})
        entry = _state.reset_step(ws, step_id)
        return _ok({"step": step_id, "status": entry["status"]})

    return _guard(run)


def write_author_note(workspace: str, step_id: str, text: str) -> Dict[str, Any]:
    """给某一步留作者指导（下次执行时注入 prompt）。空文本 = 清除。"""

    def run() -> Dict[str, Any]:
        ws = _workspace_or_raise(workspace)
        path = _note_file(ws, "author_notes", step_id)
        if not str(text or "").strip():
            if path.exists():
                path.unlink()
            return _ok({"step": step_id, "note": None})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(text).strip() + "\n", encoding="utf-8")
        return _ok({"step": step_id, "note": str(text).strip()})

    return _guard(run)


def _settings_error(message: str) -> Exception:
    return settings_mod.SettingsError(message)


# ───────────────────────── M2：新建向导 / 规划 / 设定库 / 线索 ─────────────────────────

from novel_writer import gates as _gates  # noqa: E402
from novel_writer import intake as _intake  # noqa: E402
from novel_writer import views as _views  # noqa: E402


def save_intake(workspace: str, intake: Dict[str, Any]) -> Dict[str, Any]:
    """保存新建向导的意图：写 `_studio/intake.json` 与注入用的 `intake_note.md`。"""

    def run() -> Dict[str, Any]:
        ws = _workspace_or_raise(workspace)
        clean = _intake.clean_intake(intake)
        _atomic_write_json(ws.path("studio_intake"), {"intake": clean, "saved_at": _state.now_iso()})
        note = _intake.intake_note(clean)
        path = ws.path("intake_note")
        if note:
            _views.atomic_write_text(path, note + "\n")
        elif path.exists():
            path.unlink()
        return _ok({"fields": sorted(k for k, v in clean.items() if v)})

    return _guard(run)


def get_intake(workspace: str) -> Dict[str, Any]:
    def run() -> Dict[str, Any]:
        ws = _workspace_or_raise(workspace)
        data = read_json(ws.path("studio_intake"), default=None) or {}
        return _ok({"intake": data.get("intake") or {k: "" for k in _intake.INTAKE_KEYS}})

    return _guard(run)


def planning(workspace: str) -> Dict[str, Any]:
    return _guard(lambda: _ok(_views.planning_view(_workspace_or_raise(workspace))))


def save_chapters(workspace: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    return _guard(lambda: _ok(_views.save_blueprint_chapters(_workspace_or_raise(workspace), rows)))


def codex(workspace: str) -> Dict[str, Any]:
    return _guard(lambda: _ok(_views.codex_view(_workspace_or_raise(workspace))))


def save_card(workspace: str, name: str, text: str) -> Dict[str, Any]:
    return _guard(lambda: _ok(_views.save_character_card(_workspace_or_raise(workspace), name, text)))


def threads(workspace: str) -> Dict[str, Any]:
    return _guard(lambda: _ok(_views.threads_view(_workspace_or_raise(workspace))))


def set_step_gate(workspace: str, step_id: str, gate: Optional[str]) -> Dict[str, Any]:
    """设置单步闸门覆盖；gate 为 None 表示恢复挡位预设。检查点的覆盖只能更严格（见 gates.py）。"""

    def run() -> Dict[str, Any]:
        ws = _workspace_or_raise(workspace)
        _find_step(ws, step_id)
        current = ws.settings()
        overrides = dict(current.get("step_overrides") or {})
        if gate is None:
            overrides.pop(step_id, None)
        else:
            overrides[step_id] = gate
        return _ok({"settings": ws.save_settings({**current, "step_overrides": overrides})["step_overrides"]})

    return _guard(run)


# ───────────────────────── M3：章节（写作台 / 审校 / 阅读）─────────────────────────

def chapters(workspace: str) -> Dict[str, Any]:
    return _guard(lambda: _ok(_views.chapters_overview(_workspace_or_raise(workspace))))


def chapter(workspace: str, n: int) -> Dict[str, Any]:
    return _guard(lambda: _ok(_views.chapter_detail(_workspace_or_raise(workspace), int(n))))


def save_chapter(workspace: str, n: int, text: str) -> Dict[str, Any]:
    return _guard(lambda: _ok(_views.save_chapter_text(_workspace_or_raise(workspace), int(n), text)))


def export(workspace: str, fmt: str = "md") -> Dict[str, Any]:
    return _guard(lambda: _ok(_views.export_book(_workspace_or_raise(workspace), fmt)))


# ───────────────────────── M4：矩阵 / 度量 / 灵感 / 修订 / 步骤详情 ─────────────────────────

def matrix(workspace: str) -> Dict[str, Any]:
    return _guard(lambda: _ok(_views.matrix_view(_workspace_or_raise(workspace))))


def save_matrix(workspace: str, step_overrides: Dict[str, str], stage_gears: Dict[str, str],
                extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return _guard(lambda: _ok(_views.save_matrix(_workspace_or_raise(workspace), step_overrides or {},
                                                 stage_gears or {}, extra or {})))


def metrics(workspace: str) -> Dict[str, Any]:
    return _guard(lambda: _ok(_views.metrics_view(_workspace_or_raise(workspace))))


def step_detail(workspace: str, step_id: str) -> Dict[str, Any]:
    return _guard(lambda: _ok(_views.step_detail(_workspace_or_raise(workspace), step_id)))


def ideas(workspace: str) -> Dict[str, Any]:
    return _guard(lambda: _ok(_views.ideas_view(_workspace_or_raise(workspace))))


def add_idea(workspace: str, text: str) -> Dict[str, Any]:
    return _guard(lambda: _ok(_views.add_idea(_workspace_or_raise(workspace), text)))


def request_revision(workspace: str, chapter: int, level: str, instruction: str) -> Dict[str, Any]:
    return _guard(lambda: _ok(_views.request_revision(_workspace_or_raise(workspace), int(chapter), level, instruction)))


# ───────────────────────── 后台任务总览与管理 ─────────────────────────


def list_tasks(*, novels_dir: Optional[str] = None, index_file: Optional[str] = None) -> Dict[str, Any]:
    from novel_writer import tasks as _tasks

    return _guard(lambda: _ok({"tasks": _tasks.list_tasks(
        Path(novels_dir) if novels_dir else None, Path(index_file) if index_file else None)}))


def force_stop(workspace: str) -> Dict[str, Any]:
    from novel_writer import tasks as _tasks

    return _guard(lambda: _ok(_tasks.force_stop(workspace)))


def cancel_queued(workspace: str) -> Dict[str, Any]:
    from novel_writer import tasks as _tasks

    return _guard(lambda: _ok(_tasks.cancel_queued(workspace)))


def clear_stale_worker(workspace: str) -> Dict[str, Any]:
    from novel_writer import tasks as _tasks

    return _guard(lambda: _ok(_tasks.clear_stale_worker(workspace)))


def delete_request(workspace: str, request_kind: str, request_id: str) -> Dict[str, Any]:
    from novel_writer import tasks as _tasks

    return _guard(lambda: _ok(_tasks.delete_request(workspace, request_kind, request_id)))


# ───────────────────────── M5：收件箱 / 批量推进 / 手写旁路 / AI 选段辅助 ─────────────────────────

from novel_writer import batch as _batch  # noqa: E402
from novel_writer import inbox as _inbox  # noqa: E402


def request_revision_cycle(workspace: str, chapter: int, feedback: str) -> Dict[str, Any]:
    return _guard(lambda: _ok(_views.request_revision_cycle(_workspace_or_raise(workspace), int(chapter), feedback)))


def inbox(workspace: str) -> Dict[str, Any]:
    """单本收件箱（同时刷新快照文件）。"""
    return _guard(lambda: _ok(_inbox.refresh(_workspace_or_raise(workspace))))


def inbox_all(*, novels_dir: Optional[str] = None, index_file: Optional[str] = None) -> Dict[str, Any]:
    """书架上所有小说的收件箱汇总（只读，不刷新快照；快照由各书 worker 退出时写入）。"""

    def run() -> Dict[str, Any]:
        books = []
        for root in shelf.discover(Path(novels_dir) if novels_dir else None,
                                   Path(index_file) if index_file else None):
            ws = Workspace(root)
            data = _inbox.collect(ws)
            books.append({"workspace": data["workspace"], "title": _batch._title(ws), "gear": data["gear"],
                          "total": data["total"], "blocking": data["blocking"], "counts": data["counts"],
                          "items": data["items"]})
        return _ok({
            "books": books,
            "total": sum(b["total"] for b in books),
            "blocking": sum(b["blocking"] for b in books),
        })

    return _guard(run)


def advance_all_auto(*, novels_dir: Optional[str] = None, index_file: Optional[str] = None,
                     max_chapters: int = 1, dry_run: bool = False, invoker=None) -> Dict[str, Any]:
    """同步批量推进（入口脚本与测试使用）。界面请用 spawn_advance_all_auto 放到后台。"""

    def run() -> Dict[str, Any]:
        res = _batch.advance_all_auto(novels_dir=novels_dir, index_file=index_file,
                                      max_chapters=int(max_chapters), dry_run=dry_run, invoker=invoker)
        if not res.get("ok"):
            raise _settings_error(res.get("error") or "批量参数错误")
        return _ok(res)

    return _guard(run)


def spawn_advance_all_auto(*, max_chapters: int = 1) -> Dict[str, Any]:
    """后台拉起批量入口（界面按钮用）。受 NOVEL_WRITER_NO_SPAWN 控制，测试时只返回命令不拉起。"""

    def run() -> Dict[str, Any]:
        if not isinstance(max_chapters, int) or max_chapters < 1:
            raise _settings_error("max_chapters 必须是正整数")
        entry = config.PROJECT_ROOT / "entrypoints" / "advance_all_auto.py"
        log_path = config.DATA_DIR / "advance_all_auto.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [_sys.executable, "-u", str(entry), "--max-chapters", str(max_chapters)]
        if _os.environ.get("NOVEL_WRITER_NO_SPAWN"):
            return _ok({"spawned_pid": None, "command": cmd})
        kwargs: Dict[str, Any] = {}
        if _os.name == "nt":
            kwargs["creationflags"] = 0x00000008 | 0x00000200
        else:
            kwargs["start_new_session"] = True
        with log_path.open("a", encoding="utf-8") as log:
            proc = _subprocess.Popen(cmd, cwd=str(config.PROJECT_ROOT), stdout=log, stderr=_subprocess.STDOUT,
                                     close_fds=True, **kwargs)
        return _ok({"spawned_pid": proc.pid, "command": cmd, "log": str(log_path)})

    return _guard(run)


def mark_manual(workspace: str, step_id: str, note: str = "") -> Dict[str, Any]:
    """手写旁路：产出文件已由作者写好/上传，直接把步骤标为通过（需产出存在）。"""
    return _guard(lambda: _ok(_views.mark_manual(_workspace_or_raise(workspace), step_id, note)))


def request_assist(workspace: str, chapter: int, mode: str, selection: str, instruction: str = "") -> Dict[str, Any]:
    return _guard(lambda: _ok(_views.request_assist(_workspace_or_raise(workspace), int(chapter), mode,
                                                    selection, instruction)))


def assists(workspace: str, chapter: int) -> Dict[str, Any]:
    return _guard(lambda: _ok({"assists": _views.assists_for_chapter(_workspace_or_raise(workspace), int(chapter))}))


def adopt_assist(workspace: str, rid: str) -> Dict[str, Any]:
    return _guard(lambda: _ok(_views.adopt_assist(_workspace_or_raise(workspace), rid)))


# ───────────────────────── 运行状态（实时输出与结束提示，M7 界面用）─────────────────────────


def run_status(workspace: str, lines: int = 120) -> Dict[str, Any]:
    """worker 实时状态 + 日志尾部 + 上一次运行结果。界面每 2 秒轮询一次，只读文件。"""

    def run() -> Dict[str, Any]:
        ws = _workspace_or_raise(workspace)
        worker = _state.read_worker(ws)
        alive = _state.worker_alive(ws)
        return _ok({
            "alive": alive,
            "mode": worker.get("mode"),
            "step": worker.get("step"),
            "step_title": worker.get("step_title"),
            "step_started_at": worker.get("step_started_at"),
            "attempt": worker.get("attempt"),
            "started_at": worker.get("started_at"),
            "heartbeat": worker.get("heartbeat"),
            "finished_at": worker.get("finished_at"),
            "reason": worker.get("reason"),
            "log": _state.read_log_tail(ws, lines=lines),
            "last_run": _state.read_last_run(ws),
        })

    return _guard(run)


def next_step(workspace: str) -> Dict[str, Any]:
    """「下一步」的唯一定义：与 worker 选步骤用的是同一个前沿函数（pipeline.frontier）。

    界面的「运行下一步」按钮、单步选择框的默认值、流水线表的 ▶ 标记都读这里，
    按钮执行时把返回的 step 明确传给 worker，保证显示的步骤就是运行的步骤。
    action：run（可以直接运行）/ approve（等你批准）/ answer（等你回答）/
            rerun（失败，需重跑）/ wait（正在运行）/ none（阻塞，无法自动处理）/ completed。
    """

    def run() -> Dict[str, Any]:
        ws = _workspace_or_raise(workspace)
        settings = ws.settings()
        plan = _pipeline.build_plan(ws)
        states = _state.load_state(ws)["steps"]
        statuses = {sid: e.get("status", _state.ST_PENDING) for sid, e in states.items()}
        step = _pipeline.frontier(plan, statuses, settings)
        if step is None:
            return _ok({"step": None, "title": None, "status": None, "gate": None, "action": "completed",
                        "message": "流水线已完成，没有待运行的步骤。"})
        status = statuses.get(step.id, _state.ST_PENDING)
        gate = _gates.resolve_gate(step, settings).gate
        if status == _state.ST_WAITING_REVIEW:
            action, message = "approve", f"下一步是「{step.title}」，它正在等待你批准。请先批准，或改为重跑。"
        elif status == _state.ST_WAITING_USER:
            action, message = "answer", f"下一步是「{step.title}」，需要你先回答它的问题。"
        elif status == _state.ST_RUNNING:
            action, message = "wait", f"「{step.title}」正在运行，请等待它结束。"
        elif status == _state.ST_FAILED:
            action, message = "rerun", f"下一步是「{step.title}」，上次执行失败，需要先重跑。"
        elif status == _state.ST_BLOCKED:
            action, message = "none", f"下一步是「{step.title}」，但它的执行器尚未实现，无法自动运行。"
        else:
            action = "run"
            note = "（手动步骤：点击后会运行它）" if gate == _gates.GATE_MANUAL else ""
            message = f"下一步将运行：{step.title}{note}"
        return _ok({"step": step.id, "title": step.title, "status": status, "gate": gate,
                    "action": action, "message": message})

    return _guard(run)


def migrate_characters(workspace: str, dry_run: bool = False, confirm: bool = False,
                       use_llm: bool = False) -> Dict[str, Any]:
    """把旧版 characters.json 迁移为角色小文件（第四阶段）。

    - 旧文件合法：确定性迁移（回读比对一致才提交）。
    - 旧文件损坏：use_llm=True 时由 LLM（ledger-convert）直接读取原文写出角色文件，经严格校验；
      结果需要复核，confirm=True 才提交。use_llm=False 时返回 needs_llm。
    - dry_run=True：只报告。旧文件不删除，提交后改名为 .bak。
    """
    def run() -> Dict[str, Any]:
        from novel_writer import character_store as CS
        ws = _workspace_or_raise(workspace)
        convert = None
        if use_llm:
            from novel_writer.runner import FrameworkInvoker
            invoker = FrameworkInvoker()
            workdir = ws.root / "_studio" / "convert" / "characters"
            convert = lambda legacy: CS.llm_convert_legacy(legacy, workdir, invoker)  # noqa: E731
        try:
            return _ok(CS.migrate_legacy(ws.path("characters_legacy"), ws.path("characters_dir"),
                                         ws.path("characters"), dry_run=dry_run, convert=convert,
                                         confirm=confirm))
        except CS.CharacterStoreError as exc:
            return _err("migration_failed", "；".join(exc.problems))
    return _guard(run)


def migrate_plot_ledger(workspace: str, dry_run: bool = False, confirm: bool = False,
                        use_llm: bool = False) -> Dict[str, Any]:
    """把旧版 JSON 伏笔账本迁移为小文件存储（M9 第二阶段）。

    - 旧文件合法：确定性迁移，自动提交。
    - 旧文件损坏：use_llm=True 时，由 LLM（技能 ledger-convert）直接读取原文生成小文件，经严格校验；
      结果一律需要复核，生成「伏笔迁移报告.md」，confirm=True 才提交。use_llm=False 则返回 needs_llm。
    - dry_run=True：只报告，不提交。
    旧文件不删除，提交后改名为 .bak。
    """
    def run() -> Dict[str, Any]:
        from novel_writer import plot_delta as PD
        from novel_writer import plot_store as PS
        ws = _workspace_or_raise(workspace)
        convert = None
        if use_llm:
            from novel_writer.runner import FrameworkInvoker
            invoker = FrameworkInvoker()
            workdir = ws.root / "_studio" / "convert" / "plot_ledger"
            convert = lambda legacy: PS.llm_convert_legacy(legacy, workdir, invoker)  # noqa: E731
        try:
            return _ok(PS.migrate_legacy(ws.path("plot_legacy"), ws.path("plot_items_dir"), dry_run=dry_run,
                                         register_path=None if dry_run else ws.path("plot_register"),
                                         convert=convert, confirm=confirm))
        except PD.PlotDeltaError as exc:
            return _err("migration_failed", "；".join(exc.problems))
    return _guard(run)
