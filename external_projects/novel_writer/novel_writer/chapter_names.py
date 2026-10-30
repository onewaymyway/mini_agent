"""novel_writer/chapter_names.py — 章节文件名：去掉标题里重复的章节号（纯函数，无 I/O）。

规范文件名为「第NNN章 标题.md」（超过 99 章为三位，否则两位）。历史上出现过的重复写法：
「第058章 第58章 第五十八章 复仇计划.md」「第064章 第六十四章 证据确凿.md」——章节号出现了三次。
normalize_chapter_title 会反复剥掉开头的「第…章」，只保留真正的章名。
"""

from __future__ import annotations

import re
from typing import Optional

_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}

# 开头的一个章节号：「第58章」「第058章」「第五十八章」，后面可跟空格、下划线、分隔符
_CHAPTER_PREFIX = re.compile(r"^第\s*([0-9]+|[零〇一二两三四五六七八九十百千]+)\s*章[\s_\-—：:]*")
_FILE_PREFIX = re.compile(r"^第\s*([0-9]+|[零〇一二两三四五六七八九十百千]+)\s*章")


def cn_to_int(text: str) -> Optional[int]:
    """中文数字转整数（支持到千位，如「五十八」「一百零三」「六十四」）。无法解析返回 None。"""
    if not text:
        return None
    total, section, number = 0, 0, 0
    for ch in text:
        if ch in _CN_DIGITS:
            number = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            unit = _CN_UNITS[ch]
            section += (number or 1) * unit
            number = 0
        else:
            return None
    total = section + number
    return total if total > 0 else None


def _num(token: str) -> Optional[int]:
    return int(token) if token.isdigit() else cn_to_int(token)


def chapter_number_from_name(name: str) -> Optional[int]:
    """从文件名（或标题）开头读取章节号；读不到返回 None。"""
    m = _FILE_PREFIX.match(name.strip())
    return _num(m.group(1)) if m else None


def normalize_chapter_title(raw: str) -> str:
    """剥掉开头所有重复的章节号，返回章名。

    「第058章 第58章 第五十八章 复仇计划」→「复仇计划」
    「第065章 正面交锋」→「正面交锋」
    """
    s = str(raw or "").strip()
    while True:
        m = _CHAPTER_PREFIX.match(s)
        if not m:
            break
        s = s[m.end():]
    return s.strip(" _")
