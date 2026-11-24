"""P6：成文——大纲、逐节初稿、代码校验与修复、独立评审、入库回灌、审阅动作、生成文章不成为唯一出处。全部使用假模型。"""

import re

import pytest

from article_garden import cancel, store
from article_garden.library import catalog, front_matter
from article_garden.planning import states
from article_garden.wiki import build, model
from article_garden.writing import checks, runner
from tests.fakes import good_reply
from tests.test_planning import _world
from tests.test_research import _approved, _run


class WriterLLM:
    """按提示词阶段标记分派：大纲 / 正文 / 评审；其它（摘要等）交给 tests.fakes.good_reply。"""

    def __init__(self, section=None, critic=None, outline=None):
        self.calls = []
        self.section_fn = section or default_section
        self.critic_fn = critic or (lambda p: "issues: []\n")
        self.outline_fn = outline or default_outline

    def ask(self, prompt, system=""):
        self.calls.append(prompt)
        if "【阶段:大纲】" in prompt:
            return self.outline_fn(prompt)
        if "【阶段:正文】" in prompt:
            return self.section_fn(prompt)
        if "【阶段:评审】" in prompt:
            return self.critic_fn(prompt)
        return good_reply(prompt)

    def stage_count(self, tag):
        return sum(1 for c in self.calls if tag in c)


def _ids(prompt):
    return re.findall(r"^\[(src-\d{4})\]", prompt, re.M)


def default_outline(prompt):
    ids = _ids(prompt)
    secs = [
        {"heading": "摘要", "points": ["向量数据库"], "sources": ids[:2]},
        {"heading": "核心原理", "points": ["向量数据库"], "sources": ids[:2]},
        {"heading": "小结", "points": [], "sources": ids[:1]},
        {"heading": "延伸阅读", "points": [], "sources": ids[:1]},
    ]
    return "sections:\n" + "".join(
        f"  - heading: {s['heading']}\n    points: {s['points']}\n    sources: {s['sources']}\n" for s in secs)


def default_section(prompt):
    ids = _ids(prompt.split("可用来源")[1])
    sid = ids[0]
    paras = [f"向量数据库是一种面向高维向量的检索存储方式，适合语义搜索场景。[^{sid}]",
             "",
             f"这类系统通常依靠相似度度量完成查询，并把结果交给后续的生成步骤使用。[^{sid}]"]
    return "text: |\n" + "\n".join(("  " + p) if p else "" for p in paras) + "\n"


def _setup(data_home, *, length=(20, 5000), must_have=None):
    ctx = _world(data_home)
    tid = _approved(ctx)
    _run(ctx, tid)
    c, _ = ctx.load_charter()
    c["style"]["length"] = list(length)
    if must_have is not None:
        c["style"]["must_have"] = must_have
    ctx.save_charter(c)
    return ctx, tid


def _copy_section(prompt):
    """正文直接照抄摘录（超过 copy_chars_max）。"""
    sid = _ids(prompt.split("可用来源")[1])[0]
    excerpt = re.search(rf"\[{sid}\][^\n]*\n摘录：(.+)", prompt).group(1)
    return f"text: |\n  {excerpt}[^{sid}]\n"


# ------------------------------------------------------------------ 纯校验函数

def test_checks_flag_missing_marker_unknown_source_and_forbidden_claim():
    rules = {"copy_max": 60, "overlap_max": 0.35, "forbidden": ["保本"], "out": ["个股推荐"],
             "terms": {"检索增强生成": ["RAG"]}, "library": []}
    issues = checks.check_section("没有证据标记的一段话。\n\n引用错误。[^src-0009]\n\n保本收益很高。[^src-0001]",
                                  allowed=["src-0001"], excerpts={"src-0001": "无关摘录"}, rules=rules)
    text = " ".join(issues)
    assert "缺少证据标记" in text and "未分配给本节的来源" in text and "禁用说法「保本」" in text


def test_checks_term_alias_and_copy_run():
    rules = {"copy_max": 10, "overlap_max": 0.35, "forbidden": [], "out": [],
             "terms": {"检索增强生成": ["RAG"]}, "library": []}
    ex = "检索增强生成先从资料库中召回相关片段"
    issues = checks.check_section("RAG 先从资料库中召回相关片段。[^src-0001]", allowed=["src-0001"],
                                  excerpts={"src-0001": ex}, rules=rules)
    text = " ".join(issues)
    assert "用语不统一" in text and "连续重合" in text


