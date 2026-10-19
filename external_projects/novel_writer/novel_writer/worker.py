"""novel_writer/worker.py — 后台 worker 循环（方案 6.5）。

循环每一轮：读控制指令 → 重新展开流水线 → 标脏 → 确定前沿 → 查闸门/预算 → 执行 → 落盘。
  * 停止点只在两步之间（协作式）；单步受 workflow 的 timeout / max_turns 约束。
  * 任何一步需要人（waiting_review / waiting_user / manual 闸门 / 失败 / 阻塞 / 预算耗尽）都会让 worker 退出，
    并在 `worker.json` 的 reason 里写明原因；预算耗尽是**暂停**，不是失败，可续跑。
  * 启动时若发现遗留的 running 步骤（上一个 worker 被杀），一律置为 interrupted 再继续。
  * 同一本书同一时刻只允许一个存活 worker（心跳 90 秒内视为存活）。
"""

from __future__ import annotations

import threading
import time
import traceback
from typing import Any, Callable, Dict, List, Optional

from novel_writer import autopilot as AP
from novel_writer import gates as G
from novel_writer import pipeline as P
from novel_writer import runner as R
from novel_writer import state as st
from novel_writer import steps as S
from novel_writer import textio as TX
from novel_writer.workspace import Workspace

# 步骤失败后的自动重跑（用户要求：最多自动重跑三次）。只针对 failed；
# blocked（执行器未实现）、waiting_user、waiting_review 不重跑。
MAX_AUTO_RETRIES = 3
# 每次重跑前的等待秒数（给临时性故障，如接口限流，一点恢复时间）。测试中可改为 0。
RETRY_BACKOFF_SEC = (5, 15, 30)

MODES = ("until_gate", "single")
HEARTBEAT_INTERVAL_SEC = 15.0


class Budget:
    """每次 worker 运行的预算（方案 6.3 第 5 条：耗尽 = 暂停）。None 表示不限。"""

    def __init__(self, settings: Dict[str, Any], *, clock: Callable[[], float] = time.monotonic,
                 chapter_limit: Optional[int] = None) -> None:
        b = settings.get("budget") or {}
        self.max_chapters = b.get("max_chapters_per_run")
        if settings.get("gear") == "G2" and settings.get("semi_auto_batch_chapters"):
            batch = int(settings["semi_auto_batch_chapters"])
            self.max_chapters = min(self.max_chapters, batch) if self.max_chapters else batch
        # 调用方指定的章数上限（批量推进 advance_all_auto 用）只能收紧，不能放宽
        if chapter_limit:
            self.max_chapters = min(self.max_chapters, chapter_limit) if self.max_chapters else chapter_limit
        self.max_calls = b.get("max_llm_calls_per_run")
        self.max_minutes = b.get("max_wall_minutes_per_run")
        self.chapters: set = set()
        self.calls = 0
        self._clock = clock
        self._t0 = clock()

    def check(self, step: S.StepDef) -> Optional[str]:
        if self.max_minutes and (self._clock() - self._t0) / 60.0 >= self.max_minutes:
            return "budget_time"
        if P.is_chapter_start(step) and step.chapter not in self.chapters:
            if self.max_chapters and len(self.chapters) >= self.max_chapters:
                return "budget_chapters"
        if step.counts_as_llm_call and self.max_calls and self.calls >= self.max_calls:
            return "budget_calls"
        return None

    def record(self, step: S.StepDef) -> None:
        if P.is_chapter_start(step):
            self.chapters.add(step.chapter)
        if step.counts_as_llm_call:
            self.calls += 1


class _Heartbeat:
    """执行一步期间定期刷新 worker.json 心跳，避免长步骤被误判为孤儿。"""

    def __init__(self, ws: Workspace, interval: float) -> None:
        self._ws = ws
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                st.heartbeat(self._ws)
            except OSError:
                pass

    def __enter__(self) -> "_Heartbeat":
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._stop.set()
        self._thread.join(timeout=2)


