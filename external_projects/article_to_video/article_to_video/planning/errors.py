"""planning/errors.py — 规划阶段的错误类型。"""

from __future__ import annotations

from typing import Optional


class PlanError(RuntimeError):
    """规划失败。raw 为模型原始输出（若有），由调用方存档，便于排查。"""

    def __init__(self, message: str, raw: Optional[str] = None):
        super().__init__(message)
        self.raw = raw
