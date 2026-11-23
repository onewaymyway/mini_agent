"""P5：资料收集——查询、搜索缓存、抓取（robots/节流/缓存）、摘录、来源卡分级、证据包与状态转换。全部使用假网络。"""

import pytest

from article_garden import cancel
from article_garden.planning import states, topics
from article_garden.research import fetch, pack, queries, runner, search, sources
from tests.test_planning import _world

PARA = "向量数据库是一种专门用于存储和检索高维向量的数据库，常用于语义搜索场景。"


def page(*paras, title="页面"):
    body = "".join(f"<p>{p}</p>" for p in paras)
    return f"<html><head><title>{title}</title></head><body><nav><p>导航文字很长很长很长很长</p></nav>{body}</body></html>"


class FakeSearch:
    def __init__(self, hits=(), error=None):
        self.hits = list(hits)
        self.error = error
        self.calls = 0

    def search(self, query, max_results):
        self.calls += 1
        if self.error:
            raise search.SearchError(self.error)
        return [search.Hit(**h) for h in self.hits][:max_results]


class FakeHTTP:
    def __init__(self, pages=None, robots=None, raise_on=None):
        self.pages = pages or {}
        self.robots = robots or {}
        self.raise_on = raise_on
        self.urls = []

    def __call__(self, url, timeout):
        self.urls.append(url)
        if self.raise_on and url == self.raise_on:
            raise cancel.Cancelled("用户请求停止")
        if url.endswith("/robots.txt"):
            return self.robots.get(url.split("/")[2], (404, "text/plain", ""))
        if url in self.pages:
            return 200, "text/html; charset=utf-8", self.pages[url]
        return 404, "text/html", ""


def _approved(ctx):
    topics.run(ctx, None)
    row = next(r for r in states.load_topics(ctx) if r["gap_type"] == "G2" and "向量数据库" in r["title"])
    states.approve(ctx, row["id"])
    return row["id"]


HITS = [
    {"title": "官方文档", "url": "https://docs.example.org/vector"},
    {"title": "开源项目", "url": "https://github.com/x/vdb"},
    {"title": "新闻", "url": "https://news.example.com/a"},
]
PAGES = {
    "https://docs.example.org/vector": page(PARA, title="向量数据库官方文档"),
    "https://github.com/x/vdb": page(PARA + "这是一个开源实现。", title="vdb"),
    "https://news.example.com/a": page(PARA, title="新闻"),
}


def _run(ctx, tid, search_backend=None, http=None, sleep=lambda s: None):
    http = http or FakeHTTP(pages=PAGES)
    fetcher = fetch.Fetcher(ctx, http_get=http, min_interval=0.0, sleep=sleep)
    return runner.run(ctx, tid, search_backend=search_backend or FakeSearch(HITS), fetcher=fetcher), http


# ------------------------------------------------------------------ 纯函数

def test_credibility_rules():
    assert sources.classify("https://docs.example.org/x", []) == "A"
    assert sources.classify("https://arxiv.org/abs/1", []) == "A"
    assert sources.classify("https://www.tsinghua.edu.cn/x", []) == "A"
    assert sources.classify("https://github.com/x", []) == "B"
    assert sources.classify("https://news.example.com/a", []) == "C"
    assert sources.classify("https://spam.example.com/a", ["spam"]) == "D"   # 章程 avoid 优先


def test_excerpt_keeps_only_paragraphs_with_points_and_respects_limit():
    text = "无关的一段话，讲别的东西，长度足够但没有要点。\n\n" + PARA + "\n\n" + ("向量数据库" * 200)
    ex = sources.excerpt(text, ["向量数据库"], 120)
    assert "无关" not in ex and PARA[:10] in ex and len(ex) <= 120
    assert sources.excerpt("完全无关的文字，没有任何要点相关内容在这里。", ["向量数据库"], 300) == ""


def test_queries_template_and_model_path():
    topic = {"title": "向量数据库是什么", "must_cover": ["向量数据库", "索引"], "angle": ""}
    t = queries.build(topic, "人工智能", None)
    assert 3 <= len(t) <= 8 and t[0] == "向量数据库是什么"

    class Q:
        def ask(self, prompt, system=""):
            return "queries:\n  - 向量数据库 原理\n  - 向量索引 HNSW\n  - 向量数据库 原理\n  - 向量数据库 官方文档\n"
    got = queries.build(topic, "人工智能", Q())
    assert got == ["向量数据库 原理", "向量索引 HNSW", "向量数据库 官方文档"]   # 去重


def test_queries_validator_rejects_too_few():
    class Bad:
        def __init__(self):
            self.n = 0

        def ask(self, prompt, system=""):
            self.n += 1
            return "queries:\n  - 只有一条\n" if self.n == 1 else "queries:\n  - a\n  - b\n  - c\n"
    b = Bad()
    assert len(queries.build({"title": "t", "must_cover": []}, "d", b)) == 3 and b.n == 2


# ------------------------------------------------------------------ 流程

def test_research_builds_cards_and_marks_researched(data_home):
    ctx = _world(data_home)
    tid = _approved(ctx)
    result, _ = _run(ctx, tid)
    assert result["result"] == "researched" and result["sufficient"]
    cards = sources.list_cards(ctx, tid)
    assert {c["credibility"] for c in cards} == {"A", "B", "C"}
    assert all(c["excerpt"] and c["url"].startswith("https://") and c["source_type"] == "web" for c in cards)
    p = pack.read(ctx, tid)
    assert p["sufficient"] and p["coverage"] == 1.0 and p["credibility"]["A"] == 1
    assert states.get(ctx, tid)["status"] == states.RESEARCHED