def test_document_checks_kind_templates():
    body = "# 标题\n\n## 概念\n\n正文。\n\n## 延伸阅读\n"
    issues = checks.check_document(body, length=(1, 9999), must_have=["摘要"], kind="对比",
                                   disclaimer="", library_titles=[])
    assert any("缺少必备小节「摘要」" in i for i in issues)
    assert any("对比表格" in i for i in issues)


# ------------------------------------------------------------------ 成文流程

def test_write_creates_generated_draft_and_registers_article(data_home):
    ctx, tid = _setup(data_home)
    llm = WriterLLM()
    res = runner.write(ctx, tid, llm)
    assert res["result"] == "drafted"
    aid = res["article_id"]
    assert states.get(ctx, tid)["status"] == states.DRAFTED and states.get(ctx, tid)["article_id"] == aid
    row = next(r for r in catalog.list_articles(ctx) if r["id"] == aid)
    assert row["origin"] == "generated" and row["status"] == "draft" and row["path"].startswith("library/generated/")
    meta, body = front_matter.split(ctx.path(row["path"]).read_text(encoding="utf-8"))
    assert meta["origin"] == "generated" and meta["status"] == "draft" and meta["derived_from"] == tid
    assert meta["evidence"] and "## 延伸阅读" in body and "[^src-" in body
    assert "### 参考来源" in body and "本文由 AI 辅助生成" in body
    assert (ctx.path("drafts", tid, "draft.md")).is_file() and (ctx.path("drafts", tid, "checks.json")).is_file()
    # 入库后立即摘要（回灌 A 步）
    assert store.read_yaml(ctx.path("wiki", "digests", f"{aid}.yaml"))["status"] == "ok"


def test_write_requires_researched_topic_and_sufficient_evidence(data_home):
    ctx = _world(data_home)
    tid = _approved(ctx)                                  # 仅放行，未调研
    with pytest.raises(ValueError, match="只有已调研"):
        runner.write(ctx, tid, WriterLLM())
    with pytest.raises(ValueError, match="需要文本模型"):
        runner.write(ctx, tid, None)


def test_copied_excerpt_is_rejected_then_repaired(data_home):
    ctx, tid = _setup(data_home)
    # 测试用摘录较短：把连续重合阈值降到 20 字（设置项可在文章库设置中覆盖，见 settings.py）
    store.write_yaml_atomic(ctx.settings_path, {"thresholds": {"copy_chars_max": 20}})
    state = {"n": 0}

    def section(prompt):
        state["n"] += 1
        return _copy_section(prompt) if state["n"] == 1 else default_section(prompt)
    res = runner.write(ctx, tid, WriterLLM(section=section))
    assert res["result"] == "drafted"
    # 3 个正文小节（摘要、核心原理、小结）+ 1 次因照抄而触发的修复
    assert state["n"] == 4
    assert res["checks"]["budget_used"] == 1


def test_unauthorized_marker_is_rejected(data_home):
    ctx, tid = _setup(data_home)
    seen = {"bad": 0}

    def section(prompt):
        if seen["bad"] == 0:
            seen["bad"] = 1
            return "text: |\n  向量数据库很重要。[^src-9999]\n"
        return default_section(prompt)
    res = runner.write(ctx, tid, WriterLLM(section=section))
    assert res["result"] == "drafted" and seen["bad"] == 1


def test_forbidden_claim_exhausts_repairs_and_keeps_artifacts(data_home):
    ctx, tid = _setup(data_home)
    c, _ = ctx.load_charter()
    c["risk"]["forbidden_claims"] = ["保本"]
    ctx.save_charter(c)

    def section(prompt):
        sid = _ids(prompt.split("可用来源")[1])[0]
        return f"text: |\n  这种产品可以保本保收益，适合入门。[^{sid}]\n"
    res = runner.write(ctx, tid, WriterLLM(section=section), max_rounds=2)
    assert res["result"] == "failed" and any("保本" in r for r in res["reasons"])
    t = states.get(ctx, tid)
    assert t["status"] == states.FAILED
    d = ctx.path("drafts", tid)
    assert (d / "failed.json").is_file() and (d / "outline.yaml").is_file() and (d / "brief.yaml").is_file()
    assert not any(r.get("origin") == "generated" for r in catalog.list_articles(ctx))   # 未入库