def run_worker(
    ws: Workspace,
    *,
    mode: str = "until_gate",
    invoker: Optional[R.Invoker] = None,
    handlers: Optional[Dict[str, R.PyHandler]] = None,
    heartbeat_interval: float = HEARTBEAT_INTERVAL_SEC,
    max_steps: Optional[int] = None,
    max_chapters: Optional[int] = None,
) -> Dict[str, Any]:
    """运行 worker 直到需要人介入、完成、暂停或停止。返回本次运行的摘要（不抛异常）。

    max_chapters：本次运行额外的章数上限（只能收紧设置里的预算；批量推进用它做「每本 1 章」）。
    """
    if mode not in MODES:
        return {"ok": False, "reason": "invalid_mode", "error": f"未知模式 {mode!r}，可选 {list(MODES)}"}
    if not ws.is_initialized():
        return {"ok": False, "reason": "not_initialized", "error": f"不是已初始化的工作目录：{ws.root}"}
    if not st.acquire_worker(ws, mode=mode):
        return {"ok": False, "reason": "already_running", "error": "该小说已有存活的 worker"}

    executed: List[Dict[str, Any]] = []
    stale_all: List[str] = []
    started_at = st.now_iso()
    st.log_line(ws, f"═══ 运行开始（模式 {mode}）═══")
    orphans: List[str] = []
    reason = "completed"
    error: Optional[str] = None
    try:
        with _Heartbeat(ws, heartbeat_interval):
            st.write_control(ws, command="run", mode=mode)
            orphans.extend(st.mark_orphans_interrupted(ws))
            st.adopt_legacy_steps(ws)
            budget = Budget(ws.settings(), chapter_limit=max_chapters)
            reason = _loop(ws, mode=mode, invoker=invoker, handlers=handlers, budget=budget,
                           executed=executed, stale_all=stale_all, max_steps=max_steps, started_at=started_at)
    except Exception as exc:  # noqa: BLE001 - worker 顶层兜底，原因写入 worker.json
        reason = "error"
        error = f"{type(exc).__name__}: {exc}"
        st.append_usage(ws, {"worker_error": error, "traceback": traceback.format_exc()[-2000:]})
    finally:
        st.release_worker(ws, reason)
        st.log_line(ws, f"═══ 运行结束：{reason}（本次执行 {len(executed)} 步）═══")
        st.write_last_run(ws, {
            "started_at": started_at, "finished_at": st.now_iso(), "mode": mode, "reason": reason,
            "error": error,
            "running": False,
            "executed": executed,
        })
        control = st.read_control(ws)
        if control.get("command") in ("pause", "stop"):
            st.write_control(ws, command="none")
        _refresh_inbox(ws)

    result: Dict[str, Any] = {
        "ok": error is None,
        "reason": reason,
        "executed": executed,
        "stale": stale_all,
        "orphans": orphans,
        "mode": mode,
    }
    if error:
        result["error"] = error
    return result


def _log_encoding_repairs(ws: Workspace, record: Dict[str, Any]) -> None:
    """把本步期间发生的「非 UTF-8 文件自动转存」写进运行日志，并记入该步的执行记录。"""
    repairs = TX.drain_repairs()
    for r in repairs:
        st.log_line(ws, f"🔧 已将 {r['path']} 从 {r['from']} 转存为 UTF-8（{r['reason']}），原文件备份为 {r['backup']}")
    if repairs:
        record["encoding_fixed"] = [{"path": r["path"], "from": r["from"]} for r in repairs]


def _checkpoint(ws: Workspace, *, mode: str, started_at: Optional[str], executed: List[Dict[str, Any]]) -> None:
    """每执行完一步就写一份「进行中」的运行记录并刷新收件箱，让界面不必等整轮结束才看到进展。

    `running: True` 表示这一轮还没结束；结束时 `run_worker` 的 finally 会用最终记录（running: False）覆盖。
    只是进度视图，写失败不影响执行。
    """
    try:
        st.write_last_run(ws, {
            "started_at": started_at, "finished_at": None, "mode": mode, "reason": None,
            "error": None, "running": True, "executed": list(executed),
        })
    except OSError as exc:
        st.log_line(ws, f"⚠ 写入进行中的运行记录失败（不影响执行）：{exc}")
    _refresh_inbox(ws)


def _refresh_inbox(ws: Workspace) -> None:
    """worker 退出时刷新收件箱快照；失败只记日志，不影响 worker 的结果。"""
    from novel_writer import inbox

    try:
        inbox.refresh(ws)
    except Exception as exc:  # noqa: BLE001 - 收件箱是视图，不能拖垮执行
        st.append_usage(ws, {"inbox_error": f"{type(exc).__name__}: {exc}"})


