"""R0 章节事实卡的测试。全部使用桩执行器，不调用真实 LLM。"""

import json
import re
from pathlib import Path

import pytest

from novel_writer import config, state as st, tool_api
from novel_writer.revision_flow import config as CFG
from novel_writer.revision_flow import review_cards as RC
from novel_writer.revision_flow import versions as V
from novel_writer.revision_flow.common import FlowError
from novel_writer.workspace import Workspace


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    yield tmp_path


TEXTS = {
    1: "李凌云握着玉佩走进山门。玉佩在掌心发烫，他想起师父的话。",
    2: "夜里，李凌云把玉佩放在桌上，反复端详。窗外有风。",
    3: "清晨的雾很大。李凌云没有提起玉佩，只是低头赶路。",
}


def _novel(tmp_path, total=3, passed=True, baseline=True):
    r = tool_api.create_novel("事实卡测试", novels_dir=str(tmp_path / "novels"),
                              index_file=str(tmp_path / "shelf.json"), gear="G3", total_chapters_planned=total)
    ws = Workspace(r["data"]["workspace"])
    for n in range(1, total + 1):
        p = ws.chapter_path(n, f"标题{n}", total)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# 第{n}章 标题{n}\n\n" + TEXTS.get(n, "平常的一天。") + "\n", encoding="utf-8")
    if passed:
        st.update_step(ws, "final_review", status=st.ST_PASSED)
    if baseline:
        V.init_baseline(ws)
    return ws


_PATH = re.compile(r"【章节路径】(.+?)（")
_OUT = re.compile(r"写入文件：(.+)")
_NO = re.compile(r"阅读第(\d+)章")


class CardInvoker:
    """读取提示里的章节，写出一张合法事实卡。bad_quote 控制是否额外写一条伪造引文的事实。"""

    def __init__(self, *, bad_quote=False, fail_first=0, raise_for=()):
        self.calls = []
        self.bad_quote = bad_quote
        self.fail_first = fail_first
        self.raise_for = set(raise_for)

    def __call__(self, *, skill, prompt, export_dir, step_id):
        n = int(_NO.search(prompt).group(1))
        self.calls.append((skill, n, step_id))
        if n in self.raise_for:
            raise RuntimeError("模拟执行器异常")
        out = Path(_OUT.search(prompt).group(1).strip())
        if self.fail_first and sum(1 for c in self.calls if c[1] == n) <= self.fail_first:
            out.write_text("不是 JSON", encoding="utf-8")
            return
        facts = [{"subject": "李凌云", "attribute": "持有物", "value": "玉佩", "quote": "李凌云握着玉佩走进山门"}] \
            if n == 1 else []
        if self.bad_quote:
            facts.append({"subject": "李凌云", "attribute": "年龄", "value": "三十岁", "quote": "他今年三十岁了"})
        card = {"chapter": n, "time_marker": "", "location_start": "山门", "location_end": "山门",
                "characters": [{"name": "李凌云（主角）", "role_in_chapter": "主角", "state_end": "在场"},
                               {"name": "路人乙", "role_in_chapter": "", "state_end": ""}],
                "events": ["事件"], "facts_stated": facts, "foreshadow": [],
                "opening_state": "开头", "ending_state": "结尾", "open_questions": []}
        out.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")


# ─────────────────────────── 门禁与设置 ───────────────────────────

def test_requires_baseline(tmp_path):
    ws = _novel(tmp_path, baseline=False)
    with pytest.raises(FlowError, match="完稿"):
        RC.estimate(ws)
    with pytest.raises(FlowError, match="完稿"):
        RC.generate_cards(ws, None, CardInvoker())


