"""评估全流程的自动化：失败自动重试、步骤自动衔接、（可选）自动修改直到没有可处理的问题。

一次运行的步骤顺序：
  cards（事实卡）→ checks（确定性检查）→ review（LLM 复核）→ outline（大纲偏离）→ export（报告）

  * 每一步失败会按 auto_retry_attempts 自动重试，等待 auto_retry_backoff_seconds 起按 1、2、4… 倍递增；
    FlowError（前置条件不满足，例如缺事实卡）不重试。
  * 步骤内部的单元失败（某章事实卡、某个窗口）由各步骤的缓存机制兜底：重试时只重做失败的单元。
  * 事实卡失败时停止（后续步骤依赖它）；其它步骤失败只记录，流程继续往下走，最后汇报。

自动修改（auto_fix_enabled，默认关闭，因为它会改写正文，需要作者明确开启）：
  每轮：挑出可以自动处理的问题（来自模型复核或大纲偏离、证据已核实、非矛盾、未驳回、仲裁未判误报、
  严重度不低于 auto_fix_min_severity）→ 以证据章节为目标逐条采纳 → 生成预览（模型判定不涉及的章节默认排除）
  → 确认 → 改写（失败自动续跑）→ 增量复评（基于新 head）→ 大纲偏离检查。最多 auto_fix_rounds 轮；
  某轮没有新的可处理问题即停止。矛盾类问题需要作者选定方向，不在自动范围内。

状态写入 _revision/pipeline/state.json，界面与命令行都读它来显示进度与结束原因。
"""
from __future__ import annotations

import datetime as _dt
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from novel_writer.revision_flow import changes as CH
from novel_writer.revision_flow import config as CFG
from novel_writer.revision_flow import executor
from novel_writer.revision_flow import outline_check as OC
from novel_writer.revision_flow import outline_regen as OR
from novel_writer.revision_flow import review_adopt as RA
from novel_writer.revision_flow import review_cards as RC
from novel_writer.revision_flow import review_checks as RK
from novel_writer.revision_flow import review_llm as RL
from novel_writer.revision_flow import review_report as RR
from novel_writer.revision_flow import versions as V
from novel_writer.revision_flow.common import FlowError, load_json, save_json, sub_dir

STEPS = ("cards", "checks", "review", "outline", "export")
STEP_LABEL = {"cards": "生成事实卡", "checks": "确定性检查", "review": "LLM 复核",
              "outline": "大纲偏离检查", "export": "导出评估报告"}
SEVERITY_RANK = {"轻": 0, "中": 1, "严重": 2}
Say = Callable[[str], None]


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _state_path(ws):
    return sub_dir(ws, "pipeline") / "state.json"


def load_state(ws) -> Optional[Dict[str, Any]]:
    return load_json(_state_path(ws), default=None)


def _save(ws, state: Dict[str, Any]) -> None:
    state["updated_at"] = _now()
    save_json(_state_path(ws), state)


# ─────────────────────────────── 重试 ───────────────────────────────

def attempt(say: Say, label: str, attempts: int, backoff: int, fn: Callable[[], Tuple[bool, str]]) -> Tuple[bool, str]:
    """执行 fn 直到成功或用完次数。fn 返回 (是否成功, 说明)。FlowError 视为前置条件不满足，不重试。"""
    last = ""
    for i in range(1, attempts + 1):
        say(f"{label}：第 {i}/{attempts} 次")
        try:
            ok, detail = fn()
        except FlowError as exc:
            say(f"{label}：前置条件不满足，不再重试：{exc}")
            return False, str(exc)
        except Exception as exc:  # 执行器异常等：可重试
            ok, detail = False, f"异常：{exc}"
        if ok:
            return True, detail
        last = detail
        if i < attempts:
            wait = backoff * (2 ** (i - 1))
            if wait > 0:
                say(f"{label}：未成功（{detail}），{wait} 秒后自动重试")
                time.sleep(wait)
            else:
                say(f"{label}：未成功（{detail}），立即重试")
    return False, last


