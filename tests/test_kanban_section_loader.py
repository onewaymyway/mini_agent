"""tests/test_kanban_section_loader.py — 看板板块独立加载 `SectionCache` 测试
（对应 next_doc/growth_tab_split_and_trend_index_plan.md §6.4 与 §8.4）。

纯逻辑层不依赖 streamlit：用手动完成的 Future + 可注入时钟驱动。
最后一个测试类对 Streamlit 包装层做烟雾测试（未安装 streamlit 时跳过）。
"""

from __future__ import annotations

import concurrent.futures
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "mini_agent_kanban"))

from section_loader import (  # noqa: E402
    STATUS_ERROR, STATUS_LOADING, STATUS_READY, SectionCache,
)


class _Env:
    """手动控制的"线程池"+时钟：submit 只记录 Future，由测试决定何时完成。"""

    def __init__(self):
        self.t = 1000.0
        self.futures: list[concurrent.futures.Future] = []
        self.fetch_calls = 0
        self.store: dict = {}
        self.cache = SectionCache(self.store, self.submit, now=lambda: self.t)

    def submit(self, fn):
        self.fetch_calls += 1
        f = concurrent.futures.Future()
        f._fn = fn  # noqa: SLF001
        self.futures.append(f)
        return f

    def complete(self, value=None, *, idx=-1):
        self.futures[idx].set_result(value)

    def fail(self, exc, *, idx=-1):
        self.futures[idx].set_exception(exc)


def _fetch():
    return {"x": 1}


class TestStateFlow(unittest.TestCase):
    def setUp(self):
        self.env = _Env()
        self.c = self.env.cache

    def test_loading_then_ready(self):
        s = self.c.get("a", _fetch, ttl=10)
        self.assertEqual(s.status, STATUS_LOADING)
        self.assertTrue(s.refreshing)
        self.assertEqual(self.env.fetch_calls, 1)
        # 未完成时反复 get：仍 loading，且不重复提交
        self.c.get("a", _fetch, ttl=10)
        self.assertEqual(self.env.fetch_calls, 1)
        self.env.complete({"v": 1})
        s = self.c.get("a", _fetch, ttl=10)
        self.assertEqual((s.status, s.data, s.stale, s.refreshing), (STATUS_READY, {"v": 1}, False, False))

    def test_within_ttl_no_new_request(self):
        self.c.get("a", _fetch, ttl=10)
        self.env.complete({"v": 1})
        self.c.get("a", _fetch, ttl=10)
        self.env.t += 9.9
        s = self.c.get("a", _fetch, ttl=10)
        self.assertEqual(s.status, STATUS_READY)
        self.assertFalse(s.stale)
        self.assertEqual(self.env.fetch_calls, 1)

    def test_expired_returns_stale_and_refreshes_in_background(self):
        self.c.get("a", _fetch, ttl=10)
        self.env.complete({"v": 1})
        self.c.get("a", _fetch, ttl=10)
        self.env.t += 10
        s = self.c.get("a", _fetch, ttl=10)
        self.assertEqual((s.status, s.data, s.stale, s.refreshing), (STATUS_READY, {"v": 1}, True, True))
        self.assertEqual(self.env.fetch_calls, 2)
        self.env.complete({"v": 2})
        s = self.c.get("a", _fetch, ttl=10)
        self.assertEqual((s.data, s.stale, s.refreshing), ({"v": 2}, False, False))

    def test_keys_are_independent(self):
        self.c.get("a", _fetch, ttl=10)
        self.c.get("b", _fetch, ttl=10)
        self.assertEqual(self.env.fetch_calls, 2)
        self.env.complete({"v": "a"}, idx=0)
        self.assertEqual(self.c.get("a", _fetch, 10).status, STATUS_READY)
        self.assertEqual(self.c.get("b", _fetch, 10).status, STATUS_LOADING)


