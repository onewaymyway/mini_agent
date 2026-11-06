"""构思修订：修订方向不设长度上限；超长文本原样保存，并完整注入每一步的提示词。"""

import pytest

from novel_writer import concept_revision as CR
from novel_writer import config, tool_api
from novel_writer.workspace import Workspace


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")


def _long_direction(n_chars: int) -> str:
    unit = "把复仇主线改为救赎，并让主角在第三幕做出不同的选择。"
    text = (unit * (n_chars // len(unit) + 1))[:n_chars]
    return text + "【结尾标记】"


def test_validate_accepts_very_long_direction():
    text = _long_direction(50_000)
    out, scopes = CR.validate(text, ["theme"])
    assert out == text.strip()            # 原文完整，没有截断或拒绝
    assert scopes == ["theme"]


def test_empty_direction_still_rejected():
    with pytest.raises(CR.ConceptError, match="思路"):
        CR.validate("   ", ["theme"])


def test_long_direction_is_stored_in_full_and_injected_into_steps(tmp_path):
    r = tool_api.create_novel("长思路", novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"))
    ws_path = r["data"]["workspace"]
    text = _long_direction(20_000)
    res = tool_api.create_concept_revision(ws_path, text, ["theme", "world"])
    assert res["ok"], res
    ws = Workspace(ws_path)
    from novel_writer import ondemand as OD
    req = OD.list_requests(ws, kind="concept")[-1]
    assert req["direction"] == text.strip()            # 请求里保存的是全文

    steps = CR._scope_steps(ws, "theme", text.strip(), req["id"])
    assert steps and all("【结尾标记】" in (st.instructions or "") for st in steps)
