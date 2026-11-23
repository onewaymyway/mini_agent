"""看板：概念图页面（P2）在真实脚本执行下的冒烟测试。"""

import pytest

pytest.importorskip("streamlit")

from article_garden import domains  # noqa: E402
from test_ui_smoke import run_app, texts  # noqa: E402
from test_wiki import SPECS, Router, _lib, _seed  # noqa: E402
from article_garden.wiki import build  # noqa: E402


def test_graph_empty_guides_user(data_home):
    domains.create_domain("ai", "人工智能", "ai")
    at = run_app("graph", domain="ai")
    assert not at.exception
    assert "还没有概念" in texts(at)


def test_graph_shows_concepts_definition_and_relations(data_home):
    ctx = _lib(data_home)
    _seed(ctx, SPECS)
    build.build(ctx, Router(edge_pairs={frozenset(("检索增强生成", "向量数据库")): "part_of"}))
    at = run_app("graph", domain="ai")
    assert not at.exception, [e.value for e in at.exception]
    assert at.dataframe and any("检索增强生成" in str(v) for v in at.dataframe[0].value["概念"])
    assert any(s.label == "查看概念" for s in at.selectbox)
    assert len(at.tabs) == 3
