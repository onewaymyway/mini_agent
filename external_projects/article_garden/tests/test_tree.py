"""P3：知识树——骨架、增量挂载提案、确认应用、id 稳定、过期拒绝、人工操作与覆盖度。"""

import re

import pytest
import yaml

from article_garden import store
from article_garden.digest import runner as drunner
from article_garden.library import catalog, importer
from article_garden.wiki import build, coverage, model, placement, tree
from tests.test_wiki import SPECS, Router, _lib, _seed


def _section(prompt, start, end=None):
    i = prompt.index(start) + len(start)
    j = prompt.index(end, i) if end else len(prompt)
    return prompt[i:j]


class PlaceRouter:
    """只回答挂载阶段：概念挂到同名主题（若存在），否则新建主题（默认名「综合」）。"""

    def __init__(self, topic_for=None, bad_once=False):
        self.topic_for = topic_for or {}
        self.bad_once = bad_once
        self.calls = 0
        self.batches = []

    def ask(self, prompt, system=""):
        assert "【阶段:挂载】" in prompt
        self.calls += 1
        dirs = re.findall(r"^- (n-\d+) ｜ (.+)$", _section(prompt, "方向（只能选这些）：\n", "已有主题"), re.M)
        topics = re.findall(r"^- (n-\d+) ｜ (.+?) ｜ 方向 (n-\d+)$",
                            _section(prompt, "已有主题（可挂入）：\n", "待挂载概念："), re.M)
        concepts = re.findall(r"^- (c-\d+) ｜ (.+?) ｜", _section(prompt, "待挂载概念：\n"), re.M)
        self.batches.append([c[0] for c in concepts])
        if self.bad_once and concepts:
            self.bad_once = False
            concepts = concepts[1:]            # 故意漏掉一个概念 → 应被修复
        placements = []
        for cid, cname in concepts:
            want = self.topic_for.get(cname, "综合")
            hit = next((t for t in topics if t[1] == want), None)
            if hit:
                placements.append({"concept": cid, "topic": hit[0]})
            else:
                placements.append({"concept": cid, "new_topic": want, "direction": dirs[0][0]})
        return yaml.safe_dump({"placements": placements}, allow_unicode=True, sort_keys=False)


def _charter(ctx, scope_in):
    c, _ = ctx.load_charter()
    c["scope"] = {"in": scope_in, "out": []}
    ctx.save_charter(c)


def _add_article(ctx, title, sentence, concepts, claim_texts):
    body = f"# {title}\n\n{sentence}\n\n" + "\n\n".join(f"这一部分只是为了让文章达到长度要求，第{i}段。" for i in range(10))
    importer.apply(ctx, importer.collect([(f"{title}.md", body.encode("utf-8"))]))
    row = catalog.list_articles(ctx)[-1]
    rec = {"id": row["id"], "content_hash": row["content_hash"], "status": "ok", "digest_version": 1,
           "title": title, "thesis": "论点", "kind": "概念讲解", "level": "入门", "self_score": 4,
           "concepts": concepts, "claims": [{"text": t, "quote": sentence} for t in claim_texts],
           "dangling": [], "chunks": 1, "issues": []}
    store.write_yaml_atomic(drunner.digest_path(ctx, row["id"]), rec)


def _setup(data_home, scope=("检索增强生成与大模型", "数据存储")):
    ctx = _lib(data_home)
    _seed(ctx, SPECS)
    build.build(ctx, Router())
    _charter(ctx, list(scope))
    return ctx


def _apply_new(ctx, router):
    p = placement.make_proposal(ctx, router)
    assert p is not None
    placement.apply_proposal(ctx)
    return p


def test_first_proposal_builds_skeleton_and_places_every_concept(data_home):
    ctx = _setup(data_home)
    router = PlaceRouter(topic_for={"向量数据库": "向量检索"})
    p = _apply_new(ctx, router)
    assert p["summary"]["skeleton"] == 2
    data = tree.load(ctx)
    dirs = {n["name"] for _, n in tree.nodes_at(data, tree.DIRECTION)}
    assert dirs == {"检索增强生成与大模型", "数据存储"}
    placed = tree.placed_concepts(data)
    assert set(placed) == {m["id"] for m in model.list_concepts(ctx)}     # 每个概念都已挂载
    assert router.batches and len(router.batches[0]) == len(model.list_concepts(ctx))


def test_rerun_without_changes_makes_no_llm_call_and_no_proposal(data_home):
    ctx = _setup(data_home)
    _apply_new(ctx, PlaceRouter())
    router = PlaceRouter()
    assert placement.make_proposal(ctx, router) is None
    assert router.calls == 0 and placement.load_proposal(ctx) is None


def test_new_article_triggers_incremental_attach_only(data_home):
    ctx = _setup(data_home)
    _apply_new(ctx, PlaceRouter(topic_for={"向量数据库": "向量检索"}))
    before = {nid: (n["name"], sorted(n.get("concepts") or [])) for nid, n in tree.load(ctx)["nodes"].items()}
    old_ids = set(before)

    _add_article(ctx, "知识图谱入门", "知识图谱用实体与关系表示知识。", ["知识图谱"], ["知识图谱用实体与关系表示知识"])
    build.build(ctx, Router())
    router = PlaceRouter()
    p = _apply_new(ctx, router)

    assert router.batches == [[m["id"] for m in model.list_concepts(ctx) if m["name"] == "知识图谱"]]
    assert p["summary"]["attached"] == 1 and p["summary"]["new_topics"] == 0 and p["summary"]["skeleton"] == 0  # 挂入已有主题，只动了新增的概念
    after = tree.load(ctx)["nodes"]
    for nid in old_ids:                                                       # 旧节点 id、名称不变，原有概念仍在
        assert nid in after and after[nid]["name"] == before[nid][0]
        assert set(before[nid][1]) <= set(after[nid].get("concepts") or [])


