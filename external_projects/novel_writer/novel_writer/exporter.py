"""novel_writer/exporter.py — 全书合稿导出为 txt / docx / epub（M6）。

只依赖标准库（zipfile + xml 字符串），不引入 python-docx / ebooklib，避免新增依赖。
输入统一是「按章的 Markdown 文本列表」：以 `#` 开头的行视为标题，其余非空行为段落。
生成的文件结构是最小可用的：docx 含 document.xml；epub 为 EPUB 3（mimetype + nav + 每章一页）。
"""

from __future__ import annotations

import uuid
import zipfile
from pathlib import Path
from typing import List, Sequence, Tuple
from xml.sax.saxutils import escape

FORMATS = ("md", "txt", "docx", "epub")


def _blocks(markdown: str) -> List[Tuple[str, str]]:
    """返回 [(kind, text)]，kind 为 'h'（标题）或 'p'（段落）。"""
    out: List[Tuple[str, str]] = []
    for raw in markdown.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            out.append(("h", line.lstrip("#").strip()))
        else:
            out.append(("p", line))
    return out


def to_txt(parts: Sequence[str]) -> str:
    lines: List[str] = []
    for part in parts:
        for kind, text in _blocks(part):
            lines.append(text if kind == "p" else f"【{text}】")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_docx(path: Path, title: str, parts: Sequence[str]) -> None:
    body: List[str] = []

    def para(text: str, bold: bool = False, size: int = 0) -> None:
        run_props = ""
        if bold or size:
            run_props = "<w:rPr>" + ("<w:b/>" if bold else "") + (f'<w:sz w:val="{size}"/>' if size else "") + "</w:rPr>"
        body.append(f"<w:p><w:r>{run_props}<w:t xml:space=\"preserve\">{escape(text)}</w:t></w:r></w:p>")

    para(title, bold=True, size=36)
    for part in parts:
        for kind, text in _blocks(part):
            if kind == "h":
                para(text, bold=True, size=28)
            else:
                para(text)
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>" + "".join(body) + "<w:sectPr/></w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/></Relationships>'
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)


def write_epub(path: Path, title: str, parts: Sequence[str], language: str = "zh-CN") -> None:
    book_id = f"urn:uuid:{uuid.uuid4()}"
    pages = []
    for i, part in enumerate(parts, start=1):
        inner = []
        for kind, text in _blocks(part):
            inner.append(f"<h2>{escape(text)}</h2>" if kind == "h" else f"<p>{escape(text)}</p>")
        pages.append((f"chapter{i:03d}.xhtml", "".join(inner) or "<p></p>"))

    def xhtml(head: str, body: str) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<html xmlns="http://www.w3.org/1999/xhtml" lang="{language}" xml:lang="{language}">'
            f"<head><meta charset=\"utf-8\"/><title>{escape(head)}</title></head><body>{body}</body></html>"
        )

    nav_items = "".join(f'<li><a href="{name}">第{i}章</a></li>' for i, (name, _) in enumerate(pages, start=1))
    nav = xhtml(title, f'<nav epub:type="toc" xmlns:epub="http://www.idpf.org/2007/ops"><h1>{escape(title)}</h1>'
                       f"<ol>{nav_items}</ol></nav>")
    manifest = "".join(
        f'<item id="p{i}" href="{name}" media-type="application/xhtml+xml"/>' for i, (name, _) in enumerate(pages, 1))
    spine = "".join(f'<itemref idref="p{i}"/>' for i in range(1, len(pages) + 1))
    opf = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bid">'
        f'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="bid">{book_id}</dc:identifier>'
        f"<dc:title>{escape(title)}</dc:title><dc:language>{language}</dc:language></metadata>"
        '<manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        f"{manifest}</manifest><spine>{spine}</spine></package>"
    )
    container = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
        "</rootfiles></container>"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        # mimetype 必须是第一个条目且不压缩（EPUB 规范）
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", container)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/nav.xhtml", nav)
        for name, inner in pages:
            z.writestr(f"OEBPS/{name}", xhtml(title, inner))
