"""R4 大纲重生成的测试：全部使用桩执行器，不调用真实 LLM。

验证：候选由确定性部分（章号、字数、出场人物）与 LLM 判断部分（功能、类型、视角、张力、幕）组成；
候选不进入版本；采纳派生「仅大纲」新版本，正文哈希不变、蓝图经严格读回、可 discard；
采纳前 head 变化则拒绝；按章勾选与就地修改；分批调用；坏输出重试。
"""

import hashlib
import json
import re
from pathlib import Path

import pytest

from novel_writer import blueprint_store as BS
from novel_writer import config
from novel_writer.revision_flow import outline_regen as OR
from novel_writer.revision_flow import review_cards as RC
from novel_writer.revision_flow import versions as V
from novel_writer.revision_flow.common import FlowError, version_dir

from test_revision_review_cards import CardInvoker, _novel, isolated  # noqa: F401  (复用夹具)

_BATCH = re.compile(r"写入文件：(.+)")
_NUMS = re.compile(r"第(\d+)章《")


class OutlineStub:
    """大纲判断的桩；事实卡提取交给 CardInvoker。bad_batches：前 N 次批量输出不合法。"""

    def __init__(self, *, bad_first=0, bad_struct=False, tension=5):
        self.cards = CardInvoker()
        self.calls = []
        self.bad_first = bad_first
        self.bad_struct = bad_struct
        self.tension = tension

    def __call__(self, *, skill, prompt, export_dir, step_id):
        if "提取一张结构化的" in prompt:
            return self.cards(skill=skill, prompt=prompt, export_dir=export_dir, step_id=step_id)
        out = Path(_BATCH.search(prompt).group(1).strip())
        if "【任务】反推大纲：逐章" in prompt:
            nums = [int(x) for x in _NUMS.findall(prompt)]
            self.calls.append(("batch", nums))
            if self.bad_first and sum(1 for c in self.calls if c[0] == "batch") <= self.bad_first:
                out.write_text(json.dumps({"chapters": [{"chapter": nums[0], "tension_level": 99}]}), encoding="utf-8")
                return
            data = {"chapters": [{"chapter": n, "function": f"功能{n}", "chapter_type": "冲突",
                                  "pov": "李凌云", "tension_level": self.tension} for n in nums]}
        else:
            total = max(int(x) for x in _NUMS.findall(prompt)) if _NUMS.findall(prompt) else 3
            self.calls.append(("struct", total))
            if self.bad_struct:
                data = {"acts": [{"name": "半截", "chapter_range": [1, total - 1]}], "main_plot": "", "sub_plots": []}
            else:
                data = {"acts": [{"name": "第一幕", "chapter_range": [1, 1]},
                                 {"name": "第二幕", "chapter_range": [2, total]}],
                        "main_plot": "主线概要", "sub_plots": ["支线甲"]}
        out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def count(self, kind):
        return sum(1 for c in self.calls if c[0] == kind)


def _blueprint(total, *, extra_unwritten=False):
    chapters = [{"id": n, "title": f"第{n}章 标题{n}", "function": "旧功能", "chapter_type": "铺垫",
                 "pov": "李凌云", "tension_level": 3, "word_budget": 9999, "characters_present": ["旧人物"]}
                for n in range(1, total + 1)]
    if extra_unwritten:
        chapters.append({"id": total + 1, "title": "第%d章 未写" % (total + 1), "function": "计划中",
                         "chapter_type": "收束", "pov": "李凌云", "tension_level": 8, "word_budget": 3000,
                         "characters_present": []})
    return {"meta": {"target_length": 16000}, "structure": {"total_chapters": total, "acts": [
        {"name": "旧幕", "chapter_range": [1, total]}]}, "chapters": chapters}


def _ready(tmp_path, total=3, extra_unwritten=False):
    ws = _novel(tmp_path, total=total, baseline=False)
    BS.write_store(ws.path("blueprint_dir"), ws.path("blueprint"), _blueprint(total, extra_unwritten=extra_unwritten))
    V.init_baseline(ws)
    RC.generate_cards(ws, None, CardInvoker())
    return ws


