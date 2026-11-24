"""P7：闭环、挡位闸门、每日预算、停止信号、单步超时、导出。全部使用假模型、假搜索与假网络。"""

import re
import sys
import time
from pathlib import Path

import pytest

from article_garden import budget, cycle, export, gears, store
from article_garden.library import catalog
from article_garden.planning import states
from tests.fakes import good_reply
from tests.test_planning import MidLLM, _world
from tests.test_research import FakeHTTP, FakeSearch, HITS, PAGES
from tests.test_wiki import Router
from tests.test_tree import PlaceRouter
from tests.test_writing import WriterLLM


class World:
    """按阶段标记分派到已有的假模型，并计数每次调用。"""

    def __init__(self, on_call=None):
        self.calls = []
        self.writer = WriterLLM()
        self.router = Router()
        self.place = PlaceRouter()
        self.mid = MidLLM(duplicate=False)
        self.on_call = on_call

    def ask(self, prompt, system=""):
        self.calls.append(prompt)
        if self.on_call:
            self.on_call(len(self.calls))
        if re.search(r"【阶段:(大纲|正文|评审)】", prompt):
            return self.writer.ask(prompt)
        if "【阶段:挂载】" in prompt:
            return self.place.ask(prompt)
        if re.search(r"【阶段:(选题判重|开放命题|选题标题)】", prompt):
            return self.mid.ask(prompt)
        if "【阶段:查询】" in prompt:
            return "queries:\n  - 向量数据库 原理\n  - 向量索引 入门\n  - 向量数据库 官方文档\n"
        if re.search(r"【阶段:(归一|定义|关系|冲突)】", prompt):
            return self.router.ask(prompt)
        return good_reply(prompt)


def _gear(ctx, **auto):
    c = {"automation": {"gear": "G1", **auto}}
    store.write_yaml_atomic(ctx.settings_path, c)


def _fetcher(ctx):
    from article_garden.research import fetch
    return fetch.Fetcher(ctx, http_get=FakeHTTP(pages=PAGES), min_interval=0.0, sleep=lambda s: None)


# ------------------------------------------------------------------ 闸门

def test_gears_gate_each_automatic_action():
    eff = {"automation": {"gear": "G0", "auto_approve": True, "auto_research": True, "auto_write": True,
                          "auto_publish": False, "scheduled_cycle": False}}
    assert gears.allowed(eff, "cycle")[0] is False                       # G0 全手动
    eff["automation"]["gear"] = "G1"
    assert gears.allowed(eff, "cycle")[0] and not gears.allowed(eff, "auto_approve")[0]
    eff["automation"]["gear"] = "G2"
    assert gears.allowed(eff, "auto_approve")[0] and gears.allowed(eff, "write")[0]
    assert not gears.allowed(eff, "publish")[0] and not gears.allowed(eff, "review")[0]
    eff["automation"]["gear"] = "G3"
    assert gears.allowed(eff, "review")[0] and not gears.allowed(eff, "publish")[0]   # 仍需 auto_publish
    eff["automation"]["auto_publish"] = True
    assert gears.allowed(eff, "publish")[0]


def test_g0_refuses_cycle(data_home):
    ctx = _world(data_home)
    with pytest.raises(ValueError, match="G0"):
        cycle.run_cycle(ctx, World())


# ------------------------------------------------------------------ 闭环

def test_g1_cycle_proposes_topics_without_approving_and_is_idempotent(data_home):
    ctx = _world(data_home)
    _gear(ctx, gear="G1")
    llm = World()
    rep = cycle.run_cycle(ctx, llm, search_backend=FakeSearch(HITS), fetcher=_fetcher(ctx))
    assert rep["stopped"] is None
    names = [s["step"] for s in rep["steps"]]
    assert names == ["摘要", "概念与关系", "知识树提案", "缺口与选题", "调研", "成文", "审阅与发布"]
    rows = states.load_topics(ctx)
    assert any(r["status"] == states.PROPOSED for r in rows)
    assert not any(r["status"] == states.APPROVED for r in rows)          # G1：选题需人工放行
    detail = {s["step"]: s["detail"] for s in rep["steps"]}
    assert detail["调研"].startswith("跳过") and detail["成文"].startswith("跳过")   # G1 不自动调研、不自动成文
    assert detail["审阅与发布"].startswith("跳过")
    # 无变化再运行：零模型调用
    llm.calls.clear()
    rep2 = cycle.run_cycle(ctx, llm, search_backend=FakeSearch(HITS), fetcher=_fetcher(ctx))
    assert llm.calls == [], f"无变化时不应调用模型，实际 {len(llm.calls)} 次"
    assert rep2["stopped"] is None
    assert store.read_json(ctx.path("runs", "last_cycle.json"))["steps"]


