"""看板"板块独立加载"机制（next_doc/growth_tab_split_and_trend_index_plan.md §6.4）。

一个 tab 里的每个板块各自：后台线程拉数据、各自 TTL 缓存、各自展示
loading / 错误 / 重试，任何一个慢或失败都不影响其它板块。成长顾问 tab 是
第一个使用者，其它 tab 以后可直接复用。

分两层，便于单测：

- **纯逻辑层 `SectionCache`**：不 import streamlit。操作一个普通 dict
  （运行时传入 `st.session_state`）和一个 `submit(fn) -> Future` 函数
  （运行时传 `client.submit_async`）。
- **Streamlit 包装层 `render_section()`**：只在调用时才 import streamlit。

缓存语义（stale-while-revalidate，沿用 `app.py::_fetch_sessions_list_async`
已验证过的做法——有数据就先渲染数据，不让后台刷新吞掉按钮点击）：

- 无缓存、无在途请求 → 提交后台任务，返回 `loading`；
- 有缓存且未过期 → 直接返回 `ready`，不发请求；
- 有缓存但已过期 / 被 `invalidate` → 返回旧数据（`ready`，`stale=True`），
  同时后台刷新；
- 请求失败：有旧缓存则继续展示旧数据并带 `error`；无缓存则返回 `error`。
  **失败后不自动重试**（避免后端有问题时无限刷屏），要用户点"🔄 重试"
  （`refresh`）或写操作后 `invalidate` 才会重新请求；
- `fetch_fn` 返回 `{"_error": ...}`（`AgentClient` 的惯例）视同失败；
- 同一 key 同时只允许一个在途请求；
- `invalidate` 发生在请求在途期间：在途结果仍会入缓存（能展示），但因为
  它早于这次失效，会被判定为过期并再刷新一次。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

_NS = "_section_cache"

STATUS_LOADING = "loading"
STATUS_READY = "ready"
STATUS_ERROR = "error"


@dataclass
class SectionState:
    status: str                    # loading | ready | error
    data: Any = None
    error: Optional[str] = None    # ready 时也可能带（刷新失败但仍展示旧数据）
    stale: bool = False            # 数据过期 / 被失效 / 正在刷新 / 刷新失败
    refreshing: bool = False       # 当前有在途请求
    fetched_at: Optional[float] = None


class SectionCache:
    """纯逻辑缓存。`store` 为可变映射（`st.session_state` 或普通 dict），
    `submit(fn)` 返回 `concurrent.futures.Future`，`now` 可注入便于测试。"""

    def __init__(
        self,
        store,
        submit: Callable[[Callable[[], Any]], Any],
        *,
        now: Callable[[], float] = time.time,
    ):
        self._store = store
        self._submit = submit
        self._now = now

    # ── 内部 ────────────────────────────────────────────────────────
    def _entries(self) -> dict:
        return self._store.setdefault(_NS, {})

    def _entry(self, key: str) -> dict:
        entries = self._entries()
        e = entries.get(key)
        if e is None:
            e = {
                "data": None, "has_data": False, "fetched_at": None,
                "error": None, "future": None,
                "gen": 0,            # 每次 invalidate/refresh +1
                "fresh_gen": -1,     # 当前缓存数据对应的 gen；与 gen 不等即"已失效"
                "launch_gen": 0,
                "next_fetch": None,  # refresh(key, fetch_fn) 指定的一次性取数函数
            }
            entries[key] = e
        return e

    def _collect(self, e: dict) -> None:
        fut = e["future"]
        if fut is None or not fut.done():
            return
        e["future"] = None
        try:
            result = fut.result()
        except Exception as ex:  # noqa: BLE001 - 任何异常都视作失败
            e["error"] = str(ex) or ex.__class__.__name__
            return
        if isinstance(result, dict) and result.get("_error"):
            e["error"] = str(result["_error"])
            return
        e.update(data=result, has_data=True, fetched_at=self._now(), error=None,
                 fresh_gen=e["launch_gen"])

    def _launch(self, e: dict, fetch_fn: Callable[[], Any]) -> None:
        e["launch_gen"] = e["gen"]
        # 一次性取数函数（如"强制刷新诊断"）只用于紧接着的这一次请求
        fn = e.get("next_fetch") or fetch_fn
        e["next_fetch"] = None
        try:
            e["future"] = self._submit(fn)
        except Exception as ex:  # noqa: BLE001 - 提交失败（如线程池已关闭）
            e["future"] = None
            e["error"] = str(ex) or ex.__class__.__name__

    # ── 对外 ────────────────────────────────────────────────────────
    def get(self, key: str, fetch_fn: Callable[[], Any], ttl: float) -> SectionState:
        e = self._entry(key)
        self._collect(e)

        if e["future"] is None and e["error"] is None:
            if not e["has_data"]:
                self._launch(e, fetch_fn)
            else:
                invalid = e["gen"] != e["fresh_gen"]
                expired = self._now() - (e["fetched_at"] or 0) >= ttl
                if invalid or expired:
                    self._launch(e, fetch_fn)

        # 提交瞬间就失败 / 提交的任务已经同步完成时，再收一次
        self._collect(e)

        refreshing = e["future"] is not None
        if e["has_data"]:
            invalid = e["gen"] != e["fresh_gen"]
            expired = self._now() - (e["fetched_at"] or 0) >= ttl
            stale = refreshing or invalid or expired or e["error"] is not None
            return SectionState(STATUS_READY, e["data"], e["error"], stale, refreshing, e["fetched_at"])
        if e["error"] is not None:
            return SectionState(STATUS_ERROR, None, e["error"], False, refreshing, None)
        return SectionState(STATUS_LOADING, None, None, False, refreshing, None)

    def peek(self, key: str) -> Any:
        """只读缓存数据，不发请求；没有则 None。"""
        e = self._entries().get(key)
        return e["data"] if e and e["has_data"] else None

    def refresh(self, key: str, fetch_fn: Optional[Callable[[], Any]] = None) -> None:
        """用户点"🔄 重试/刷新"：清掉错误并标记失效，下一次 `get` 重新请求。
        旧数据保留，界面不闪空。

        `fetch_fn`：可选，仅用于**紧接着的下一次**请求（之后恢复使用 `get`
        传入的取数函数）。例如诊断板块的"🔄 刷新诊断数据"要带
        `refresh_diagnostics=true` 重建一次，之后的普通刷新不应再带该参数。"""
        e = self._entries().get(key)
        if e is None:
            return
        e["gen"] += 1
        e["error"] = None
        if fetch_fn is not None:
            e["next_fetch"] = fetch_fn

    def invalidate(self, *keys: str) -> None:
        """写操作之后使若干板块失效（旧数据仍可见，下一次 `get` 后台刷新）。"""
        for k in keys:
            self.refresh(k)

    def invalidate_all(self) -> None:
        self.invalidate(*list(self._entries().keys()))

    def is_pending(self, *keys: str) -> bool:
        entries = self._entries()
        return any((entries.get(k) or {}).get("future") is not None for k in keys)


# ── Streamlit 包装层 ───────────────────────────────────────────────────


def get_cache(submit: Callable[[Callable[[], Any]], Any], store=None) -> SectionCache:
    """取绑定到 `st.session_state` 的 `SectionCache`（`store` 仅供测试注入）。"""
    if store is None:
        import streamlit as st

        store = st.session_state
    return SectionCache(store, submit)


def invalidate_sections(submit, *keys: str, store=None) -> None:
    """写操作回调里调用：使若干板块失效。"""
    get_cache(submit, store).invalidate(*keys)


def render_section(
    key: str,
    label: str,
    fetch_fn: Callable[[], Any],
    ttl: float,
    render_fn: Callable[[Any], None],
    *,
    submit: Callable[[Callable[[], Any]], Any],
    ui_key: Optional[str] = None,
    poll_seconds: float = 1.0,
    store=None,
) -> SectionState:
    """渲染一个独立加载的板块。

    - loading：原地显示"⏳ 正在加载 {label}…"，并用 `@st.fragment(run_every=…)`
      只重跑本板块来轮询（**不**用 `st.rerun(scope="fragment")`：该写法在
      「全量重跑」阶段首次进入 fragment 时会抛 `StreamlitInvalidLayoutContextError`，
      见 `app.py::_dialog_sync_fetch` 注释）；加载结束后做一次整页 `st.rerun()`，
      让下一轮换成不带 `run_every` 的 fragment，停止轮询；
    - error：本板块显示错误信息和"🔄 重试"按钮，只重试本板块；
    - ready：调用 `render_fn(data)`；`stale` 时在板块顶部显示小字提示。

    `ui_key`：同一份数据被多个板块共享（相同 `key`）时，给各板块不同的
    `ui_key` 以避免按钮 widget key 冲突，默认等于 `key`。
    """
    import streamlit as st

    cache = get_cache(submit, store)
    ui = ui_key or key
    first = cache.get(key, fetch_fn, ttl)
    polling = first.refreshing

    deco = st.fragment(run_every=poll_seconds) if polling else st.fragment

    @deco
    def _body() -> None:
        s = cache.get(key, fetch_fn, ttl)
        if s.status == STATUS_LOADING:
            st.caption(f"⏳ 正在加载 {label}…")
        elif s.status == STATUS_ERROR:
            st.warning(f"{label}加载失败：{s.error}")
            if st.button("🔄 重试", key=f"_sec_retry::{ui}"):
                cache.refresh(key)
                st.rerun()
        else:
            if s.error is not None:
                st.caption(f"⚠️ {label}刷新失败，显示的是旧数据：{s.error}")
                if st.button("🔄 重试", key=f"_sec_retry::{ui}"):
                    cache.refresh(key)
                    st.rerun()
            elif s.stale:
                st.caption("数据可能略有延迟（后台刷新中）" if s.refreshing else "数据可能略有延迟")
            render_fn(s.data)
        if polling and not s.refreshing:
            st.rerun()  # 结束轮询：整页重跑一次，换成无 run_every 的 fragment

    _body()
    return first
