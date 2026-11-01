"""回归：目录名被转义（#U5c0f 形式）的导出包，创建项目后仍须能读取正文与人设卡。

根因记录：导入时的规范化副本位于临时目录并被删除，项目里没有保存，导致 source_text 与性别检索读到 0 章。
修复：创建项目时把规范化副本落盘到 source/normalized/，manifest 记录 working_dir，读取统一走 working_source_dir。
"""

import json
from pathlib import Path

import pytest

from conftest import build_export
from novel_to_video import chapter_io as cio
from novel_to_video import gender_review
from novel_to_video import project_store as ps
from novel_to_video import tool_api
from novel_to_video.name_normalize import ESCAPE_RE, decode_name


def _escape(name: str) -> str:
    return "".join(f"#U{ord(ch):04x}" if ord(ch) > 127 else ch for ch in name)


@pytest.fixture
def escaped_src(tmp_path):
    src = build_export(tmp_path / "src", chapters=(1, 2))
    # 正文里放入角色名与代词，使性别线索检索有可读的结果
    (src / "正文" / "第001章 测试章1.md").write_text(
        "# 第1章 测试章1\n\n林远走进房间，他父亲的声音还在耳边。\n", encoding="utf-8")
    # 把包含中文的目录与文件名整体转义，模拟压缩工具的转义行为
    root = tmp_path / "esc" / _escape(src.name)  # 保留外层导出目录（转义后）
    for path in sorted(src.rglob("*"), key=lambda p: len(p.parts)):
        rel = path.relative_to(src)
        target = root.joinpath(*[_escape(p) for p in rel.parts])
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
    return root


def test_escaped_export_detected_and_decoded(escaped_src):
    assert ESCAPE_RE.search(escaped_src.name)
    assert decode_name(escaped_src.name) == "小说_20261101_101309"


def test_project_keeps_normalized_copy_and_reads_chapter_text(escaped_src, tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_TO_VIDEO_PROVIDERS", "stub")
    res = tool_api.create_project_from_source(str(tmp_path / "out"), str(escaped_src), title="t")
    assert res["ok"], res
    proj = Path(res["data"]["project_dir"])
    man = json.loads((proj / "source" / "manifest.json").read_text(encoding="utf-8"))
    assert man["working_dir"] == "source/normalized"
    assert (proj / "source" / "normalized" / "正文").is_dir()
    txt = cio.source_text(proj, 1)
    assert "林远走进房间" in txt


def test_gender_evidence_reads_from_normalized_copy(escaped_src, tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_TO_VIDEO_PROVIDERS", "stub")
    res = tool_api.create_project_from_source(str(tmp_path / "out"), str(escaped_src), title="t")
    proj = Path(res["data"]["project_dir"])
    data = gender_review.suggest_all(proj)
    assert sum(v["evidence_total"] for v in data["suggestions"].values()) > 0


def test_plain_export_has_no_working_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_TO_VIDEO_PROVIDERS", "stub")
    src = build_export(tmp_path / "src", chapters=(1,))
    res = tool_api.create_project_from_source(str(tmp_path / "out"), str(src), title="t")
    proj = Path(res["data"]["project_dir"])
    man = json.loads((proj / "source" / "manifest.json").read_text(encoding="utf-8"))
    assert man["working_dir"] is None
    assert ps.working_source_dir(proj) == Path(man["source_dir"])
