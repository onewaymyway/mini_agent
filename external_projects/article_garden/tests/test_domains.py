import zipfile

import pytest

from article_garden import domains


def test_create_lists_and_layout(data_home):
    ctx = domains.create_domain("ai", "人工智能", "ai")
    assert ctx.charter_path.is_file()
    for d in domains.LAYOUT_DIRS:
        assert ctx.path(d).is_dir()
    rows = domains.list_domains()
    assert [r["id"] for r in rows] == ["ai"] and rows[0]["name"] == "人工智能" and rows[0]["status"] == "active"
    charter, warns = ctx.load_charter()
    assert warns == [] and len(charter["scope"]["in"]) == 10 and charter["domain"] == "人工智能"


@pytest.mark.parametrize("bad", ["", "A", "1ai", "a", "ai ai", "../x", "a" * 33, "中文"])
def test_invalid_ids_rejected(bad):
    with pytest.raises(domains.DomainError):
        domains.create_domain(bad, "x")


def test_duplicate_and_empty_name_rejected():
    domains.create_domain("ai", "AI")
    with pytest.raises(domains.DomainError):
        domains.create_domain("ai", "另一个")
    with pytest.raises(domains.DomainError):
        domains.create_domain("fin", "  ")


def test_unregistered_directory_is_not_overwritten(data_home):
    (data_home / "domains" / "ghost").mkdir(parents=True)
    with pytest.raises(domains.DomainError):
        domains.create_domain("ghost", "幽灵")


def test_isolation_between_domains(data_home):
    a = domains.create_domain("a1", "库A", "ai")
    b = domains.create_domain("b1", "库B")
    (a.imported_dir / "same.md").write_text("A 的文章", encoding="utf-8")
    (b.imported_dir / "same.md").write_text("B 的文章", encoding="utf-8")
    assert a.article_count() == 1 and b.article_count() == 1
    assert (a.imported_dir / "same.md").read_text(encoding="utf-8") == "A 的文章"
    assert (b.imported_dir / "same.md").read_text(encoding="utf-8") == "B 的文章"
    # 章程互不影响
    assert a.load_charter()[0]["scope"]["in"] and not b.load_charter()[0]["scope"]["in"]


def test_path_cannot_escape_domain_root():
    ctx = domains.create_domain("a1", "库A")
    domains.create_domain("b1", "库B")
    for parts in (("..", "b1", "charter.yaml"), ("..", "..", "etc"), ("/etc/passwd",)):
        with pytest.raises(domains.DomainError):
            ctx.path(*parts)
    assert ctx.path("library", "imported").is_dir()


def test_context_for_unknown_domain_raises():
    with pytest.raises(domains.DomainError):
        domains.DomainContext("nope")


def test_rename_keeps_id_and_directory():
    ctx = domains.create_domain("ai", "旧名")
    domains.rename_domain("ai", "新名")
    assert domains.get_domain("ai")["name"] == "新名" and ctx.root.is_dir()
    with pytest.raises(domains.DomainError):
        domains.rename_domain("ai", " ")


def test_archive_hides_but_keeps_data_and_restore():
    ctx = domains.create_domain("ai", "AI")
    (ctx.imported_dir / "a.md").write_text("x", encoding="utf-8")
    domains.archive_domain("ai")
    assert domains.list_domains() == []
    assert [d["id"] for d in domains.list_domains(include_archived=True)] == ["ai"]
    assert (ctx.imported_dir / "a.md").is_file()
    domains.restore_domain("ai")
    assert [d["id"] for d in domains.list_domains()] == ["ai"]


def test_delete_requires_exact_name_and_makes_backup(data_home):
    ctx = domains.create_domain("ai", "人工智能")
    (ctx.imported_dir / "a.md").write_text("内容", encoding="utf-8")
    with pytest.raises(domains.DomainError):
        domains.delete_domain("ai", "错误名称")
    assert ctx.root.is_dir()  # 名称不符：什么都没发生
    backup = domains.delete_domain("ai", "人工智能")
    assert not ctx.root.exists() and domains.list_domains(include_archived=True) == []
    with zipfile.ZipFile(backup) as zf:
        assert any(n.endswith("library/imported/a.md") for n in zf.namelist())


def test_copy_charter_from_other_domain_without_data():
    src = domains.create_domain("ai", "AI", "ai")
    (src.imported_dir / "a.md").write_text("x", encoding="utf-8")
    dst = domains.create_domain("ai2", "AI副本", copy_charter_from="ai")
    assert dst.load_charter()[0]["scope"]["in"] == src.load_charter()[0]["scope"]["in"]
    assert dst.article_count() == 0
    assert domains.get_domain("ai2")["template"] == "copy:ai"
    with pytest.raises(domains.DomainError):
        domains.create_domain("x1", "X", copy_charter_from="missing")


def test_unknown_template_rejected_and_nothing_created(data_home):
    with pytest.raises(KeyError):
        domains.create_domain("ai", "AI", "no_such_template")
    assert domains.list_domains(include_archived=True) == []


def test_corrupt_registry_raises_clear_error_and_is_not_overwritten(data_home):
    data_home.mkdir(parents=True)
    reg = data_home / "domains.json"
    reg.write_text("{not json", encoding="utf-8")
    with pytest.raises(domains.DomainError, match="注册表无法解析"):
        domains.list_domains()
    with pytest.raises(domains.DomainError):
        domains.create_domain("ai", "AI")
    assert reg.read_text(encoding="utf-8") == "{not json"   # 原文件原样保留
