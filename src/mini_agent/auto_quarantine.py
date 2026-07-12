"""
auto_quarantine.py — 可加载对象（skill / tool / agent）的运行时自动屏蔽机制

设计背景：platform_filter.py 里的 LoadPolicy 是"静态声明式"过滤——作者在
skill/tool/agent 元数据里写 platforms/tags，用户在 platform_policy.json 里写
deny/allow 名单，这些都需要人工提前知道"这个东西在某平台跑不了"。

本模块补上运行时的"自动学习"一环：agent 实际尝试使用某个 skill/tool/agent
时，如果反复遇到"环境不兼容"类错误（命令/模块缺失、进程起不来、语法不支持等），
且从未有过一次成功，就自动把它加入运行时黑名单，写入 runtime_quarantine.json，
下次启动直接不再加载它的描述信息（通过 LoadPolicy.is_allowed 统一 gating）。

## 总开关

默认 **关闭**（platform_policy.json 里 auto_quarantine.enabled 未声明或为 false）。
关闭时：
  - record_failure() / record_success() 直接 no-op，不写盘、不计数；
  - is_quarantined() 恒返回 False（即使历史文件里已有记录，也不会拦截）。
开启方式：编辑 platform_policy.json，或使用 /quarantine enable。

## 判定规则（避免误杀）

只有 classify_error() 归类为以下"环境不兼容"类别时才计数：
  not_found / import / process / syntax
（网络抖动、超时、权限拒绝、参数错误等不计数——这些多是偶发或用户可控问题，
不该导致"一朝失败、永久拉黑"。）

同一个 (kind, name) 在当前平台标签集合下累计失败达到阈值（默认 3，platform_policy.json
的 auto_quarantine.fail_threshold 可调）才会被拉黑；期间只要成功过一次，计数清零。
拉黑后不会自动解除，需要用户显式 /quarantine remove 或编辑配置文件。

## kind 取值

  "tool"   — mini_agent/tool_executor.py 里工具调用失败时上报
  "skill"  — 同上，工具调用失败时归因给当前所有 active 的 skill
  "agent"  — mini_agent/role_agents/dispatcher.py 里角色 Agent 运行失败时上报

配置文件位置：<project_root>/runtime_quarantine.json（与 platform_policy.json 同目录）
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# 只有这些 error_category（见 perception/observability.py::classify_error）
# 才被视为"环境不兼容"，计入自动屏蔽的失败计数。
ENVIRONMENTAL_CATEGORIES = frozenset({"not_found", "import", "process", "syntax"})

DEFAULT_FAIL_THRESHOLD = 3


def _key(kind: str, name: str) -> str:
    return f"{kind}:{name}"


@dataclass
class QuarantineRecord:
    kind: str
    name: str
    fail_count: int = 0
    first_failed_at: float = 0.0
    last_failed_at: float = 0.0
    last_reason: str = ""
    platform_tags: list = field(default_factory=list)
    quarantined: bool = False
    quarantined_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "name": self.name,
            "fail_count": self.fail_count,
            "first_failed_at": self.first_failed_at,
            "last_failed_at": self.last_failed_at,
            "last_reason": self.last_reason,
            "platform_tags": list(self.platform_tags),
            "quarantined": self.quarantined,
            "quarantined_at": self.quarantined_at,
        }

    @classmethod
    def from_dict(cls, kind: str, name: str, data: dict) -> "QuarantineRecord":
        return cls(
            kind=kind,
            name=name,
            fail_count=int(data.get("fail_count", 0)),
            first_failed_at=float(data.get("first_failed_at", 0.0)),
            last_failed_at=float(data.get("last_failed_at", 0.0)),
            last_reason=str(data.get("last_reason", "")),
            platform_tags=list(data.get("platform_tags", [])),
            quarantined=bool(data.get("quarantined", False)),
            quarantined_at=float(data.get("quarantined_at", 0.0)),
        )


class QuarantineStore:
    """
    运行时黑名单存取器。持久化到 <project_root>/runtime_quarantine.json。

    是否真正生效（记录失败 / 拦截加载）受 platform_filter.LoadPolicy 的
    auto_quarantine 总开关控制，默认关闭。
    """

    def __init__(self, project_root: Optional[Path] = None) -> None:
        self.project_root = (project_root or Path.cwd()).resolve()
        self._records: dict[str, QuarantineRecord] = {}
        self._load()

    @property
    def config_path(self) -> Path:
        return self.project_root / "runtime_quarantine.json"

    # ── 开关（转发到 LoadPolicy，避免两处配置源不一致）───────────────────────

    @staticmethod
    def _enabled() -> bool:
        from mini_agent.platform_filter import get_load_policy
        return get_load_policy().auto_quarantine_enabled

    @staticmethod
    def _threshold() -> int:
        from mini_agent.platform_filter import get_load_policy
        return get_load_policy().auto_quarantine_fail_threshold

    # ── 持久化 ────────────────────────────────────────────────────────────

    def _load(self) -> None:
        self._records = {}
        path = self.config_path
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        entries = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(entries, dict):
            return
        for k, v in entries.items():
            if not isinstance(v, dict):
                continue
            kind = v.get("kind") or (k.split(":", 1)[0] if ":" in k else "")
            name = v.get("name") or (k.split(":", 1)[1] if ":" in k else k)
            if not kind or not name:
                continue
            self._records[_key(kind, name)] = QuarantineRecord.from_dict(kind, name, v)

    def _save(self) -> None:
        path = self.config_path
        data = {"entries": {k: r.to_dict() for k, r in self._records.items()}}
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def reload(self) -> None:
        self._load()

    # ── 记录 ──────────────────────────────────────────────────────────────

    def record_failure(
        self,
        kind: str,
        name: str,
        error_category: str,
        error_text: str = "",
    ) -> bool:
        """
        记录一次失败。返回本次记录后是否"刚好触发拉黑"（用于调用方打印提示）。

        总开关关闭时直接 no-op、返回 False。
        非 ENVIRONMENTAL_CATEGORIES 的错误不计数、返回 False。
        """
        if not self._enabled():
            return False
        if error_category not in ENVIRONMENTAL_CATEGORIES:
            return False

        from mini_agent.platform_filter import current_platform

        k = _key(kind, name)
        rec = self._records.get(k)
        now = time.time()
        if rec is None:
            rec = QuarantineRecord(kind=kind, name=name, first_failed_at=now)
            self._records[k] = rec

        if rec.quarantined:
            # 已被拉黑的对象理论上不会再被调用（is_allowed 已经拦截了），
            # 但防御性地仍然更新一下时间戳/原因，不重复触发"刚好拉黑"提示。
            rec.last_failed_at = now
            rec.last_reason = f"{error_category}: {error_text}"[:500]
            self._save()
            return False

        rec.fail_count += 1
        rec.last_failed_at = now
        rec.last_reason = f"{error_category}: {error_text}"[:500]
        rec.platform_tags = sorted(current_platform())

        just_quarantined = False
        if rec.fail_count >= self._threshold():
            rec.quarantined = True
            rec.quarantined_at = now
            just_quarantined = True

        self._save()
        return just_quarantined

    def record_success(self, kind: str, name: str) -> None:
        """
        调用成功：清零失败计数（不影响已 quarantined 的对象——
        拉黑后需要用户显式解除，见模块 docstring）。
        """
        if not self._enabled():
            return
        k = _key(kind, name)
        rec = self._records.get(k)
        if rec is None or rec.quarantined:
            return
        if rec.fail_count == 0:
            return
        rec.fail_count = 0
        rec.last_reason = ""
        self._save()

    # ── 查询 ──────────────────────────────────────────────────────────────

    def is_quarantined(self, kind: str, name: str) -> bool:
        if not self._enabled():
            return False
        rec = self._records.get(_key(kind, name))
        return bool(rec and rec.quarantined)

    def unquarantine(self, kind: str, name: str) -> bool:
        """手动解除单个对象（无论总开关是否开启都允许操作，方便用户随时清理）。"""
        k = _key(kind, name)
        rec = self._records.get(k)
        if rec is None:
            return False
        del self._records[k]
        self._save()
        return True

    def list_all(self) -> list[dict]:
        """按 kind/name 排序返回全部记录（含未拉黑、仅有失败计数的）。"""
        return [
            r.to_dict()
            for r in sorted(self._records.values(), key=lambda r: (r.kind, r.name))
        ]

    def list_quarantined(self) -> list[dict]:
        return [d for d in self.list_all() if d["quarantined"]]

    def clear(self) -> None:
        self._records = {}
        self._save()


# ── 模块级单例（与 platform_filter.get_load_policy 同一模式）──────────────

_quarantine_store: Optional[QuarantineStore] = None


def init_quarantine_store(project_root: Optional[Path] = None) -> QuarantineStore:
    global _quarantine_store
    _quarantine_store = QuarantineStore(project_root)
    return _quarantine_store


def get_quarantine_store() -> QuarantineStore:
    global _quarantine_store
    if _quarantine_store is None:
        _quarantine_store = QuarantineStore()
    return _quarantine_store
