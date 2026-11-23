"""P4：缺口检测、选题去重与打分、拒绝记录、状态机、预算与挡位门槛、开放命题（默认关闭）。"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from article_garden import settings
from article_garden.planning import states, topics
from article_garden.planning import gaps
from article_garden.wiki import build
from tests.test_tree import PlaceRouter, _add_article, _apply_new, _setup
from tests.test_wiki import Router

ROOT = Path(__file__).resolve().parent.parent


class MidLLM:
    """只回答判重与开放命题；duplicate_all 决定判重结果。"""

    def __init__(self, duplicate=False, open_node=None):
        self.duplicate = duplicate
        self.open_node = open_node
        self.stages = []

    def ask(self, prompt, system=""):
        if "【阶段:选题判重】" in prompt:
            self.stages.append("判重")
            ids = re.findall(r"^(p\d+)：候选", prompt, re.M)
            return yaml.safe_dump({"decisions": [{"id": i, "duplicate": self.duplicate} for i in ids]})
        if "【阶段:开放命题】" in prompt:
            self.stages.append("开放")
            return yaml.safe_dump({"topics": [{"title": "向量索引的工程实践", "node": self.open_node,
                                               "kind": "教程", "level": "进阶",
                                               "must_cover": ["向量索引"], "angle": "从工程角度讲"}]},
                                  allow_unicode=True)
        raise AssertionError("不应调用的阶段：" + prompt[:40])


def _world(data_home):
    ctx = _setup(data_home)
    _apply_new(ctx, PlaceRouter())   # 应用知识树：主题「综合」存在，缺口检测才有主题可查
    return ctx


def test_gap_detection_finds_G2_and_coverage_gaps(data_home):
    ctx = _world(data_home)
    topics.run(ctx, None)
    gaps = json.loads(ctx.path("plan", "gaps.json").read_text(encoding="utf-8"))["gaps"]
    types = {g["type"] for g in gaps}
    assert "G2" in types                         # 向量数据库、大语言模型 被多篇提到却没有专文
    assert {"G1s", "G6"} & types                 # 小库中“综合”主题内容偏浅、且缺综述
    g2 = next(g for g in gaps if g["type"] == "G2" and g["name"] == "向量数据库")
    assert len(g2["articles"]) >= 2 and g2["reason"]


def test_candidates_carry_reason_score_and_breakdown(data_home):
    ctx = _world(data_home)
    summary = topics.run(ctx, None)
    rows = states.load_topics(ctx)
    assert summary["proposed"] == len(rows) >= 1
    for r in rows:
        assert r["status"] == states.PROPOSED and r["reason"] and r["gap_key"]
        assert set(r["breakdown"]) == {"severity", "importance", "evidence", "overlap"}
        assert r["score"] == round(r["breakdown"]["severity"] * r["breakdown"]["importance"]
                                   * r["breakdown"]["evidence"] - r["breakdown"]["overlap"], 2)
        assert r["must_cover"] and r["title"]


def test_concept_duplicates_of_existing_articles_are_dropped(data_home):
    ctx = _world(data_home)
    summary = topics.run(ctx, None)
    # 「综合」主题的概览与综述，与已有文章的概念集完全相同 → 高度重复，剔除
    assert summary["dropped"].get("与已有文章概念高度重复", 0) >= 1
    assert all("综合" not in r["title"] for r in states.load_topics(ctx))


def test_mid_band_without_model_is_flagged_and_with_model_judged(data_home):
    ctx = _world(data_home)
    s = topics.run(ctx, None)
    assert s["pending_mid"] >= 1
    assert any("未判重" in f for r in states.load_topics(ctx) for f in r["flags"])

    ctx.path("plan", "topics.jsonl").unlink()           # 清空队列，同一库上重新判重
    llm = MidLLM(duplicate=True)
    s2 = topics.run(ctx, llm)
    assert "判重" in llm.stages and s2["dropped"].get("模型判定实质重复", 0) >= 1


def test_rerun_does_not_duplicate_queue(data_home):
    ctx = _world(data_home)
    topics.run(ctx, None)
    n = len(states.load_topics(ctx))
    s = topics.run(ctx, None)
    assert s["proposed"] == 0 and len(states.load_topics(ctx)) == n
    assert s["dropped"].get("已在队列中", 0) >= 1


def test_reject_needs_reason_and_blocks_reproposal(data_home):
    ctx = _world(data_home)
    topics.run(ctx, None)
    tid = states.load_topics(ctx)[0]["id"]
    key = states.load_topics(ctx)[0]["gap_key"]
    with pytest.raises(ValueError, match="原因"):
        states.reject(ctx, tid, "  ")
    row = states.reject(ctx, tid, states.REJECT_REASONS[0])
    assert row["status"] == states.REJECTED and row["reason_rejected"] == states.REJECT_REASONS[0]
    assert any(r["gap_key"] == key for r in states.load_rejected(ctx))
    s = topics.run(ctx, None)
    assert s["dropped"].get("已拒绝过", 0) >= 1
    assert not any(r.get("gap_key") == key and r["status"] == states.PROPOSED for r in states.load_topics(ctx))


def test_state_transitions_are_enforced(data_home):
    ctx = _world(data_home)
    topics.run(ctx, None)
    tid = states.load_topics(ctx)[0]["id"]
    assert states.approve(ctx, tid)["status"] == states.APPROVED
    with pytest.raises(ValueError, match="不能从"):
        states.approve(ctx, tid)
    with pytest.raises(ValueError, match="只能修改待审"):
        states.set_title(ctx, tid, "新标题")
    assert states.reject(ctx, tid, "其它")["status"] == states.REJECTED   # 已放行 → 可再拒绝（允许）
    with pytest.raises(ValueError, match="不能从"):
        states.approve(ctx, tid)                         # 已拒绝是终态
    assert states.get(ctx, tid)["history"][-1]["to"] == states.REJECTED


def test_out_of_scope_topics_are_removed(data_home):
    ctx = _world(data_home)
    c, _ = ctx.load_charter()
    c["scope"]["out"] = ["向量数据库"]
    ctx.save_charter(c)
    s = topics.run(ctx, None)
    assert s["dropped"].get("超出范围", 0) >= 1
    assert not any("向量数据库" in r["title"] or "向量数据库" in r["must_cover"] for r in states.load_topics(ctx))


def test_per_cycle_budget_caps_new_topics(data_home):
    ctx = _world(data_home)
    settings.save_override("domain", "budget", "max_topics_per_cycle", 1, ctx)
    s = topics.run(ctx, None)
    assert s["proposed"] == 1 and len(states.load_topics(ctx)) == 1


def test_auto_approve_only_in_gears_that_allow_it(data_home):
    ctx = _world(data_home)
    settings.save_override("domain", "automation", "auto_approve", True, ctx)
    topics.run(ctx, None)
    assert all(r["status"] == states.PROPOSED for r in states.load_topics(ctx))   # G0：不放行
    settings.save_override("domain", "automation", "gear", "G2", ctx)
    from article_garden.planning import topics as tp
    # 新一轮仍会有待办被生成的前提：清空队列后重跑
    ctx.path("plan", "topics.jsonl").unlink()
    tp.run(ctx, None)
    rows = states.load_topics(ctx)
    assert rows and all(r["status"] == states.APPROVED and r["history"][0]["note"] == "自动放行" for r in rows)


def test_open_topics_off_by_default_never_calls_model(data_home):
    ctx = _world(data_home)
    class Forbidden(MidLLM):
        def ask(self, prompt, system=""):
            assert "【阶段:开放命题】" not in prompt
            return super().ask(prompt, system)

    topics.run(ctx, Forbidden(duplicate=False, open_node="n-0004"))
    assert not any(r["gap_type"] == "OPEN" for r in states.load_topics(ctx))


def test_open_topics_when_enabled_are_validated_and_deduped(data_home):
    ctx = _world(data_home)
    from article_garden.wiki import tree
    topic_id = tree.nodes_at(tree.load(ctx), tree.TOPIC)[0][0]
    settings.save_override("domain", "automation", "open_topics", True, ctx)
    llm = MidLLM(duplicate=False, open_node=topic_id)
    topics.run(ctx, llm)
    assert "开放" in llm.stages
    open_rows = [r for r in states.load_topics(ctx) if r["gap_type"] == "OPEN"]
    assert open_rows and open_rows[0]["title"] == "向量索引的工程实践"


def test_approve_and_reject_from_cli(data_home):
    ctx = _world(data_home)
    topics.run(ctx, None)
    tid = states.load_topics(ctx)[0]["id"]
    env = dict(os.environ, ARTICLE_GARDEN_HOME=str(data_home), PYTHONIOENCODING="utf-8")
    run = lambda *a: subprocess.run([sys.executable, str(ROOT / "entrypoints" / "plan_topics.py"), *a],
                                    cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
    r = run("ai", "--approve", tid)
    assert json.loads(r.stdout)["status"] == "approved"
    tid2 = states.load_topics(ctx)[-1]["id"]
    assert run("ai", "--reject", tid2, "--reason", "暂不需要").returncode == 0
    assert states.get(ctx, tid2)["status"] == states.REJECTED


def test_topics_page_renders_kanban(data_home):
    from tests.test_ui_smoke import run_app, texts
    ctx = _world(data_home)
    topics.run(ctx, None)
    at = run_app("topics", domain="ai")
    assert not at.exception, [e.value for e in at.exception]
    assert "待审" in texts(at) and any(b.label == "放行" for b in at.button)


def test_G3_prerequisite_without_introductory_article(data_home):
    ctx = _world(data_home)
    build.build(ctx, Router(edge_pairs={frozenset(("向量数据库", "大语言模型")): "prerequisite"}), force=True)
    g3 = [g for g in gaps.detect(ctx) if g["type"] == "G3"]
    # 排序后 向量数据库 在前，因此边为「向量数据库 依赖 大语言模型」，被依赖的 dst=大语言模型 应被报告
    assert any(g["name"] == "大语言模型" for g in g3)          # 被依赖的概念没有入门讲解


def test_G4_contrast_without_comparison_article(data_home):
    ctx = _world(data_home)
    _add_article(ctx, "向量检索补充", "向量数据库与检索增强生成经常一起出现。",
                 ["向量数据库", "检索增强生成"], ["向量数据库与检索增强生成经常一起出现"])
    build.build(ctx, Router(edge_pairs={frozenset(("向量数据库", "检索增强生成")): "contrasts"}), force=True)
    g4 = [g for g in gaps.detect(ctx) if g["type"] == "G4"]
    assert g4 and "向量数据库" in g4[0]["name"] and "检索增强生成" in g4[0]["name"]