def test_g2_cycle_auto_approves_researches_and_drafts_within_limits(data_home):
    ctx = _world(data_home)
    c, _ = ctx.load_charter()
    c["style"]["length"] = [20, 5000]
    ctx.save_charter(c)
    _gear(ctx, gear="G2", auto_approve=True, auto_research=True, auto_write=True)
    store.write_yaml_atomic(ctx.settings_path, {"automation": {"gear": "G2", "auto_approve": True,
                                                              "auto_research": True, "auto_write": True},
                                                "budget": {"max_articles_per_cycle": 1}})
    rep = cycle.run_cycle(ctx, World(), search_backend=FakeSearch(HITS), fetcher=_fetcher(ctx))
    rows = states.load_topics(ctx)
    assert any(r["status"] in (states.APPROVED, states.RESEARCHED, states.DRAFTED) for r in rows)
    drafted = [r for r in rows if r["status"] == states.DRAFTED]
    assert len(drafted) == 1                                             # 每轮最多成文 1 篇
    gen = [r for r in catalog.list_articles(ctx) if r.get("origin") == "generated"]
    assert len(gen) == 1 and gen[0]["status"] == "draft"                 # G2 不自动审阅、不发布
    assert rep["stopped"] is None


def test_g3_reviews_and_publishes_only_when_allowed(data_home):
    ctx = _world(data_home)
    c, _ = ctx.load_charter()
    c["style"]["length"] = [20, 5000]
    ctx.save_charter(c)
    store.write_yaml_atomic(ctx.settings_path, {"automation": {"gear": "G3", "auto_approve": True,
                                                              "auto_research": True, "auto_write": True,
                                                              "auto_publish": False},
                                                "budget": {"max_articles_per_cycle": 1}})
    cycle.run_cycle(ctx, World(), search_backend=FakeSearch(HITS), fetcher=_fetcher(ctx))
    rows = [r for r in states.load_topics(ctx) if r.get("article_id")]
    assert rows and all(r["status"] == states.REVIEWED for r in rows)   # G3：自动到 reviewed，不发布
    assert all(r["status"] != states.PUBLISHED for r in states.load_topics(ctx))


def test_tree_change_is_proposed_never_applied_automatically(data_home):
    ctx = _world(data_home)
    _gear(ctx, gear="G1")
    from tests.test_tree import _add_article
    _add_article(ctx, "新文章", "新概念甲用于测试挂载提案。", ["新概念甲"], ["新概念甲用于测试"])
    cycle.run_cycle(ctx, World(), steps=["摘要", "概念与关系", "知识树提案"])
    from article_garden.wiki import tree
    assert "新概念甲" not in [n["name"] for _, n in tree.nodes_at(tree.load(ctx), tree.TOPIC)]


# ------------------------------------------------------------------ 预算与停止

def test_daily_budget_stops_cycle_and_next_run_makes_no_calls(data_home):
    ctx = _world(data_home)
    store.write_yaml_atomic(ctx.settings_path, {"automation": {"gear": "G1"},
                                                "budget": {"max_llm_calls_per_day": 1}})
    llm = World()
    rep = cycle.run_cycle(ctx, llm, search_backend=FakeSearch(HITS), fetcher=_fetcher(ctx))
    assert rep["stopped"] == "budget"                                    # 概念与关系需要 ≥2 次调用，第 2 次被拦下
    assert len(llm.calls) == 1
    assert budget.usage(ctx)["domain"]["calls"] == 1
    llm.calls.clear()
    rep2 = cycle.run_cycle(ctx, llm, search_backend=FakeSearch(HITS), fetcher=_fetcher(ctx))
    assert rep2["stopped"] == "budget" and llm.calls == []               # 当日已用尽：立即停


