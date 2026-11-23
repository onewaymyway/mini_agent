"""P1 阶段 A：导入、哈希、索引、去重与入口脚本。"""

import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from article_garden import domains
from article_garden.library import catalog, front_matter, hashing, importer
from tests.fakes import article

ROOT = Path(__file__).resolve().parent.parent


def _ctx(domain_id="ai"):
    domains.create_domain(domain_id, "人工智能", "ai")
    return domains.DomainContext(domain_id)


def test_front_matter_roundtrip_and_bad_yaml_is_body():
    text = front_matter.dump({"id": "a-0001", "title": "标题"}, "正文一段")
    meta, body = front_matter.split(text)
    assert meta == {"id": "a-0001", "title": "标题"} and body.strip() == "正文一段"
    meta, body = front_matter.split("---\na: [x\n---\n正文")
    assert meta == {} and body.startswith("---")          # 坏 front matter：整段当正文，不抛异常


def test_hash_ignores_whitespace_but_not_words():
    assert hashing.content_hash("向量 数据库\n\n是") == hashing.content_hash("向量数据库是")
    assert hashing.content_hash("向量数据库") != hashing.content_hash("向量存储库")


def test_collect_bytes_title_from_front_matter_and_heading():
    md = front_matter.dump({"title": "元信息标题"}, article("正文标题")).encode("utf-8")
    c = importer.collect([("a.md", md), ("b.md", article("二号").encode("utf-8"))])
    assert [x.title for x in c] == ["元信息标题", "二号"]
    assert all(x.chars >= importer.MIN_CHARS for x in c)


def test_gbk_file_is_decoded():
    raw = article("中文旧编码").encode("gb18030")
    (c,) = importer.collect([("old.md", raw)])
    assert c.error == "" and c.title == "中文旧编码"


def test_zip_expands_text_members_and_skips_noise():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("docs/one.md", article("甲"))
        zf.writestr("docs/two.txt", article("乙"))
        zf.writestr("docs/pic.png", b"\x89PNG")
        zf.writestr("__MACOSX/docs/._one.md", b"junk")
    cands = importer.collect([("pack.zip", buf.getvalue())])
    assert sorted(c.source for c in cands) == ["pack.zip!docs/one.md", "pack.zip!docs/two.txt"]


def test_unsupported_and_short_files_are_skipped(data_home):
    ctx = _ctx()
    rows = importer.preview(ctx, importer.collect([("x.pdf", b"%PDF"), ("tiny.md", b"# hi\n\nshort")]))
    assert [r["action"] for r in rows] == ["skipped", "skipped"]
    assert "不支持" in rows[0]["reason"] and "不足" in rows[1]["reason"]


def test_preview_then_apply_assigns_ids_and_leaves_source_untouched(data_home, tmp_path):
    ctx = _ctx()
    src = tmp_path / "src"
    src.mkdir()
    f1 = src / "a.md"
    f1.write_text(article("第一篇"), encoding="utf-8")
    (src / "b.md").write_text(article("第二篇", marker="不同"), encoding="utf-8")
    before = {p.name: p.read_bytes() for p in src.iterdir()}

    cands = importer.collect_paths([str(src)])
    rows = importer.preview(ctx, cands)
    assert [r["action"] for r in rows] == ["new", "new"]
    assert catalog.list_articles(ctx) == []                       # 预览不写入

    res = importer.apply(ctx, cands)
    assert res["imported"] == ["a-0001", "a-0002"]
    assert {p.name: p.read_bytes() for p in src.iterdir()} == before   # 源文件字节不变
    meta, body = front_matter.split((ctx.imported_dir / "a-0001.md").read_text(encoding="utf-8"))
    assert meta["id"] == "a-0001" and meta["origin"] == "imported" and "第一篇" in meta["title"]
    assert "向量数据库" in body and not body.startswith("---")


def test_duplicates_across_library_and_within_batch(data_home):
    ctx = _ctx()
    importer.apply(ctx, importer.collect([("x.md", article("原文").encode())]))
    same_but_spaced = article("原文").replace("\n", "\r\n").replace("向量", "向 量")
    rows = importer.preview(ctx, importer.collect([("again.md", same_but_spaced.encode()),
                                                   ("n1.md", article("新").encode()),
                                                   ("n2.md", article("新").encode())]))
    assert rows[0]["action"] == "duplicate" and "a-0001" in rows[0]["reason"]
    assert rows[1]["action"] == "new" and rows[2]["action"] == "duplicate"
    assert "本次导入中已有" in rows[2]["reason"]


def test_missing_path_reported_not_raised(data_home):
    (c,) = importer.collect_paths([str(data_home / "nope")])
    assert "路径不存在" in c.error


def test_index_rebuilt_from_manually_placed_files(data_home):
    ctx = _ctx()
    (ctx.imported_dir / "hand.md").write_text(article("手放的文章"), encoding="utf-8")
    rows = catalog.list_articles(ctx)
    assert [r["id"] for r in rows] == ["a-0001"] and rows[0]["title"] == "手放的文章"
    importer.apply(ctx, importer.collect([("n.md", article("新文章", marker="新").encode())]))
    assert [r["id"] for r in catalog.list_articles(ctx)] == ["a-0001", "a-0002"]


def test_corrupt_index_raises_domain_error_and_keeps_file(data_home):
    ctx = _ctx()
    idx = ctx.path("library", "index.json")
    idx.write_text("{坏", encoding="utf-8")
    with pytest.raises(domains.DomainError, match="文章索引无法解析"):
        catalog.list_articles(ctx)
    assert idx.read_text(encoding="utf-8") == "{坏"


def test_cli_import_preview_then_apply(data_home, tmp_path):
    _ctx()
    f = tmp_path / "one.md"
    f.write_text(article("命令行导入"), encoding="utf-8")
    env = dict(os.environ, ARTICLE_GARDEN_HOME=str(data_home), PYTHONIOENCODING="utf-8")
    run = lambda *a: subprocess.run([sys.executable, str(ROOT / "entrypoints" / "import_library.py"), *a],
                                    cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
    r = run("ai", str(f))
    out = json.loads(r.stdout)
    assert r.returncode == 0 and out["applied"] is False and out["rows"][0]["action"] == "new"
    assert catalog.list_articles(domains.DomainContext("ai")) == []
    r = run("ai", str(f), "--apply")
    assert json.loads(r.stdout)["imported"] == ["a-0001"]
    assert run("nope", str(f)).returncode == 1
