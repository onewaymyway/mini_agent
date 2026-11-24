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


# ------------------------------------------------------------------ 匹配与浏览器备用

def test_matching_ignores_punctuation_and_accepts_charter_aliases():
    from article_garden.research import sources
    assert sources.covers_point("AI-Economic Protocol", "这是 AI Economic Protocol 的介绍")
    assert sources.covers_point("假说生成", "文中讨论的是假设生成的方法", {"假说生成": ["假设生成"]})
    assert not sources.covers_point("假说生成", "文中讨论的是别的内容", {"假说生成": ["假设生成"]})
    para = "AI Economic Protocol 需要身份与结算。"
    assert sources.excerpt(para, ["AI-Economic Protocol"], 200) == para


def test_thin_detection_flags_script_shells_only():
    from article_garden.research import fetch
    assert fetch.thin("<html><script>var x=1;</script><body><div id=app></div></body></html>")
    assert not fetch.thin("<html><body>" + "正文内容。" * 80 + "</body></html>")


def test_browser_is_used_for_empty_shell_pages_only(data_home):
    ctx = _world(data_home)
    rendered = []
    body = "<html><body>" + "这是通过脚本渲染后的完整正文内容。" * 20 + "</body></html>"

    def render(url):
        rendered.append(url)
        return body, ""
    shell = Net([(200, "text/html", "<html><script>x</script></html>")])
    f = fetch.Fetcher(ctx, http_get=shell, min_interval=0, sleep=lambda s: None, render=render)
    res = f.fetch("https://spa.example.org/article")
    assert res["ok"] and res["via"] == "browser" and rendered == ["https://spa.example.org/article"]


def test_browser_is_not_used_for_robots_denials_or_access_challenges(data_home):
    ctx = _world(data_home)
    rendered = []

    def render(url):
        rendered.append(url)
        return "<html>" + "x" * 500 + "</html>", ""
    denied = Net([(200, "text/plain", "User-agent: *\nDisallow: /")])
    f = fetch.Fetcher(ctx, http_get=denied, min_interval=0, sleep=lambda s: None, render=render)
    assert f.fetch("https://zhihu.example.org/p/1")["ok"] is False
    challenge = Net([(404, "text/html", ""), (403, "text/html", "")])   # robots 允许；页面返回 403
    f2 = fetch.Fetcher(ctx, http_get=challenge, min_interval=0, sleep=lambda s: None, render=render)
    res = f2.fetch("https://guard.example.org/x")
    assert res["ok"] is False and "不使用浏览器绕过" in res["error"]
    assert rendered == []


def test_server_error_falls_back_to_browser_when_enabled(data_home):
    ctx = _world(data_home)
    body = "<html><body>" + "渲染后可读的完整文章正文。" * 20 + "</body></html>"
    def http(url, t):
        return (521, "text/html", "") if not url.endswith("robots.txt") else (404, "text/html", "")
    f = fetch.Fetcher(ctx, http_get=http, min_interval=0, sleep=lambda s: None,
                      render=lambda url: (body, ""))
    res = f.fetch("https://devpress.example.net/a")
    assert res["ok"] and res["via"] == "browser"


def test_render_without_playwright_reports_why(monkeypatch):
    import builtins
    from article_garden.research import browser
    real = builtins.__import__

    def fake(name, *a, **k):
        if name.startswith("playwright"):
            raise ImportError("no")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)
    from pathlib import Path
    html, err = browser.render_fn(9222, Path("/tmp/none"), timeout_sec=1)("https://x.example/")
    assert html is None and "playwright" in err


def test_any_plain_failure_falls_back_to_browser_when_enabled(data_home):
    """开启浏览器后，普通抓取的任何失败（超时、网络错误、404、5xx、521、非网页）都改用浏览器。"""
    ctx = _world(data_home)
    body = "<html><body>" + "浏览器获取到的页面内容。" * 20 + "</body></html>"
    cases = [(0, "", "TimeoutError: timed out"), (404, "text/html", ""), (500, "text/html", ""),
             (521, "text/html", ""), (200, "application/pdf", "%PDF")]
    for status, ctype, text in cases:
        seen = []

        def http(url, t, status=status, ctype=ctype, text=text):
            return (404, "text/html", "") if url.endswith("robots.txt") else (status, ctype, text)

        def render(url, seen=seen):
            seen.append(url)
            return body, ""
        f = fetch.Fetcher(ctx, http_get=http, min_interval=0, sleep=lambda s: None, render=render)
        res = f.fetch(f"https://site{status}.example.org/a")
        assert res["ok"] and res["via"] == "browser", (status, res)
        assert len(seen) == 1, status


