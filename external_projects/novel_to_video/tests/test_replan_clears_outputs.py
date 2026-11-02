"""重新规划：清空本章派生产物，进度回到 C-3；剧本保留；旧的失败阶段不再残留。"""
import json

from novel_to_video import chapter_io as cio
from novel_to_video import tool_api
from novel_to_video.steps.chapter_script import run_script
from novel_to_video.steps.chapter_shots import run_shots
from test_chapter_pipeline import ChapterLLM, project  # noqa: F401  (fixture)


def _build_outputs(p):
    run_script(p, 1, ChapterLLM())
    run_shots(p, 1, ChapterLLM())
    cdir = cio.chapter_dir(p, 1)
    (cdir / "audio").mkdir(exist_ok=True)
    (cdir / "audio" / "s01.wav").write_bytes(b"x")
    (cdir / "clips").mkdir(exist_ok=True)
    (cdir / "clips" / "s01.mp4").write_bytes(b"x")
    (cdir / "review.json").write_text("{}", encoding="utf-8")
    (cdir / "video.mp4").write_bytes(b"x")
    # 模拟之前失败的 C-8 阶段与旧的校验结果
    cio.mark_stage(p, 1, "clips", "failed", "seconds must be in [4, 12]")
    cio.mark_stage(p, 1, "audio", "done")
    return cdir


def test_replan_removes_all_derived_outputs(project):
    cdir = _build_outputs(project)
    r = tool_api.replan_shots(str(project), 1)
    assert r["ok"], r
    for gone in ("audio", "clips", "shots.yaml", "review.json", "video.mp4"):
        assert not (cdir / gone).exists(), gone
    assert "audio/" in r["data"]["removed"] and "clips/" in r["data"]["removed"]


def test_replan_keeps_the_script(project):
    cdir = _build_outputs(project)
    tool_api.replan_shots(str(project), 1)
    assert (cdir / "script.md").is_file()             # 剧本保留
    assert (cdir / "script_review.yaml").is_file()    # 剧本校验结果保留


def test_replan_resets_stages_to_before_c3(project):
    _build_outputs(project)
    tool_api.replan_shots(str(project), 1)
    stages = cio.load_state(project, 1)["stages"]
    assert set(stages) <= {"script"}                 # 旧的 C-6 配音、C-8 失败都不再残留
    row = cio.chapter_row(project, 1)
    assert row["status"] == "pending" and "C-3" in row["message"]


def test_replan_refuses_when_nothing_to_replan(project):
    r = tool_api.replan_shots(str(project), 1)
    assert not r["ok"]
