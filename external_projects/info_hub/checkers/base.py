"""订阅检查器基类。

Checker 只负责“取到当前候选列表（最新在前）”，不比较、不写盘：
- 游标比较、更新判定由 core/pipeline.py 完成；
- 关键词过滤、顺序处理由 adapters/subscription.py 完成。
新增订阅站点：在 checkers/ 下新建模块，继承 Checker 并设置 name，即被 registry 自动发现。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from adapters.base import FetchContext, SourceSpec


@dataclass
class Cursor:
    """一个候选“最新进度”。value 是用于比较的游标值（如章节标题、视频 id、url）。"""
    value: str
    title: str
    url: str = ""
    published_at: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class CheckResult:
    cursors: list[Cursor]                 # 最新在前
    error: Optional[str] = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None


class Checker(ABC):
    name: str = ""
    requires_browser: bool = False

    @abstractmethod
    def validate(self, spec: SourceSpec) -> list[str]:
        """校验配置，返回错误列表。"""

    @abstractmethod
    def check(self, spec: SourceSpec, ctx: FetchContext) -> CheckResult:
        """取候选列表（最新在前）。不应抛网络异常，应转为 error。"""
