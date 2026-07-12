"""
platform_filter.py — 可加载对象（skill / agent / hook / tool）的平台与 tag 过滤内核

设计背景见 next_doc/（platform-tag-loading-design）。核心思路：
  - 四类"可加载对象"（Skill / AgentProfile / Hook / Tool）各自在自己的元数据里
    声明 platforms（平台限制）和 tags（标签），本模块不关心它们的具体格式，
    只提供统一的判定：LoadPolicy.is_allowed(platforms, tags) -> (bool, reason)
  - 过滤发生在"发现/注册"阶段，不满足条件的对象根本不会进入可用集合，
    因此其描述不会出现在任何 catalog / prompt / tool schema 里，行为也不会被触发。

默认行为（向后兼容）：
  - 对象不声明 platforms  -> 不限制平台，所有平台可用
  - 对象不声明 tags       -> 不受 tag 白/黑名单管辖，默认放行
  - 项目根目录不存在 platform_policy.json -> 全局不做任何限制（no-op）

平台标签集合（当前内置，后续可扩展）：
    termux / pc / windows / macos / linux / android

其中 "pc" 是聚合语义标签：windows / macos / linux（非 termux）环境会同时
带有 "pc" 标签，方便对象只声明 platforms: [pc] 就能覆盖三大桌面平台。
"""

from __future__ import annotations

import os
import platform as _platform_mod
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import json

# ── 内置平台标签常量（后续扩展只需在这里加常量 + current_platform() 里加探测逻辑）──

PLATFORM_TERMUX = "termux"
PLATFORM_PC = "pc"
PLATFORM_WINDOWS = "windows"
PLATFORM_MACOS = "macos"
PLATFORM_LINUX = "linux"
PLATFORM_ANDROID = "android"

KNOWN_PLATFORMS = frozenset({
    PLATFORM_TERMUX, PLATFORM_PC, PLATFORM_WINDOWS,
    PLATFORM_MACOS, PLATFORM_LINUX, PLATFORM_ANDROID,
})

# 环境变量：手动强制指定当前平台标签集合，逗号分隔，最高优先级（测试/CI/容器场景）
_ENV_OVERRIDE = "MINI_AGENT_PLATFORM_TAGS"


def _is_termux() -> bool:
    if os.environ.get("TERMUX_VERSION"):
        return True
    prefix = os.environ.get("PREFIX", "")
    return "com.termux" in prefix


def current_platform() -> set[str]:
    """
    探测当前运行环境，返回一组平台标识（可能同时命中多个，如 {"linux","pc"}）。

    优先级：
      1. 环境变量 MINI_AGENT_PLATFORM_TAGS 显式覆盖（用于测试/CI/手动指定）
      2. Termux 特征探测
      3. platform.system() 常规判断
    """
    override = os.environ.get(_ENV_OVERRIDE, "").strip()
    if override:
        return {t.strip() for t in override.split(",") if t.strip()}

    tags: set[str] = set()

    if _is_termux():
        tags.add(PLATFORM_TERMUX)
        tags.add(PLATFORM_LINUX)
        tags.add(PLATFORM_ANDROID)
        return tags

    sysname = _platform_mod.system()  # "Windows" | "Darwin" | "Linux" | ...
    if sysname == "Windows":
        tags.add(PLATFORM_WINDOWS)
        tags.add(PLATFORM_PC)
    elif sysname == "Darwin":
        tags.add(PLATFORM_MACOS)
        tags.add(PLATFORM_PC)
    elif sysname == "Linux":
        tags.add(PLATFORM_LINUX)
        tags.add(PLATFORM_PC)
    # 未识别的系统：不打任何平台标签，只有"不限制平台"的对象才会被加载

    return tags


@dataclass
class LoadConstraint:
    """一个可加载对象声明的平台/tag 限制。空列表 = 不限制。"""
    platforms: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


@dataclass
class _FilteredRecord:
    kind: str        # "skill" | "agent" | "hook" | "tool"
    name: str
    reason: str