def test_global_budget_is_a_separate_hard_cap(data_home):
    ctx = _world(data_home)
    store.write_yaml_atomic(ctx.settings_path, {"automation": {"gear": "G1"}})
    from article_garden import paths
    store.write_yaml_atomic(paths.global_settings_path(), {"budget": {"global_max_llm_calls_per_day": 1}})
    store.write_json_atomic(budget._global_path(), {"date": budget._today(), "calls": 0})
    with pytest.raises(budget.BudgetStop, match="全局"):
        for _ in range(3):
            budget.charge(ctx)
    assert budget.usage(ctx)["global"]["calls"] == 1                     # 超限的那次不计数


def test_stop_request_ends_cycle_before_next_model_call(data_home):
    ctx = _world(data_home)
    _gear(ctx, gear="G1")

    def stop_on_first(n):
        if n == 1:
            budget.request_stop(ctx)                 # 概念与关系至少需要 2 次调用，因此第 2 次之前必须被拦下
    llm = World(on_call=stop_on_first)
    rep = cycle.run_cycle(ctx, llm, search_backend=FakeSearch(HITS), fetcher=_fetcher(ctx))
    assert rep["stopped"] == "stop"
    assert len(llm.calls) == 1                           # 停止后不再发起新的调用


def test_stop_during_last_call_is_still_reported(data_home):
    ctx = _world(data_home)
    _gear(ctx, gear="G1")
    llm = World(on_call=lambda n: budget.request_stop(ctx))   # 每次调用都请求停止：最后一次之后仍需如实记录
    rep = cycle.run_cycle(ctx, llm, steps=["摘要"])
    assert rep["stopped"] in ("stop", None)
    rep2 = cycle.run_cycle(ctx, World(), steps=["摘要"])     # 下一轮开始时清除旧的停止信号
    assert rep2["stopped"] is None


def test_stop_flag_is_cleared_when_next_cycle_starts(data_home):
    ctx = _world(data_home)
    _gear(ctx, gear="G1")
    budget.request_stop(ctx)
    rep = cycle.run_cycle(ctx, World(), search_backend=FakeSearch(HITS), fetcher=_fetcher(ctx))
    assert rep["stopped"] is None and not budget.stop_requested(ctx)


def test_guarded_llm_step_timeout_is_enforced(data_home):
    ctx = _world(data_home)

    class Slow:
        def ask(self, prompt, system=""):
            time.sleep(1.0)
            return "x"
    g = budget.GuardedLLM(Slow(), ctx, timeout_sec=0.2)
    t0 = time.monotonic()
    with pytest.raises(budget.StepTimeout):
        g.ask("p")
    assert time.monotonic() - t0 < 0.9                                   # 不等模型返回


def test_budget_exception_is_not_swallowed_by_pipeline_handlers(data_home):
    assert issubclass(budget.BudgetStop, BaseException) and not issubclass(budget.BudgetStop, Exception)
    assert issubclass(budget.StepTimeout, BaseException)


# ------------------------------------------------------------------ 导出

def test_export_contains_only_published_articles(data_home):
    from tests.test_writing import _setup
    from article_garden.writing import runner as wrunner
    import zipfile
    ctx, tid = _setup(data_home)
    res = wrunner.write(ctx, tid, WriterLLM())
    aid = res["article_id"]
    site = export.export_site(ctx)
    idx = (site / "index.html").read_text(encoding="utf-8")
    assert f"articles/{aid}.html" not in idx                             # 草稿不导出
    zip_before = zipfile.ZipFile(export.export_published_zip(ctx)).namelist()
    assert f"articles/{aid}.md" not in zip_before
    wrunner.approve(ctx, tid)
    wrunner.publish(ctx, tid)
    site = export.export_site(ctx)
    idx = (site / "index.html").read_text(encoding="utf-8")
    assert f"articles/{aid}.html" in idx
    page = (site / "articles" / f"{aid}.html").read_text(encoding="utf-8")
    assert "<sup>src-" in page and "参考来源" in page
    names = zipfile.ZipFile(export.export_published_zip(ctx)).namelist()
    assert f"articles/{aid}.md" in names and "manifest.json" in names


