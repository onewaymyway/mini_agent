"""地点细化 L2：C-1 剧本规则（场次标题写到具体场所）与剧本粒度警告。

关闭开关时提示词、校验与缓存行为必须与之前一致；开启后才启用新规则。
"""
import json

from novel_to_video import prompts, tool_api
from novel_to_video.steps.chapter_script import check_script, run_script

from test_chapter_pipeline import project  # noqa: F401  (fixture)

SCRIPT_TEXT_SRC = "原文一二三"


class RecordingLLM:
    """记录每次发给模型的提示词，并返回固定剧本。"""
    name = "recording"

    def __init__(self, script):
        self.script = script
        self.prompts = []

    def ask(self, prompt, system=""):
        self.prompts.append(prompt)
        return self.script


def _first_area_name(proj):
    locs = json.loads((proj / "global" / "locations.json").read_text(encoding="utf-8"))["locations"]
    return locs[0]["name"]


# ---------------------------------------------------------------- 提示词

def test_script_prompt_off_is_unchanged_and_on_asks_for_specific_place():
    args = (3, "测试", SCRIPT_TEXT_SRC, 756)
    off = prompts.script_prompt(*args)
    assert prompts.script_prompt(*args, spots_enabled=False) == off
    assert "区域·场所" not in off

    on = prompts.script_prompt(*args, spots_enabled=True)
    assert "区域·场所·时间" in on
    assert "铁城·市政厅办公室" in on
    assert "每个非空行" in on          # 其它规则保留
    assert on.count(SCRIPT_TEXT_SRC) == 1  # 原文仍只出现一次


def test_script_fix_prompt_keeps_spot_rule_when_enabled():
    fx = (3, "测试", SCRIPT_TEXT_SRC, 756, "### 场次 1｜a", [{"level": "error", "message": "x"}])
    assert "区域·场所·时间" in prompts.script_fix_prompt(*fx, spots_enabled=True)
    assert "区域·场所" not in prompts.script_fix_prompt(*fx)


# ---------------------------------------------------------------- 校验

def _check(title, area_names):
    script = f"{title}\n旁白：天亮了。\n"
    return check_script(script, "原文", [], target_chars=0, area_names=area_names)


def test_area_only_title_warns_when_enabled():
    res = _check("### 场次 1｜铁城·清晨（外景）", ["铁城"])
    assert res["status"] == "pass"           # 只是警告，不判失败
    msgs = [i["message"] for i in res["issues"] if i["level"] == "warning"]
    assert any("只写了区域「铁城」" in m and "场次 1" in m for m in msgs)


def test_area_only_title_single_token_warns():
    res = _check("### 场次 2｜铁城（外景）", ["铁城"])
    assert any("只写了区域" in i["message"] for i in res["issues"])


def test_specific_place_does_not_warn():
    res = _check("### 场次 1｜铁城·市政厅办公室·清晨（内景）", ["铁城"])
    assert not any("只写了区域" in i["message"] for i in res["issues"])


def test_no_warning_when_feature_disabled():
    res = _check("### 场次 1｜铁城·清晨（外景）", None)
    assert not any("只写了区域" in i["message"] for i in res["issues"])


def test_unknown_place_does_not_warn():
    res = _check("### 场次 1｜未知·清晨（外景）", ["铁城"])
    assert not any("只写了区域" in i["message"] for i in res["issues"])


def test_name_not_in_area_library_does_not_warn():
    # 地点名不在区域库中（例如它本身就是某个场所）→ 不提醒
    res = _check("### 场次 1｜东三街·清晨（外景）", ["铁城"])
    assert not any("只写了区域" in i["message"] for i in res["issues"])


# ---------------------------------------------------------------- run_script 集成

def test_run_script_toggle_switches_prompt_warning_and_cache(project):  # noqa: F811
    area = _first_area_name(project)
    script = f"### 场次 1｜{area}·清晨（外景）\n旁白：天亮了。\n"

    # 1) 关闭：提示词不含新规则，校验无地点警告
    off_llm = RecordingLLM(script)
    off = run_script(project, 1, off_llm)
    assert off["status"] == "pass"
    assert "区域·场所·时间" not in off_llm.prompts[0]
    assert not any("只写了区域" in i["message"] for i in off["issues"])

    # 2) 再次运行（关闭）：缓存命中，不调用模型
    again = run_script(project, 1, RecordingLLM(script))
    assert again["skipped"] is True

    # 3) 开启：缓存失效，重新调用模型，提示词带新规则，并给出地点警告
    assert tool_api.set_location_spots(str(project), True)["ok"]
    on_llm = RecordingLLM(script)
    on = run_script(project, 1, on_llm)
    assert on["skipped"] is False
    assert "区域·场所·时间" in on_llm.prompts[0]
    assert any("只写了区域" in i["message"] for i in on["issues"]), on["issues"]
    assert on["status"] == "pass"
