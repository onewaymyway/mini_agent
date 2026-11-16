"""人物档案的流程修正：齐全门禁步骤；大纲先行模式下章节简述依赖门禁；建档步骤覆盖目录中的全部人设卡。"""
from types import SimpleNamespace

import pytest

from novel_writer import character_sync as CSY
from novel_writer import chapter_ops as CO
from novel_writer import pipeline as PL
from novel_writer import steps as S
from novel_writer.brief import MODE_OUTLINE_FIRST


def test_gate_step_depends_on_refresh_and_uses_its_handler():
    g = S.characters_complete_step()
    assert g.id == "characters_complete" and g.handler == "characters_complete"
    assert g.deps == ("characters_refresh",)


def test_build_step_covers_every_card_on_disk():
    b = S.build_characters_step(["林远"])
    assert "全部人设卡" in b.instructions and "以目录为准" in b.instructions


def test_gate_handler_passes_with_summary(monkeypatch):
    monkeypatch.setattr(CSY, "check", lambda ws: {"cards": ["林远"], "store_count": 1})
    out = CO.h_characters_complete(None, S.characters_complete_step(), {})
    assert "1 张人设卡均已建档" in out["summary"]


def test_gate_handler_raises_upstream_error_when_profiles_missing(monkeypatch):
    def boom(ws):
        raise CSY.CharacterSyncError("人物档案未同步", upstream_step=CSY.BUILD_STEP)
    monkeypatch.setattr(CSY, "check", boom)
    with pytest.raises(CSY.CharacterSyncError) as ei:
        CO.h_characters_complete(None, S.characters_complete_step(), {})
    assert ei.value.upstream_step == "build_characters"


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    from novel_writer import config
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    return tmp_path


def test_outline_plan_routes_briefs_through_the_gate(isolated):
    from novel_writer import tool_api
    from novel_writer.workspace import Workspace
    r = tool_api.create_novel("门禁计划测试", novels_dir=str(isolated / "novels"),
                              index_file=str(isolated / "shelf.json"), gear="G3",
                              total_chapters_planned=3, writing_mode=MODE_OUTLINE_FIRST)
    assert r["ok"], r
    ws = Workspace(r["data"]["workspace"])
    by_id = {s.id: s for s in PL.build_plan(ws)}
    assert "characters_complete" in by_id and by_id["characters_complete"].deps == ("characters_refresh",)
    briefs = [s for s in by_id.values() if s.id.startswith("brief_draft")]
    assert briefs, "大纲先行模式应生成章节简述步骤"
    assert briefs[0].deps == ("characters_complete",)      # 第一幕简述必须等门禁通过
