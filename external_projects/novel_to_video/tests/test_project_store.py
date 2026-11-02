import json
from datetime import datetime
from pathlib import Path

import yaml

from conftest import build_export
from novel_to_video import project_store as ps
from novel_to_video import tool_api
from novel_to_video.source_import import import_source


def test_create_project_layout(tmp_path, export_dir):
    result = import_source(export_dir)
    proj = ps.create_project(tmp_path / "out", result, title="测试书",
                             now=datetime(2026, 11, 1, 10, 0, 0))
    assert proj.name == "测试书_20261101_100000"
    for rel in ["project.json", "source/manifest.json", "global/characters.json",
                "global/locations.json", "global/review.json", "chapters/index.yaml",
                "PROGRESS.md", "global/assets", "exports", "logs",
                "chapters/001", "chapters/002", "chapters/003"]:
        assert (proj / rel).exists(), rel

    meta = json.loads((proj / "project.json").read_text(encoding="utf-8"))
    assert meta["title"] == "测试书"
    assert meta["video"]["orientation"] == "landscape" and meta["gear"] == "G0"   # 默认横屏
    assert meta["target_chapter_duration_sec"] == 90
    assert meta["source_fingerprint"] == result.fingerprint

    index = yaml.safe_load((proj / "chapters" / "index.yaml").read_text(encoding="utf-8"))
    assert [c["no"] for c in index["chapters"]] == [1, 2, 3]
    assert all(c["status"] == "pending" and c["selected"] for c in index["chapters"])

    chars = json.loads((proj / "global" / "characters.json").read_text(encoding="utf-8"))
    assert len(chars["characters"]) == 2


def test_chapter_selection_is_persisted(tmp_path, export_dir):
    result = import_source(export_dir)
    proj = ps.create_project(tmp_path / "out", result, chapter_spec="2-3")
    index = yaml.safe_load((proj / "chapters" / "index.yaml").read_text(encoding="utf-8"))
    assert [c["selected"] for c in index["chapters"]] == [False, True, True]
    assert not (proj / "chapters" / "001").exists()


def test_unique_dir_name(tmp_path, export_dir):
    result = import_source(export_dir)
    t = datetime(2026, 11, 1, 10, 0, 0)
    a = ps.create_project(tmp_path / "out", result, title="书", now=t)
    b = ps.create_project(tmp_path / "out", result, title="书", now=t)
    assert a != b and b.name.endswith("_2")


def test_refuses_blocked_result(tmp_path):
    import pytest
    result = import_source(tmp_path / "missing")
    with pytest.raises(ValueError):
        ps.create_project(tmp_path / "out", result)


def test_progress_is_append_only(tmp_path, export_dir):
    result = import_source(export_dir)
    proj = ps.create_project(tmp_path / "out", result, title="书")
    before = (proj / "PROGRESS.md").read_text(encoding="utf-8")
    ps.append_progress(proj, "- 用户要求：台词更口语化")
    after = (proj / "PROGRESS.md").read_text(encoding="utf-8")
    assert after.startswith(before) and "台词更口语化" in after


def test_atomic_write_leaves_no_tmp(tmp_path):
    target = tmp_path / "a.json"
    ps.write_json_atomic(target, {"x": 1})
    ps.write_json_atomic(target, {"x": 2})
    assert ps.read_json(target) == {"x": 2}
    assert [p.name for p in tmp_path.iterdir()] == ["a.json"]


def test_tool_api_dry_run_writes_nothing(tmp_path, export_dir):
    out = tmp_path / "out"
    res = tool_api.create_project_from_source(str(out), str(export_dir), dry_run=True, chapter_spec="1-2")
    assert res["ok"] and res["data"]["selected_chapters"] == [1, 2]
    assert not out.exists()


def test_tool_api_blocked_source_returns_error(tmp_path):
    res = tool_api.create_project_from_source(str(tmp_path / "out"), str(tmp_path / "nope"))
    assert not res["ok"] and res["data"]["ok"] is False
    assert not (tmp_path / "out").exists()


def test_tool_api_bad_chapter_spec(tmp_path, export_dir):
    res = tool_api.create_project_from_source(str(tmp_path / "out"), str(export_dir), chapter_spec="99")
    assert not res["ok"] and "章节不存在" in res["error"]["message"]


def test_list_and_drift(tmp_path, export_dir):
    out = tmp_path / "out"
    res = tool_api.create_project_from_source(str(out), str(export_dir), title="书")
    assert res["ok"]
    rows = tool_api.list_projects(str(out))["data"]
    assert len(rows) == 1 and rows[0]["chapters"] == 3 and rows[0]["characters"] == 2
    assert tool_api.check_source_drift(res["data"]["project_dir"])["data"]["changed"] is False
    (export_dir / "正文" / "第001章 测试章1.md").write_text("# 第1章\n" + "改" * 400, encoding="utf-8")
    assert tool_api.check_source_drift(res["data"]["project_dir"])["data"]["changed"] is True


def test_health():
    res = tool_api.project_health()
    assert res["ok"] and res["data"]["ok"]