def test_review_settings_defaults_and_validation(tmp_path):
    ws = _novel(tmp_path)
    cur = CFG.load(ws)
    assert cur["review_max_llm_calls"] == 0              # 不设上限
    assert cur["review_dimensions"] == ["character_profile", "logic", "contradiction", "continuity", "timeline",
                                        "character_logic", "foreshadow"]
    assert "rule_legacy" not in cur["review_dimensions"]            # 规则式死亡/地点判断默认关闭
    assert "pacing" not in cur["review_dimensions"]        # 主观维度默认关闭
    CFG.update(ws, {"review_dimensions": ["style_drift", "contradiction", "contradiction"]})
    assert CFG.load(ws)["review_dimensions"] == ["contradiction", "style_drift"]
    for bad in ({"review_dimensions": []}, {"review_dimensions": ["foo"]}, {"review_window": 1},
                {"review_max_llm_calls": -1}):
        with pytest.raises(FlowError):
            CFG.update(ws, bad)
    cur["review_dimensions"].append("x")                    # 返回值是副本，不污染默认值
    assert "x" not in CFG.load(ws)["review_dimensions"]


# ─────────────────────────── 引文核对 ───────────────────────────

def test_quote_matching_ignores_whitespace_and_punctuation():
    norm = RC.normalize_for_match("李凌云握着玉佩，走进山门。")
    assert RC.quote_in_text("李凌云握着玉佩 走进山门", norm)
    assert not RC.quote_in_text("李凌云丢了玉佩", norm)
    assert not RC.quote_in_text("", norm) and not RC.quote_in_text("，。", norm)


def test_parse_card_drops_unverifiable_quotes_and_rejects_bad_structure():
    text = TEXTS[1]
    raw = {"chapter": 1, "characters": [{"name": "李凌云（主角）"}],
           "facts_stated": [{"subject": "李凌云", "attribute": "持有物", "value": "玉佩", "quote": "握着玉佩走进山门"},
                            {"subject": "李凌云", "attribute": "年龄", "value": "三十", "quote": "他今年三十"},
                            {"subject": "师父", "attribute": "身份", "value": "道长", "quote": ""}],
           "foreshadow": [{"ref": "F1", "action": "plant", "quote": "玉佩在掌心发烫"}]}
    card = RC.parse_card(json.dumps(raw, ensure_ascii=False), 1, text, {"李凌云": "李凌云"})
    assert [f["attribute"] for f in card["facts_stated"]] == ["持有物"]
    assert {d["reason"] for d in card["dropped"]} == {"引文不在本章原文中", "缺少引文"}
    assert card["foreshadow"][0]["action"] == "plant"
    for bad in ("不是json", "[]", json.dumps({"chapter": 2}),
                json.dumps({"chapter": 1, "facts_stated": [{"subject": "a"}]}),
                json.dumps({"chapter": 1, "foreshadow": [{"ref": "F1", "action": "boom", "quote": "x"}]}),
                json.dumps({"chapter": 1, "characters": [{"role_in_chapter": "x"}]})):
        with pytest.raises(FlowError):
            RC.parse_card(bad, 1, text)


def test_unregistered_characters_only_flagged_when_registry_exists():
    raw = json.dumps({"chapter": 1, "characters": [{"name": "路人乙"}, {"name": "李凌云（青年）"}]}, ensure_ascii=False)
    with_reg = RC.parse_card(raw, 1, "x", {"李凌云": "李凌云"})
    assert with_reg["unregistered_characters"] == ["路人乙"]
    assert [c["name"] for c in with_reg["characters"]] == ["路人乙", "李凌云"]
    assert RC.parse_card(raw, 1, "x", {})["unregistered_characters"] == []     # 没有登记表不误报


# ─────────────────────────── 生成与缓存 ───────────────────────────

def test_generate_estimate_and_cache_hit(tmp_path):
    ws = _novel(tmp_path)
    est = RC.estimate(ws)
    assert (est["chapters"], est["cards_to_generate"], est["cards_calls"], est["max_llm_calls"]) == (3, 3, 3, None)
    inv = CardInvoker()
    res = RC.generate_cards(ws, None, inv)
    assert (res["generated"], res["cached"], res["calls"], res["failed"]) == (3, 0, 3, [])
    assert all(c[0] == "memory-manager" for c in inv.calls)
    assert RC.estimate(ws)["cards_to_generate"] == 0
    inv2 = CardInvoker()
    res2 = RC.generate_cards(ws, None, inv2)
    assert (res2["generated"], res2["cached"], res2["calls"]) == (0, 3, 0) and inv2.calls == []
    cards, missing = RC.load_cards(ws)
    assert sorted(cards) == [1, 2, 3] and missing == []
    assert cards[1]["facts_stated"][0]["quote"] == "李凌云握着玉佩走进山门"


