import json
import subprocess
import sys
from pathlib import Path

import pytest

from article_to_video.importer import ArticleImportError, import_article
from article_to_video.project_store import (
    list_projects,
    load_project,
    normalize_branding,
    normalize_settings,
    update_branding,
)
from conftest import PROJECT_DIR, REFERENCE_ARTICLE


@pytest.fixture
def reference_text():
    return REFERENCE_ARTICLE.read_text(encoding="utf-8")


def test_reference_article_counts(reference_text):
    from article_to_video.source.md_parse import parse_markdown
    from article_to_video.importer import summarize

    p = parse_markdown(reference_text)
    s = summarize(p, reference_text)
    # 与设计文档第 3 节的统计一致
    assert len(p["sections"]) == 52
    assert s["blocks_by_kind"]["code"] == 35
    assert s["chinese_chars"] == 7022
    assert s["est_full_read_minutes"] == 27.9
    assert s["tables"] == 2
    assert s["formulas"] == 16
    # 代码块几乎全是可解析的链；存在回路（如 “超级AI … → 超级AI”）
    assert s["code_shapes"].get("chain", 0) >= 27
    assert s["code_loops"] >= 1


def test_import_creates_project_with_expected_files(tmp_path, reference_text):
    res = import_article(tmp_path, source_text=reference_text, source_name="ref.md",
                         author="测试作者", branding={"watermark": {"text": "测试水印"}})
    pdir = Path(res["project_dir"])
    for rel in ("source/article.md", "source/blocks.json", "source/meta.json", "project.json", "progress.log"):
        assert (pdir / rel).is_file(), rel
    project = load_project(pdir)
    assert project["title"] == "从康波周期到超级AI：我们正在进入第六次技术革命，还是走向一个“后康波时代”？"
    assert project["stage"] == "P0_imported"
    assert project["branding"]["author"] == "测试作者"
    assert project["author_source"] == "input"
    from article_to_video.app_settings import load_effective_project  # 生效值（原始文件只保存与默认不同的字段）
    eff = load_effective_project(pdir)["branding"]
    assert eff["watermark"]["enabled"] is True
    assert eff["watermark"]["text"] == "测试水印"


def test_author_missing_warns_and_is_not_guessed(tmp_path, reference_text):
    res = import_article(tmp_path, source_text=reference_text)
    assert res["author"] == "" and res["author_source"] == "none"
    assert any("未填写作者名" in w for w in res["warnings"])


def test_author_read_from_front_matter_when_input_blank(tmp_path):
    res = import_article(tmp_path, source_text="---\nauthor: 甲\n---\n# 标题\n\n正文", author="")
    assert res["author"] == "甲" and res["author_source"] == "front_matter"


def test_title_falls_back_to_h1_then_filename(tmp_path):
    assert import_article(tmp_path, source_text="# 一个标题\n\n正文")["title"] == "一个标题"
    assert import_article(tmp_path, source_text="没有标题的正文", source_name="my-note.md")["title"] == "my-note"


def test_same_title_gets_unique_directory(tmp_path):
    a = import_article(tmp_path, source_text="# 同名\n\nA")
    b = import_article(tmp_path, source_text="# 同名\n\nB")
    assert Path(a["project_dir"]).name == "同名"
    assert Path(b["project_dir"]).name == "同名_2"


def test_empty_text_is_rejected(tmp_path):
    with pytest.raises(ArticleImportError):
        import_article(tmp_path, source_text="   \n  ")


def test_list_projects_returns_imported(tmp_path, reference_text):
    import_article(tmp_path, source_text=reference_text, author="作者甲")
    items = list_projects(tmp_path)
    assert len(items) == 1
    assert items[0]["author"] == "作者甲"
    assert items[0]["stage"] == "P0_imported"


def test_watermark_disabled_without_text():
    b, warnings = normalize_branding({"watermark": {"enabled": True, "text": "  "}})
    assert b["watermark"]["enabled"] is False
    assert any("水印未填写文字" in w for w in warnings)


def test_invalid_watermark_values_fall_back():
    b, warnings = normalize_branding({"watermark": {"text": "x", "position": "upside_down",
                                                    "mode": "spin", "opacity": 9}})
    assert b["watermark"]["position"] == "top_right"
    assert b["watermark"]["mode"] == "static"
    assert b["watermark"]["opacity"] == 1.0
    assert len(warnings) == 2


def test_watermark_text_is_capped():
    b, _ = normalize_branding({"watermark": {"text": "字" * 200}})
    assert len(b["watermark"]["text"]) == 60


def test_invalid_settings_fall_back_with_warning():
    s, warnings = normalize_settings({"mode": "movie", "target_minutes": 999})
    assert s["mode"] == "condensed"
    assert s["target_minutes"] == 60.0
    assert warnings


def test_update_branding_keeps_source_and_blocks_unchanged(tmp_path, reference_text):
    res = import_article(tmp_path, source_text=reference_text, author="旧名")
    pdir = Path(res["project_dir"])
    before = {p: (pdir / p).read_bytes() for p in ("source/article.md", "source/blocks.json", "source/meta.json")}
    update_branding(pdir, {"author": "新名", "watermark": {"text": "新水印", "position": "bottom_left"}})
    project = load_project(pdir)
    assert project["branding"]["author"] == "新名"
    assert project["branding"]["watermark"]["text"] == "新水印"
    assert project["branding"]["watermark"]["position"] == "bottom_left"
    for p, data in before.items():
        assert (pdir / p).read_bytes() == data, p


def test_cli_import_and_list(tmp_path):
    src = tmp_path / "article.md"
    src.write_text("# 命令行导入\n\n正文一段。", encoding="utf-8")
    root = tmp_path / "home"
    env_args = [sys.executable, str(PROJECT_DIR / "entrypoints" / "import_article.py"),
                "--file", str(src), "--author", "CLI作者", "--projects-root", str(root),
                "--watermark-text", "水印", "--watermark-position", "center"]
    out = subprocess.run(env_args, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    payload = json.loads(out.stdout)
    assert payload["ok"] is True and payload["author"] == "CLI作者"

    out2 = subprocess.run([sys.executable, str(PROJECT_DIR / "entrypoints" / "list_projects.py")],
                          capture_output=True, text=True, encoding="utf-8", timeout=60,
                          env={**__import__("os").environ, "ARTICLE_TO_VIDEO_HOME": str(root)})
    assert out2.returncode == 0, out2.stderr
    listed = json.loads(out2.stdout)["projects"]
    assert [p["title"] for p in listed] == ["命令行导入"]
