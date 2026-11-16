"""一句话起稿 v2：故事结构（篇幅、幕、多线）、迁移、长篇规则、剧情与结构一致、按部分修订的检查点与自动范围、卡片映射。"""
import copy
import json
import re

import pytest

from novel_writer.brainstorm import cards as C
from novel_writer.brainstorm import seed_draft as SD
from novel_writer.brainstorm import seed_gen as SG
from novel_writer.brainstorm import seed_revise as SR
from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace
from test_brainstorm_seed_s0 import good


def long_draft():
    d = good()
    d["storyline"] = {
        "logline": "渔女追查被典当的记忆，牵出整座港口的交易",
        "structure": {"type": "多幕长篇", "total_chapters": 60, "acts": [
            {"title": "潮起", "chapters": "1–20", "goal": "入局"},
            {"title": "暗涌", "chapters": "21–40", "goal": "深入"},
            {"title": "潮落", "chapters": "41–60", "goal": "代价"}]},
        "threads": [
            {"name": "母亲的记忆", "role": "main", "opening": "发现典当", "development": "追查", "turn": "真相", "ending": "取回", "meets": ["黑市账本"]},
            {"name": "黑市账本", "role": "sub", "opening": "账本出现", "development": "被盗", "turn": "被灭口", "ending": "沉没", "meets": []},
        ]}
    d["plot"] = {"acts": [{"title": t, "beats": ["转折"]} for t in ("潮起", "暗涌", "潮落")],
                 "twists": ["反转"], "endings": ["结局"]}
    return d


class ByGen:
    def __init__(self, full, fail=()):
        self.full, self.fail, self.prompts = full, set(fail), []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        label = re.search(r"当前的「(.+?)」", prompt).group(1)
        sec = next(k for k, v in SD.SECTION_LABELS.items() if v == label)
        if sec in self.fail:
            return json.dumps({"premise": "乱写"}, ensure_ascii=False)     # 缺少本部分
        return json.dumps({sec: self.full[sec]}, ensure_ascii=False)


@pytest.fixture
def ws(tmp_path):
    w = BrainstormWorkspace(tmp_path / "p")
    w.create("p")
    SD.save_version(w, good(), kind="seed", reason="起稿", seed={"idea": "记忆当货币", "constraints": ""})
    return w


def test_legacy_storyline_is_migrated_to_one_main_thread():
    clean = SD.validate_draft(good())
    st = clean["storyline"]
    assert set(st) == {"logline", "structure", "threads"}
    assert st["threads"][0]["role"] == "main" and st["threads"][0]["turn"]
    assert st["structure"]["type"].startswith("起承转合")


def test_long_form_requires_three_acts_and_two_threads():
    d = long_draft()
    d["storyline"]["threads"] = d["storyline"]["threads"][:1]
    with pytest.raises(BrainstormError, match="至少需要 2 条线索"):
        SD.validate_draft(d)
    d = long_draft()
    d["storyline"]["structure"]["acts"] = d["storyline"]["structure"]["acts"][:2]
    d["storyline"]["structure"]["acts"][1]["chapters"] = "21–60"
    d["plot"]["acts"] = d["plot"]["acts"][:2]
    with pytest.raises(BrainstormError, match="至少需要 3 幕"):
        SD.validate_draft(d)


def test_act_ranges_must_cover_total_contiguously():
    d = long_draft()
    d["storyline"]["structure"]["acts"][2]["chapters"] = "41–59"
    with pytest.raises(BrainstormError, match="连续覆盖 1–60"):
        SD.validate_draft(d)


def test_plot_acts_must_match_structure_titles():
    d = long_draft()
    d["plot"]["acts"][1]["title"] = "别的幕"
    with pytest.raises(BrainstormError, match="不一致"):
        SD.validate_draft(d)


def test_thread_role_and_names_are_checked():
    d = long_draft()
    d["storyline"]["threads"][1]["role"] = "weird"
    with pytest.raises(BrainstormError, match="类型必须是"):
        SD.validate_draft(d)
    d = long_draft()
    d["storyline"]["threads"][1]["name"] = d["storyline"]["threads"][0]["name"]
    with pytest.raises(BrainstormError, match="线索名重复"):
        SD.validate_draft(d)