class TestFailures(unittest.TestCase):
    def setUp(self):
        self.env = _Env()
        self.c = self.env.cache

    def test_error_without_cache_is_error_and_not_auto_retried(self):
        self.c.get("a", _fetch, ttl=10)
        self.env.fail(RuntimeError("boom"))
        s = self.c.get("a", _fetch, ttl=10)
        self.assertEqual((s.status, s.error), (STATUS_ERROR, "boom"))
        for _ in range(3):
            self.c.get("a", _fetch, ttl=10)
        self.assertEqual(self.env.fetch_calls, 1)  # 不自动重试

    def test_error_dict_counts_as_failure(self):
        self.c.get("a", _fetch, ttl=10)
        self.env.complete({"_error": "HTTP 500"})
        s = self.c.get("a", _fetch, ttl=10)
        self.assertEqual((s.status, s.error), (STATUS_ERROR, "HTTP 500"))

    def test_refresh_after_error_retries_only_that_section(self):
        self.c.get("a", _fetch, ttl=10)
        self.c.get("b", _fetch, ttl=10)
        self.env.fail(RuntimeError("x"), idx=0)
        self.env.complete({"ok": 1}, idx=1)
        self.assertEqual(self.c.get("a", _fetch, 10).status, STATUS_ERROR)
        self.c.refresh("a")
        s = self.c.get("a", _fetch, 10)
        self.assertEqual(s.status, STATUS_LOADING)
        self.assertEqual(self.env.fetch_calls, 3)
        self.assertEqual(self.c.get("b", _fetch, 10).data, {"ok": 1})
        self.assertEqual(self.env.fetch_calls, 3)  # b 没被动

    def test_failed_refresh_keeps_old_data(self):
        self.c.get("a", _fetch, ttl=10)
        self.env.complete({"v": 1})
        self.c.get("a", _fetch, ttl=10)
        self.env.t += 11
        self.c.get("a", _fetch, ttl=10)          # 触发后台刷新
        self.env.complete({"_error": "slow"})
        s = self.c.get("a", _fetch, ttl=10)
        self.assertEqual((s.status, s.data, s.error, s.stale), (STATUS_READY, {"v": 1}, "slow", True))
        n = self.env.fetch_calls
        self.c.get("a", _fetch, ttl=10)
        self.assertEqual(self.env.fetch_calls, n)  # 出错后不自动重试

    def test_submit_failure_becomes_error(self):
        def bad_submit(fn):
            raise RuntimeError("pool closed")

        c = SectionCache({}, bad_submit)
        s = c.get("a", _fetch, 10)
        self.assertEqual((s.status, s.error), (STATUS_ERROR, "pool closed"))


class TestInvalidate(unittest.TestCase):
    def setUp(self):
        self.env = _Env()
        self.c = self.env.cache
        self.c.get("a", _fetch, ttl=100)
        self.env.complete({"v": 1})
        self.c.get("a", _fetch, ttl=100)

    def test_invalidate_keeps_old_data_and_refreshes(self):
        self.c.invalidate("a")
        s = self.c.get("a", _fetch, ttl=100)
        self.assertEqual((s.status, s.data, s.stale, s.refreshing), (STATUS_READY, {"v": 1}, True, True))
        self.assertEqual(self.env.fetch_calls, 2)
        self.env.complete({"v": 2})
        self.assertEqual(self.c.get("a", _fetch, 100).data, {"v": 2})
        self.assertEqual(self.env.fetch_calls, 2)

    def test_invalidate_unknown_key_is_noop(self):
        self.c.invalidate("nope")
        self.assertEqual(self.env.fetch_calls, 1)

    def test_invalidate_during_inflight_triggers_another_refresh(self):
        self.c.invalidate("a")
        self.c.get("a", _fetch, 100)             # 在途（早于下一次失效）
        self.c.invalidate("a")                   # 写操作发生在请求在途期间
        self.env.complete({"v": "old-inflight"})
        s = self.c.get("a", _fetch, 100)         # 在途结果入缓存，但立刻判定为过期并再刷新
        self.assertEqual((s.data, s.stale, s.refreshing), ({"v": "old-inflight"}, True, True))
        self.assertEqual(self.env.fetch_calls, 3)
        self.env.complete({"v": "new"})
        s = self.c.get("a", _fetch, 100)
        self.assertEqual((s.data, s.stale), ({"v": "new"}, False))

    def test_invalidate_clears_error_so_it_retries(self):
        self.c.invalidate("a")
        self.c.get("a", _fetch, 100)
        self.env.fail(RuntimeError("e"))
        self.assertIsNotNone(self.c.get("a", _fetch, 100).error)
        self.c.invalidate("a")
        s = self.c.get("a", _fetch, 100)
        self.assertTrue(s.refreshing)

    def test_invalidate_all_and_peek_and_pending(self):
        self.assertEqual(self.c.peek("a"), {"v": 1})
        self.assertIsNone(self.c.peek("zzz"))
        self.assertFalse(self.c.is_pending("a"))
        self.c.invalidate_all()
        self.c.get("a", _fetch, 100)
        self.assertTrue(self.c.is_pending("a"))


class TestSharedKeyAndRealThreads(unittest.TestCase):
    def test_shared_key_single_inflight(self):
        """诊断信息 / Agent 对你的了解 共用同一个 key：只发一次请求。"""
        env = _Env()
        env.cache.get("growth_diagnostics", _fetch, 30)
        env.cache.get("growth_diagnostics", _fetch, 30)
        self.assertEqual(env.fetch_calls, 1)

    def test_concurrent_gets_with_real_executor_single_request(self):
        calls = {"n": 0}
        gate = threading.Event()

        def fetch():
            calls["n"] += 1
            gate.wait(2)
            return {"v": 1}

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            cache = SectionCache({}, ex.submit)
            states = [cache.get("a", fetch, 10) for _ in range(5)]
            self.assertTrue(all(s.status == STATUS_LOADING for s in states))
            gate.set()
            ex.shutdown(wait=True)
        self.assertEqual(calls["n"], 1)
        self.assertEqual(cache.get("a", fetch, 10).data, {"v": 1})


