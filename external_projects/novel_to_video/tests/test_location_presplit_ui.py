"""地点细化 L4 界面：全书素材页的区域预拆解面板（AppTest，替换模型调用）。"""
import json
from pathlib import Path

from streamlit.testing.v1 import AppTest

from conftest import build_export
from novel_to_video import tool_api

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def test_assets_page_presplit_propose_and_confirm(tmp_path, monkeypatch):
    src = build_export(tmp_path / "src")
    out = tmp_path / "out"
    res = tool_api.create_project_from_source(str(out), str(src), title="书")
    proj = Path(res["data"]["project_dir"])
    area = next(l for l in json.loads((proj / "global" / "locations.json").read_text(encoding="utf-8"))["locations"]
                if (l.get("level") or "area") == "area")

    calls = {"propose": 0, "confirm": []}

    def fake_propose(project_dir, area_id, **kw):
        calls["propose"] += 1
        return {"ok": True, "data": {"area_id": area_id, "candidates": [
            {"name": "市政厅办公室", "spot_kind": "interior", "reason": "多次对话"},
            {"name": "东三街", "spot_kind": "exterior", "reason": "追逐"}]}, "error": None}

    def fake_confirm(project_dir, area_id, spots, **kw):
        calls["confirm"] = [s["name"] for s in spots]
        return {"ok": True, "data": {"added": ["loc_99"] * len(spots)}, "error": None}

    monkeypatch.setattr(tool_api, "presplit_propose", fake_propose)
    monkeypatch.setattr(tool_api, "presplit_confirm", fake_confirm)

    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.radio(key="nav").set_value("🎭 全书素材").run()
    at.text_input(key="assets_root").input(str(out)).run()
    assert not at.exception
    assert any("区域预拆解" in m.value for m in at.markdown)
    assert at.checkbox(key="presplit_on").value is True          # 默认开启

    at.button(key="presplit_propose").click().run()
    assert not at.exception
    assert calls["propose"] == 1
    assert at.checkbox(key=f"presplit_pick_{area['id']}_0") is not None

    at.button(key="presplit_confirm").click().run()
    assert not at.exception
    assert calls["confirm"] == ["市政厅办公室", "东三街"]
    assert any("已写入 2 个场景点" in s.value for s in at.success)


def test_assets_page_presplit_toggle_off_hides_actions(tmp_path):
    src = build_export(tmp_path / "src")
    out = tmp_path / "out"
    tool_api.create_project_from_source(str(out), str(src), title="书")
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.radio(key="nav").set_value("🎭 全书素材").run()
    at.text_input(key="assets_root").input(str(out)).run()
    at.checkbox(key="presplit_on").uncheck().run()
    assert not at.exception
    assert any("区域预拆解已关闭" in i.value for i in at.info)
    assert not any(b.key == "presplit_propose" for b in at.button)
