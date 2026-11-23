"""research/extract.py — 正文提取：trafilatura 优先（若已安装），否则标准库解析段落（方案 Q8）。

标准库回退会去掉 nav/header/footer/aside/script/style 等区域，只保留段落类文本。
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Tuple

SKIP_TAGS = {"script", "style", "nav", "header", "footer", "aside", "noscript", "form", "svg", "iframe"}
BLOCK_TAGS = {"p", "li", "h1", "h2", "h3", "h4", "td", "blockquote"}


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.in_title = False
        self.title = ""
        self.paras: list = []
        self.cur = None

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self.skip += 1
        elif tag == "title":
            self.in_title = True
        elif tag in BLOCK_TAGS and self.skip == 0:
            self._flush()
            self.cur = []

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
        elif tag == "title":
            self.in_title = False
        elif tag in BLOCK_TAGS:
            self._flush()

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif self.cur is not None and self.skip == 0:
            self.cur.append(data)

    def _flush(self):
        if self.cur is not None:
            text = " ".join("".join(self.cur).split())
            if text:
                self.paras.append(text)
            self.cur = None


def extract(html: str) -> Tuple[str, str]:
    """返回 (标题, 正文)；正文段落之间以空行分隔。"""
    try:  # 可选依赖
        import trafilatura  # type: ignore

        text = trafilatura.extract(html, include_comments=False, favor_precision=True) or ""
        meta = trafilatura.extract_metadata(html)
        title = (getattr(meta, "title", None) or "").strip() if meta else ""
        if text.strip():
            return title, text.strip()
    except ImportError:
        pass
    except Exception:  # noqa: BLE001 — 回退到标准库
        pass
    parser = _Parser()
    parser.feed(html or "")
    parser._flush()
    return " ".join(parser.title.split()), "\n\n".join(parser.paras)