def test_critic_issue_triggers_redraft_then_passes(data_home):
    ctx, tid = _setup(data_home)
    state = {"n": 0}

    def critic(prompt):
        state["n"] += 1
        if state["n"] == 1:
            return ("issues:\n  - section: 核心原理\n    kind: 无证据支持\n"
                    "    note: 第二段结论没有依据\n")
        return "issues: []\n"
    llm = WriterLLM(critic=critic)
    res = runner.write(ctx, tid, llm)
    assert res["result"] == "drafted"
    assert state["n"] == 2 and res["checks"]["critic_rounds"] == 2
    assert llm.stage_count("【阶段:正文】") > 2          # 核心原理一节被重写


def test_persistent_critic_issue_fails_without_registering(data_home):
    ctx, tid = _setup(data_home)
    critic = lambda p: "issues:\n  - section: 摘要\n    kind: 与证据矛盾\n    note: 数字不一致\n"
    res = runner.write(ctx, tid, WriterLLM(critic=critic))
    assert res["result"] == "failed"
    assert states.get(ctx, tid)["status"] == states.FAILED
    assert not any(r.get("origin") == "generated" for r in catalog.list_articles(ctx))


def test_outline_missing_required_heading_is_repaired(data_home):
    ctx, tid = _setup(data_home)
    state = {"n": 0}

    def outline(prompt):
        state["n"] += 1
        ids = _ids(prompt)
        if state["n"] == 1:   # 缺少「小结」
            return ("sections:\n  - heading: 摘要\n    points: [向量数据库]\n    sources: [" + ids[0] + "]\n"
                    "  - heading: 核心原理\n    points: [向量数据库]\n    sources: [" + ids[0] + "]\n"
                    "  - heading: 延伸阅读\n    points: []\n    sources: [" + ids[0] + "]\n")
        return default_outline(prompt)
    res = runner.write(ctx, tid, WriterLLM(outline=outline))
    assert res["result"] == "drafted" and state["n"] == 2


def test_stop_during_writing_reverts_to_previous_status(data_home):
    ctx, tid = _setup(data_home)

    def section(prompt):
        raise cancel.Cancelled("用户请求停止")
    with pytest.raises(cancel.Cancelled):
        runner.write(ctx, tid, WriterLLM(section=section))
    t = states.get(ctx, tid)
    assert t["status"] == states.RESEARCHED and "用户停止" in t["history"][-1]["note"]


# ------------------------------------------------------------------ 审阅

def _drafted(data_home):
    ctx, tid = _setup(data_home)
    res = runner.write(ctx, tid, WriterLLM())
    return ctx, tid, res["article_id"]


def test_approve_then_publish_updates_article_status(data_home):
    ctx, tid, aid = _drafted(data_home)
    runner.approve(ctx, tid)
    assert states.get(ctx, tid)["status"] == states.REVIEWED
    assert next(r for r in catalog.list_articles(ctx) if r["id"] == aid)["status"] == "reviewed"
    runner.publish(ctx, tid)
    assert states.get(ctx, tid)["status"] == states.PUBLISHED
    row = next(r for r in catalog.list_articles(ctx) if r["id"] == aid)
    assert row["status"] == "published"
    meta, _ = front_matter.split(ctx.path(row["path"]).read_text(encoding="utf-8"))
    assert meta["status"] == "published"


def test_publish_requires_review_first(data_home):
    ctx, tid, _ = _drafted(data_home)
    with pytest.raises(ValueError):
        runner.publish(ctx, tid)


def test_rewrite_with_feedback_reuses_article_id_and_keeps_revision(data_home):
    ctx, tid, aid = _drafted(data_home)
    llm = WriterLLM()
    res = runner.write(ctx, tid, llm, feedback="请把小结写得更具体")
    assert res["article_id"] == aid
    assert any("请把小结写得更具体" in c for c in llm.calls if "【阶段:正文】" in c)
    assert list((ctx.path("drafts", tid) / "revisions").glob("r*.md"))


def test_abandon_requires_reason_and_archives_article(data_home):
    ctx, tid, aid = _drafted(data_home)
    with pytest.raises(ValueError):
        runner.abandon(ctx, tid, "  ")
    runner.abandon(ctx, tid, "选题已过时")
    assert states.get(ctx, tid)["status"] == states.DROPPED
    assert next(r for r in catalog.list_articles(ctx) if r["id"] == aid)["status"] == "archived"


