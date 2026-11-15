"""一句话起稿 S2：整稿 / 分部分反馈修订、范围守卫、无改动不落盘、重试、后台与接口。"""
import copy
import json
import time

import pytest

from novel_writer.brainstorm import api as BA
from novel_writer.brainstorm import cards as C
from novel_writer.brainstorm import seed_draft as SD
from novel_writer.brainstorm import seed_gen as SG
from novel_writer.brainstorm import seed_revise as SR
from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace
from test_brainstorm_seed_s0 import good
from test_brainstorm_seed_s1 import Gen


@pytest.fixture
def ws(tmp_path):
    w = BrainstormWorkspace(tmp_path / "p")
    w.create("p")
    SD.save_version(w, good(), kind="seed", reason="起稿", seed={"idea": "记忆当货币", "constraints": "要悬疑"})
    return w


def cur(ws):
    return SD.load(ws)["draft"]


def with_cast_list(d):
    d = copy.deepcopy(d)
    d["cast"] = [{"id": k, **v} for k, v in d["cast"].items()]
    return d


def test_section_revise_changes_only_selected(ws):
    old = cur(ws)
    new_world = copy.deepcopy(old["world"])
    new_world["rules"].append({"text": "新规则：潮汐决定典当价"})
    out = SR.run(ws, "世界观更硬核", ["world"], generator=Gen({"world": new_world}))
    assert out["changed"] == ["world"] and out["ver"] == 2 and out["base"] == 1
    new = cur(ws)
    assert new["world"] == new_world
    for s in SD.SECTIONS:
        if s != "world":
            assert new[s] == old[s]
    v = SD.load(ws)
    assert v["kind"] == "revise" and "世界观" in v["reason"] and v["seed"]["idea"] == "记忆当货币"


def test_out_of_scope_changes_discarded_with_warning(ws):
    old = cur(ws)
    reply = {"world": {**old["world"], "taboos": ["新禁忌"]}, "premise": "偷偷改了命题", "plot": old["plot"]}
    logs = []
    out = SR.run(ws, "改禁忌", ["world"], generator=Gen(reply), log=lambda k, t: logs.append((k, t)))
    assert out["discarded"] == ["premise"]                       # plot 与原稿相同，不算越界
    assert cur(ws)["premise"] == old["premise"] and cur(ws)["world"]["taboos"] == ["新禁忌"]
    assert any(k == "warning" and "核心命题" in t for k, t in logs)


def test_missing_scoped_section_retries_with_error(ws):
    g = Gen({"premise": "x"}, {"world": {**cur(ws)["world"], "taboos": ["T"]}})
    out = SR.run(ws, "改", ["world"], generator=g)
    assert out["attempts"] == 2 and "缺少需要修改的部分" in g.prompts[1] and "世界观" in g.prompts[1]


def test_whole_feedback_may_change_multiple_sections(ws):
    d = with_cast_list(cur(ws))
    d["premise"] = "更黑暗的命题"
    d["plot"]["endings"] = ["全员沉没"]
    out = SR.run(ws, "整体更黑暗", None, generator=Gen(d))
    assert out["changed"] == ["premise", "plot"] and out["discarded"] == []
    assert "整稿" in SD.load(ws)["reason"]


def test_cast_ids_stable_when_model_keeps_ids(ws):
    d = with_cast_list(cur(ws))
    d["cast"][0]["want"] = "新的渴望"
    d["cast"].append({"name": "新人", "role": "反派"})
    SR.run(ws, "加反派", ["cast"], generator=Gen({"cast": d["cast"]}))
    cast = cur(ws)["cast"]
    assert cast["c1"]["want"] == "新的渴望" and cast["c2"]["name"] == "老钟" and len(cast) == 3


def test_no_change_writes_no_version(ws):
    with pytest.raises(BrainstormError) as e:
        SR.run(ws, "改", ["world"], generator=Gen({"world": cur(ws)["world"]}))
    assert "没有产生任何实际改动" in str(e.value)
    assert [v["ver"] for v in SD.versions(ws)] == [1]


def test_invalid_result_two_failures_write_nothing(ws):
    bad = {"world": {"rules": "不是列表"}}
    with pytest.raises(BrainstormError):
        SR.run(ws, "改", ["world"], generator=Gen(bad))
    assert [v["ver"] for v in SD.versions(ws)] == [1]


