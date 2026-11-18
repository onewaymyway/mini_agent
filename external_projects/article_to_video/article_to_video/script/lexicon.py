"""script/lexicon.py — 发音词典：原词 → 朗读写法。只影响配音（tts），字幕仍显示原写法。

存放在项目目录 lexicon.json（设计文档写作 lexicon.yaml；为免新增依赖改用 JSON，内容相同）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict

from ..project_store import read_json, write_json_atomic

LEXICON_FILE = "lexicon.json"


def load_lexicon(project_dir: Path) -> Dict[str, str]:
    p = Path(project_dir) / LEXICON_FILE
    return {str(k): str(v) for k, v in read_json(p).items()} if p.is_file() else {}


def save_lexicon(project_dir: Path, mapping: Dict[str, str]) -> Dict[str, str]:
    clean = {k.strip(): v.strip() for k, v in mapping.items() if k.strip() and v.strip()}
    write_json_atomic(Path(project_dir) / LEXICON_FILE, clean)
    return clean


def parse_lexicon_text(text: str) -> Dict[str, str]:
    """每行一条：原词=读法（支持中英文等号）。空行与无等号的行忽略。"""
    out: Dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r"^\s*(.+?)\s*[=＝]\s*(.+?)\s*$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def apply_lexicon(text: str, lexicon: Dict[str, str]) -> "str | None":
    """返回替换后的朗读文本；没有任何替换时返回 None（tts 字段留空）。
    较长的词先替换，避免短词抢先匹配。"""
    out = text
    for word in sorted(lexicon, key=len, reverse=True):
        if word and word in out:
            out = out.replace(word, lexicon[word])
    return out if out != text else None
