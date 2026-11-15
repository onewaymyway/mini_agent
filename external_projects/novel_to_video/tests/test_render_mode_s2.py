"""动态漫模式 S2：渲染方式字段、阶段表、创建后只读、动态漫整章门禁；视频模式不变。"""
import json
from pathlib import Path

import pytest

from conftest import build_export
from novel_to_video import chapter_io as cio
from novel_to_video import chapter_pipeline as cp
from novel_to_video import project_store as ps
from novel_to_video import render_mode as RM
from novel_to_video import tool_api
from novel_to_video.providers.image import StubImageProvider
from novel_to_video.providers.tts import SilenceTTS
from novel_to_video.providers.video import StubVideoProvider


def _create(tmp_path, options=None, name="src"):
    src = build_export(tmp_path / name, chapters=(1,))
    return tool_api.create_project_from_source(str(tmp_path / "out"), str(src), title="测试",
                                               options=options)


# ---------- 纯规则

def test_normalize_defaults_and_rejects_unknown():
    assert RM.normalize(None) == "video"
    assert RM.normalize("") == "video"
    assert RM.normalize("video") == "video"
    assert RM.normalize("still") == "still"
    with pytest.raises(ValueError):
        RM.normalize("gif")


def test_video_stage_table_is_exactly_the_current_pipeline():
    # 视频模式的阶段表必须与改动前完全一致
    assert RM.stages_for("video") == ["script", "shots", "assets", "audio", "prompts", "clips", "compose"]
    assert cp.STAGES == RM.stages_for("video")


def test_still_stage_table_order():
    assert RM.stages_for("still") == ["script", "shots", "assets", "audio", "prompts",
                                      "stills", "segments", "compose"]


def test_legacy_project_without_field_is_video():
    assert RM.mode_of({"title": "旧项目"}) == "video"
    assert RM.mode_of(None) == "video"


def test_still_is_not_runnable_until_stages_are_implemented():
    reason = RM.unsupported_reason("still")
    assert reason and "尚未实现" in reason and "stills" in reason
    assert RM.unsupported_reason("video") is None


# ---------- 创建与只读

def test_new_project_defaults_to_video(tmp_path):
    res = _create(tmp_path)
    assert res["ok"], res
    meta = json.loads((Path(res["data"]["project_dir"]) / "project.json").read_text(encoding="utf-8"))
    assert meta["render_mode"] == "video"


def test_new_project_can_be_created_as_still(tmp_path):
    res = _create(tmp_path, options={"render_mode": "still"})
    assert res["ok"], res
    meta = json.loads((Path(res["data"]["project_dir"]) / "project.json").read_text(encoding="utf-8"))
    assert meta["render_mode"] == "still"


def test_invalid_mode_rejected_without_creating_anything(tmp_path):
    out = tmp_path / "out"
    res = _create(tmp_path, options={"render_mode": "gif"})
    assert not res["ok"] and "渲染方式" in res["error"]["message"]
    assert not out.exists() or not any(out.iterdir())   # 没有留下空项目目录


def test_render_mode_is_read_only_after_creation():
    # 渲染方式没有修改接口；所有 set_* 只能改各自的开关
    assert not hasattr(tool_api, "set_render_mode")
    assert not hasattr(ps, "set_render_mode")


def test_existing_setters_keep_render_mode(tmp_path):
    res = _create(tmp_path, options={"render_mode": "still"})
    pdir = Path(res["data"]["project_dir"])
    tool_api.set_location_spots(str(pdir), False)
    tool_api.set_character_looks(str(pdir), False)
    meta = json.loads((pdir / "project.json").read_text(encoding="utf-8"))
    assert meta["render_mode"] == "still"


# ---------- 当前阶段随模式变化

def _mark_done(pdir, no, stages):
    for st in stages:
        cio.mark_stage(pdir, no, st, "done")


def test_current_stage_follows_mode(tmp_path):
    res = _create(tmp_path, options={"render_mode": "still"})
    pdir = Path(res["data"]["project_dir"])
    _mark_done(pdir, 1, ["script", "shots", "assets", "audio", "prompts"])
    assert cp._current_stage(pdir, 1) == "stills"


def test_current_stage_video_unchanged(tmp_path):
    res = _create(tmp_path)
    pdir = Path(res["data"]["project_dir"])
    _mark_done(pdir, 1, ["script", "shots", "assets", "audio", "prompts"])
    assert cp._current_stage(pdir, 1) == "clips"
    _mark_done(pdir, 1, ["clips", "compose"])
    assert cp._current_stage(pdir, 1) == "compose"   # 全部完成 → 最后一个阶段


# ---------- 整章门禁：动态漫在任何模型调用之前被拒绝

class ExplodingLLM:
    name = "exploding"

    def ask(self, prompt, system=None):
        raise AssertionError("动态漫门禁失败：不应调用文本模型")


def test_still_chapter_refused_before_any_model_call(tmp_path):
    res = _create(tmp_path, options={"render_mode": "still"})
    pdir = Path(res["data"]["project_dir"])
    out = cp.run_chapter(pdir, 1, text_llm=ExplodingLLM(), image=StubImageProvider(),
                         video=StubVideoProvider(), tts=SilenceTTS())
    assert out["status"] == "failed" and "尚未实现" in out["message"]
    row = cio.chapter_row(pdir, 1)       # 章节状态记在 chapters/index.yaml
    assert row["status"] == "failed" and "尚未实现" in row["message"]
    assert not cio.load_state(pdir, 1).get("stages")   # 没有任何阶段被标记为运行
