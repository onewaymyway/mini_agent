"""P2：概念归一、定义（可追溯）、断言账本、关系边（无依据丢弃）、冲突与增量。"""

import re

import pytest
import yaml

from article_garden import domains, joblog
from article_garden.digest import runner as drunner
from article_garden.library import catalog, importer
from article_garden.wiki import build, model
from article_garden.wiki import validate_util as vu


def _body(title, sentence):
    filler = "\n\n".join(f"这一部分只是为了让文章达到长度要求，第{i}段没有新的概念。" for i in range(10))
    return f"# {title}\n\n{sentence}\n\n{filler}\n"


class Router:
    """按提示词里的阶段标记分派回复；可配置归一判断、关系、冲突，并统计各阶段调用次数。"""

    def __init__(self, same_pairs=(), edge_pairs=None, bad_quote_pairs=(), conflicts=None,
                 bad_definition_once=False, bad_conflict_once=False):
        self.same = {frozenset(p) for p in same_pairs}
        self.edge_pairs = edge_pairs or {}
        self.bad_quote = {frozenset(p) for p in bad_quote_pairs}
        self.conflicts = set(conflicts or ())          # 概念名集合：这些概念返回一条跨文章冲突
        self.bad_def = bad_definition_once
        self.bad_conf = bad_conflict_once
        self.stage_calls = {}

    def ask(self, prompt, system=""):
        stage = re.search(r"【阶段:(\w+)】", prompt).group(1)
        self.stage_calls[stage] = self.stage_calls.get(stage, 0) + 1
        return getattr(self, "_" + stage)(prompt)

    def _归一(self, prompt):
        decisions = []
        for pid, a, b in re.findall(r"^(p\d+): (.+?) ｜ (.+)$", prompt, re.M):
            decisions.append({"id": pid, "same": frozenset((a.strip(), b.strip())) in self.same})
        return yaml.safe_dump({"decisions": decisions}, allow_unicode=True)

    def _定义(self, prompt):
        canon = re.search(r"概念「(.+?)」写一段定义", prompt).group(1)
        ids = [line.split("：")[0].strip() for line in prompt.splitlines() if re.match(r"^\S+#\d+：", line)]
        if self.bad_def:
            self.bad_def = False
            return yaml.safe_dump({"definition": f"{canon}是一种数据结构。", "based_on": ["不存在#9"]}, allow_unicode=True)
        d = f"{canon}是一种把文本转换为高维向量以便检索的数据结构，常用于语义搜索与检索增强生成等场景，便于按相似度查找相关内容，是理解该领域的基础概念之一。"
        return yaml.safe_dump({"definition": d, "based_on": ids}, allow_unicode=True, sort_keys=False)

    def _关系(self, prompt):
        body = re.search(r"<article>\n(.*)\n</article>", prompt, re.S).group(1)
        sentence = next(l for l in body.splitlines() if l.strip() and not l.startswith("#") and len(l) > 8)
        edges = []
        for a, b in re.findall(r"^- (.+?) ｜ (.+)$", prompt, re.M):
            key = frozenset((a.strip(), b.strip()))
            if key in self.edge_pairs:
                quote = "编造的依据句子不在原文中" if key in self.bad_quote else sentence.strip()[:30]
                edges.append({"src": a.strip(), "dst": b.strip(), "type": self.edge_pairs[key],
                              "quote": quote, "rationale": "原文明确并列或包含"})
        return yaml.safe_dump({"edges": edges}, allow_unicode=True)

    def _冲突(self, prompt):
        claims = re.findall(r"^(\S+#\d+)（来自 (\S+)）：", prompt, re.M)
        canon = re.search(r"概念「(.+?)」的若干断言", prompt).group(1)
        if self.bad_conf:
            self.bad_conf = False
            return yaml.safe_dump({"conflicts": [{"a": claims[0][0], "b": claims[0][0], "reason": "同一条"}]},
                                  allow_unicode=True)
        if canon in self.conflicts:
            first = claims[0]
            second = next(c for c in claims if c[1] != first[1])
            return yaml.safe_dump({"conflicts": [{"a": first[0], "b": second[0], "reason": "结论相反"}]},
                                  allow_unicode=True)
        return "conflicts: []\n"


def _seed(ctx, specs):
    """specs: {标题: (句子, [概念…], [断言文本…])} —— 直接写入文章与摘要，精确控制输入。"""
    pairs = []
    for title, (sentence, concepts, claim_texts) in specs.items():
        body = _body(title, sentence)
        pairs.append((f"{title}.md", body))
    importer.apply(ctx, importer.collect([(n, b.encode("utf-8")) for n, b in pairs]))
    rows = catalog.list_articles(ctx)
    for row, (title, (sentence, concepts, claim_texts)) in zip(rows, specs.items()):
        rec = {"id": row["id"], "content_hash": row["content_hash"], "status": "ok", "digest_version": 1,
               "title": title, "thesis": "论点", "kind": "概念讲解", "level": "入门", "self_score": 4,
               "concepts": concepts, "claims": [{"text": t, "quote": sentence} for t in claim_texts],
               "dangling": [], "chunks": 1, "issues": []}
        drunner.digest_path(ctx, row["id"]).parent.mkdir(parents=True, exist_ok=True)
        from article_garden import store
        store.write_yaml_atomic(drunner.digest_path(ctx, row["id"]), rec)


