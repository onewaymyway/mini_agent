"""整章重置：清空本章全部生成产物，进度与状态回到从剧本开始；全书设定与原文保留；运行中拒绝。"""
import json

from novel_to_video import chapter_io as cio
from novel_to_video import tool_api
from novel_to_video import runtime as rt
from novel_to_video.steps.chapter_script import run_script
from novel_to_video.steps.chapter_shots import run_shots
from test_chapter_pipeline import ChapterLLM, project  # noqa: F401  (fixture)


def _build_everything(p):
    run_script(p, 1, ChapterLLM())
    run_shots(p, 1, ChapterLLM())
    cdir = cio.chapter_dir(p, 1)
    for sub in ("audio", "clips", "stills", "subs"):
        (cdir / sub).mkdir(exist_ok=True)
        (cdir / sub / "s01.bin").write_bytes(b"x")
    (cdir / "video.mp4").write_bytes(b"x")
    (cdir / "cover.png").write_bytes(b"x")
    (cdir / "review.json").write_text("{}", encoding="utf-8")
    cio.mark_stage(p, 1, "clips", "failed", "boom")
    return cdir


def test_reset_removes_all_chapter_outputs_and_restarts_from_script(project):
    cdir = _build_everything(project)
    before = sorted(p.name for p in cdir.iterdir())
    assert "script.md" in before and "shots.yaml" in before
    r = tool_api.reset_chapter(str(project), 1)
    assert r["ok"], r
    assert sorted(r["data"]["removed"]) == before
    assert [p.name for p in cdir.iterdir()] == ["state.json"]   # 只剩空进度文件
    assert cio.load_state(project, 1)["stages"] == {}
    row = cio.chapter_row(project, 1)
    assert row["status"] == "pending"
    assert "C-1" in row["message"]


def test_reset_keeps_global_settings_and_other_chapters(project):
    _build_everything(project)
    gdir = project / "global"
    before_global = sorted(p.name for p in gdir.iterdir()) if gdir.is_dir() else []
    tool_api.reset_chapter(str(project), 1)
    after_global = sorted(p.name for p in gdir.iterdir()) if gdir.is_dir() else []
    assert after_global == before_global
    assert (project / "project.json").is_file()


def test_reset_refused_while_running(project):
    _build_everything(project)
    rt.update(project, state="running")
    try:
        r = tool_api.reset_chapter(str(project), 1)
        assert not r["ok"]
        assert (cio.chapter_dir(project, 1) / "script.md").is_file()     # 没有删除任何东西
    finally:
        rt.update(project, state="idle")


def test_reset_unknown_chapter_fails_without_creating_dirs(project):
    r = tool_api.reset_chapter(str(project), 999)
    assert not r["ok"]
    assert not cio.chapter_dir(project, 999).exists()


def test_chapter_page_module_exposes_reset_section():
    # 章节生产页与驾驶舱共用同一后端 reset_chapter；这里确认页面渲染函数存在且调用的是该接口
    import inspect
    from ui import chapter_page
    src = inspect.getsource(chapter_page._reset_chapter_section)
    assert "tool_api.reset_chapter" in src
    assert "_reset_chapter_section(pdir, no)" in inspect.getsource(chapter_page.page_chapters)
