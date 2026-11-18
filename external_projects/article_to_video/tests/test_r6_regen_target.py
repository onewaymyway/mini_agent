"""R6：重新生成必须从原文重新调用模型（不复用摘要）；目标时长在设置与大纲之间同步且不被取整。"""
import json
from pathlib import Path
import tempfile

from article_to_video import app_settings as S
from article_to_video.planning.runner import (
    load_spine, plan_outline, set_project_target, _partial_path,
)
from helpers_p1 import ScriptedLLM, covering_spine, good_digest, make_project


def _project(tmp_path):
    return make_project(tmp_path)


def test_regenerate_ignores_existing_digests_by_default(tmp_path):
    pdir = _project(tmp_path)
    first = ScriptedLLM(good_digest, covering_spine)
    plan_outline(pdir, engine="llm", llm=first)
    groups = first.calls["digest"]
    assert groups >= 3

    again = ScriptedLLM(good_digest, covering_spine)
    plan_outline(pdir, engine="llm", llm=again, force=True, reuse_digests=False)
    assert again.calls["digest"] == groups          # 全部组重新调用模型，没有复用


def test_resume_still_reuses_when_asked(tmp_path):
    pdir = _project(tmp_path)
    plan_outline(pdir, engine="llm", llm=ScriptedLLM(good_digest, covering_spine))
    again = ScriptedLLM(good_digest, covering_spine)
    plan_outline(pdir, engine="llm", llm=again, force=True, reuse_digests=True)
    assert again.calls["digest"] == 0               # 流水线/续跑的默认行为不变


def test_regenerate_drops_stale_partial_results(tmp_path):
    pdir = _project(tmp_path)
    (pdir / "plan").mkdir(exist_ok=True)
    _partial_path(pdir).write_text(json.dumps({"digests": [
        {"group_id": "g01", "input_sha": "x", "summary": "旧"}]}, ensure_ascii=False), encoding="utf-8")
    llm = ScriptedLLM(good_digest, covering_spine)
    plan_outline(pdir, engine="llm", llm=llm, reuse_digests=False)
    assert llm.calls["digest"] >= 3                 # 旧的部分结果没有被复用
    assert not _partial_path(pdir).exists()


def test_regenerate_uses_setting_target_minutes(tmp_path):
    pdir = _project(tmp_path)
    S.set_field(pdir, "settings", "target_minutes", 20.0)
    plan_outline(pdir, engine="rules", force=True, reuse_digests=False)
    assert load_spine(pdir)["target_minutes"] == 20.0
    S.set_field(pdir, "settings", "target_minutes", 30.0)
    plan_outline(pdir, engine="rules", force=True, reuse_digests=False)
    spine = load_spine(pdir)
    assert spine["target_minutes"] == 30.0
    assert abs(sum(p["budget_minutes"] for p in spine["parts"]) - 30.0) < 0.1


def test_outline_target_is_synced_to_settings_and_not_rounded(tmp_path):
    pdir = _project(tmp_path)
    plan_outline(pdir, engine="rules", force=True, reuse_digests=False)
    assert set_project_target(pdir, 12.5) == 12.5
    assert S.effective_group(pdir, "settings")["target_minutes"] == 12.5
    assert load_spine(pdir)["target_minutes"] == 12.5
    # 之后重新生成沿用这个值，而不是旧的设置
    plan_outline(pdir, engine="rules", force=True, reuse_digests=False)
    assert load_spine(pdir)["target_minutes"] == 12.5


def test_set_project_target_clamps_range(tmp_path):
    pdir = _project(tmp_path)
    assert set_project_target(pdir, 1) == 3.0
    assert set_project_target(pdir, 999) == 60.0
