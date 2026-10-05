"""tests/test_kanban_growth_tab_sections.py —— 成长顾问 tab 板块独立加载
（next_doc/growth_tab_split_and_trend_index_plan.md B7）。

用 `streamlit.testing.v1.AppTest` 驱动 `render_growth_tab()`，配一个带调用计数
的假 client。`AppTest` 不会触发 `run_every` 定时重跑，所以"等后台取数完成"
用 `time.sleep` + 再 `run()` 一次来模拟（与 test_kanban_section_loader.py 的
烟雾测试同一取舍）。

覆盖：各板块独立出现、一个板块失败不影响其它、TTL 内重跑不重复请求、主题地图
按需加载、首次触达 ack 只调一次、诊断占位当作错误、强制刷新诊断只带一次
`refresh=True`、不再调用 `/growth/summary`。
"""
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

KANBAN_DIR = Path(__file__).resolve().parent.parent / "apps" / "mini_agent_kanban"

FAKE_MODULE = '''
import concurrent.futures
import time

CALLS = {}
OVERRIDES = {}
_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=8)


def reset():
    CALLS.clear()
    OVERRIDES.clear()


def _hit(name, *a):
    CALLS.setdefault(name, []).append(a)


def _resp(name, default):
    return OVERRIDES[name] if name in OVERRIDES else default


class FakeClient:
    def submit_async(self, fn, *a, **k):
        return _POOL.submit(fn, *a, **k)

    # 异步任务（growth_scan 等）：没有正在跟踪的任务
    def get_latest_async_job(self, key):
        return {}

    def get_async_job(self, job_id):
        return {"status": "done", "result": {}}

    def growth_overview(self):
        _hit("overview")
        return _resp("overview", {
            "candidates": [
                {"candidate_id": "c1", "title": "候选甲", "status": "pending",
                 "confidence": 0.8, "evidence_count": 3, "rationale": "r"},
            ],
            "reports": [],
            "retrospective": {"total_candidates": 7, "accepted": 3, "dismissed": 2,
                              "reports_generated": 5},
            "first_touch_notice_shown": True,
        })

    def growth_diagnostics(self, refresh=False):
        _hit("diagnostics", refresh)
        return _resp("diagnostics", {
            "config": {"enabled": True, "min_evidence_count": 3,
                       "notification_frequency": "daily",
                       "notification_min_confidence": 0.5},
            "signal_scan": {}, "memory": {}, "cron_jobs": {},
            "user_profile": {"summary": "喜欢写代码"},
        })

    def growth_topic_map(self):
        _hit("topic_map")
        return _resp("topic_map", {"topic_map": [
            {"topic": "主题甲", "current_status": "pending", "peak_confidence": 0.9,
             "occurrences": 4, "times_accepted": 1, "times_dismissed": 0,
             "evidence_trend": [], "candidate_id": "c1"},
        ]})

    def growth_health_trend(self, limit=30):
        _hit("health_trend")
        return _resp("health_trend", {"health_trend": []})

    def growth_followups(self):
        _hit("followups")
        return _resp("followups", {"followups": [
            {"candidate_id": "c1", "title": "回访甲"},
        ]})

    def growth_align(self):
        _hit("align")
        return _resp("align", {"enabled": True, "unmatched_interests": [], "llm_suggested_matches": []})

    def growth_pursuits(self):
        _hit("pursuits")
        return _resp("pursuits", {"pursuits": []})

    def growth_pursuits_portfolio_summary(self):
        _hit("portfolio")
        return {}

    def growth_pursuits_related_directions(self):
        _hit("related")
        return {}

    def growth_reports_refresh_candidates(self):
        _hit("refresh_candidates")
        return _resp("refresh_candidates", {"refresh_candidates": []})

    def growth_first_touch_ack(self):
        _hit("first_touch_ack")
        return {"ok": True}

    def get_user_profile_preferences(self):
        return {"preferences": {}}

    def growth_report(self, *a, **k):
        return {}
'''

SCRIPT = f'''
import sys
sys.path.insert(0, {str(KANBAN_DIR)!r})
sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})
import app as kanban_app
import _fake_growth_client as fake
kanban_app.render_growth_tab(fake.FakeClient())
'''