class LoadPolicy:
    """
    全局加载策略：从 platform_policy.json 读取 tag 允许/禁止规则，
    并结合 current_platform() 对具体对象做出放行/拒绝判定。

    配置文件位置：<project_root>/platform_policy.json（与 agent_config.json 同目录）

    格式：
        {
          "platform_override": null,        // 可选，手动指定当前平台标签，覆盖自动探测
          "tags": {
            "deny": ["experimental"],
            "allow": []                     // 非空则启用白名单模式
          },
          "auto_quarantine": {
            "enabled": false,               // [运行时自动屏蔽] 总开关，默认关闭
            "fail_threshold": 3             // 连续多少次"环境不兼容"失败后自动拉黑
          }
        }

    auto_quarantine 说明见 mini_agent.auto_quarantine 模块 docstring：
    该开关只控制"是否允许运行时因反复失败而自动拉黑对象"这一整套机制，
    与本文件原有的静态 platforms/tags 声明式过滤是两套独立、互补的机制。
    """

    # auto_quarantine 默认值（未在 platform_policy.json 里声明时使用）
    _AUTO_QUARANTINE_DEFAULT_ENABLED = False
    _AUTO_QUARANTINE_DEFAULT_THRESHOLD = 3

    def __init__(self, project_root: Optional[Path] = None) -> None:
        self.project_root = (project_root or Path.cwd()).resolve()
        self._deny_tags: set[str] = set()
        self._allow_tags: set[str] = set()
        self._platform_override: Optional[set[str]] = None
        self._filtered_log: list[_FilteredRecord] = []
        self._auto_quarantine_enabled: bool = self._AUTO_QUARANTINE_DEFAULT_ENABLED
        self._auto_quarantine_fail_threshold: int = self._AUTO_QUARANTINE_DEFAULT_THRESHOLD
        self._load()

    # ── 配置加载 ───────────────────────────────────────────────────────────

    @property
    def config_path(self) -> Path:
        return self.project_root / "platform_policy.json"

    def _load(self) -> None:
        path = self.config_path
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(data, dict):
            return

        override = data.get("platform_override")
        if isinstance(override, list) and override:
            self._platform_override = {str(t).strip() for t in override if str(t).strip()}
        elif isinstance(override, str) and override.strip():
            self._platform_override = {t.strip() for t in override.split(",") if t.strip()}

        tags_cfg = data.get("tags")
        if isinstance(tags_cfg, dict):
            deny = tags_cfg.get("deny")
            allow = tags_cfg.get("allow")
            if isinstance(deny, list):
                self._deny_tags = {str(t).strip() for t in deny if str(t).strip()}
            if isinstance(allow, list):
                self._allow_tags = {str(t).strip() for t in allow if str(t).strip()}

        aq_cfg = data.get("auto_quarantine")
        if isinstance(aq_cfg, dict):
            enabled = aq_cfg.get("enabled")
            if isinstance(enabled, bool):
                self._auto_quarantine_enabled = enabled
            threshold = aq_cfg.get("fail_threshold")
            if isinstance(threshold, int) and threshold > 0:
                self._auto_quarantine_fail_threshold = threshold

    def reload(self) -> None:
        """重新读取配置文件（用于热更新场景）。"""
        self._deny_tags = set()
        self._allow_tags = set()
        self._platform_override = None
        self._auto_quarantine_enabled = self._AUTO_QUARANTINE_DEFAULT_ENABLED
        self._auto_quarantine_fail_threshold = self._AUTO_QUARANTINE_DEFAULT_THRESHOLD
        self._load()

    # ── auto_quarantine 开关 ──────────────────────────────────────────────

    @property
    def auto_quarantine_enabled(self) -> bool:
        """[运行时自动屏蔽] 总开关，默认 False（需在 platform_policy.json 里显式开启）。"""
        return self._auto_quarantine_enabled

    @property
    def auto_quarantine_fail_threshold(self) -> int:
        return self._auto_quarantine_fail_threshold

    def set_auto_quarantine_enabled(self, enabled: bool) -> None:
        """运行时切换开关并写回 platform_policy.json（供 /quarantine enable|disable 使用）。"""
        self._auto_quarantine_enabled = enabled
        self._persist_auto_quarantine()

    def _persist_auto_quarantine(self) -> None:
        path = self.config_path
        data: dict = {}
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    data = {}
            except Exception:
                data = {}
        aq = data.get("auto_quarantine")
        if not isinstance(aq, dict):
            aq = {}
        aq["enabled"] = self._auto_quarantine_enabled
        aq.setdefault("fail_threshold", self._auto_quarantine_fail_threshold)
        data["auto_quarantine"] = aq
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    # ── 平台判定 ───────────────────────────────────────────────────────────

    @property
    def active_platforms(self) -> set[str]:
        if self._platform_override is not None:
            return self._platform_override
        return current_platform()

    def _platform_ok(self, declared: list[str]) -> bool:
        if not declared:
            return True  # 未声明 = 不限制平台
        return bool(set(declared) & self.active_platforms)

    # ── tag 判定 ───────────────────────────────────────────────────────────

    def _tags_ok(self, declared: list[str]) -> tuple[bool, str]:
        if not declared:
            return True, ""  # 未打 tag 的对象默认放行，不受白/黑名单管辖

        declared_set = set(declared)

        if self._deny_tags and (declared_set & self._deny_tags):
            hit = sorted(declared_set & self._deny_tags)
            return False, f"tag in deny list: {hit}"

        if self._allow_tags and not (declared_set & self._allow_tags):
            return False, f"tag not in allow list (declared={sorted(declared_set)})"

        return True, ""

    # ── 综合判定 ───────────────────────────────────────────────────────────

    def is_allowed(
        self,
        platforms: Optional[list[str]] = None,
        tags: Optional[list[str]] = None,
        kind: str = "",
        name: str = "",
    ) -> tuple[bool, str]:
        """
        判定一个可加载对象是否允许加载。

        kind/name 仅用于记录被过滤日志，便于调试；不影响判定逻辑本身。
        """
        platforms = platforms or []
        tags = tags or []

        if not self._platform_ok(platforms):
            reason = f"platform mismatch: declared={platforms}, current={sorted(self.active_platforms)}"
            if kind and name:
                self._filtered_log.append(_FilteredRecord(kind, name, reason))
            return False, reason

        ok, reason = self._tags_ok(tags)
        if not ok:
            if kind and name:
                self._filtered_log.append(_FilteredRecord(kind, name, reason))
            return False, reason

        # [auto_quarantine] 运行时自动屏蔽名单检查：默认关闭（见开关说明），
        # 只有显式开启且该 (kind, name) 因反复"环境不兼容"失败被自动拉黑时才生效。
        # 与上面两段静态判定不同，这是运行期学习出来的、可随时撤销的动态名单，
        # 见 mini_agent.auto_quarantine.QuarantineStore。
        if self._auto_quarantine_enabled and kind and name:
            from mini_agent.auto_quarantine import get_quarantine_store
            if get_quarantine_store().is_quarantined(kind, name):
                reason = "runtime-quarantined: repeated environment-incompatible failures"
                self._filtered_log.append(_FilteredRecord(kind, name, reason))
                return False, reason

        return True, ""


    # ── 调试/可观测性 ─────────────────────────────────────────────────────

    @property
    def filtered_log(self) -> list[dict]:
        """本次运行期间被过滤掉的对象清单：[{kind, name, reason}, ...]"""
        return [
            {"kind": r.kind, "name": r.name, "reason": r.reason}
            for r in self._filtered_log
        ]


# ── 模块级单例（与 hooks/loader.py 的 init_hooks / get_hook_manager 同一模式） ──

_load_policy: Optional[LoadPolicy] = None


def init_load_policy(project_root: Optional[Path] = None) -> LoadPolicy:
    global _load_policy
    _load_policy = LoadPolicy(project_root)
    return _load_policy


def get_load_policy() -> LoadPolicy:
    """
    获取全局 LoadPolicy 单例；若尚未 init，则用默认 project_root=cwd 惰性创建，
    保证在测试 / 未显式初始化的调用路径下也不会抛异常（行为退化为"无限制"）。
    """
    global _load_policy
    if _load_policy is None:
        _load_policy = LoadPolicy()
    return _load_policy
