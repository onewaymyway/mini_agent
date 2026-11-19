"""第二期：含结构段落的口播——结构检测、依赖画面的校验、段落级修订（只改有问题的段落，ID 不变）、提示词规则。

依据 next_doc/article_to_video_storyboard_model_redesign_plan.md 第 5 节。
"""
import copy
import json
import re

import pytest

from article_to_video.planning.runner import load_spine
from article_to_video.project_store import write_json_atomic
from article_to_video.script import writer as w
from article_to_video.script.repair import _KIND_HINT
from article_to_video.script.restructure import STRUCT_REV, refresh_part
from article_to_video.script.structure import (beat_structure, missing_nodes, structure_issues,
                                               structure_report, visual_ref_hits)
from article_to_video.script.validate import validate_script
from helpers_p4 import scripted_project


class Rec:
    """记录调用次数的假模型；fn(prompt) -> 字符串。"""

    def __init__(self, fn):
        self.fn = fn
        self.calls = 0

    def ask(self, prompt, system=""):
        self.calls += 1
        return self.fn(prompt)


def fake_revision(prompt):
    """按提示词中列出的节点与数字，生成一段不依赖画面的口语稿（每句都可追溯到原文）。"""
    nodes = re.search(r"必须在口播中提及的流程节点（按顺序讲清先后）：(.*)", prompt).group(1).split("、")
    nums = re.search(r"表格中的关键数字（口播要讲清楚，不得新增）：(.*)", prompt).group(1).split("、")
    sents = ["这一段按顺序说明几个环节。"]
    sents += [f"其中有{n}。" for n in nodes if n and n != "（无）"]
    sents += [f"关键数字是{x}。" for x in nums if x and x != "（无）"]
    return json.dumps({"sentences": [{"text": t, "quote": False, "emphasis": []} for t in sents]},
                      ensure_ascii=False)