def test_force_and_chapter_subset(tmp_path):
    ws = _novel(tmp_path)
    RC.generate_cards(ws, None, CardInvoker())
    inv = CardInvoker()
    res = RC.generate_cards(ws, None, inv, chapters=[2], force=True)
    assert res["total"] == 1 and res["generated"] == 1 and [c[1] for c in inv.calls] == [2]
    with pytest.raises(FlowError, match="找不到章节"):
        RC.generate_cards(ws, None, CardInvoker(), chapters=[9])


def test_cache_key_follows_chapter_content(tmp_path):
    """版本里章节内容不同（不同 sha）就不会命中旧缓存。"""
    ws = _novel(tmp_path)
    RC.generate_cards(ws, "v001", CardInvoker())
    sources = RC.list_chapter_sources(ws, "v001")
    assert RC.load_card(ws, 1, sources[0]["sha"]) is not None
    assert RC.load_card(ws, 1, "0" * 64) is None
    # 旧 schema 的卡片视为未命中
    path = RC.card_path(ws, 1, sources[0]["sha"])
    data = json.loads(path.read_text(encoding="utf-8"))
    data["schema"] = 0
    path.write_text(json.dumps(data), encoding="utf-8")
    assert RC.load_card(ws, 1, sources[0]["sha"]) is None


def test_forged_quote_dropped_and_recorded(tmp_path):
    ws = _novel(tmp_path)
    RC.generate_cards(ws, None, CardInvoker(bad_quote=True))
    cards, _ = RC.load_cards(ws)
    assert all(f["attribute"] != "年龄" for c in cards.values() for f in c["facts_stated"])
    assert any(d["text"].startswith("李凌云.年龄") for d in cards[1]["dropped"])
    assert cards[1]["unregistered_characters"] == []        # 测试书没有角色档案，不误报


def test_bad_json_retries_then_succeeds_and_failure_is_isolated(tmp_path):
    ws = _novel(tmp_path)
    res = RC.generate_cards(ws, None, CardInvoker(fail_first=1))
    assert res["generated"] == 3 and res["calls"] == 6        # 每章第一次坏 JSON，第二次成功
    ws2 = _novel(tmp_path / "b")
    res2 = RC.generate_cards(ws2, None, CardInvoker(raise_for={2}))
    assert res2["generated"] == 2 and [f["chapter"] for f in res2["failed"]] == [2]
    assert "执行器异常" in res2["failed"][0]["errors"][0]
    cards, missing = RC.load_cards(ws2)
    assert sorted(cards) == [1, 3] and missing == [2]          # 失败章明确缺卡，不被静默忽略


def test_call_limit_pauses_and_resumes_from_cache(tmp_path):
    ws = _novel(tmp_path)
    CFG.update(ws, {"review_max_llm_calls": 2})
    res = RC.generate_cards(ws, None, CardInvoker())
    assert res["paused"] and res["generated"] == 2 and res["calls"] == 2
    res2 = RC.generate_cards(ws, None, CardInvoker())
    assert not res2["paused"] and res2["cached"] == 2 and res2["generated"] == 1


def test_default_has_no_call_limit(tmp_path):
    ws = _novel(tmp_path, total=8)
    res = RC.generate_cards(ws, None, CardInvoker())
    assert not res["paused"] and res["generated"] == 8


def test_review_never_touches_versions_or_pipeline_state(tmp_path):
    ws = _novel(tmp_path)
    before_manifest = V.manifest(ws, "v001")
    state_file = ws.root / "项目状态" / "pipeline_state.json"
    state_before = state_file.read_bytes() if state_file.exists() else None
    RC.generate_cards(ws, None, CardInvoker())
    assert V.verify(ws, "v001") == [] and V.manifest(ws, "v001") == before_manifest
    assert (state_file.read_bytes() if state_file.exists() else None) == state_before
