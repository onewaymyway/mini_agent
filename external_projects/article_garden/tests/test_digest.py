"""P1 阶段 A：摘要校验、修复循环、分块、增量运行与章程草稿。"""

import pytest
import yaml

from article_garden import domains, joblog, settings
from article_garden.library import catalog
from article_garden.digest import charter_draft, runner, summarize as sm, validate
from article_garden.digest.llm_io import parse_yaml
from article_garden.library import importer
from tests.fakes import FakeLLM, article, good_reply


def _ctx():
    domains.create_domain("ai", "人工智能", "ai")
    return domains.DomainContext("ai")


def _import(ctx, *pairs):
    importer.apply(ctx, importer.collect([(n, t.encode("utf-8")) for n, t in pairs]))


# ------------------------------------------------------------------ 校验

def test_squash_treats_markdown_and_spacing_as_noise():
    assert validate.appears("向量 数据库", "**向量数据库**用于")
    assert not validate.appears("向量存储", "向量数据库")


def test_quote_not_in_article_is_rejected():
    obj = {"title": "t", "thesis": "x", "kind": "概念讲解", "level": "入门", "self_score": 3,
           "concepts": [], "claims": [{"text": "a", "quote": "原文里没有这句话"}], "dangling": []}
    issues = validate.validate_full(obj, article("标题"))
    assert any("逐字摘录" in i for i in issues)


def test_full_validation_enum_score_and_bool_guard():
    text = article("标题")
    base = {"title": "t", "thesis": "x", "kind": "概念讲解", "level": "入门", "self_score": 3,
            "concepts": [], "claims": [], "dangling": []}
    assert validate.validate_full(base, text) == []
    assert any("kind" in i for i in validate.validate_full({**base, "kind": "随便"}, text))
    assert any("self_score" in i for i in validate.validate_full({**base, "self_score": True}, text))
    assert any("self_score" in i for i in validate.validate_full({**base, "self_score": 6}, text))


def test_parse_strips_fences_and_reports_errors():
    obj, err = parse_yaml("```yaml\na: 1\n```")
    assert obj == {"a": 1} and err is None
    _, err = parse_yaml("这不是 yaml: [")
    assert err and "YAML" in err
    assert parse_yaml("")[1]


# ------------------------------------------------------------------ 修复循环

def test_short_article_one_call_valid():
    llm = FakeLLM()
    d = sm.summarize(article("标题", marker="向量数据库在此"), llm)
    assert d["kind"] == "概念讲解" and d["claims"] and d["concepts"] == ["向量数据库"]
    assert len(llm.calls) == 1 and d["chunks"] == 1


def test_bad_quote_is_repaired_with_specific_feedback():
    text = article("标题")
    bad = yaml.safe_dump({"title": "t", "thesis": "x", "kind": "概念讲解", "level": "入门", "self_score": 3,
                          "concepts": [], "claims": [{"text": "a", "quote": "编造的句子完全不在原文中"}],
                          "dangling": []}, allow_unicode=True)
    llm = FakeLLM(script=[bad])            # 第一次返回坏引文，第二次交给 good_reply（修复）
    d = sm.summarize(text, llm, max_repair_rounds=3)
    assert len(llm.calls) == 2
    assert "逐字摘录" in llm.calls[1] and "编造的句子" in llm.calls[1]   # 修复提示点名了具体问题
    assert validate.appears(d["claims"][0]["quote"], text)     # 修复后的引文确实来自原文


def test_fenced_reply_accepted():
    fenced = "```yaml\n" + good_reply(sm.full_prompt(article("标题"))) + "\n```"
    d = sm.summarize(article("标题"), FakeLLM(script=[fenced]))
    assert d["thesis"] == "论点"


def test_gives_up_after_per_article_budget_and_keeps_last_output():
    junk = yaml.safe_dump({"kind": "随便"}, allow_unicode=True)
    llm = FakeLLM(fn=lambda p: junk)
    with pytest.raises(sm.DigestFailed) as ei:
        sm.summarize(article("标题"), llm, max_repair_rounds=2)
    assert len(llm.calls) == 1 + 2                  # 首次 + 2 轮修复
    assert ei.value.issues and "kind" in "".join(ei.value.issues)
    assert ei.value.last_output == junk


# ------------------------------------------------------------------ 分块

def test_long_article_is_chunked_then_meta_step():
    sections = []
    for k in range(3):
        sections.append(f"## 第{k}节\n\n" + ("向量数据库在这里讨论一个具体的细节。" * 130))
    text = "# 长文\n\n" + "\n\n".join(sections)
    assert len(text) > sm.CHUNK_THRESHOLD
    llm = FakeLLM()
    d = sm.summarize(text, llm)
    chunks = sm.split_chunks(text)
    assert len(chunks) >= 2 and d["chunks"] == len(chunks)
    assert len(llm.calls) == len(chunks) + 1          # 每块一次 + 一次元信息
    assert d["kind"] == "综述" and d["claims"] and d["concepts"] == ["向量数据库"]


def test_split_chunks_keeps_headings_and_respects_target():
    body = "# A\n\n" + "甲" * 3000 + "\n\n## B\n\n" + "乙" * 3000
    chunks = sm.split_chunks(body, target=4000)
    assert len(chunks) == 2 and chunks[0].startswith("# A") and chunks[1].startswith("## B")