def _loop(
    ws: Workspace, *, mode: str, invoker: Optional[R.Invoker], handlers: Optional[Dict[str, R.PyHandler]],
    budget: Budget, executed: List[Dict[str, Any]], stale_all: List[str], max_steps: Optional[int],
    started_at: Optional[str] = None,
) -> str:
    while True:
        st.adopt_legacy_steps(ws)
        if max_steps is not None and len(executed) >= max_steps:
            return "max_steps"
        control = st.read_control(ws)
        if control.get("command") == "stop":
            return "stopped"
        if control.get("command") == "pause":
            return "paused"

        settings = ws.settings()
        plan = P.build_plan(ws)
        stale_all.extend(st.mark_stale(ws, plan))
        statuses = {sid: entry.get("status", st.ST_PENDING) for sid, entry in st.load_state(ws)["steps"].items()}
        index = P.plan_index(plan)

        requested = control.get("run_step")
        target: Optional[S.StepDef] = None
        if requested and requested in index and statuses.get(requested, st.ST_PENDING) not in st.FINISHED_STATUSES:
            target = index[requested]
        if target is None:
            target = P.frontier(plan, statuses, settings)
        if target is None:
            return "completed"

        status = statuses.get(target.id, st.ST_PENDING)
        autopilot = settings.get("gear") == "G4" and mode == "until_gate"
        if autopilot and status in (st.ST_WAITING_REVIEW, st.ST_WAITING_USER):
            if AP.handle_waiting(ws, target, status):
                continue
            return status
        if autopilot and status == st.ST_STALE and not requested == target.id:
            # 托管挡不自动重跑「因上游变化而需重跑」的步骤：这类重跑可能覆盖已确认的内容，必须由作者确认。
            st.log_line(ws, f"⏸ 托管：{target.id} 因上游产物变化需要重跑，已暂停，等待你确认后再继续")
            return "stale_confirm"
        if status in (st.ST_WAITING_REVIEW, st.ST_WAITING_USER, st.ST_FAILED, st.ST_BLOCKED, st.ST_RUNNING):
            return status

        decision = G.resolve_gate(target, settings)
        if decision.gate == G.GATE_SKIP:
            st.update_step(ws, target.id, status=st.ST_SKIPPED, note="闸门为 skip，已跳过")
            continue
        is_requested = target.id == requested
        if decision.gate == G.GATE_MANUAL and not is_requested:
            return "manual"

        blocked_by = None if autopilot else budget.check(target)  # 托管挡：不受预算限制，只受暂停/停止约束
        if blocked_by:
            return blocked_by

        if is_requested:
            st.write_control(ws, run_step=None)
        st.update_step(ws, target.id, auto_retries=0)
        status_after = R.run_step(ws, target, invoker=invoker, settings=settings, handlers=handlers)
        budget.record(target)
        retries = 0
        while status_after == st.ST_FAILED and retries < MAX_AUTO_RETRIES:
            if st.read_control(ws).get("command") in ("pause", "stop"):
                st.log_line(ws, f"⏹ {target.id} 失败，但收到暂停/停止指令，不再自动重跑")
                break
            blocked_by = budget.check(target)
            if blocked_by:
                st.log_line(ws, f"⏹ {target.id} 失败，本次预算已用尽，不再自动重跑")
                break
            retries += 1
            delay = RETRY_BACKOFF_SEC[min(retries - 1, len(RETRY_BACKOFF_SEC) - 1)] if RETRY_BACKOFF_SEC else 0
            st.log_line(ws, f"↻ {target.id} 失败，{delay} 秒后自动重跑（第 {retries}/{MAX_AUTO_RETRIES} 次）")
            st.update_step(ws, target.id, auto_retries=retries)
            if delay:
                time.sleep(delay)
            status_after = R.run_step(ws, target, invoker=invoker, settings=settings, handlers=handlers)
            budget.record(target)
        after = st.get_step_state(ws, target.id)
        executed.append({
            "step": target.id, "title": target.title, "status": status_after,
            "summary": after.get("summary") or after.get("error") or "",
            "duration_sec": after.get("duration_sec"),
            "auto_retries": retries,
        })
        _log_encoding_repairs(ws, executed[-1])
        _checkpoint(ws, mode=mode, started_at=started_at, executed=executed)

        if autopilot and status_after in (st.ST_WAITING_REVIEW, st.ST_WAITING_USER):
            continue  # 托管：下一轮循环里自动处理
        if status_after in (st.ST_FAILED, st.ST_BLOCKED, st.ST_WAITING_REVIEW, st.ST_WAITING_USER):
            return status_after
        if mode == "single":
            return "single_done"
