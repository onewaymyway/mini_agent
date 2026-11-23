"""wiki/validate_util.py — 归一键与子串判断（复用摘要校验的规则，避免两处各写一套）。"""

from __future__ import annotations

from ..digest import validate as _v


def key(s: str) -> str:
    return _v.squash(s).lower()


def appears(needle: str, haystack: str) -> bool:
    return _v.appears(needle, haystack)
