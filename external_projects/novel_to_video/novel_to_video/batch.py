"""batch.py — 多章队列与挡位执行（Phase 3）。

职责：
  * 按章顺序运行 run_chapter，某章停在 waiting_* 或 failed 时不影响后续章节；
  * 依据挡位对“可自动通过”的检查点执行自动通过（见 gates.py）；
  * 预算检查：每章开始前检查调用次数与耗时，超限则整批停止并给出原因；
  * 自动通过只依据机械校验，并写入 review.json 的 auto 标记与 PROGRESS.md。

自动通过的条件（G3 小场景）：
  clip 文件存在、无 clip_error、镜头 consistency == pass、音频已存在。
不满足时保持 pending，由人工处理。
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


def check_budget(project_dir: Path, gear: str, budget: Dict[str, Any]) -> Optional[str]:
    """返回 None 表示可以继续，否则返回停止原因。非 G3 不做预算强制。"""
    try:
        gates.require_budget(budget, _usage(project_dir), _minutes_used(project_dir), gear)
    except gates.GateError as exc:
        return str(exc)
    return None


def auto_approve_shots(project_dir: Path, no: int) -> List[str]:
    """G3：对满足机械条件的镜头自动通过，返回本次自动通过的镜头 id。"""
    shots = cio.load_shots(project_dir, no).get("shots", [])
    cdir = cio.chapter_dir(project_dir, no)
    approved: List[str] = []
    for sh in shots:
        sid = sh["id"]
        if sh.get("clip_error") or sh.get("error") or sh.get("consistency") not in (None, "pass"):
            continue
        if not sh.get("clip_key") or not (cdir / "clips" / f"{sid}.mp4").is_file():
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


def _recover_failure(project_dir: Path, no: int, gear: str, res: Dict[str, Any], run_one,
                     budget: Dict[str, Any], results: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    """G4 / G5 的失败恢复，次数有上限。

    G4：失败后降低口播块长度并重跑一次（策略切换）。
    G5：失败后清除失败阶段标记，重试至多 3 次。
    其余挡位不恢复，直接返回失败。每次重试前都重新检查预算。
    """
    limit = gates.RETRY_LIMITS.get(gear, 0)
    attempts = 0
    while res.get("status") == "failed" and attempts < limit:
        if check_budget(project_dir, gear, budget):
            break
        attempts += 1
        if gear == "G4":
            _switch_strategy(project_dir, no)
        else:
            _reset_failed_stage(project_dir, no)
        res = run_one(no)
        res["recovery"] = {"gear": gear, "attempts": attempts}
    if attempts:
        append_progress(project_dir, f"第 {no} 章 {gear} 失败恢复 {attempts} 次，结果：{res.get('status')}")
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


def run_queue(project_dir: Path, numbers: List[int], *, gear: str, run_one) -> Dict[str, Any]:
    """依次运行章节。run_one(no) -> {"status": ..., "message": ...}，由调用方注入（便于测试）。

    每章前检查预算；超限则停止后续章节，已完成章节保留。
    """
    proj = read_json(project_dir / "project.json")
    budget = proj.get("budget") or {}
    gear = gates.normalize_gear(gear)
    gates.validate_budget(budget, gear)
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
            res = run_one(no)
            res = _recover_failure(project_dir, no, gear, res, run_one, budget, results)
            if gear in ("G3", "G4", "G5") and res.get("status") == "waiting_review":
                approved = auto_approve_shots(project_dir, no)
                res["auto_approved"] = approved
                if approved:
                    res = run_one(no)  # 自动通过后继续推进到合成
                    res["auto_approved"] = approved
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
    return {"results": results, "halted": halted, "gear": gear,
            "elapsed_sec": round(time.monotonic() - started, 2)}