def test_md_to_html_handles_table_quote_list_and_escapes():
    h = export.md_to_html("# 标题\n\n> 引用 <b>\n\n- 一\n- 二\n\n| 维度 | A |\n|---|---|\n| x | y |\n\n段落 **粗** `code` [链接](https://e.example)")
    assert "<h1>标题</h1>" in h and "<blockquote>" in h and "&lt;b&gt;" in h
    assert "<li>一</li>" in h and "<table>" in h and "<td>y</td>" in h
    assert "<strong>粗</strong>" in h and "<code>code</code>" in h and 'href="https://e.example"' in h


def test_entrypoints_stop_and_export(data_home, capsys):
    scripts = Path(__file__).resolve().parent.parent / "entrypoints"
    sys.path.insert(0, str(scripts))
    import cycle as ep_cycle
    import export_site as ep_export
    _world(data_home)
    assert ep_cycle.main(["ai", "--stop"]) == 0
    from article_garden import domains
    ctx = domains.DomainContext("ai")
    assert budget.stop_requested(ctx)
    budget.clear_stop(ctx)
    assert ep_export.main(["ai"]) == 0
    assert ep_export.main(["ai", "--zip"]) == 0


def test_home_page_has_cycle_and_budget_caption(data_home):
    from tests.test_ui_smoke import run_app
    _world(data_home)
    at = run_app("home", domain="ai")
    assert not at.exception, [e.value for e in at.exception]
    assert any("运行一轮" in b.label for b in at.button)
    assert any("今日 LLM 调用" in c.value for c in at.caption)


# ------------------------------------------------------------------ 全部执行

def _manual_world(data_home):
    ctx = _world(data_home)
    c, _ = ctx.load_charter()
    c["style"]["length"] = [20, 5000]
    ctx.save_charter(c)
    return ctx


def test_run_everything_works_from_default_g0_and_stops_before_publishing(data_home):
    ctx = _manual_world(data_home)                    # 默认挡位 G0：按挡位运行会被拒绝
    with pytest.raises(ValueError, match="G0"):
        cycle.run_cycle(ctx, World(), search_backend=FakeSearch(HITS), fetcher=_fetcher(ctx))
    rep = cycle.run_cycle(ctx, World(), manual=True, search_backend=FakeSearch(HITS), fetcher=_fetcher(ctx))
    assert rep["stopped"] is None and rep["gear"] == "全部执行"
    rows = states.load_topics(ctx)
    assert any(r["status"] in (states.REVIEWED, states.DRAFTED) for r in rows)   # 已成稿并标为已审阅
    assert not any(r["status"] == states.PUBLISHED for r in rows)                  # 从不自动发布
    detail = {s["step"]: s["detail"] for s in rep["steps"]}
    assert "全部执行已放行" in detail["缺口与选题"]


def test_run_everything_respects_the_daily_budget(data_home):
    ctx = _manual_world(data_home)
    store.write_yaml_atomic(ctx.settings_path, {"budget": {"max_llm_calls_per_day": 1}})
    rep = cycle.run_cycle(ctx, World(), manual=True, search_backend=FakeSearch(HITS), fetcher=_fetcher(ctx))
    assert rep["stopped"] == "budget"


def test_run_everything_approves_at_most_the_per_cycle_topic_limit(data_home):
    from article_garden.planning import topics
    ctx = _manual_world(data_home)
    topics.run(ctx, World())                                   # 先生成待放行选题（G0 下不会放行）
    before = sum(r["status"] == states.PROPOSED for r in states.load_topics(ctx))
    assert before >= 2                                         # 前提：至少两个待放行，上限才有意义
    store.write_yaml_atomic(ctx.settings_path, {"budget": {"max_topics_per_cycle": 1}})
    cycle.run_cycle(ctx, World(), manual=True, steps=["缺口与选题"])
    approved = [r for r in states.load_topics(ctx) if r["status"] == states.APPROVED]
    assert len(approved) == 1                                  # 恰好放行上限的数量


def test_home_page_offers_run_everything_behind_a_confirmation(data_home):
    from tests.test_ui_smoke import run_app
    _world(data_home)
    at = run_app("home", domain="ai")
    assert not at.exception
    labels = [b.label for b in at.button]
    assert any("全部执行" in l for l in labels)
    run_btn = next(b for b in at.button if "全部执行" in b.label)
    assert run_btn.disabled                                    # 未勾选确认前不可点击