def _hash_tree(root: Path, sub: str) -> str:
    """只比较 sub 目录内的相对路径与内容（不含版本号前缀，便于跨版本比较）。"""
    h = hashlib.sha256()
    base = root / sub
    for p in sorted(base.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(base)).encode("utf-8"))
            h.update(p.read_bytes())
    return h.hexdigest()


def _bp_of(ws, vid):
    return BS.read_store(version_dir(ws, vid) / OR.BLUEPRINT_REL)


# ─────────────────────────── 候选 ───────────────────────────

def test_candidate_mixes_deterministic_and_judged_parts(tmp_path):
    ws = _ready(tmp_path)
    res = OR.generate_candidate(ws, None, OutlineStub())
    cand = OR.load_candidate(ws, res["id"])
    new = {c["id"]: c for c in cand["candidate"]["chapters"]}
    assert new[1]["function"] == "功能1" and new[1]["chapter_type"] == "冲突" and new[1]["tension_level"] == 5
    assert new[1]["word_budget"] >= 100 and new[1]["word_budget"] % 100 == 0          # 实际字数（取整百）
    assert new[1]["characters_present"] == ["李凌云", "路人乙"]                         # 事实卡人物，不是旧的
    assert new[1]["title"] == "第1章 标题1"                                            # 保留蓝图章名
    assert cand["candidate"]["structure"]["acts"][-1]["chapter_range"] == [2, 3]
    assert cand["candidate"]["structure"]["actual_summary"]["main_plot"] == "主线概要"
    assert cand["summary"]["changed"] == 3 and cand["summary"]["structure_changed"] is True
    assert cand["status"] == "candidate" and cand["base_version"] == "v001"


def test_candidate_does_not_touch_versions(tmp_path):
    ws = _ready(tmp_path)
    before = _hash_tree(ws.root, "_versions")
    OR.generate_candidate(ws, None, OutlineStub())
    assert _hash_tree(ws.root, "_versions") == before and V.load_index(ws)["head"] == "v001"
    assert V.verify(ws, "v001") == []


def test_candidate_requires_cards_for_every_chapter(tmp_path):
    ws = _novel(tmp_path, total=3, baseline=False)
    BS.write_store(ws.path("blueprint_dir"), ws.path("blueprint"), _blueprint(3))
    V.init_baseline(ws)
    with pytest.raises(FlowError, match="事实卡"):
        OR.generate_candidate(ws, None, OutlineStub())


def test_bad_batch_output_is_retried_then_accepted(tmp_path):
    ws = _ready(tmp_path)
    stub = OutlineStub(bad_first=1)
    OR.generate_candidate(ws, None, stub)
    assert stub.count("batch") == 2                                                   # 第一次不合法，重试一次成功


def test_structure_must_cover_book(tmp_path):
    ws = _ready(tmp_path)
    with pytest.raises(FlowError, match="幕"):
        OR.generate_candidate(ws, None, OutlineStub(bad_struct=True))


def test_batches_split_by_twenty_chapters(tmp_path):
    ws = _ready(tmp_path, total=25)
    assert OR.estimate(ws)["batches"] == 2 and OR.estimate(ws)["calls_typical"] == 3
    stub = OutlineStub()
    OR.generate_candidate(ws, None, stub)
    assert [len(c[1]) for c in stub.calls if c[0] == "batch"] == [20, 5]
    assert stub.count("struct") == 1


def test_unwritten_blueprint_chapters_are_reported_not_dropped(tmp_path):
    ws = _ready(tmp_path, extra_unwritten=True)
    res = OR.generate_candidate(ws, None, OutlineStub())
    assert res["unwritten"] == [4]
    OR.adopt(ws, res["id"])
    assert 4 in {c["id"] for c in _bp_of(ws, "v002")["chapters"]}                     # 采纳不删除计划中的章节


# ─────────────────────────── 采纳 ───────────────────────────

