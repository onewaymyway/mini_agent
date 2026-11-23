"""看板：文章库页面（P1）在真实脚本执行下的冒烟测试。"""

import pytest

pytest.importorskip("streamlit")

from article_garden import domains  # noqa: E402
from article_garden.digest import runner  # noqa: E402
from article_garden.library import importer  # noqa: E402
from tests.fakes import FakeLLM, article  # noqa: E402
from test_ui_smoke import run_app, texts  # noqa: E402


def test_library_empty_shows_import_guidance(data_home):
    domains.create_domain("ai", "人工智能", "ai")
    at = run_app("library", domain="ai")
    assert not at.exception
    assert "库里还没有文章" in texts(at)


def test_library_lists_articles_with_digest_status(data_home):
    domains.create_domain("ai", "人工智能", "ai")
    ctx = domains.DomainContext("ai")
    importer.apply(ctx, importer.collect([("a.md", article("甲").encode()), ("b.md", article("乙", "x").encode())]))
    runner.run_digest(ctx, FakeLLM(), only=["a-0001"])
    at = run_app("library", domain="ai")
    assert not at.exception, [e.value for e in at.exception]
    table = at.dataframe[0].value
    statuses = dict(zip(table["编号"], table["摘要"]))
    assert statuses == {"a-0001": "已摘要", "a-0002": "未摘要"}
    assert any("开始摘要" in b.label for b in at.button)
    assert any(s.label == "查看文章" for s in at.selectbox)