def _lib(data_home):
    domains.create_domain("ai", "人工智能", "ai")
    return domains.DomainContext("ai")


SPECS = {
    "检索增强生成": ("检索增强生成结合向量数据库与大语言模型，先检索再生成。",
                 ["检索增强生成", "向量数据库", "大语言模型"],
                 ["检索增强生成先检索相关资料再生成答案", "向量数据库按相似度检索向量"]),
    "向量入门": ("向量数据库用于存储高维向量并按相似度检索。",
              ["向量数据库", "向量 数据库", "RAG"],
              ["向量数据库按相似度检索向量"]),
    "大模型概览": ("大语言模型是基于海量文本训练的生成模型。",
               ["大语言模型（LLM）", "大语言模型"],
               ["大语言模型基于海量文本训练"]),
}


def test_normalize_exact_alias_and_llm_confirmed_merges(data_home):
    ctx = _lib(data_home)
    ctx.save_charter({**ctx.load_charter()[0], "terms": {"检索增强生成": ["RAG"]}})
    _seed(ctx, SPECS)
    router = Router(same_pairs=[("大语言模型", "大语言模型（LLM）")])
    build.build(ctx, router)
    names = {c["name"] for c in model.list_concepts(ctx)}
    assert "向量数据库" in names                   # “向量 数据库” 去空白后相同 → 合并
    assert "检索增强生成" in names and "RAG" not in names    # 章程术语表别名 → 规范词
    assert "大语言模型" in names and "大语言模型（LLM）" not in names   # LLM 确认同指 → 合并
    assert router.stage_calls.get("归一") == 1                 # 只有中间分才问 LLM


def test_llm_rejection_keeps_concepts_separate(data_home):
    ctx = _lib(data_home)
    _seed(ctx, SPECS)
    build.build(ctx, Router(same_pairs=[]))
    names = {c["name"] for c in model.list_concepts(ctx)}
    assert {"大语言模型", "大语言模型（LLM）"} <= names


def test_definitions_are_traceable_to_claims(data_home):
    ctx = _lib(data_home)
    _seed(ctx, SPECS)
    build.build(ctx, Router())
    claims = {c["id"]: c for c in model.read_jsonl(ctx, "claims.jsonl")}
    for meta in model.list_concepts(ctx):
        if meta["status"] != "ok":
            continue
        assert meta["based_on"] and set(meta["based_on"]) <= set(meta["claims"])
        for cid in meta["based_on"]:
            row = claims[cid]
            body = catalog.read_body(ctx, catalog.get(ctx, row["article_id"]))
            assert vu.appears(row["quote"], body)          # 断言的依据摘录确实在原文中
        assert meta["name"] in model.read_page(ctx, meta["id"])[1]


def test_definition_repaired_when_based_on_is_unknown(data_home):
    ctx = _lib(data_home)
    _seed(ctx, SPECS)
    router = Router(bad_definition_once=True)
    build.build(ctx, router)
    assert router.stage_calls["定义"] > 3              # 有一次修复调用
    statuses = {m["status"] for m in model.list_concepts(ctx)}
    assert statuses <= {"ok", "no_source"} and "ok" in statuses   # 无依据概念应标 no_source，而非失败


def test_edges_without_evidence_are_dropped(data_home):
    ctx = _lib(data_home)
    _seed(ctx, SPECS)
    router = Router(edge_pairs={frozenset(("检索增强生成", "向量数据库")): "part_of",
                                frozenset(("检索增强生成", "大语言模型")): "related"},
                    bad_quote_pairs=[("检索增强生成", "大语言模型")])
    build.build(ctx, router)
    edges = model.read_jsonl(ctx, "edges.jsonl")
    names = {m["id"]: m["name"] for m in model.list_concepts(ctx)}
    kept = {(names[e["src"]], names[e["dst"]], e["type"]) for e in edges}
    assert ("向量数据库", "检索增强生成", "part_of") in kept   # 方向以模型给出的 src/dst 为准
    assert not any("大语言模型" in k[0] + k[1] for k in kept)    # 摘录编造 → 整条丢弃
    assert all(e["evidence"] and e["evidence"][0]["quote"] for e in edges)