def test_adopt_derives_outline_only_version_and_keeps_body(tmp_path):
    ws = _ready(tmp_path)
    body_before = _hash_tree(ws.root, "_versions/v001/正文")
    res = OR.generate_candidate(ws, None, OutlineStub())
    out = OR.adopt(ws, res["id"])
    assert out["version"] == "v002" and V.load_index(ws)["head"] == "v002"
    assert _hash_tree(ws.root, "_versions/v002/正文") == body_before                   # 正文一字不动
    assert V.verify(ws, "v002") == [] and V.verify(ws, "v001") == []
    bp = _bp_of(ws, "v002")                                                           # 严格读回通过
    assert bp["chapters"][0]["function"] == "功能1" and bp["chapters"][0]["word_budget"] != 9999
    assert OR.load_candidate(ws, res["id"])["status"] == "adopted"
    log = (version_dir(ws, "v002") / OR.CHANGELOG).read_text(encoding="utf-8")
    assert "大纲重生成" in log and "正文未改动" in log
    assert _bp_of(ws, "v001")["chapters"][0]["function"] == "旧功能"                  # 原版本蓝图不变


def test_partial_adopt_with_inline_edit(tmp_path):
    ws = _ready(tmp_path)
    res = OR.generate_candidate(ws, None, OutlineStub())
    OR.adopt(ws, res["id"], chapters=[1, 3], edits={3: {"function": "作者改写的功能"}})
    bp = {c["id"]: c for c in _bp_of(ws, "v002")["chapters"]}
    assert bp[1]["function"] == "功能1"
    assert bp[2]["function"] == "旧功能"                                              # 未勾选的章保持原样
    assert bp[3]["function"] == "作者改写的功能"


def test_edits_restricted_to_selected_chapters_and_known_fields(tmp_path):
    ws = _ready(tmp_path)
    res = OR.generate_candidate(ws, None, OutlineStub())
    with pytest.raises(FlowError, match="未勾选"):
        OR.adopt(ws, res["id"], chapters=[1], edits={2: {"function": "x"}})
    with pytest.raises(FlowError, match="不允许修改"):
        OR.adopt(ws, res["id"], edits={1: {"title": "x"}})
    assert V.load_index(ws)["head"] == "v001"                                         # 失败不改 head


def test_adopt_refused_when_head_moved_on(tmp_path):
    ws = _ready(tmp_path)
    c1 = OR.generate_candidate(ws, None, OutlineStub())
    c2 = OR.generate_candidate(ws, None, OutlineStub())
    OR.adopt(ws, c1["id"])                                                            # head → v002
    with pytest.raises(FlowError, match="重新生成"):
        OR.adopt(ws, c2["id"])
    assert V.load_index(ws)["head"] == "v002"


def test_discard_candidate_and_discard_version(tmp_path):
    ws = _ready(tmp_path)
    res = OR.generate_candidate(ws, None, OutlineStub())
    OR.adopt(ws, res["id"])
    V.discard(ws, "v002")                                                             # 撤销派生版本
    assert V.load_index(ws)["head"] == "v001"
    assert _bp_of(ws, "v001")["chapters"][0]["function"] == "旧功能"
    other = OR.generate_candidate(ws, None, OutlineStub())
    OR.discard_candidate(ws, other["id"])
    with pytest.raises(FlowError, match="不能采纳"):
        OR.adopt(ws, other["id"])


def test_diff_is_pure_and_reports_kinds():
    old = _blueprint(3)
    new = _blueprint(3)
    new["chapters"][0]["function"] = "改"
    new["chapters"] = new["chapters"][:2]                                             # 第 3 章在新蓝图中消失
    d = OR.diff_blueprints(old, new)
    kinds = {r["id"]: r["kind"] for r in d["chapters"]}
    assert kinds == {1: "changed", 3: "unwritten"} and d["counts"]["changed"] == 1


# ─────────────────────────── 界面冒烟 ───────────────────────────

def test_outline_tab_shows_candidate_and_controls(tmp_path, monkeypatch):
    from test_revision_review_llm import _render
    ws = _ready(tmp_path)
    OR.generate_candidate(ws, None, OutlineStub())
    at = _render(ws, monkeypatch)
    assert not at.exception
    assert any("候选大纲" in s.label for s in at.selectbox)
    assert any("采纳为新版本" in b.label for b in at.button)