@pytest.fixture
def ready(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir, spine = scripted_project(tmp_path)
    return pdir, spine


def _part(spine, pid):
    return next(p for p in spine["parts"] if p["id"] == pid)


def _blocks(pdir):
    return w.load_blocks(pdir)


def _beat(pdir, pid, bid):
    return next(b for b in w.load_script(pdir, pid)["beats"] if b["id"] == bid)


def _set_sentence(pdir, pid, bid, index, text):
    script = w.load_script(pdir, pid)
    beat = next(b for b in script["beats"] if b["id"] == bid)
    beat["sentences"][index] = dict(beat["sentences"][index], text=text)
    write_json_atomic(w._script_path(pdir, pid), script)


# ---------- 结构检测 ----------

def test_structure_is_detected_from_the_reference_article(ready):
    pdir, _ = ready
    blocks = _blocks(pdir)
    assert beat_structure(_beat(pdir, "P03", "P03.b06"), blocks)["kinds"] == ["table"]
    assert "formula" in beat_structure(_beat(pdir, "P03", "P03.b02"), blocks)["kinds"]
    chain = beat_structure(_beat(pdir, "P01", "P01.b04"), blocks)
    assert "chain" in chain["kinds"] and "信息化" in chain["nodes"]
    rows = structure_report(w.load_script(pdir, "P03"), blocks)
    assert rows and all("beat" in r and "nodes_missing" in r for r in rows)


def test_plain_beats_have_no_structure_and_no_issues(ready):
    pdir, _ = ready
    blocks = _blocks(pdir)
    script = w.load_script(pdir, "P01")
    plain = [b for b in script["beats"] if not beat_structure(b, blocks)["kinds"]]
    assert plain
    for b in plain:
        assert structure_issues(b, blocks) == []


# ---------- 校验：依赖画面（句子级错误）与节点缺失（警告） ----------

def _single_beat_verdict(pdir, pid, beat, blocks):
    spine_part = _part(load_spine(pdir), pid)
    source = w._part_source(pdir, spine_part["section_ids"])
    script = dict(w.load_script(pdir, pid), beats=[beat])
    return validate_script(script, source, spine_part["section_ids"], check_pacing=False, blocks=blocks)


def test_visual_reference_is_a_sentence_error_only_in_structured_beats(ready):
    pdir, _ = ready
    blocks = _blocks(pdir)
    beat = copy.deepcopy(_beat(pdir, "P03", "P03.b06"))
    beat["sentences"][0]["text"] = "如图所示，这是三组数字。"
    v = _single_beat_verdict(pdir, "P03", beat, blocks)
    assert any("依赖画面" in e for e in v["errors"])
    assert any(f["kind"] == "visual_ref" for f in v["flags"])
    # 没有结构的段落：同样的句子不算错误（只有含结构段落才有此要求）
    plain = copy.deepcopy(next(b for b in w.load_script(pdir, "P01")["beats"] if not beat_structure(b, blocks)["kinds"]))
    plain["sentences"][0]["text"] = "如图所示，这是一个普通的说明。"
    v2 = _single_beat_verdict(pdir, "P01", plain, blocks)
    assert not any("依赖画面" in e for e in v2["errors"])


def test_visual_reference_is_ignored_without_blocks_for_backward_compatibility(ready):
    pdir, _ = ready
    beat = copy.deepcopy(_beat(pdir, "P03", "P03.b06"))
    beat["sentences"][0]["text"] = "如图所示，这是三组数字。"
    v = _single_beat_verdict(pdir, "P03", beat, None)
    assert not any("依赖画面" in e for e in v["errors"])


def test_missing_flow_nodes_are_warnings_not_errors(ready):
    pdir, _ = ready
    blocks = _blocks(pdir)
    beat = _beat(pdir, "P01", "P01.b04")
    assert missing_nodes(beat, beat_structure(beat, blocks))       # 参考文章的口播没有提及这些节点
    v = _single_beat_verdict(pdir, "P01", copy.deepcopy(beat), blocks)
    assert not any("依赖画面" in e for e in v["errors"])
    assert any("流程节点未在口播中提及" in x for x in v["warnings"])


def test_repair_has_a_hint_for_visual_references():
    assert "visual_ref" in _KIND_HINT and "如图所示" in _KIND_HINT["visual_ref"]


def test_writer_prompt_version_and_rule_are_second_phase():
    assert w.PROMPT_VERSION == "s3-v2"
    part = {"id": "P01", "title": "章", "section_ids": ["s01"], "role": "", "one_line": ""}
    prompt = w.build_prompt(part, "原文", persona="host", budget_chars=400, glossary=[], prev_tail="",
                            issues=[])
    assert "不得写“如图所示”" in prompt and "先是" in prompt


# ---------- 段落级修订 ----------

def test_refresh_rewrites_only_flagged_beats_and_keeps_their_ids(ready):
    pdir, spine = ready
    blocks = _blocks(pdir)
    before = w.load_script(pdir, "P03")
    _set_sentence(pdir, "P03", "P03.b06", 0, "如图所示，这是三组数字。")
    before = w.load_script(pdir, "P03")
    targets = {b["id"] for b in before["beats"] if structure_issues(b, blocks)}
    assert "P03.b06" in targets

    llm = Rec(fake_revision)
    out = refresh_part(pdir, "P03", llm)
    assert out["action"] == "revised"
    assert set(out["rewritten"]) == targets
    after = w.load_script(pdir, "P03")
    assert [b["id"] for b in after["beats"]] == [b["id"] for b in before["beats"]]
    for b_old, b_new in zip(before["beats"], after["beats"]):
        if b_old["id"] in targets:
            assert structure_issues(b_new, blocks) == []
            assert "如图所示" not in "".join(s["text"] for s in b_new["sentences"])
        else:
            assert b_new["sentences"] == b_old["sentences"]     # 其它段落原样保留
        assert b_new["source_refs"] == b_old["source_refs"] and b_new["role"] == b_old["role"]
    assert after["confirmed"] is False and after["structure_rev"] == STRUCT_REV
    assert llm.calls == len(targets) and llm.calls >= 1


def test_second_refresh_is_a_no_op(ready):
    pdir, _ = ready
    refresh_part(pdir, "P03", Rec(fake_revision))
    llm = Rec(fake_revision)
    out = refresh_part(pdir, "P03", llm)
    assert out["action"] == "unchanged" and llm.calls == 0


def test_failed_revision_keeps_the_original_and_stays_confirmed(ready):
    pdir, _ = ready
    _set_sentence(pdir, "P03", "P03.b06", 0, "如图所示，这是三组数字。")
    before = w.load_script(pdir, "P03")
    out = refresh_part(pdir, "P03", Rec(lambda p: "这不是 JSON"))
    assert out["action"] == "failed" and out["failed"]
    after = w.load_script(pdir, "P03")
    assert after["beats"] == before["beats"] and after["confirmed"] is True


def test_structure_report_counts_missing_nodes(ready):
    pdir, _ = ready
    rows = {r["beat"]: r for r in structure_report(w.load_script(pdir, "P01"), _blocks(pdir))}
    assert rows["P01.b04"]["nodes_total"] > 0
    assert rows["P01.b04"]["nodes_missing"]                    # 参考文章口播未提及这些节点