# ------------------------------------------------------------------ wiki：生成文章不能成为唯一出处

def test_generated_only_concept_gets_no_definition(data_home):
    ctx, _ = _setup(data_home)
    cluster = {"canonical": "向量数据库", "surfaces": ["向量数据库"], "articles": ["a-0099"]}
    claims = [{"id": "a-0099#1", "article_id": "a-0099", "text": "向量数据库按相似度检索",
               "quote": "向量数据库按相似度检索", "concepts": ["c-0001"], "origin": "generated"}]
    llm = WriterLLM()
    build._write_concept(ctx, llm, "c-0001", cluster, claims, 3, True)
    meta, body = model.read_page(ctx, "c-0001")
    assert meta["status"] == "generated_only" and llm.calls == []
    assert "暂不生成定义" in body


def test_mixed_claims_use_only_imported_for_definition(data_home):
    ctx, _ = _setup(data_home)
    cluster = {"canonical": "向量数据库", "surfaces": ["向量数据库"], "articles": ["a-0001", "a-0099"]}
    claims = [
        {"id": "a-0099#1", "article_id": "a-0099", "text": "生成文章断言", "quote": "生成文章断言", "origin": "generated"},
        {"id": "a-0001#1", "article_id": "a-0001", "text": "导入文章断言", "quote": "导入文章断言", "origin": "imported"},
    ]
    seen = []

    class Def:
        def ask(self, prompt, system=""):
            seen.append(prompt)
            return good_reply_def(prompt)
    build._write_concept(ctx, Def(), "c-0002", cluster, claims, 3, True)
    assert seen and "a-0099#1" not in seen[0] and "a-0001#1" in seen[0]


def good_reply_def(prompt):
    import yaml
    canon = re.search(r"概念「(.+?)」写一段定义", prompt).group(1)
    definition = f"{canon}是一种按相似度检索高维向量的数据结构，常用于语义搜索与检索增强生成，是理解该领域的基础概念。"
    return yaml.safe_dump({"definition": definition, "based_on": ["a-0001#1"]}, allow_unicode=True)


# ------------------------------------------------------------------ 看板与入口

def test_review_page_renders_drafted_article(data_home):
    from tests.test_ui_smoke import run_app
    ctx, tid, _ = _drafted(data_home)
    at = run_app("review", domain="ai")
    assert not at.exception, [e.value for e in at.exception]
    assert any(b.label == "通过审阅" for b in at.button)


def test_entrypoint_approve_and_abandon(data_home, capsys):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "entrypoints"))
    import write_article as ep
    ctx, tid, _ = _drafted(data_home)
    assert ep.main(["ai", tid, "--approve"]) == 0
    assert states.get(ctx, tid)["status"] == states.REVIEWED
    assert ep.main(["ai", tid, "--publish"]) == 0
    assert states.get(ctx, tid)["status"] == states.PUBLISHED


def test_generated_articles_produce_no_relation_edges(data_home):
    """P6：生成文章不参与关系抽取（边必须有导入文章的依据）。"""
    ctx, tid, aid = _drafted(data_home)
    row = next(r for r in catalog.list_articles(ctx) if r["id"] == aid)
    assert row["origin"] == "generated"
    calls = {"edge": 0}

    class Edge:
        def ask(self, prompt, system=""):
            if "【阶段:关系】" in prompt:
                calls["edge"] += 1
            return good_reply(prompt)
    build._build_edges(ctx, Edge(), [{"row": row, "rec": {"concepts": ["向量数据库", "索引"]}}],
                       [{"canonical": "向量数据库", "surfaces": ["向量数据库"], "articles": [aid]},
                        {"canonical": "索引", "surfaces": ["索引"], "articles": [aid]}],
                       {"向量数据库": "c-0001", "索引": "c-0002"}, 3)
    assert calls["edge"] == 0


# ------------------------------------------------------------------ 批量成文

def test_write_batch_skips_ineligible_and_continues_after_failure(data_home):
    ctx, tid = _setup(data_home)
    res = runner.write_batch(ctx, ["t-9999", tid, tid], WriterLLM())      # 不存在的跳过；重复 id 只处理一次
    assert res["total"] == 2 and res["drafted"] == 1
    assert [r["result"] for r in res["results"]] == ["skipped", "drafted"]
    assert states.get(ctx, tid)["status"] == states.DRAFTED
    again = runner.write_batch(ctx, [tid], WriterLLM())                    # 已成稿不属于批量范围
    assert again["results"][0]["result"] == "skipped"
    with pytest.raises(ValueError):
        runner.write_batch(ctx, [], WriterLLM())
    with pytest.raises(ValueError):
        runner.write_batch(ctx, [tid], None)


