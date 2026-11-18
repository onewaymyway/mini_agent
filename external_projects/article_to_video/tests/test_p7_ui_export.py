"""P7：多期导出（期号与上期回顾/下期预告、分期独立文件名与工作目录）、驾驶舱页面与命令行入口。"""
import json
import sys

import pytest

from conftest import PROJECT_DIR
from helpers_p5 import fake_part
from article_to_video.compose.config import save_compose_config
from article_to_video.compose.export import export_final
from article_to_video.project_store import read_json, update_branding, write_json_atomic
from helpers_p2 import make_project

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402
from helpers_ui_nav import go_page, go_step


@pytest.fixture
def two_episode_project(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = make_project(tmp_path)
    from article_to_video.planning.runner import confirm_spine, plan_outline
    plan_outline(pdir, engine="rules")
    confirm_spine(pdir)
    p = pdir / "project.json"
    pj = read_json(p)
    pj.setdefault("settings", {}).setdefault("video", {})["resolution"] = "720P"
    write_json_atomic(p, pj)
    fake_part(pdir, "P01", 2.0)
    fake_part(pdir, "P02", 2.0)
    update_branding(pdir, {"author": "作者甲", "credits": {"title_card": True, "end_card": True,
                                                          "lower_third": False, "corner_tag": False},
                           "watermark": {"enabled": False}})
    save_compose_config(pdir, {"transition": "cut"})
    return pdir


def test_episode_export_uses_episode_label_and_separate_work_dir(two_episode_project):
    pdir = two_episode_project
    ep1 = {"index": 1, "total": 2, "recap": None, "preview": "下期预告：第二期的要点"}
    ep2 = {"index": 2, "total": 2, "recap": "上期回顾：第一期的要点", "preview": None}
    r1 = export_final(pdir, part_ids=["P01"], episode=ep1, label="_第1期")
    r2 = export_final(pdir, part_ids=["P02"], episode=ep2, label="_第2期")
    assert r1["files"]["video"].endswith("_第1期.mp4") and r2["files"]["video"].endswith("_第2期.mp4")
    assert r2["files"]["srt"].endswith("_第2期.srt")
    works = {p.name for p in (pdir / "exports" / ".work").iterdir()}
    assert len(works) == 2                      # 两期各有独立工作目录，互不覆盖
    assert r1["sequence"][0]["kind"] == "title_card" and r2["sequence"][-1]["kind"] == "end_card"


def test_single_episode_keeps_plain_filename(two_episode_project):
    rep = export_final(two_episode_project, part_ids=["P01", "P02"])
    assert "_第" not in rep["files"]["video"]  # 单期不加期号后缀（标题本身可含「第」）


def test_cockpit_page_loads_with_status_and_gear(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    make_project(tmp_path)
    at = AppTest.from_file(str(PROJECT_DIR / "app.py"), default_timeout=120).run()
    go_page(at, "🧭 驾驶舱")
    assert not at.exception, [e.value for e in at.exception]
    assert any(b.label.startswith("▶ 运行流水线") for b in at.button)


def test_cli_run_pipeline_g0_waits_at_outline(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = make_project(tmp_path)
    sys.path.insert(0, str(PROJECT_DIR / "entrypoints"))
    import run_pipeline as rp
    code = rp.main(["--id", pdir.name, "--until", "export", "--gear", "G0", "--offline"])
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and out["state"] == "waiting" and out["waiting"]["stage"] == "outline"


def test_cli_g2_without_budget_is_refused(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = make_project(tmp_path)
    sys.path.insert(0, str(PROJECT_DIR / "entrypoints"))
    import run_pipeline as rp
    code = rp.main(["--id", pdir.name, "--gear", "G2", "--offline"])
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and out["ok"] is False and "预算" in out["error"]
