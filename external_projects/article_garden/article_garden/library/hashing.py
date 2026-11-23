"""hashing.py — 文章内容哈希。

哈希基于“去掉所有空白后的正文”：换行符、缩进、空行的差异不算内容变化，
因此同一篇文章分别以 CRLF / LF 保存、或仅调整空行，都会被识别为重复或未变。
"""

from __future__ import annotations

import hashlib
import re

_WS = re.compile(r"\s+")


def normalize(body: str) -> str:
    return _WS.sub("", body or "")


def content_hash(body: str) -> str:
    return hashlib.sha256(normalize(body).encode("utf-8")).hexdigest()[:16]
