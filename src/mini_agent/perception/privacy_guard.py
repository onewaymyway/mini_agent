"""
perception/privacy_guard.py — 隐私信息保护

工作原理：
  发送给模型之前，把消息里的隐私值替换成占位符（{{SECRET_1}} 等）。
  收到模型回复之后，把回复里的占位符还原成真实值。

  这样模型永远看不到真实的 key/token，但生成的命令/代码里会有占位符，
  还原之后可以直接执行，整个流程对 agent 上层透明。

配置示例（agent_config.json）：
  {
    "privacy": {
      "enabled": true,
      "secrets": [
        {"name": "OPENAI_KEY",  "value": "sk-abc123"},
        {"name": "GITHUB_TOKEN","value": "ghp_xyz"}
      ]
    }
  }

  或者在 load_config() 里通过 privacy_secrets 参数传入列表：
    [{"name": "MY_KEY", "value": "actual-value"}, ...]

  也可以直接从环境变量自动采集（auto_env_patterns 匹配的环境变量名）。

默认行为：
  - 如果 secrets 列表为空、auto_env_patterns 也没命中任何变量，则 guard 是空操作。
  - 占位符格式：{{SECRET_<N>}}，N 从 1 开始，每次 PrivacyGuard 实例化时重新编号。
  - 同一个 value 只分配一个占位符（多次出现用同一个占位符）。
  - value 为空字符串的条目跳过（避免把空串替换成占位符导致文本损坏）。
  - 值长度 < 4 的条目跳过（太短容易误替换普通单词）。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional


# ── 单条隐私条目 ──────────────────────────────────────────────────────────────

@dataclass
class SecretEntry:
    """一条需要屏蔽的隐私值。"""
    name: str        # 人类可读标签，用于日志（不出现在发给模型的内容里）
    value: str       # 需要屏蔽的真实值
    placeholder: str = ""   # 分配的占位符，由 PrivacyGuard 填入


# ── 主类 ──────────────────────────────────────────────────────────────────────

class PrivacyGuard:
    """
    隐私信息屏蔽/还原引擎。

    使用方式：
        guard = PrivacyGuard.from_config(cfg.privacy)
        messages = guard.redact_messages(messages)   # 发送前
        resp_text = guard.restore(resp_text)          # 收到回复后
    """

    # 默认自动从环境变量里捞取的名称模式（常见 key/token 环境变量）
    _DEFAULT_ENV_PATTERNS: list[str] = [
        r".*_API_KEY$",
        r".*_API_TOKEN$",
        r".*_SECRET$",
        r".*_SECRET_KEY$",
        r".*_ACCESS_TOKEN$",
        r".*_AUTH_TOKEN$",
        r".*_PRIVATE_KEY$",
        r"^OPENAI_API_KEY$",
        r"^ANTHROPIC_API_KEY$",
        r"^GITHUB_TOKEN$",
        r"^GITLAB_TOKEN$",
        r"^HF_TOKEN$",
    ]

    def __init__(
        self,
        secrets: Optional[list[SecretEntry]] = None,
        auto_env_patterns: Optional[list[str]] = None,
        placeholder_prefix: str = "SECRET",
    ) -> None:
        self._placeholder_prefix = placeholder_prefix
        self._entries: list[SecretEntry] = []
        # value → placeholder，避免同一个值分配多个占位符
        self._value_to_ph: dict[str, str] = {}
        self._ph_to_value: dict[str, str] = {}
        self._counter = 0

        # 从 auto_env_patterns 捞环境变量
        patterns = auto_env_patterns if auto_env_patterns is not None else self._DEFAULT_ENV_PATTERNS
        _compiled = [re.compile(p) for p in patterns]
        for env_name, env_val in os.environ.items():
            if env_val and any(pat.match(env_name) for pat in _compiled):
                self._register(SecretEntry(name=env_name, value=env_val))

        # 显式 secrets 列表（优先级更高，但不重复注册相同 value）
        for entry in (secrets or []):
            self._register(entry)

    def _register(self, entry: SecretEntry) -> None:
        """注册一条隐私值。相同 value 不重复分配占位符。"""
        v = entry.value
        if not v or len(v) < 4:
            return  # 空值或过短，跳过
        if v in self._value_to_ph:
            entry.placeholder = self._value_to_ph[v]
            self._entries.append(entry)
            return
        self._counter += 1
        ph = f"{{{{{self._placeholder_prefix}_{self._counter}}}}}"
        entry.placeholder = ph
        self._value_to_ph[v] = ph
        self._ph_to_value[ph] = v
        self._entries.append(entry)

    # ── 公开 API ──────────────────────────────────────────────────────────────

    @property
    def active(self) -> bool:
        """是否有任何需要屏蔽的值。"""
        return bool(self._value_to_ph)

    def redact(self, text: str) -> str:
        """把 text 里的隐私值全部替换成占位符。"""
        if not self._value_to_ph:
            return text
        # 按值长度降序替换，避免短值先替换导致长值的子串匹配失败
        for value, ph in sorted(self._value_to_ph.items(), key=lambda kv: -len(kv[0])):
            if value in text:
                text = text.replace(value, ph)
        return text

    def restore(self, text: str) -> str:
        """把 text 里的占位符还原成真实值。"""
        if not self._ph_to_value:
            return text
        for ph, value in self._ph_to_value.items():
            if ph in text:
                text = text.replace(ph, value)
        return text

    def redact_messages(self, messages: list[dict]) -> list[dict]:
        """
        对消息列表做深度屏蔽，返回新列表（不修改原始对象）。
        支持 content 为字符串或 list[dict]（多模态块）两种形式。
        """
        if not self.active:
            return messages
        result = []
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, str):
                new_content = self.redact(content)
                if new_content is not content:
                    msg = {**msg, "content": new_content}
            elif isinstance(content, list):
                new_blocks = []
                changed = False
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        new_text = self.redact(block.get("text", ""))
                        if new_text != block.get("text", ""):
                            block = {**block, "text": new_text}
                            changed = True
                    new_blocks.append(block)
                if changed:
                    msg = {**msg, "content": new_blocks}
            result.append(msg)
        return result

    def redact_system(self, system: str) -> str:
        """对 system prompt 做屏蔽（通常不含 key，但保持一致性）。"""
        return self.redact(system) if self.active else system

    # ── 工厂方法 ──────────────────────────────────────────────────────────────

    @classmethod
    def from_config(cls, cfg: "PrivacyConfig") -> "PrivacyGuard":
        """从 PrivacyConfig 构造 PrivacyGuard。disabled 时返回空操作实例。"""
        if not cfg.enabled:
            return _NullGuard()  # type: ignore[return-value]
        secrets = [
            SecretEntry(name=s.get("name", f"secret_{i}"), value=s.get("value", ""))
            for i, s in enumerate(cfg.secrets)
        ]
        return cls(
            secrets=secrets,
            auto_env_patterns=cfg.auto_env_patterns if cfg.auto_env_patterns is not None else cls._DEFAULT_ENV_PATTERNS,
            placeholder_prefix=cfg.placeholder_prefix,
        )

    def summary(self) -> str:
        """返回当前注册的隐私条目摘要（不含真实值）。"""
        if not self._entries:
            return "(no secrets registered)"
        lines = []
        seen_ph: set[str] = set()
        for e in self._entries:
            if e.placeholder not in seen_ph:
                seen_ph.add(e.placeholder)
                lines.append(f"  {e.placeholder} ← {e.name} ({len(e.value)} chars)")
        return "\n".join(lines)


class _NullGuard(PrivacyGuard):
    """disabled 时的空操作实现，所有方法直接返回原值，无任何开销。"""

    def __init__(self) -> None:  # type: ignore[override]
        self._entries = []
        self._value_to_ph = {}
        self._ph_to_value = {}

    @property
    def active(self) -> bool:
        return False

    def redact(self, text: str) -> str:
        return text

    def restore(self, text: str) -> str:
        return text

    def redact_messages(self, messages: list[dict]) -> list[dict]:
        return messages

    def redact_system(self, system: str) -> str:
        return system


__all__ = ["PrivacyGuard", "SecretEntry", "_NullGuard"]