def test_valid_long_draft_passes_and_renders_threads():
    clean = SD.validate_draft(long_draft())
    text = SD.storyline_text(clean["storyline"])
    assert "结构：多幕长篇（约 60 章）" in text and "第 2 幕「暗涌」，21–40" in text
    assert "线索「黑市账本」（支线）" in text and "交汇：黑市账本" in text


def test_generation_prompt_carries_target_and_structure_rules(ws):
    prompt = SG.build_prompt(ws, "想法", target_chapters=80, structure="群像")
    assert "目标篇幅：约 80 章" in prompt and "total_chapters 必须填 80" in prompt
    assert "结构偏好：群像" in prompt and "禁止套路" in prompt
    assert "目标篇幅未指定" in SG.build_prompt(ws, "想法")


def test_run_stores_target_and_structure_in_seed(ws):
    class G:
        def __call__(self, p):
            return json.dumps(long_draft(), ensure_ascii=False)
    out = SG.run(ws, "想法", target_chapters=60, structure="多幕长篇", generator=G())
    seed = SD.load(ws, out["ver"])["seed"]
    assert seed["target_chapters"] == 60 and seed["structure"] == "多幕长篇"


def test_target_out_of_range_is_rejected(ws):
    with pytest.raises(BrainstormError, match="1–2000"):
        SG.run(ws, "想法", target_chapters=5000, generator=lambda p: "{}")


def test_section_checkpoint_is_reused_after_failed_run(ws):
    d = copy.deepcopy(SD.load(ws)["draft"])
    d["premise"] = "新的命题"
    d["world"]["taboos"] = ["新禁忌"]
    g1 = ByGen(d, fail={"world"})
    with pytest.raises(BrainstormError, match="修订「世界观」失败"):
        SR.run(ws, "改", ["premise", "world"], generator=g1)
    assert SD.load(ws)["ver"] == 1                                 # 没有写入新版本
    g2 = ByGen(d)
    out = SR.run(ws, "改", ["premise", "world"], generator=g2)
    assert "premise" in out["reused"]                              # 命题沿用检查点
    assert len(g2.prompts) == 1 and "世界观" in g2.prompts[0]      # 只重做失败的部分
    assert SD.load(ws)["draft"]["premise"] == "新的命题"


def test_changing_structure_pulls_plot_into_scope(ws):
    base = long_draft()
    SD.save_version(ws, base, kind="seed", reason="长篇", seed={"idea": "x", "constraints": ""})
    d = copy.deepcopy(base)
    d["storyline"]["structure"]["acts"][1]["title"] = "新的中段"
    d["plot"]["acts"][1]["title"] = "新的中段"
    out = SR.run(ws, "中段改名", ["storyline"], generator=ByGen(d))
    assert "plot" in out["section_attempts"]                       # 剧情被自动纳入修订范围
    assert SD.load(ws)["draft"]["plot"]["acts"][1]["title"] == "新的中段"


def test_card_plot_keeps_act_chapter_ranges():
    mod = C.validate_module("plot", {"acts": [{"title": "潮起", "chapters": "1–20", "beats": ["入局"]}],
                                     "twists": [], "endings": []})
    assert mod["acts"][0]["chapters"] == "1–20"


def test_act_numbering_prefixes_do_not_break_title_match():
    d = long_draft()
    d["plot"]["acts"] = [{"title": "第一幕：潮起", "beats": ["转折"]}, {"title": "2. 暗涌", "beats": ["转折"]},
                         {"title": "第三幕 潮落", "beats": ["转折"]}]
    clean = SD.validate_draft(d)
    assert [a["title"] for a in clean["plot"]["acts"]] == ["潮起", "暗涌", "潮落"]
    d["storyline"]["structure"]["acts"][0]["title"] = "第一幕：潮起"
    assert SD.validate_draft(d)["storyline"]["structure"]["acts"][0]["title"] == "潮起"