def test_browser_result_is_used_even_when_short_after_a_failure(data_home):
    ctx = _world(data_home)
    f = fetch.Fetcher(ctx, http_get=lambda u, t: (404, "text/html", "") if u.endswith("robots.txt") else (500, "", ""),
                      min_interval=0, sleep=lambda s: None, render=lambda u: ("<html>短</html>", ""))
    res = f.fetch("https://short.example.org/a")
    assert res["ok"] and res["via"] == "browser"


def test_robots_and_access_denials_never_reach_the_browser(data_home):
    ctx = _world(data_home)
    seen = []

    def render(url):
        seen.append(url)
        return "<html>" + "x" * 300 + "</html>", ""
    # robots 明确禁止
    f = fetch.Fetcher(ctx, http_get=lambda u, t: (200, "text/plain", "User-agent: *\nDisallow: /"),
                      min_interval=0, sleep=lambda s: None, render=render)
    assert f.fetch("https://no.example.org/a")["ok"] is False
    # 401 需要登录
    f2 = fetch.Fetcher(ctx, http_get=lambda u, t: (404, "", "") if u.endswith("robots.txt") else (401, "", ""),
                       min_interval=0, sleep=lambda s: None, render=render)
    assert f2.fetch("https://login.example.org/a")["ok"] is False
    assert seen == []


# ------------------------------------------------------------------ 自动启动调试浏览器

def test_debug_browser_is_not_launched_when_port_already_open(monkeypatch, tmp_path):
    from article_garden.research import browser
    launched = []
    monkeypatch.setattr(browser, "port_open", lambda port, host="127.0.0.1": True)
    monkeypatch.setattr(browser.subprocess, "Popen", lambda *a, **k: launched.append(a))
    ok, note = browser.ensure_debug_browser(9222, tmp_path / "p")
    assert ok and launched == []


def test_debug_browser_reports_missing_chrome(monkeypatch, tmp_path):
    from article_garden.research import browser
    monkeypatch.setattr(browser, "port_open", lambda port, host="127.0.0.1": False)
    monkeypatch.setattr(browser, "find_chrome", lambda: None)
    ok, note = browser.ensure_debug_browser(9222, tmp_path / "p")
    assert ok is False and "未找到 Chrome" in note


def test_debug_browser_is_started_with_debug_port_and_separate_profile(monkeypatch, tmp_path):
    from article_garden.research import browser
    state = {"open": False}
    calls = []
    monkeypatch.setattr(browser, "port_open", lambda port, host="127.0.0.1": state["open"])
    monkeypatch.setattr(browser, "find_chrome", lambda: "/usr/bin/chrome-test")

    def fake_popen(args, **kwargs):
        calls.append(args)
        state["open"] = True                       # 启动后端口即就绪
    monkeypatch.setattr(browser.subprocess, "Popen", fake_popen)
    ok, note = browser.ensure_debug_browser(9333, tmp_path / "profile", wait_sec=2)
    assert ok and "已自动启动" in note
    args = calls[0]
    assert args[0] == "/usr/bin/chrome-test"
    assert "--remote-debugging-port=9333" in args
    assert any(a.startswith("--user-data-dir=") and "profile" in a for a in args)
    assert (tmp_path / "profile").is_dir()


def test_browser_fallback_retries_after_auto_start_for_any_plain_failure(data_home, monkeypatch, tmp_path):
    """调试浏览器未开启时，普通抓取失败也会自动启动浏览器再试一次。"""
    from article_garden.research import browser
    ctx = _world(data_home)
    state = {"open": False, "rendered": []}
    monkeypatch.setattr(browser, "port_open", lambda port, host="127.0.0.1": state["open"])
    monkeypatch.setattr(browser, "find_chrome", lambda: "/usr/bin/chrome-test")

    def fake_popen(args, **kwargs):
        state["open"] = True
    monkeypatch.setattr(browser.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(browser, "_LAUNCH_LOCK", __import__("threading").Lock())

    def fake_render(url):
        state["rendered"].append(url)
        return "<html>" + "正文内容。" * 60 + "</html>", "已自动启动调试浏览器"
    f = fetch.Fetcher(ctx, http_get=lambda u, t: (404, "text/html", "") if u.endswith("robots.txt") else (521, "", ""),
                      min_interval=0, sleep=lambda s: None, render=fake_render)
    res = f.fetch("https://down.example.org/a")
    assert res["ok"] and res["via"] == "browser" and state["rendered"]
