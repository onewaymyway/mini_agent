"""pipeline/budget.py — 预算账本：生图调用数与总耗时。超限抛出 BudgetExceeded，流水线停止。

账本保存在 parts 之外的 budget.json，跨次运行累计。生图次数只统计真正调用 provider 的次数，
缓存复用不计入。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, Optional

from ..project_store import read_json, write_json_atomic


class BudgetExceeded(RuntimeError):
    pass


class Ledger:
    def __init__(self, project_dir: Path, *, max_images: Optional[int] = None,
                 max_minutes: Optional[float] = None):
        self.path = Path(project_dir) / "budget.json"
        data: Dict[str, Any] = read_json(self.path) if self.path.is_file() else {}
        self.image_calls = int(data.get("image_calls", 0))
        self.elapsed_sec = float(data.get("elapsed_sec", 0.0))
        self.max_images = max_images
        self.max_minutes = max_minutes
        self._mark = time.monotonic()

    def checkpoint(self) -> None:
        """把自上次检查点以来的耗时计入账本，并检查总耗时上限。"""
        now = time.monotonic()
        self.elapsed_sec += now - self._mark
        self._mark = now
        self.save()
        if self.max_minutes is not None and self.elapsed_sec > self.max_minutes * 60:
            raise BudgetExceeded(f"总耗时超过预算 {self.max_minutes:g} 分钟，已停止")

    def count_image(self) -> None:
        if self.max_images is not None and self.image_calls >= self.max_images:
            raise BudgetExceeded(f"生图次数已达预算上限 {self.max_images}，已停止")
        self.image_calls += 1
        self.save()

    def save(self) -> None:
        write_json_atomic(self.path, {"image_calls": self.image_calls,
                                      "elapsed_sec": round(self.elapsed_sec, 2),
                                      "limits": {"max_images": self.max_images,
                                                 "max_minutes": self.max_minutes}})

    def summary(self) -> Dict[str, Any]:
        return {"image_calls": self.image_calls, "max_images": self.max_images,
                "elapsed_sec": round(self.elapsed_sec, 1), "max_minutes": self.max_minutes}


class BudgetedImageProvider:
    """包装生图 provider：每次真正生图前计数并检查上限。"""

    def __init__(self, inner, ledger: Ledger):
        self.inner = inner
        self.ledger = ledger
        self.name = getattr(inner, "name", "image")

    def generate(self, prompt, save_path, **kwargs):
        self.ledger.count_image()  # 超限在调用前抛出，不产生费用
        return self.inner.generate(prompt, save_path, **kwargs)
