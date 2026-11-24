"""调研网络：网络错误不缓存、二进制文件网址不发出请求、调研代理开关、循环失败原因。全部假网络。"""

from article_garden import cycle, settings
from article_garden.research import fetch
from tests.test_planning import _world


class Net:
    """按调用顺序返回预设结果，并记录请求的网址。"""

    def __init__(self, script):
        self.script = list(script)
        self.urls = []

    def __call__(self, url, timeout):
        self.urls.append(url)
        if self.script:
            return self.script.pop(0)
        return (404, "text/html", "")


def test_network_failure_is_not_cached_so_a_later_attempt_can_succeed(data_home):
    ctx = _world(data_home)
    net = Net([(0, "", "ConnectionError: 连接被拒绝"),          # 第一次：断网（robots）
               (404, "text/html", "")])                           # 之后：robots 不存在，允许
    f = fetch.Fetcher(ctx, http_get=net, min_interval=0, sleep=lambda s: None)
    ok1, why1 = f.allowed("https://news.example.org/a")
    assert ok1 is False and "无法连接该网站" in why1
    ok2, _ = f.allowed("https://news.example.org/b")            # 不应读到断网时的缓存
    assert ok2 is True
    assert len([u for u in net.urls if u.endswith("robots.txt")]) == 2


def test_binary_urls_are_skipped_without_any_request(data_home):
    ctx = _world(data_home)
    net = Net([])
    f = fetch.Fetcher(ctx, http_get=net, min_interval=0, sleep=lambda s: None)
    res = f.fetch("https://arxiv.org/pdf/2002.02770v1.pdf")
    assert res["ok"] is False and "pdf" in res["error"]
    assert net.urls == []                                       # 连网址都不请求


def test_research_proxy_switch_exists_and_defaults_off(data_home):
    d = settings.defaults()
    assert d["research"]["use_system_proxy"] is False


def test_cycle_failure_line_names_failing_steps_and_reasons():
    failed = [{"step": "摘要", "status": "failed", "detail": "TypeError: x"},
              {"step": "调研", "status": "timeout", "detail": "单次模型调用超时"}]
    assert cycle._failure_summary(failed) == "摘要：TypeError: x；调研：单次模型调用超时"


def test_old_cached_network_error_is_ignored(data_home):
    """旧版本留下的“网络错误”缓存（状态 0，七天有效）不得继续阻止抓取。"""
    import time
    from article_garden import store
    ctx = _world(data_home)
    host = "news.example.org"
    p = ctx.path("research", "cache", "robots", fetch._hash(host) + ".json")
    store.write_json_atomic(p, {"ts": time.time(), "status": 0, "text": "ConnectionError: 旧的断网记录"})
    net = Net([(404, "text/html", "")])
    f = fetch.Fetcher(ctx, http_get=net, min_interval=0, sleep=lambda s: None)
    ok, _ = f.allowed(f"https://{host}/a")
    assert ok is True and net.urls                              # 重新询问网站，而不是沿用旧记录


def test_settings_page_shows_the_research_proxy_switch(data_home):
    from tests.test_ui_smoke import run_app
    _world(data_home)
    at = run_app("settings", domain="ai")
    assert not at.exception
    labels = [c.label for c in at.checkbox] + [t.label for t in at.toggle] if hasattr(at, "toggle") else [c.label for c in at.checkbox]
    # 应同时出现在「本库」的模型、搜索与调研页与「全局默认」页，两处都要能看到
    assert sum("调研抓取使用系统代理" in l for l in labels) >= 2, labels


def test_research_all_runs_every_approved_topic_and_isolates_failures(data_home):
    import sys
    from pathlib import Path
    from article_garden.planning import states
    from tests.test_research import _approved
    ctx = _world(data_home)
    tid = _approved(ctx)                                  # 放行一个选题
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "entrypoints"))
    import research as ep
    import json, io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert ep.main(["ai", "--all"]) == 0
    out = json.loads(buf.getvalue())
    assert out["total"] >= 1 and any(r["topic"] == tid for r in out["results"])
    assert states.get(ctx, tid)["status"] in (states.RESEARCHED, states.DROPPED)


def test_research_all_with_nothing_approved_says_so(data_home, capsys):
    import sys
    from pathlib import Path
    _world(data_home)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "entrypoints"))
    import research as ep
    assert ep.main(["ai", "--all"]) == 0
    assert "没有已放行的选题" in capsys.readouterr().out