def _run_with_retry(ws, pid: str, invoker, attempts: int, backoff: int, say: Say) -> Dict[str, Any]:
    """启动改写；未完成则按续跑自动继续。返回最后的运行记录。"""
    run: Optional[Dict[str, Any]] = None
    for i in range(1, attempts + 1):
        try:
            if run is None:
                say(f"改写：第 {i}/{attempts} 次启动")
                run = executor.start(ws, pid, invoker, progress=say)
            else:
                say(f"改写：第 {i}/{attempts} 次续跑（从未完成章节继续）")
                run = executor.resume(ws, run["id"], invoker, progress=say)
        except FlowError as exc:
            return {"id": run["id"] if run else None, "status": "failed", "error": str(exc)}
        except Exception as exc:
            say(f"改写：异常：{exc}")
            if run is None:
                return {"id": None, "status": "failed", "error": str(exc)}
            run = {**run, "status": "running"}
        if run.get("status") == "done":
            return run
        if run.get("status") not in executor.RESUMABLE:
            return run
        if i < attempts and backoff > 0:
            wait = backoff * (2 ** (i - 1))
            say(f"改写未完成（{run.get('status')}），{wait} 秒后续跑")
            time.sleep(wait)
    return run or {"status": "failed", "error": "未知"}


# ─────────────────────────────── 步骤 ───────────────────────────────

def _step_fns(ws, vid: str, invoker, say: Say) -> Dict[str, Callable[[], Tuple[bool, str]]]:
    def cards():
        res = RC.generate_cards(ws, vid, invoker, progress=say)
        bad = res["failed"]
        if res["paused"]:
            return False, "达到调用上限，已暂停"
        return (not bad), (f"失败 {len(bad)} 章" if bad else f"完成（生成 {res['generated']}，缓存 {res['cached']}）")

    def checks():
        RK.run_checks(ws, vid, progress=say)
        return True, "完成"

    def review():
        res = RL.run_review(ws, vid, invoker, progress=say)
        if res["paused"]:
            return False, "达到调用上限，已暂停"
        bad = res["failed"]
        return (not bad), (f"失败 {len(bad)} 处" if bad else f"完成（调用 {res['calls']} 次）")

    def outline():
        try:
            OR.current_blueprint(ws, vid)
        except Exception:  # 没有蓝图或蓝图不可读：跳过大纲步骤
            return True, "跳过（没有可读的蓝图）"
        res = OC.run_check(ws, vid, invoker, progress=say)
        bad = res["failed"]
        return (not bad), (f"失败 {len(bad)} 批" if bad else "完成")

    def export():
        res = RR.export(ws, vid, progress=say)
        return True, f"完成（{res['findings']} 条问题）"

    return {"cards": cards, "checks": checks, "review": review, "outline": outline, "export": export}


def eligible(ws, head: str, min_severity: str) -> List[Dict[str, Any]]:
    """自动修改可以处理的问题（见模块说明）。"""
    floor = SEVERITY_RANK.get(min_severity, 1)
    out = []
    for f in RK.load_findings(ws, head)["findings"]:
        if f["source"] not in ("llm", "outline") or f["status"] not in RA.ADOPTABLE or f.get("change_id"):
            continue
        if not f.get("evidence_ok", True) or f["category"] == "contradiction":
            continue
        if SEVERITY_RANK.get(f["severity"], 0) < floor:
            continue
        verdict = (f.get("adjudication") or {}).get("verdict")
        if verdict in ("false_positive", "uncertain"):
            continue
        if not RA.options(f):
            continue
        out.append(f)
    return out


def _targets(f: Dict[str, Any]) -> List[int]:
    chs = sorted({e["chapter"] for e in f["evidence"] if e.get("quote") and e.get("verified", True)})
    return chs or sorted(f["chapters"])


# ─────────────────────────────── 入口 ───────────────────────────────