def test_invalid_placement_is_repaired(data_home):
    ctx = _setup(data_home)
    router = PlaceRouter(bad_once=True)
    p = _apply_new(ctx, router)
    assert router.calls >= 2 and p["summary"]["attached"] + p["summary"]["new_topics"] >= 1
    assert set(tree.placed_concepts(tree.load(ctx))) == {m["id"] for m in model.list_concepts(ctx)}


def test_validator_rejects_pinned_topic_and_unknown_direction(data_home):
    ctx = _setup(data_home)
    _apply_new(ctx, PlaceRouter())
    data = tree.load(ctx)
    topic = tree.nodes_at(data, tree.TOPIC)[0][0]
    tree.set_pinned(ctx, topic, True)
    work = tree.load(ctx)
    batch = [{"id": "c-0001"}]
    v = placement._validator(batch, work)
    assert any("已锁定" in i for i in v({"placements": [{"concept": "c-0001", "topic": topic}]}))
    assert any("不存在" in i for i in v({"placements": [{"concept": "c-0001", "new_topic": "X", "direction": "n-9999"}]}))
    assert v({"placements": [{"concept": "c-0001", "new_topic": "X", "direction": tree.nodes_at(work, tree.DIRECTION)[0][0]}]}) == []


def test_stale_proposal_is_refused(data_home):
    ctx = _setup(data_home)
    _apply_new(ctx, PlaceRouter())
    _add_article(ctx, "知识图谱入门", "知识图谱用实体与关系表示知识。", ["知识图谱"], ["知识图谱用实体与关系表示知识"])
    build.build(ctx, Router())
    assert placement.make_proposal(ctx, PlaceRouter()) is not None
    tree.rename(ctx, tree.nodes_at(tree.load(ctx), tree.DIRECTION)[0][0], "改了名的方向")   # 树已变化
    with pytest.raises(ValueError, match="过期"):
        placement.apply_proposal(ctx)
    assert placement.load_proposal(ctx) is not None          # 过期的提案保留，便于查看后放弃


def test_apply_backs_up_previous_tree(data_home):
    ctx = _setup(data_home)
    _apply_new(ctx, PlaceRouter())
    assert not ctx.path("wiki", "tree.prev.yaml").exists()      # 首次写入没有旧树，无需备份
    tree.rename(ctx, tree.nodes_at(tree.load(ctx), tree.DIRECTION)[0][0], "改名后")
    assert ctx.path("wiki", "tree.prev.yaml").is_file()         # 覆盖已有树时先备份


def test_detach_when_concept_removed(data_home):
    ctx = _setup(data_home)
    _apply_new(ctx, PlaceRouter())
    gone = next(m["id"] for m in model.list_concepts(ctx) if m["name"] == "向量数据库")
    model.delete_page(ctx, gone)
    _apply_new(ctx, PlaceRouter())
    assert gone not in tree.placed_concepts(tree.load(ctx))


def test_human_operations_rename_move_merge_pin(data_home):
    ctx = _setup(data_home)
    _apply_new(ctx, PlaceRouter())
    data = tree.load(ctx)
    dirs = tree.nodes_at(data, tree.DIRECTION)
    topic = tree.nodes_at(data, tree.TOPIC)[0][0]
    tree.rename(ctx, topic, "新主题名")
    assert tree.load(ctx)["nodes"][topic]["name"] == "新主题名" and tree.load(ctx)["nodes"][topic]["human_edited"]
    tree.move(ctx, topic, dirs[1][0])
    assert tree.load(ctx)["nodes"][topic]["parent"] == dirs[1][0]
    with pytest.raises(ValueError):
        tree.move(ctx, dirs[0][0], dirs[1][0])               # 方向不能移动
    with pytest.raises(ValueError):
        tree.move(ctx, topic, topic)                         # 主题只能进方向
    tree.set_pinned(ctx, topic, True)
    assert tree.load(ctx)["nodes"][topic]["pinned"] is True
    other = tree.create_topic(ctx, "另一个主题", dirs[1][0])
    tree.merge(ctx, topic, other)
    d2 = tree.load(ctx)
    assert topic not in d2["nodes"] and d2["nodes"][other]["concepts"]   # 概念并入，源节点删除


def test_coverage_colors_and_written_without_llm(data_home):
    ctx = _setup(data_home)
    _apply_new(ctx, PlaceRouter())
    empty = tree.create_topic(ctx, "暂无文章的主题", tree.nodes_at(tree.load(ctx), tree.DIRECTION)[0][0])
    cov = coverage.compute(ctx)
    assert cov["nodes"][empty]["color"] == "red" and cov["nodes"][empty]["article_count"] == 0
    with_articles = next(nid for nid, n in tree.load(ctx)["nodes"].items() if n.get("concepts"))
    assert cov["nodes"][with_articles]["article_count"] >= 1
    assert cov["nodes"][with_articles]["color"] in {"yellow", "green"}
    assert coverage.read(ctx)["nodes"] == cov["nodes"]           # 已写入 coverage.json


def test_tree_page_renders(data_home):
    from tests.test_ui_smoke import run_app, texts
    ctx = _setup(data_home)
    _apply_new(ctx, PlaceRouter())
    at = run_app("tree", domain="ai")
    assert not at.exception, [e.value for e in at.exception]
    assert "知识树" in texts(at) and len(at.expander) >= 2