# ------------------------------------------------------------------ 增量与隔离

def test_incremental_runs_skip_unchanged_and_isolate_failures(data_home):
    ctx = _ctx()
    _import(ctx, ("a.md", article("甲")), ("b.md", article("乙", marker="BAD")), ("c.md", article("丙", marker="x")))
    settings.save_override("domain", "budget", "max_repair_rounds", 1, ctx)

    def fn(prompt):
        return yaml.safe_dump({"kind": "随便"}) if "BAD" in prompt else good_reply(prompt)

    counts = runner.run_digest(ctx, FakeLLM(fn=fn))
    assert counts == {"ok": 2, "failed": 1, "skipped": 0}     # 失败的一篇不影响其它篇
    rows = {r["id"]: runner.status_of(ctx, r) for r in catalog.list_articles(ctx)}
    assert rows == {"a-0001": "已摘要", "a-0002": "失败", "a-0003": "已摘要"}
    rec = runner.load_digest(ctx, "a-0002")
    assert rec["status"] == "failed" and rec["issues"] and rec["last_output"]

    llm = FakeLLM()
    assert runner.run_digest(ctx, llm) == {"ok": 1, "failed": 0, "skipped": 2}   # 只重试失败的一篇
    assert len(llm.calls) == 1

    llm = FakeLLM()
    assert runner.run_digest(ctx, llm) == {"ok": 0, "failed": 0, "skipped": 3}
    assert llm.calls == []                                                       # 无变更：零调用


def test_changed_content_is_redigested(data_home):
    from article_garden.library import catalog, hashing
    ctx = _ctx()
    _import(ctx, ("a.md", article("甲")))
    runner.run_digest(ctx, FakeLLM())
    row = catalog.list_articles(ctx)[0]
    new_body = article("甲改", marker="新内容")
    ctx.path(row["path"]).write_text(new_body, encoding="utf-8")
    data = catalog.load(ctx)
    data["articles"][0]["content_hash"] = hashing.content_hash(new_body)
    catalog.save(ctx, data)
    llm = FakeLLM()
    assert runner.run_digest(ctx, llm) == {"ok": 1, "failed": 0, "skipped": 0}
    assert len(llm.calls) >= 1


def test_run_writes_joblog_events(data_home):
    ctx = _ctx()
    _import(ctx, ("a.md", article("甲")))
    runner.run_digest(ctx, FakeLLM())
    events = [r.get("event") for r in joblog.tail(ctx.root, 50, scopes=["digest"])]
    assert "run_start" in events and "step_end" in events and "run_end" in events


def test_digest_record_is_plain_yaml_with_source_hash(data_home):
    from article_garden.library import catalog
    ctx = _ctx()
    _import(ctx, ("a.md", article("甲")))
    runner.run_digest(ctx, FakeLLM())
    row = catalog.list_articles(ctx)[0]
    rec = yaml.safe_load(runner.digest_path(ctx, row["id"]).read_text(encoding="utf-8"))
    assert rec["status"] == "ok" and rec["content_hash"] == row["content_hash"]
    assert rec["digest_version"] == sm.DIGEST_VERSION and rec["claims"][0]["quote"]


# ------------------------------------------------------------------ 章程草稿

CHARTER_REPLY = yaml.safe_dump({
    "domain": "人工智能",
    "scope": {"in": ["大语言模型", "检索增强生成"], "out": ["个股推荐"]},
    "audience": {"primary": "入门读者", "levels": ["入门", "进阶"]},
    "style": {"tone": "平实", "length": [1500, 3000], "must_have": ["摘要"], "ai_disclosure": True},
    "terms": {"检索增强生成": ["RAG"]},
    "sources": {"prefer": ["论文"], "avoid": ["营销号"], "min_credibility": "B"},
    "risk": {"disclaimer": "", "forbidden_claims": []},
}, allow_unicode=True, sort_keys=False)


def test_charter_draft_requires_digests_then_needs_adoption(data_home):
    ctx = _ctx()
    with pytest.raises(ValueError, match="摘要"):
        charter_draft.draft(ctx, FakeLLM(script=[CHARTER_REPLY]))
    _import(ctx, ("a.md", article("甲")))
    runner.run_digest(ctx, FakeLLM())
    before = ctx.load_charter()[0]
    bad = yaml.safe_dump({"domain": "x", "scope": {"in": []}})     # 第一次不合格 → 修复
    llm = FakeLLM(script=[bad, CHARTER_REPLY])
    res = charter_draft.draft(ctx, llm)
    assert res["articles"] == 1 and len(llm.calls) == 2
    assert ctx.load_charter()[0] == before                          # 草稿不改正式章程
    assert charter_draft.load_draft(ctx)["scope"]["in"] == ["大语言模型", "检索增强生成"]
    charter_draft.adopt(ctx)
    now = ctx.load_charter()[0]
    assert now["scope"]["in"] == ["大语言模型", "检索增强生成"] and now["terms"]["检索增强生成"] == ["RAG"]
    assert ctx.path("charter.yaml.bak").is_file()                   # 旧章程已备份


def test_adopt_without_draft_is_an_error(data_home):
    ctx = _ctx()
    with pytest.raises(ValueError, match="草稿"):
        charter_draft.adopt(ctx)
