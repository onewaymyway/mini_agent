"""batch.py — 多章队列与挡位执行（Phase 3）。

职责：
  * 按章顺序运行 run_chapter，某章停在 waiting_* 或 failed 时不影响后续章节；
  * 依据挡位对“可自动通过”的检查点执行自动通过（见 gates.py）；
  * 预算检查：每章开始前检查调用次数与耗时，超限则整批停止并给出原因；
  * 自动通过只依据机械校验，并写入 review.json 的 auto 标记与 PROGRESS.md。

自动通过的条件（G3 小场景）：
  clip 文件存在、无 clip_error、镜头 consistency == pass、音频已存在。
不满足时保持 pending，由人工处理。

G6 不跳过失败的章节：失败后持续重试（见 _recover_failure），直到成功、用户停止、预算用尽或达到可选的单章重试上限；
后几种情况整个队列停下，不再开始后面的章节。
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import cancel
from . import chapter_io as cio
from . import gates
from . import runtime as rt
from . import joblog
from . import shot_review as sr
from .call_log import count_calls
from .project_store import append_progress, load_project, read_json


def _usage(project_dir: Path) -> Dict[str, int]:
    return {"video": count_calls(project_dir, "video"), "image": count_calls(project_dir, "image")}


def _minutes_used(project_dir: Path) -> float:
    path = project_dir / "logs" / "batch_runs.json"
    if not path.is_file():
        return 0.0
    return float(read_json(path).get("minutes_used", 0.0))


def _add_minutes(project_dir: Path, minutes: float) -> None:
    from .project_store import write_json_atomic
    path = project_dir / "logs" / "batch_runs.json"
    data = read_json(path) if path.is_file() else {"minutes_used": 0.0}
    data["minutes_used"] = round(float(data.get("minutes_used", 0.0)) + minutes, 3)
    write_json_atomic(path, data)


def _mode(project_dir: Path) -> str:
    from . import render_mode as RM
    return RM.mode_of(read_json(project_dir / "project.json"))


def check_budget(project_dir: Path, gear: str, budget: Dict[str, Any], extra_minutes: float = 0.0) -> Optional[str]:
    """返回 None 表示可以继续，否则返回停止原因。只有 G3 / G6 做预算强制。
    extra_minutes：本章已经运行、但还没有记入 batch_runs.json 的分钟数（章内反复重试时耗时要实时计入）。"""
    try:
        gates.require_budget(budget, _usage(project_dir), _minutes_used(project_dir) + max(0.0, extra_minutes), gear,
                             mode=_mode(project_dir))
    except gates.GateError as exc:
        return str(exc)
    return None


def auto_approve_shots(project_dir: Path, no: int) -> List[str]:
    """G3：对满足机械条件的镜头自动通过，返回本次自动通过的镜头 id。"""
    shots = cio.load_shots(project_dir, no).get("shots", [])
    mode = sr.project_mode(project_dir)
    key, err = sr._FIELDS[mode]
    approved: List[str] = []
    for sh in shots:
        sid = sh["id"]
        if sh.get(err) or sh.get("error") or sh.get("consistency") not in (None, "pass"):
            continue
        # 视频：需要 clip_key 与视频文件；动态漫：需要当前场景图（动效片段在合成前再生成）
        f = sr.asset_file(project_dir, no, sh, mode)
        if not sh.get(key) or f is None or not f.is_file():
            continue
        if sr.shot_states(project_dir, no).get(sid) == "approved":
            continue
        item = sr.approve(project_dir, no, sid)
        item["auto"] = True
        approved.append(sid)
    if approved:
        data = sr.load_shot_review(project_dir, no)
        for sid in approved:
            data["items"][sid]["auto"] = True
        sr._save(project_dir, no, data)
        append_progress(project_dir, f"G3 自动通过第 {no} 章镜头：{', '.join(approved)}")
    return approved


def _since(t0: Optional[float]) -> float:
    """自 t0（time.monotonic 起点）以来的分钟数；t0 为 None 时为 0。"""
    return 0.0 if t0 is None else (time.monotonic() - t0) / 60.0


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _wait_or_stop(project_dir: Path, seconds: float) -> bool:
    """重试前的等待。每 0.5 秒检查一次停止请求；等待期间被要求停止则返回 False。"""
    left = float(seconds)
    while left > 0:
        if rt.stop_requested(project_dir):
            return False
        step = min(0.5, left)
        _sleep(step)
        left -= step
    return not rt.stop_requested(project_dir)


SAME_FAILURE_LIMIT = 5   # G6：完全相同的失败信息连续出现这么多次就停止重试并说明原因，避免无意义地重试上百次


def _recover_failure(project_dir: Path, no: int, gear: str, res: Dict[str, Any], run_one,
                     budget: Dict[str, Any], results: Dict[int, Dict[str, Any]],
                     t0: Optional[float] = None) -> Dict[str, Any]:
    """失败恢复，由挡位决定策略与次数（gates.retry_limit）。

    G4：失败后降低口播块长度并重跑一次（策略切换）。
    G5：失败后清除失败阶段标记，重试至多 3 次。
    G6：同 G5 的做法，但不设固定次数上限，重试之间等待（逐步拉长，可被停止请求打断），
        直到成功、用户停止、预算用尽或达到预算里的 max_chapter_retries。
    其余挡位不恢复，直接返回失败。每次重试前都重新检查停止请求与预算（章内耗时实时计入）。

    仍然失败时，G6 会在结果里写入 g6_halt（队列据此停止，而不是跳到下一章）。
    """
    limit = gates.retry_limit(gear, budget)
    persistent = gear in gates.FULL_AUTO_GEARS          # G6：持续重试
    attempts = 0
    halt: Optional[str] = None
    same_count, last_msg = 1, res.get("message")
    while res.get("status") == "failed" and (limit is None or attempts < limit):
        if persistent and same_count >= SAME_FAILURE_LIMIT:
            halt = (f"同一错误已连续出现 {same_count} 次，重试没有任何变化（说明问题不会自行恢复，需要修复后再继续）")
            break
        if rt.stop_requested(project_dir):
            halt = cancel.STOP_MESSAGE
            break
        reason = check_budget(project_dir, gear, budget, _since(t0))
        if reason:
            halt = reason
            break
        attempts += 1
        if persistent:
            wait = gates.retry_backoff_sec(attempts)
            joblog.log(project_dir, "queue",
                       f"G6：第 {no} 章失败（{res.get('message') or '未知原因'}），{wait:g} 秒后第 {attempts} 次重试",
                       level="warn")
            if not _wait_or_stop(project_dir, wait):
                attempts -= 1
                halt = cancel.STOP_MESSAGE
                break
        if gear == "G4":
            _switch_strategy(project_dir, no)
        else:
            _reset_failed_stage(project_dir, no)
        res = run_one(no)
        res["recovery"] = {"gear": gear, "attempts": attempts}
        msg = res.get("message")
        same_count = same_count + 1 if (res.get("status") == "failed" and msg and msg == last_msg) else 1
        last_msg = msg
    if persistent and res.get("status") == "failed":
        if halt is None and limit is not None and attempts >= limit:
            halt = f"第 {no} 章已重试 {attempts} 次仍失败（已达单章重试上限 {limit} 次）"
        elif halt is not None and halt != cancel.STOP_MESSAGE:
            halt = f"{halt}；第 {no} 章仍未成功（{res.get('message') or '未知原因'}）"
        res["g6_halt"] = halt or f"第 {no} 章失败，G6 不跳过失败章节，队列已停止"
    if attempts:
        append_progress(project_dir, f"第 {no} 章 {gear} 失败恢复 {attempts} 次，结果：{res.get('status')}")
    return res


MAX_PREPARE_ROUNDS = 3


def _prepare_and_rerun(project_dir: Path, no: int, gear: str, budget: Dict[str, Any], res: Dict[str, Any],
                       run_one, prepare, t0: Optional[float] = None) -> Dict[str, Any]:
    """G6：章节停在 waiting_assets（缺设定/缺定妆图/等待确认）时，自动补齐素材并重跑本章。
    每轮之前检查停止请求与预算；某轮没有任何进展（没有新通过的设定或新生成的定妆图）即停止，不空转。"""
    rounds = 0
    while res.get("status") == "waiting_assets" and rounds < MAX_PREPARE_ROUNDS:
        if rt.stop_requested(project_dir):
            break
        reason = check_budget(project_dir, gear, budget, _since(t0))
        if reason:
            res["message"] = f"{res.get('message') or ''}（自动补素材已停止：{reason}）"
            break
        rounds += 1
        joblog.log(project_dir, "queue", f"G6：第 {no} 章等待素材，自动补全（第 {rounds} 轮）")
        try:
            info = prepare(no) or {}
        except Exception as exc:  # noqa: BLE001 - 引擎初始化等失败按本章失败处理，由失败恢复继续重试，不拖垮整个队列
            joblog.log(project_dir, "queue", f"G6：第 {no} 章自动补素材出错：{type(exc).__name__}：{exc}", level="error")
            res = {"status": "failed", "message": f"自动补素材出错：{type(exc).__name__}：{exc}"}
            break
        progressed = bool(info.get("progress"))
        res = run_one(no)
        res["auto_prepared"] = info
        if not progressed:
            joblog.log(project_dir, "queue", f"G6：第 {no} 章自动补素材没有新进展，停止重试", level="warn")
            break
    if rounds:
        append_progress(project_dir, f"G6 第 {no} 章自动补素材 {rounds} 轮，结果：{res.get('status')}")
    return res


def _switch_strategy(project_dir: Path, no: int) -> None:
    """G4 策略切换：把镜头口播块上限收紧一半，触发重新切分。"""
    from .chapter_io import MAX_BLOCK_CHARS
    cfg_path = cio.chapter_dir(project_dir, no) / "strategy.json"
    from .project_store import write_json_atomic
    write_json_atomic(cfg_path, {"max_block_chars": max(10, MAX_BLOCK_CHARS // 2)})
    cio.mark_stage(project_dir, no, "shots", "failed", "G4 策略切换：重新切分镜头")


def _reset_failed_stage(project_dir: Path, no: int) -> None:
    """G5：仅清除失败阶段的标记，让流水线从该阶段重新执行（已成功阶段保持不变）。"""
    state = cio.load_state(project_dir, no)
    for name, st in list(state.get("stages", {}).items()):
        if st.get("status") == "failed":
            state["stages"][name] = {"status": "pending", "error": None}
    cio.save_state(project_dir, no, state)


def run_queue(project_dir: Path, numbers: List[int], *, gear: str, run_one, prepare=None) -> Dict[str, Any]:
    """依次运行章节。run_one(no) -> {"status": ..., "message": ...}，由调用方注入（便于测试）。

    每章前检查预算；超限则停止后续章节，已完成章节保留。
    prepare(no) -> dict：仅 G6 使用，由调用方注入。章节停在「等待素材」时，自动补全本章所需的设定、性别、
    定妆图并确认，然后重新运行本章（最多 MAX_PREPARE_ROUNDS 轮，避免在付费接口上无限循环）。
    """
    proj = read_json(project_dir / "project.json")
    budget = proj.get("budget") or {}
    gear = gates.normalize_gear(gear)
    gates.validate_budget(budget, gear, _mode(project_dir))
    results: Dict[int, Dict[str, Any]] = {}
    halted: Optional[str] = None
    started = time.monotonic()
    for no in numbers:
        if rt.stop_requested(project_dir):
            halted = cancel.STOP_MESSAGE
            joblog.log(project_dir, "queue", "队列已停止（用户请求）", level="warn")
            break
        reason = check_budget(project_dir, gear, budget)
        if reason:
            halted = reason
            break
        t0 = time.monotonic()
        rt.begin_chapter(project_dir, no)
        joblog.log(project_dir, "queue", f"队列：开始第 {no} 章（挡位 {gear}）")
        try:
            def run_and_prepare(n: int) -> Dict[str, Any]:
                """跑一章；G6 下停在「等待素材」时自动补素材并重跑。失败恢复的每次重试也走这里，
                这样某章重试成功后紧接着遇到缺素材，同样会被自动补齐，而不是停在半途被跳过。"""
                r = run_one(n)
                if gear in gates.FULL_AUTO_GEARS and prepare is not None:
                    r = _prepare_and_rerun(project_dir, n, gear, budget, r, run_one, prepare, t0)
                return r

            res = run_and_prepare(no)
            prepared = res.get("auto_prepared")
            res = _recover_failure(project_dir, no, gear, res, run_and_prepare, budget, results, t0)
            prepared = res.get("auto_prepared") or prepared      # 重试过程中补的素材也要保留摘要
            if gear in gates.AUTO_REVIEW_GEARS and res.get("status") == "waiting_review":
                approved = auto_approve_shots(project_dir, no)
                res["auto_approved"] = approved
                if approved:
                    res = run_one(no)  # 自动通过后继续推进到合成
                    res["auto_approved"] = approved
            if prepared and "auto_prepared" not in res:
                res["auto_prepared"] = prepared      # 重跑会换掉结果字典，G6 的素材补全摘要要保留
        except cancel.Cancelled:
            # 用户立即停止：本章停在当前阶段（章节已回到断点），后续章节不再开始
            _add_minutes(project_dir, (time.monotonic() - t0) / 60.0)
            res = {"status": "stopped", "message": "已手动停止，重新运行本章即可从断点继续"}
            results[no] = res
            rt.note_chapter(project_dir, no, res)
            joblog.log(project_dir, "queue", f"队列：第 {no} 章已立即停止", level="warn")
            halted = cancel.STOP_MESSAGE
            break
        _add_minutes(project_dir, (time.monotonic() - t0) / 60.0)
        results[no] = res
        rt.note_chapter(project_dir, no, res)
        reason = f"：{res.get('message')}" if res.get("message") else ""
        joblog.log(project_dir, "queue", f"队列：第 {no} 章结束 → {res.get('status')}{reason}",
                   level={"done": "ok", "failed": "error"}.get(res.get("status"), "info"))
        if res.get("status") == "stopped":   # 章节流水线报告了手动停止
            halted = cancel.STOP_MESSAGE
            break
        if gear in gates.FULL_AUTO_GEARS and res.get("status") == "failed":
            # G6 不跳过失败章节：重试没能让它成功（用户停止 / 预算用尽 / 达到单章重试上限）时整个队列停下，
            # 后面的章节不再开始；修正原因（或调高预算）后重新运行即可从这一章继续。
            halted = res.get("g6_halt") or f"第 {no} 章失败，G6 不跳过失败章节，队列已停止"
            joblog.log(project_dir, "queue", f"G6：队列在第 {no} 章停止 —— {halted}", level="error")
            break
    return {"results": results, "halted": halted, "gear": gear,
            "elapsed_sec": round(time.monotonic() - started, 2)}
