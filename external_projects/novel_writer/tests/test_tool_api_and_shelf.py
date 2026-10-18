from novel_writer import shelf, tool_api


def test_create_default_location_and_list(tmp_path):
    novels = tmp_path / "novels"
    idx = tmp_path / "shelf.json"
    r = tool_api.create_novel("青云传", novels_dir=str(novels), index_file=str(idx), gear="G1", quality="draft")
    assert r["ok"], r
    assert (novels / r["data"]["workspace"].split("/")[-1]).is_dir()
    lst = tool_api.list_novels(novels_dir=str(novels), index_file=str(idx))
    assert lst["ok"] and len(lst["data"]["novels"]) == 1
    n = lst["data"]["novels"][0]
    assert n["title"] == "青云传" and n["gear"] == "G1" and n["quality"] == "draft"
    assert not idx.exists()  # 默认位置不写索引


def test_create_custom_location_registers_in_index(tmp_path):
    novels = tmp_path / "novels"
    idx = tmp_path / "shelf.json"
    custom = tmp_path / "elsewhere" / "我的书"
    r = tool_api.create_novel("我的书", workspace=str(custom), novels_dir=str(novels), index_file=str(idx))
    assert r["ok"]
    titles = [n["title"] for n in tool_api.list_novels(novels_dir=str(novels), index_file=str(idx))["data"]["novels"]]
    assert titles == ["我的书"]
    assert shelf.unregister(custom, index_file=idx) is True
    assert tool_api.list_novels(novels_dir=str(novels), index_file=str(idx))["data"]["novels"] == []


def test_errors_are_returned_not_raised(tmp_path):
    bad = tool_api.create_novel("???", novels_dir=str(tmp_path))
    assert not bad["ok"] and bad["error"]["type"] == "workspace_error"
    bad2 = tool_api.create_novel("书", novels_dir=str(tmp_path), gear="G9")
    assert not bad2["ok"] and bad2["error"]["type"] == "validation_error"
    assert not tool_api.get_novel(str(tmp_path / "nope"))["ok"]


def test_update_settings_merges_and_validates(tmp_path):
    r = tool_api.create_novel("书", novels_dir=str(tmp_path))
    ws = r["data"]["workspace"]
    ok = tool_api.update_settings(ws, {"gear": "G2", "budget": {"max_chapters_per_run": 5}})
    assert ok["ok"] and ok["data"]["gear"] == "G2"
    assert ok["data"]["budget"]["max_chapters_per_run"] == 5 and ok["data"]["budget"]["max_llm_calls_per_run"] == 60
    assert not tool_api.update_settings(ws, {"gear": "bad"})["ok"]
    assert tool_api.get_novel(ws)["data"]["settings"]["gear"] == "G2"


def test_broken_novel_does_not_break_shelf(tmp_path):
    r = tool_api.create_novel("书", novels_dir=str(tmp_path))
    ws = r["data"]["workspace"]
    from pathlib import Path
    (Path(ws) / "项目状态/workspace_config.json").write_text("{bad", encoding="utf-8")
    novels = tool_api.list_novels(novels_dir=str(tmp_path), index_file=str(tmp_path / "i.json"))["data"]["novels"]
    assert len(novels) == 1  # workspace_config 损坏时 recover 仍可工作（回落到 manifest 标题）


def test_project_health_ok(tmp_path, monkeypatch):
    from novel_writer import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "REPORTS_DIR", tmp_path / "reports")
    r = tool_api.project_health()
    assert r["ok"] and r["data"]["ok"], r
    assert r["data"]["skills_total"] == 24
