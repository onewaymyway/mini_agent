"""
async_job_ui.py —— 看板"点击按钮 → 提交异步任务 → 轮询直到有结果"的通用封装。

背景：涉及 LLM 调用的接口（生成/修订执行规范、整体关闭重判、成长顾问扫描/
采纳/报告刷新……）后端现在统一走 `api/async_jobs.py` 的异步任务机制——POST
接口立即返回 `{"job_id", "key"}`，不再同步跑完 LLM 调用才响应。这个模块封装
"前端怎么用"的那一半：点击按钮提交、渲染等待提示、按固定间隔轮询、拿到终态
后交还结果——所有涉及 LLM 调用的看板按钮都应该复用这两个函数，不要各自再手写
一遍 `st.spinner()` + 同步等待。

用法（典型两步）：

    key = f"execution_spec_generate:{goal_id}"   # 与后端 key 保持一致，
                                                    # 方便刷新页面后用
                                                    # get_latest_async_job 找回
    if st.button("生成执行规范草稿"):
        if start_async_job(client, key, lambda: client.generate_execution_spec(goal_id, ...)):
            st.rerun()

    result = run_async_job(client, key, label="正在生成执行规范草稿")
    if result is not None:
        if "_error" in result:
            st.error(f"生成失败：{result['_error']}")
        else:
            st.success("草稿已生成")
            # ... 用 result["spec"] / result["effective_path"] 渲染 ...
            st.rerun()

`run_async_job()` 在任务还在跑的分支里会调用 `st.rerun()`，所以放在这个调用
之后的代码本次渲染不会执行——这是预期行为，等价于"这次先渲染等待提示，下一次
渲染再看是不是跑完了"。
"""

from __future__ import annotations

import time

import streamlit as st

# 轮询间隔：太短会给 daemon 增加不必要的请求量，太长会让"点了按钮好久没反应"
# 的观感变差——1.5s 是两者之间一个能接受的折中，后续如果需要可以做成参数。
DEFAULT_POLL_INTERVAL_SECONDS = 1.5


def _state_key(key: str) -> str:
    return f"_async_job_id::{key}"


def start_async_job(client, key: str, submit_fn) -> bool:
    """点击按钮时调用：提交任务，把返回的 job_id 记进
    `st.session_state`，随后调用方应紧跟 `st.rerun()`，把接手轮询的工作
    交给 `run_async_job()`。

    `submit_fn()` 应该是一个无参 callable，调用后发起 HTTP 请求并返回
    `{"job_id", "key"}` 或 `{"_error": ...}`（`AgentClient` 里那些
    `_post()` 封装的标准返回形状）。

    返回 True 表示提交成功（`session_state` 已经记下 job_id）；返回
    False 表示提交本身失败（网络错误、后端 400/500 等），此时已经用
    `st.error()` 展示了失败原因，调用方不需要重复处理。
    """
    resp = submit_fn()
    if not isinstance(resp, dict) or resp.get("_error"):
        err = resp.get("_error") if isinstance(resp, dict) else "未知错误"
        st.error(f"提交失败：{err}")
        return False
    job_id = resp.get("job_id")
    if not job_id:
        st.error("提交失败：服务端未返回 job_id（可能是一次未经过异步任务改造的旧接口）")
        return False
    st.session_state[_state_key(key)] = job_id
    return True


def run_async_job(
    client,
    key: str,
    *,
    label: str = "正在处理，请稍候…",
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    try_recover_from_backend: bool = True,
):
    """渲染当前任务的等待/结果状态。应该在每次渲染时都调用（不只是点击按钮
    那一次），因为轮询依赖的是"任务还在跑就 `st.rerun()`，下次渲染再检查"
    这个循环。

    返回值三种情况：
    - `None`：没有正在跟踪的任务（还没点过按钮，或者上次的任务结果已经被
      调用方消费掉了）。调用方这次渲染应该正常显示"点击按钮开始"这类初始
      状态的 UI。
    - 任务仍在跑：本函数内部会渲染一条"⏳ ..."提示，`sleep` 一小段时间后
      调用 `st.rerun()`——**这个分支永远不会 return**，调用方写在
      `run_async_job(...)` 调用之后的代码这次渲染不会执行。
    - 任务已经有终态（done/error）：返回一个 dict。
      - 成功：原样返回后端任务的 `result`（也就是原来这个接口"直接返回"
        时的响应体，比如 `{"spec": ..., "effective_path": ...}`）。
      - 失败：返回 `{"_error": "..."}`，与 `AgentClient` 里其它同步方法
        失败时的返回形状保持一致，调用方可以用同一套 `if "_error" in
        result` 判断逻辑处理，不需要区分"是提交失败还是任务跑到一半失败"。
      两种情况下都会先把 `session_state` 里的 job_id 清掉，避免同一个
      已经消费过的结果在下次渲染时被重复返回。
    """
    state_key = _state_key(key)
    job_id = st.session_state.get(state_key)

    if job_id is None and try_recover_from_backend:
        # 页面被整个刷新过、`session_state` 丢失的场景：从后端"这个 key
        # 最近一次任务"找回进度，而不是让用户以为"任务丢了"——任务本身
        # 从来不受前端有没有在看影响，继续在后台跑到底。
        latest = client.get_latest_async_job(key)
        job = latest.get("job") if isinstance(latest, dict) else None
        if isinstance(job, dict) and job.get("status") == "running":
            job_id = job.get("job_id")
            st.session_state[state_key] = job_id

    if job_id is None:
        return None

    job = client.get_async_job(job_id)
    if not isinstance(job, dict) or job.get("_error"):
        # 查询本身失败（网络问题/daemon 重启瞬间还没起来），不清掉
        # session_state——下次渲染再试一次，而不是把一个"可能还在跑"的
        # 任务直接当成失败丢弃。
        err = job.get("_error") if isinstance(job, dict) else "查询任务状态失败"
        st.warning(f"⚠️ 暂时无法查询任务状态（{err}），稍后自动重试…")
        time.sleep(poll_interval)
        st.rerun()

    status = job.get("status")
    if status == "running":
        started_at = job.get("started_at") or time.time()
        elapsed = max(0, int(time.time() - started_at))
        st.info(f"⏳ {label}（已运行 {elapsed}s，可以先切到别的 Tab，任务会在后台继续跑，回来再看结果）")
        time.sleep(poll_interval)
        st.rerun()

    st.session_state.pop(state_key, None)
    if status == "done":
        return job.get("result")
    # status == "error"
    return {"_error": job.get("error") or "任务执行失败（未知原因）"}