def test_cached_search_and_pages_are_not_requested_again(data_home):
    ctx = _world(data_home)
    tid = _approved(ctx)
    _, http1 = _run(ctx, tid)
    assert any(u for u in http1.urls if "robots" not in u)
    backend = FakeSearch(HITS)
    result, http2 = _run(ctx, tid, search_backend=backend)   # researched → 可再次调研
    assert backend.calls == 0                                  # 搜索全部命中缓存
    assert [u for u in http2.urls if not u.endswith("robots.txt")] == []   # 网页全部命中缓存
    assert result["result"] == "researched"


def test_insufficient_evidence_drops_topic_with_reason(data_home):
    ctx = _world(data_home)
    tid = _approved(ctx)
    only_c = [{"title": "新闻", "url": "https://news.example.com/a"}]
    result, _ = _run(ctx, tid, search_backend=FakeSearch(only_c))
    assert result["result"] == "dropped" and not result["sufficient"]
    row = states.get(ctx, tid)
    assert row["status"] == states.DROPPED and "证据不足" in row["history"][-1]["note"]
    assert pack.read(ctx, tid)["reasons"]                       # 不硬写：原因写在证据包里


def test_robots_disallow_prevents_fetch(data_home):
    ctx = _world(data_home)
    tid = _approved(ctx)
    http = FakeHTTP(pages=PAGES, robots={"github.com": (200, "text/plain", "User-agent: *\nDisallow: /")})
    result, _ = _run(ctx, tid, http=http)
    assert "https://github.com/x/vdb" not in [u for u in http.urls]          # 没有请求页面本身
    assert any("robots" in k for k in result["skipped"]) or any("禁止" in k for k in result["skipped"])


def test_robots_unreadable_is_treated_as_deny(data_home):
    ctx = _world(data_home)
    f = fetch.Fetcher(ctx, http_get=FakeHTTP(robots={"x.example": (500, "text/plain", "")}),
                      min_interval=0.0, sleep=lambda s: None)
    ok, why = f.allowed("https://x.example/page")
    assert not ok and "无法读取" in why


def test_throttle_sleeps_between_requests_to_same_host(data_home):
    ctx = _world(data_home)
    sleeps = []
    f = fetch.Fetcher(ctx, http_get=FakeHTTP(pages={"https://h.example/a": page(PARA), "https://h.example/b": page(PARA)}),
                      min_interval=1.0, sleep=sleeps.append)
    f.fetch("https://h.example/a")
    f.fetch("https://h.example/b")
    assert sleeps and sleeps[0] > 0


def test_search_failure_degrades_to_manual_sources(data_home):
    ctx = _world(data_home)
    tid = _approved(ctx)
    runner.add_manual_text(ctx, tid, PARA, title="手动资料一", url="https://a.example/x", credibility="A")
    runner.add_manual_text(ctx, tid, PARA + "补充。", title="手动资料二", url="https://b.example/y", credibility="B")
    result, _ = _run(ctx, tid, search_backend=FakeSearch(error="网络不可用"))
    assert result["search_errors"] and result["result"] == "researched"   # 仅凭手动资料仍可充分
    assert {c["source_type"] for c in sources.list_cards(ctx, tid)} == {"manual"}


def test_manual_text_rejects_empty_and_bad_credibility(data_home):
    ctx = _world(data_home)
    tid = _approved(ctx)
    with pytest.raises(ValueError):
        runner.add_manual_text(ctx, tid, "   ")
    with pytest.raises(ValueError):
        runner.add_manual_text(ctx, tid, PARA, credibility="Z")


def test_remove_source_reevaluates_pack(data_home):
    ctx = _world(data_home)
    tid = _approved(ctx)
    _run(ctx, tid)
    before = pack.read(ctx, tid)["sources_credible"]
    sid = sources.list_cards(ctx, tid)[0]["id"]
    runner.remove_source(ctx, tid, sid)
    assert pack.read(ctx, tid)["sources_total"] == len(sources.list_cards(ctx, tid))
    assert pack.read(ctx, tid)["sources_credible"] <= before


def test_research_requires_approved_topic(data_home):
    ctx = _world(data_home)
    topics.run(ctx, None)
    tid = states.load_topics(ctx)[0]["id"]                      # 仍为待审
    with pytest.raises(ValueError, match="只有已放行"):
        _run(ctx, tid)


def test_interruption_reverts_status_to_previous(data_home):
    ctx = _world(data_home)
    tid = _approved(ctx)
    with pytest.raises(cancel.Cancelled):
        _run(ctx, tid, http=FakeHTTP(pages=PAGES, raise_on="https://docs.example.org/vector"))
    row = states.get(ctx, tid)
    assert row["status"] == states.APPROVED and "用户停止" in row["history"][-1]["note"]


def test_research_page_renders(data_home):
    from tests.test_ui_smoke import run_app
    ctx = _world(data_home)
    tid = _approved(ctx)
    _run(ctx, tid)
    at = run_app("research", domain="ai")
    assert not at.exception, [e.value for e in at.exception]
    assert any(b.label == "开始调研（后台）" for b in at.button)
    assert at.dataframe and any(s.label == "选择选题" for s in at.selectbox)
