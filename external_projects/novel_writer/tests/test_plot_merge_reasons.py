"""伏笔合并读取增量失败时，必须给出具体原因（不存在 / 格式不对），并能接受带 BOM 或代码围栏的合法增量。"""

from __future__ import annotations

import pytest

from novel_writer import chapter_ops as C
from novel_writer import plot_store as PS
from novel_writer import steps as S
from novel_writer import config, tool_api
from novel_writer.workspace import Workspace


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    yield tmp_path


def _ws(tmp_path) -> Workspace:
    r = tool_api.create_novel("合并", novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"),
                              gear="G3", total_chapters_planned=2)
    ws = Workspace(r["data"]["workspace"])
    PS.write_store(ws.path("plot_items_dir"), {"foreshadowing": []})   # 空账本
    return ws


def _step(n=1):
    return S.StepDef(id=f"ch{n:02d}.plot_merge", title="t", stage="p2", category=S.CAT_CHAPTER_REVIEW,
                     executor=S.EXECUTOR_PYTHON, handler="plot_merge", chapter=n)


def test_missing_delta_says_file_does_not_exist(tmp_path):
    ws = _ws(tmp_path)
    with pytest.raises(C.ChapterOpError) as e:
        C.h_plot_merge(ws, _step(), {})
    assert "不存在" in str(e.value)


def test_invalid_json_delta_reports_the_json_error(tmp_path):
    ws = _ws(tmp_path)
    p = ws.path("plot_delta", nn=1)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"planted": [', encoding="utf-8")
    with pytest.raises(C.ChapterOpError) as e:
        C.h_plot_merge(ws, _step(), {})
    assert "不是合法 JSON" in str(e.value)


def test_delta_with_bom_and_fence_is_accepted(tmp_path):
    ws = _ws(tmp_path)
    p = ws.path("plot_delta", nn=1)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\xef\xbb\xbf```json\n" + b'{"planted": [], "advanced": [], "resolved": []}' + b"\n```\n")
    res = C.h_plot_merge(ws, _step(), {})
    assert "伏笔已合并" in res["summary"]