@pytest.fixture
def fake(tmp_path, monkeypatch):
    (tmp_path / "_fake_growth_client.py").write_text(FAKE_MODULE, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.syspath_prepend(str(KANBAN_DIR))
    sys.modules.pop("_fake_growth_client", None)
    import _fake_growth_client as mod
    mod.reset()
    script = tmp_path / "_growth_tab_script.py"
    script.write_text(SCRIPT.replace(repr(str(Path(__file__).resolve().parent)), repr(str(tmp_path))),
                      encoding="utf-8")
    mod.SCRIPT_PATH = str(script)
    yield mod
    sys.modules.pop("_fake_growth_client", None)


def _settle(at, rounds=2):
    for _ in range(rounds):
        time.sleep(0.4)
        at.run()
    return at


def _start(fake):
    at = AppTest.from_file(fake.SCRIPT_PATH, default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    return _settle(at)


def _expander_labels(at):
    return [e.label for e in at.expander]


def _warnings(at):
    return [w.value for w in at.warning]


def test_sections_render_independently_with_data(fake):
    at = _start(fake)
    assert not at.exception, at.exception
    # 概览指标
    assert [m.value for m in at.metric] == ["7", "3", "2", "5"]
    labels = _expander_labels(at)
    assert any("诊断信息" in l for l in labels)
    assert any("健康度趋势" in l for l in labels)
    assert any("该回访一下了" in l for l in labels)
    # 待处理候选来自概览缓存（没有额外请求）
    assert any("候选甲" in m.value for m in at.markdown)
    assert len(fake.CALLS["overview"]) == 1


def test_never_calls_growth_summary(fake):
    # FakeClient 根本没有 growth_summary：若还有调用会直接抛 AttributeError
    at = _start(fake)
    assert not at.exception, at.exception


def test_one_failing_section_does_not_affect_others(fake):
    fake.OVERRIDES["followups"] = {"_error": "boom"}
    at = _start(fake)
    assert not at.exception, at.exception
    assert any("加载失败：boom" in w for w in _warnings(at))
    # 其它板块照常
    assert [m.value for m in at.metric] == ["7", "3", "2", "5"]
    assert any("诊断信息" in l for l in _expander_labels(at))
    # 失败板块自带重试按钮
    assert any(b.key and b.key.startswith("_sec_retry::") for b in at.button)


def test_reruns_within_ttl_do_not_refetch(fake):
    at = _start(fake)
    for _ in range(3):
        at.run()
    assert not at.exception, at.exception
    for name in ("overview", "diagnostics", "health_trend", "followups",
                 "align", "pursuits", "refresh_candidates"):
        assert len(fake.CALLS.get(name, [])) == 1, (name, fake.CALLS.get(name))


def test_diagnostics_and_profile_share_one_request(fake):
    _start(fake)
    assert len(fake.CALLS["diagnostics"]) == 1


def test_topic_map_loaded_only_when_toggled_on(fake):
    at = _start(fake)
    assert "topic_map" not in fake.CALLS
    at.toggle(key="growth_topic_map_toggle").set_value(True).run()
    _settle(at)
    assert not at.exception, at.exception
    assert len(fake.CALLS["topic_map"]) == 1
    assert any("成长主题地图（1 个方向）" in m.value for m in at.markdown)


def test_topic_map_timeout_placeholder_is_error_not_cached_empty(fake):
    fake.OVERRIDES["topic_map"] = {"topic_map": [], "_note": "timed out"}
    at = _start(fake)
    at.toggle(key="growth_topic_map_toggle").set_value(True).run()
    _settle(at)
    assert any("成长主题地图加载失败" in w and "timed out" in w for w in _warnings(at))


def test_diagnostics_note_placeholder_is_treated_as_error(fake):
    fake.OVERRIDES["diagnostics"] = {"_note": "circuit open"}
    at = _start(fake)
    assert not at.exception, at.exception
    assert any("诊断信息加载失败" in w and "circuit open" in w for w in _warnings(at))
    # 概览不受影响
    assert [m.value for m in at.metric] == ["7", "3", "2", "5"]


def test_first_touch_notice_shown_and_ack_called_once(fake):
    ov = fake.FakeClient().growth_overview()
    ov["first_touch_notice_shown"] = False
    fake.reset()
    fake.OVERRIDES["overview"] = ov
    at = _start(fake)
    for _ in range(3):
        at.run()
    assert not at.exception, at.exception
    assert any("已为你开启「成长顾问」" in i.value for i in at.info)
    assert len(fake.CALLS["first_touch_ack"]) == 1


def test_first_touch_notice_hidden_when_already_shown(fake):
    at = _start(fake)
    assert not any("已为你开启「成长顾问」" in i.value for i in at.info)
    assert "first_touch_ack" not in fake.CALLS


def test_force_refresh_diagnostics_requests_refresh_once(fake):
    import time as _t
    fake.OVERRIDES["diagnostics"] = {
        "config": {"enabled": True}, "signal_scan": {}, "cron_jobs": {},
        "memory": {"backfill_candidates_count": 3,
                   "backfill_candidates_count_computed_at": _t.time()},
    }
    at = _start(fake)
    assert [c[0] for c in fake.CALLS["diagnostics"]] == [False]
    at.button(key="growth_diag_refresh_btn").click().run()
    _settle(at)
    assert not at.exception, at.exception
    assert [c[0] for c in fake.CALLS["diagnostics"]] == [False, True]
    # 之后的普通渲染不再带 refresh=True
    at.run()
    assert [c[0] for c in fake.CALLS["diagnostics"]] == [False, True]


def test_pursuits_bundle_fetches_portfolio_and_related_in_background(fake):
    fake.OVERRIDES["pursuits"] = {"pursuits": [
        {"goal_id": "g1", "title": "方向甲", "recurring": True, "cycle_count": 2,
         "schedule": "interval:86400", "saturation": {}},
    ]}
    at = _start(fake)
    assert not at.exception, at.exception
    assert len(fake.CALLS["portfolio"]) == 1
    assert len(fake.CALLS["related"]) == 1
    assert any("正在自主推进" in l for l in _expander_labels(at))
