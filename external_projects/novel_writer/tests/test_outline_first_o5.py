"""大纲先行 O5：向导开放、策略创建、写作页简述查看数据。"""

import ast
import sys
from pathlib import Path

from novel_writer import brief_sync as BSY
from novel_writer import tool_api
from novel_writer.workspace import Workspace

sys.path.insert(0, str(Path(__file__).parent))
from test_outline_first_o3 import isolated, ws  # noqa: E402,F401
from test_outline_first_o4 import _edit, _passed_check  # noqa: E402

UI = Path(__file__).resolve().parent.parent / "ui"


def _create(tmp_path, **kw):
    r = tool_api.create_novel("o5", novels_dir=str(tmp_path / "n2"), index_file=str(tmp_path / "s2.json"), **kw)
    assert r["ok"], r
    return Workspace(r["data"]["workspace"])


def test_create_outline_first_with_policy(tmp_path, isolated):
    w = _create(tmp_path, writing_mode="outline_first", brief_change_policy="auto_rewrite")
    s = w.settings()
    assert s["writing_mode"] == "outline_first" and s["brief_change_policy"] == "auto_rewrite"


def test_create_defaults_unchanged(tmp_path, isolated):
    s = _create(tmp_path).settings()
    assert s["writing_mode"] == "direct" and s["brief_change_policy"] == "ask"


def test_wizard_no_longer_forces_direct():
    src = (UI / "wizard.py").read_text(encoding="utf-8")
    ast.parse(src)
    assert 'writing_mode="direct"' not in src and "开发中" not in src
    assert "outline_first" in src and "brief_change_policy" in src


def test_ui_files_parse():
    for name in ("wizard.py", "briefs.py", "writing.py"):
        ast.parse((UI / name).read_text(encoding="utf-8"))


def test_direct_mode_has_no_brief_view(tmp_path, isolated):
    assert BSY.chapter_brief_view(_create(tmp_path), 1) is None


def test_brief_view_shows_brief_deviations_and_change_flag(ws):
    _passed_check(ws)
    v = BSY.chapter_brief_view(ws, 1)
    assert v["brief"]["chapter"] == 1 and v["changed"] is False
    _edit(ws, 1)
    v = BSY.chapter_brief_view(ws, 1)
    assert v["changed"] is True and v["diff"]


def test_brief_view_missing_brief_is_none(ws):
    assert BSY.chapter_brief_view(ws, 99) is None
