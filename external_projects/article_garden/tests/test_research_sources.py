"""检索来源与多轮检索：官方接口解析、来源开关、证据不足时继续换查询、轮数上限、摘要入库。全部假网络、假来源。"""

import json

from article_garden import store
from article_garden.planning import states
from article_garden.research import fetch, providers, queries, runner
from article_garden.research.search import Hit
from tests.test_research import _approved
from tests.test_planning import _world

ABS = ("向量数据库用于存储与检索高维向量，是语义搜索与检索增强生成的基础设施。"
       "本摘要专门讨论向量数据库的索引结构、召回率与延迟之间的取舍。") * 2

ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><id>http://arxiv.org/abs/2401.00001v1</id>
    <title>Vector databases: a survey</title>
    <summary>%s</summary></entry>
</feed>""" % ABS

CROSSREF_JSON = json.dumps({"message": {"items": [
    {"DOI": "10.1000/xyz", "title": ["Vector database indexing"],
     "abstract": "<jats:p>" + ABS + "</jats:p>", "URL": "https://doi.org/10.1000/xyz"}]}})

WIKI_JSON = json.dumps({"query": {"search": [
    {"title": "向量数据库", "snippet": "<span>向量</span>数据库是一种专门的数据库"}]}})


def _http(url, timeout):
    if "arxiv" in url:
        return 200, "application/xml", ARXIV_XML
    if "crossref" in url:
        return 200, "application/json", CROSSREF_JSON
    if "wikipedia" in url:
        return 200, "application/json", WIKI_JSON
    return 404, "text/plain", ""


def test_arxiv_results_carry_abstracts_and_abs_urls():
    hits = providers.ArxivSearch(_http).search("vector database", 5)
    assert hits and hits[0].source == "arxiv" and hits[0].url == "https://arxiv.org/abs/2401.00001v1"
    assert "向量数据库" in hits[0].snippet


def test_crossref_results_carry_abstracts_without_markup():
    hits = providers.CrossrefSearch(_http).search("vector database", 5)
    assert hits[0].source == "crossref" and "<jats" not in hits[0].snippet and "向量数据库" in hits[0].snippet


def test_wikipedia_results_are_parsed_and_stripped():
    hits = providers.WikiSearch(_http).search("向量数据库", 5)
    assert hits[0].url == "https://zh.wikipedia.org/wiki/向量数据库" or "wiki" in hits[0].url
    assert hits[0].snippet == "向量数据库是一种专门的数据库"


def test_source_toggles_decide_which_backends_are_used(data_home):
    from article_garden import settings
    ctx = _world(data_home)
    store.write_yaml_atomic(ctx.settings_path, {"research": {"use_arxiv": False, "use_crossref": False,
                                                             "use_wikipedia": False}})
    eff, _ = settings.effective_settings(ctx)
    names = [getattr(b, "name", "web")
             for b in providers.default_backends(eff, _http)]
    assert names == ["web"]


class RoundBackend:
    """只有在查询含有「论文」这类新角度词时才返回结果：用来验证“证据不足时换查询”。"""
    name = "fake"
    languages = ("zh", "en")

    def __init__(self):
        self.calls = []

    def search(self, query, max_results):
        self.calls.append(query)
        if "论文" not in query:
            return []
        return [Hit(title=f"论文{i}", url=f"https://arxiv.org/abs/9{i}", snippet=ABS, source="arxiv")
                for i in (1, 2)]


def _fetcher(ctx):
    return fetch.Fetcher(ctx, http_get=lambda u, t: (404, "text/plain", ""), min_interval=0, sleep=lambda s: None)


def test_insufficient_evidence_triggers_new_queries_until_enough(data_home):
    ctx = _world(data_home)
    tid = _approved(ctx)
    be = RoundBackend()
    res = runner.run(ctx, tid, search_backend=be, fetcher=_fetcher(ctx))
    assert res["result"] == "researched"
    assert res["rounds"] == 2                              # 第一轮不够，第二轮换了新查询才够
    assert any("论文" in q for q in be.calls)
    assert states.get(ctx, tid)["status"] == states.RESEARCHED


def test_search_stops_at_the_round_limit_when_nothing_is_found(data_home):
    ctx = _world(data_home)
    tid = _approved(ctx)
    store.write_yaml_atomic(ctx.settings_path, {"research": {"max_query_rounds": 2}})

    class Empty(RoundBackend):
        def search(self, query, max_results):
            self.calls.append(query)
            return []
    be = Empty()
    res = runner.run(ctx, tid, search_backend=be, fetcher=_fetcher(ctx))
    assert res["result"] == "dropped" and res["rounds"] == 2
    assert len(set(be.calls)) == len(be.calls)             # 每个查询只发出一次


def test_api_abstracts_are_used_directly_without_fetching_pages(data_home):
    ctx = _world(data_home)
    tid = _approved(ctx)
    fetched = []

    class Api(RoundBackend):
        def search(self, query, max_results):
            return [Hit(title=f"摘要{i}", url=f"https://arxiv.org/abs/8{i}", snippet=ABS, source="arxiv")
                    for i in (1, 2)]
    f = fetch.Fetcher(ctx, http_get=lambda u, t: (fetched.append(u) or (404, "", "")), min_interval=0,
                      sleep=lambda s: None)
    res = runner.run(ctx, tid, search_backend=Api(), fetcher=f)
    assert res["result"] == "researched" and fetched == []
    assert [c["source_type"] for c in [store.read_yaml(p) for p in
            (ctx.path("research", "packs", tid, "sources")).glob("src-*.yaml")]] == ["abstract", "abstract"]


def test_abstract_from_search_result_is_used_when_the_page_is_unavailable(data_home):
    ctx = _world(data_home)
    tid = _approved(ctx)

    class Web(RoundBackend):
        def search(self, query, max_results):
            return [Hit(title=f"网页{i}", url=f"https://site{i}.example.org/a", snippet=ABS, source="web")
                    for i in (1, 2)]
    res = runner.run(ctx, tid, search_backend=Web(), fetcher=_fetcher(ctx))
    types = [store.read_yaml(p)["source_type"] for p in
             (ctx.path("research", "packs", tid, "sources")).glob("src-*.yaml")]
    assert res["added"] >= 2 and types and set(types) == {"abstract"}   # 页面不可用时使用搜索结果摘要


def test_expansion_never_repeats_a_used_query():
    topic = {"title": "向量数据库是什么", "must_cover": ["向量数据库", "索引"]}
    used = {"向量数据库是什么 论文"}
    zh, en = queries.expand(topic, used, ["vector database"], n=4)
    assert "向量数据库是什么 论文" not in zh and len(zh) == 4
    assert en and all("survey" in q or "review" in q or "applications" in q or "limitations" in q
                      or "tutorial" in q or "benchmark" in q for q in en)