def test_edge_with_unknown_type_is_dropped(data_home):
    ctx = _lib(data_home)
    _seed(ctx, SPECS)
    obj = {"edges": [{"src": "检索增强生成", "dst": "向量数据库", "type": "随便", "quote": "检索增强生成结合",
                      "rationale": "x"}]}
    accepted, dropped = build.content.parse_edges(obj, [("检索增强生成", "向量数据库")],
                                                  catalog.read_body(ctx, catalog.list_articles(ctx)[0]))
    assert accepted == [] and "不在允许列表内" in dropped[0]


def test_conflicts_need_two_articles_and_are_stored_with_both_sides(data_home):
    ctx = _lib(data_home)
    _seed(ctx, {
        "甲": ("向量数据库按相似度检索。", ["向量数据库"], ["向量数据库只支持精确匹配"]),
        "乙": ("向量数据库按相似度检索。", ["向量数据库"], ["向量数据库支持相似度检索"]),
    })
    router = Router(conflicts=["向量数据库"], bad_conflict_once=True)
    build.build(ctx, router)
    conflicts = model.read_jsonl(ctx, "conflicts.jsonl")
    assert len(conflicts) == 1
    sides = conflicts[0]["sides"]
    assert {s["article_id"] for s in sides} == {"a-0001", "a-0002"} and all(s["quote"] for s in sides)


def test_rerun_reuses_definitions_and_keeps_ids(data_home):
    ctx = _lib(data_home)
    _seed(ctx, SPECS)
    build.build(ctx, Router())
    ids_before = {m["name"]: m["id"] for m in model.list_concepts(ctx)}
    router = Router()
    build.build(ctx, router)
    assert router.stage_calls.get("定义", 0) == 0          # 依据未变：不再调用模型
    assert {m["name"]: m["id"] for m in model.list_concepts(ctx)} == ids_before


def test_human_edited_page_is_never_overwritten(data_home):
    ctx = _lib(data_home)
    _seed(ctx, SPECS)
    build.build(ctx, Router())
    cid = next(m["id"] for m in model.list_concepts(ctx) if m["name"] == "向量数据库")
    meta, _ = model.read_page(ctx, cid)
    model.write_page(ctx, cid, {**meta, "human_edited": True}, "# 向量数据库\n\n我手写的定义。")
    build.build(ctx, Router(), force=True)
    assert "我手写的定义" in model.read_page(ctx, cid)[1]


def test_no_digests_raises(data_home):
    ctx = _lib(data_home)
    with pytest.raises(ValueError, match="摘要"):
        build.build(ctx, Router())


def test_build_writes_run_log(data_home):
    ctx = _lib(data_home)
    _seed(ctx, SPECS)
    build.build(ctx, Router())
    events = [r.get("event") for r in joblog.tail(ctx.root, 100, scopes=["wiki"])]
    assert "run_start" in events and "run_end" in events
    steps = [r.get("stage") for r in joblog.tail(ctx.root, 100, scopes=["wiki"]) if r.get("event") == "step_end"]
    assert {"归一", "定义", "关系", "冲突"} <= set(steps)


def test_rerun_reuses_edge_and_conflict_results(data_home):
    ctx = _lib(data_home)
    _seed(ctx, {
        "甲": ("向量数据库按相似度检索。", ["向量数据库", "检索增强生成"], ["向量数据库只支持精确匹配"]),
        "乙": ("向量数据库按相似度检索。", ["向量数据库", "检索增强生成"], ["向量数据库支持相似度检索"]),
    })
    build.build(ctx, Router(conflicts=["向量数据库"], edge_pairs={frozenset(("向量数据库", "检索增强生成")): "related"}))
    first_edges = model.read_jsonl(ctx, "edges.jsonl")
    router = Router(conflicts=["向量数据库"], edge_pairs={frozenset(("向量数据库", "检索增强生成")): "related"})
    build.build(ctx, router)
    assert router.stage_calls.get("关系", 0) == 0 and router.stage_calls.get("冲突", 0) == 0
    assert model.read_jsonl(ctx, "edges.jsonl") == first_edges and len(model.read_jsonl(ctx, "conflicts.jsonl")) == 1


def test_force_bypasses_edge_and_conflict_caches(data_home):
    ctx = _lib(data_home)
    _seed(ctx, SPECS)
    build.build(ctx, Router())                       # 第一次：没有任何关系
    assert model.read_jsonl(ctx, "edges.jsonl") == []
    build.build(ctx, Router(edge_pairs={frozenset(("检索增强生成", "向量数据库")): "related"}))
    assert model.read_jsonl(ctx, "edges.jsonl") == []   # 缓存命中（依据未变）
    build.build(ctx, Router(edge_pairs={frozenset(("检索增强生成", "向量数据库")): "related"}), force=True)
    assert model.read_jsonl(ctx, "edges.jsonl")          # --force 必须重做