def run(ws, invoker, progress: Optional[Say] = None, *, auto_fix: Optional[bool] = None) -> Dict[str, Any]:
    """运行评估全流程。auto_fix=None 时使用设置中的 auto_fix_enabled。返回汇总（同时写入 state.json）。"""
    say = progress or (lambda _t: None)
    cfg = CFG.load(ws)
    attempts, backoff = cfg["auto_retry_attempts"], cfg["auto_retry_backoff_seconds"]
    fix = cfg["auto_fix_enabled"] if auto_fix is None else auto_fix
    vid = RC.resolve_version(ws, None)
    state: Dict[str, Any] = {"started_at": _now(), "version_start": vid, "steps": {}, "rounds": [],
                             "auto_fix": bool(fix), "stopped_reason": "", "version_end": vid}
    _save(ws, state)
    say(f"评估全流程：版本 {vid}，每步最多 {attempts} 次尝试，自动修改{'开启' if fix else '关闭'}")

    fns = _step_fns(ws, vid, invoker, say)
    all_ok = True
    for name in STEPS:
        label = STEP_LABEL[name]
        say(f"▶ {label}")
        ok, detail = attempt(say, label, attempts, backoff, fns[name])
        state["steps"][name] = {"ok": ok, "detail": detail}
        _save(ws, state)
        say(f"{'✔' if ok else '✘'} {label}：{detail}")
        if not ok:
            all_ok = False
            if name == "cards":
                state["stopped_reason"] = "事实卡未齐全，后续步骤依赖它，流程已停止"
                say(state["stopped_reason"])
                _save(ws, state)
                return state

    pending = eligible(ws, vid, cfg["auto_fix_min_severity"])
    if not fix:
        state["stopped_reason"] = (f"自动修改关闭：{len(pending)} 条问题可以采纳，等待作者决定"
                                   if pending else "评估完成，没有可自动处理的问题")
    elif not all_ok:
        state["stopped_reason"] = "有评估步骤未完成，为避免基于不完整结果改写，未进入自动修改"
    else:
        _auto_fix(ws, invoker, cfg, state, say, attempts, backoff)
    state["version_end"] = V.load_index(ws)["head"]
    state["pending_adoptable"] = len(eligible(ws, state["version_end"], cfg["auto_fix_min_severity"]))
    say(f"流程结束：{state['stopped_reason']}")
    _save(ws, state)
    return state


def _auto_fix(ws, invoker, cfg, state, say: Say, attempts: int, backoff: int) -> None:
    rounds = cfg["auto_fix_rounds"]
    for rnd in range(1, rounds + 1):
        head = V.load_index(ws)["head"]
        items = eligible(ws, head, cfg["auto_fix_min_severity"])
        if not items:
            state["stopped_reason"] = f"第 {rnd} 轮开始时没有可自动处理的问题，结束"
            say(state["stopped_reason"])
            return
        say(f"▶ 自动修改第 {rnd}/{rounds} 轮：{len(items)} 条可处理问题（基于 {head}）")
        ids, skipped = [], []
        for f in items:
            try:
                ch = RA.adopt(ws, head, f["id"], option_index=0, target_chapters=_targets(f), progress=say)
                ids.append(ch["id"])
            except FlowError as exc:
                skipped.append({"id": f["id"], "reason": str(exc)})
                say(f"跳过「{f['title']}」：{exc}")
        if not ids:
            state["stopped_reason"] = f"第 {rnd} 轮没有能够采纳的问题，结束"
            say(state["stopped_reason"])
            return
        record: Dict[str, Any] = {"round": rnd, "base": head, "adopted": ids, "skipped": skipped}
        try:
            pv = CH.build_preview(ws, head, ids, invoker=invoker, progress=say)
            CH.confirm_preview(ws, pv["id"])
            run_rec = _run_with_retry(ws, pv["id"], invoker, attempts, backoff, say)
        except FlowError as exc:
            record["error"] = str(exc)
            state["rounds"].append(record)
            state["stopped_reason"] = f"第 {rnd} 轮预览或改写前置条件不满足：{exc}"
            say(state["stopped_reason"])
            return
        record["run"] = {"id": run_rec.get("id"), "status": run_rec.get("status"),
                         "target": run_rec.get("target_version")}
        state["rounds"].append(record)
        _save(ws, state)
        if run_rec.get("status") != "done":
            state["stopped_reason"] = f"第 {rnd} 轮改写未完成（{run_rec.get('status')}），已停止，可在「运行」页续跑"
            say(state["stopped_reason"])
            return
        RA.sync_applied(ws)
        say(f"第 {rnd} 轮改写完成，开始增量复评")
        inc = RA.increment(ws, invoker, progress=say)
        record["increment"] = {k: len(inc[k]) for k in ("resolved", "still_open", "new")}
        ok, detail = attempt(say, "大纲偏离检查", attempts, backoff, _outline_fn(ws, inc["version"], invoker, say))
        record["outline"] = {"ok": ok, "detail": detail}
        _save(ws, state)
    state["stopped_reason"] = f"已达自动修改轮数上限（{rounds} 轮），仍有问题请作者决定或再次运行"


def _outline_fn(ws, vid: str, invoker, say: Say):
    def fn():
        try:
            OR.current_blueprint(ws, vid)
        except Exception:  # 没有蓝图或蓝图不可读：跳过大纲步骤
            return True, "跳过（没有可读的蓝图）"
        res = OC.run_check(ws, vid, invoker, progress=say)
        return (not res["failed"]), ("完成" if not res["failed"] else f"失败 {len(res['failed'])} 批")
    return fn