def test_write_batch_stop_ends_whole_batch_and_reverts(data_home):
    ctx, tid = _setup(data_home)

    def section(prompt):
        raise cancel.Cancelled("用户请求停止")
    with pytest.raises(cancel.Cancelled):
        runner.write_batch(ctx, [tid], WriterLLM(section=section))
    assert states.get(ctx, tid)["status"] == states.RESEARCHED


def test_review_page_batch_section_lists_writable_topics(data_home):
    from tests.test_ui_smoke import run_app
    ctx, tid = _setup(data_home)
    at = run_app("review", domain="ai")
    assert not at.exception, [e.value for e in at.exception]
    assert any(c.key == f"wb_{tid}" for c in at.checkbox)
    at.checkbox(key=f"wb_{tid}").check().run()
    assert any(b.label == "批量成文（1 个，后台）" and not b.disabled for b in at.button)


def test_entrypoint_batch_all_with_nothing_says_so(data_home, capsys):
    import sys
    from pathlib import Path
    _world(data_home)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "entrypoints"))
    import write_article as ep
    assert ep.main(["ai", "--batch-all"]) == 0
    assert "没有可成文的已调研选题" in capsys.readouterr().out


# ------------------------------------------------------------------ 字数是软目标

def test_length_outside_range_is_only_a_note_unless_far_off():
    body = "## 摘要\n" + "字" * 700
    soft = checks.check_document(body, length=(100, 500), must_have=[], kind="概念讲解", disclaimer="", library_titles=[])
    assert soft == []                                              # 略超上限：不算问题
    assert "超出" in checks.length_note(body, (100, 500)) and checks.length_note(body, (100, 1000)) == ""
    far = checks.check_document("## 摘要\n" + "字" * 2000, length=(100, 500), must_have=[], kind="概念讲解",
                                disclaimer="", library_titles=[])
    assert far and "严重偏离" in far[0]
    short = checks.check_document("## 摘要\n" + "字" * 20, length=(100, 500), must_have=[], kind="概念讲解",
                                  disclaimer="", library_titles=[])
    assert short and "严重偏离" in short[0]


def test_write_succeeds_when_length_slightly_over_and_records_note(data_home):
    ctx, tid = _setup(data_home, length=(100, 200))                 # 假模型正文约 334 字：超出上限但未超过 3 倍
    res = runner.write(ctx, tid, WriterLLM())
    assert res["result"] == "drafted" and "超出" in res["checks"]["length_note"]


def test_write_fails_when_length_far_off(data_home):
    ctx, tid = _setup(data_home, length=(5, 30))                    # 334 字 > 30×3，严重偏离
    assert runner.write(ctx, tid, WriterLLM())["result"] == "failed"


def test_length_tolerance_comes_from_settings(data_home):
    from article_garden import settings
    ctx, tid = _setup(data_home, length=(5, 30))                    # 假模型正文约 334 字，默认 3 倍容忍（90）下会失败
    assert settings.effective_settings(ctx)[0]["thresholds"]["length_high_factor"] == 3.0
    settings.save_override("domain", "thresholds", "length_high_factor", 20.0, ctx)   # 放宽到 600 字
    res = runner.write(ctx, tid, WriterLLM())
    assert res["result"] == "drafted" and "超出" in res["checks"]["length_note"]


def test_length_factor_settings_are_validated_and_shown_in_ui(data_home):
    import pytest as _pt
    from article_garden import settings
    from tests.test_ui_smoke import run_app
    ctx = _world(data_home)                                         # 默认章程（设置页字数下限控件要求 ≥100）
    with _pt.raises(ValueError):
        settings.save_override("domain", "thresholds", "length_high_factor", 0.5, ctx)   # 不能小于 1
    with _pt.raises(ValueError):
        settings.save_override("domain", "thresholds", "length_low_factor", 1.5, ctx)    # 不能大于 1
    at = run_app("settings", domain="ai")
    assert not at.exception, [e.value for e in at.exception]
    assert any("字数上限容忍倍数" in (n.label or "") for n in at.number_input)