try:
    import streamlit  # noqa: F401
    _HAS_ST = True
except ImportError:  # pragma: no cover
    _HAS_ST = False


@unittest.skipUnless(_HAS_ST, "streamlit 未安装")
class TestStreamlitWrapperSmoke(unittest.TestCase):
    """用 streamlit.testing 的 AppTest 跑包装层：loading / ready / error / 重试。"""

    SCRIPT = '''
import sys
sys.path.insert(0, {appdir!r})
import concurrent.futures
import streamlit as st
from section_loader import render_section

class _Sub:
    def __call__(self, fn):
        f = concurrent.futures.Future()
        mode = st.session_state.get("mode", "ok")
        if mode == "ok":
            f.set_result({{"n": 7}})
        elif mode == "err":
            f.set_result({{"_error": "HTTP 500"}})
        # mode == "hang": 永不完成
        return f

render_section("k", "示例板块", lambda: None, 30,
               lambda d: st.write("DATA=" + str(d["n"])), submit=_Sub())
st.write("OTHER-SECTION-OK")
'''

    def _app(self, mode):
        from streamlit.testing.v1 import AppTest

        appdir = str(Path(__file__).resolve().parent.parent / "apps" / "mini_agent_kanban")
        at = AppTest.from_string(self.SCRIPT.format(appdir=appdir), default_timeout=10)
        at.session_state["mode"] = mode
        return at

    @staticmethod
    def _texts(at):
        return [m.value for m in at.markdown] + [c.value for c in at.caption] + [w.value for w in at.warning]

    def test_ready(self):
        at = self._app("ok").run()
        self.assertFalse(at.exception)
        texts = " ".join(self._texts(at))
        self.assertIn("DATA=7", texts)
        self.assertIn("OTHER-SECTION-OK", texts)

    def test_loading_does_not_block_rest_of_page(self):
        at = self._app("hang").run()
        self.assertFalse(at.exception)
        texts = " ".join(self._texts(at))
        self.assertIn("正在加载 示例板块", texts)
        self.assertIn("OTHER-SECTION-OK", texts)

    def test_error_shows_message_and_retry_button(self):
        at = self._app("err").run()
        self.assertFalse(at.exception)
        self.assertIn("示例板块加载失败：HTTP 500", " ".join(self._texts(at)))
        self.assertEqual(len(at.button), 1)
        self.assertIn("OTHER-SECTION-OK", " ".join(self._texts(at)))
        # 点重试：不抛异常（refresh + 整页重跑），且该板块再次发起请求
        at.button[0].click().run()
        self.assertFalse(at.exception)


@unittest.skipUnless(_HAS_ST, "streamlit 未安装")
class TestStreamlitNestedFragmentSmoke(unittest.TestCase):
    """方案 §12 风险 2：成长顾问 tab 的板块可能已被 `@st.fragment` 包住，
    `render_section` 内部又是 fragment；多个板块还共享同一处调用代码（循环）。
    这里验证嵌套 + 循环时各板块独立渲染、互不影响（loading 的不挡 ready 的）。"""

    SCRIPT = '''
import sys
sys.path.insert(0, {appdir!r})
import concurrent.futures
import streamlit as st
from section_loader import render_section

def sub(fn):
    f = concurrent.futures.Future()
    return f

class _Fast:
    def __call__(self, fn):
        f = concurrent.futures.Future()
        f.set_result({{"n": 1}})
        return f

@st.fragment
def outer():
    for key in ("a", "b"):
        submit = _Fast() if key == "a" else sub
        render_section(key, "板块" + key, lambda: None, 30,
                       lambda d, key=key: st.write("DATA-" + key), submit=submit)
outer()
'''

    def test_nested_and_looped_sections_are_independent(self):
        from streamlit.testing.v1 import AppTest

        appdir = str(Path(__file__).resolve().parent.parent / "apps" / "mini_agent_kanban")
        at = AppTest.from_string(self.SCRIPT.format(appdir=appdir), default_timeout=10).run()
        self.assertFalse(at.exception)
        texts = " ".join([m.value for m in at.markdown] + [c.value for c in at.caption])
        self.assertIn("DATA-a", texts)
        self.assertIn("正在加载 板块b", texts)
        self.assertNotIn("DATA-b", texts)


if __name__ == "__main__":
    unittest.main()