def test_affects_hint_filtered(ws):
    w = copy.deepcopy(cur(ws)["world"])
    w["taboos"] = ["T"]
    out = SR.run(ws, "改", ["world"], generator=Gen({"world": w, "_affects": ["cast", "world", "bogus", "plot", "cast"]}))
    assert out["affects"] == ["cast", "plot"]                    # 范围内/未知键被过滤并去重；仅提示，不自动修改
    assert cur(ws)["cast"] == SD.load(ws, 1)["draft"]["cast"]


def test_unknown_key_in_output_is_error(ws):
    w = {**cur(ws)["world"], "taboos": ["T"]}
    g = Gen({"world": w, "mystery": 1}, {"world": w})
    out = SR.run(ws, "改", ["world"], generator=g)
    assert out["attempts"] == 2 and "未知键" in g.prompts[1]


def test_prompt_has_feedback_scope_seed_and_cast_ids(ws):
    g = Gen({"world": {**cur(ws)["world"], "taboos": ["T"]}})
    SR.run(ws, "世界观改成近未来", ["world"], generator=g)
    p = g.prompts[0]
    assert "世界观改成近未来" in p and "只允许修改：世界观" in p and "记忆当货币" in p and "要悬疑" in p
    assert '"id": "c1"' in p


def test_input_validation(ws):
    for fb, sec in (("  ", None), ("x", ["bogus"]), ("x" * 3001, None)):
        with pytest.raises(BrainstormError):
            SR.run(ws, fb, sec, generator=Gen({}))
    with pytest.raises(BrainstormError):
        SR.run(ws, "x", ["world"], base_ver=9, generator=Gen({}))


def test_revise_from_older_base_version(ws):
    w = {**cur(ws)["world"], "taboos": ["T2"]}
    SR.run(ws, "改", ["world"], generator=Gen({"world": w}))             # v2
    p = cur(ws)["premise"]
    out = SR.run(ws, "改命题", ["premise"], base_ver=1, generator=Gen({"premise": "基于v1的新命题"}))
    assert out["base"] == 1 and out["ver"] == 3
    assert cur(ws)["world"] == SD.load(ws, 1)["draft"]["world"] and cur(ws)["premise"] != p


def test_cards_untouched(ws):
    before = C.content_hash(ws)
    SR.run(ws, "改", ["world"], generator=Gen({"world": {**cur(ws)["world"], "taboos": ["T"]}}))
    assert C.content_hash(ws) == before


def test_diff_between_versions(ws):
    SR.run(ws, "改", ["world"], generator=Gen({"world": {**cur(ws)["world"], "taboos": ["新禁忌"]}}))
    d = SR.diff(ws, 1, 2)
    assert d["changed"] == ["world"] and "新禁忌" in d["diffs"]["world"]


def _wait(ws, rid):
    end = time.time() + 5
    while time.time() < end:
        st = SG.run_state(ws, rid)
        if st["status"] != "running":
            return st
        time.sleep(0.05)
    raise AssertionError("超时")


def test_background_and_api(ws):
    w = {**cur(ws)["world"], "taboos": ["T3"]}
    r = BA.seed_revise_start(str(ws.root), "改禁忌", ["world"], generator=Gen({"world": w}))
    assert r["ok"]
    st = _wait(ws, r["data"]["run"])
    assert st["status"] == "done" and st["result"]["changed"] == ["world"]
    assert BA.seed_diff(str(ws.root), 1, 2)["data"]["changed"] == ["world"]
    assert BA.seed_restore(str(ws.root), 1)["data"]["ver"] == 3
    assert BA.seed_revise_start(str(ws.root), " ")["ok"] is False
    assert BA.seed_revise_start(str(ws.root), "x", ["bogus"])["ok"] is False


def test_concurrent_with_seed_task_refused(ws):
    from novel_writer.fsutil import atomic_write_json
    atomic_write_json(ws.root / SG.RUNS_DIR / "sfake.json", {"run": "sfake", "status": "running"})
    with pytest.raises(BrainstormError):
        SR.start_run(ws, "改", ["world"], generator=Gen({}))
