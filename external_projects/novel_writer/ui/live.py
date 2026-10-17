"""🔵 运行面板（实时）：当前步骤与已用时、运行日志尾部（含子 agent 的工具调用输出）、
结束提示与上一次运行结果。

用 `st.fragment(run_every=...)` 每 2 秒轮询一次状态文件，不会整页重跑。
运行结束时：弹出一次 toast，并刷新整页（让流水线表格更新）。结束横幅与「上一次运行结果」
保留在页面上，直到下一次运行开始。
"""

from __future__ import annotations

import datetime as _dt

import streamlit as st

from novel_writer import tool_api
from ui.components import REASON_LABEL

POLL_SECONDS = 2
_WAS_ALIVE = "live_was_alive"
_NOTIFIED = "live_notified_finish"
_STEP_MARK = {"passed": "✔", "waiting_review": "⏸", "waiting_user": "❓", "failed": "✖", "blocked": "⛔"}


def _parse(ts):
    try:
        return _dt.datetime.fromisoformat(ts) if ts else None
    except ValueError:
        return None


def _elapsed(started) -> str:
    t0 = _parse(started)
    if t0 is None:
        return "—"
    secs = int((_dt.datetime.now(t0.tzinfo) - t0).total_seconds())
    m, s = divmod(max(secs, 0), 60)
    return f"{m} 分 {s} 秒" if m else f"{s} 秒"


def _clock(ts) -> str:
    t = _parse(ts)
    return t.strftime("%H:%M:%S") if t else "—"


def _finish_banner(status: dict) -> None:
    last = status.get("last_run") or {}
    reason = last.get("reason") or status.get("reason") or "completed"
    label = REASON_LABEL.get(reason, reason)
    done = len(last.get("executed") or [])
    text = f"{label}　·　结束于 {_clock(last.get('finished_at') or status.get('finished_at'))}　·　本次执行 {done} 步"
    if reason == "completed":
        st.success("✅ 运行结束：" + text)
    elif reason in ("single_done", "waiting_review", "waiting_user", "manual"):
        st.info("⏹ 运行结束：" + text)
    elif reason in ("failed", "blocked", "error"):
        st.error("❌ 运行结束：" + text + (f"\n\n{last['error']}" if last.get("error") else ""))
    else:
        st.warning("⏸ 运行结束：" + text)


def _last_run_table(last: dict) -> None:
    rows = last.get("executed") or []
    if not rows:
        st.caption("上一次运行没有执行任何步骤。")
        return
    st.markdown(f"**上一次运行结果**（{_clock(last.get('started_at'))} – {_clock(last.get('finished_at'))}）")
    st.table([{
        "步骤": r.get("step", ""),
        "状态": f"{_STEP_MARK.get(r.get('status'), '•')} {r.get('status', '')}",
        "耗时": f"{r['duration_sec']}s" if r.get("duration_sec") is not None else "—",
        "结果摘要": r.get("summary") or "",
    } for r in rows])


@st.fragment(run_every=POLL_SECONDS)
def _panel(ws: str) -> None:
    res = tool_api.run_status(ws, lines=160)
    if not res["ok"]:
        st.error(res["error"]["message"])
        return
    status = res["data"]
    alive = status["alive"]

    was_alive = st.session_state.get(_WAS_ALIVE + ws, False)
    if alive:
        st.session_state[_WAS_ALIVE + ws] = True
        st.info(f"🔵 后台运行中：{status.get('step_title') or status.get('step') or '准备中'}"
                f"　·　当前步骤已用时 {_elapsed(status.get('step_started_at'))}"
                f"　·　第 {status.get('attempt') or 1} 次尝试")
    else:
        if was_alive:
            st.session_state[_WAS_ALIVE + ws] = False
            finished = (status.get("last_run") or {}).get("finished_at") or status.get("finished_at")
            if st.session_state.get(_NOTIFIED + ws) != finished:
                st.session_state[_NOTIFIED + ws] = finished
                st.toast("✅ 后台运行已结束，结果见下方。")
                # 整页刷新一次，让流水线表格与指标同步到最新状态
                st.rerun(scope="app")
        if status.get("last_run") or status.get("reason"):
            _finish_banner(status)

    with st.expander("📜 运行输出（实时，含子 agent 的工具调用）", expanded=alive or not status.get("log")):
        lines = status.get("log") or []
        if lines:
            st.code("\n".join(lines), language=None)
        else:
            st.caption("还没有运行记录。点下面的「▶」开始运行，输出会在这里实时出现。")
        st.caption(f"日志保存在 _studio/run.log，每次运行以「═══ 运行开始」分隔；这里显示最近 {len(lines)} 行。")

    if status.get("last_run"):
        _last_run_table(status["last_run"])


def render_live_panel(ws: str) -> None:
    _panel(ws)
