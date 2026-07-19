"""
reminders/loader.py
~~~~~~~~~~~~~~~~~~~
扫描 reminder 目录，解析 YAML frontmatter + 正文，生成 Reminder 对象列表。

Reminder 文件格式示例（.md）：

    ---
    name: bash_permission_error
    trigger_event: tool_error          # tool_error | post_tool | user_intent | pattern | pre_tool | format_issue
    condition:
      tool_name: bash                  # 可选：限定工具名（正则）
      error_pattern: "Permission denied"  # 正则匹配错误/输出内容
    inject_as: user                    # user | assistant
    priority: 80                       # 0-100，同场景多条时取最高
    enabled: true
    ---

    **注意**：bash 权限错误通常可以通过 ... 解决。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

# ── 可选依赖：PyYAML（若不存在，则用简单正则解析 frontmatter）──────────────
try:
    import yaml as _yaml  # type: ignore
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


# ── 触发事件类型常量 ──────────────────────────────────────────────────────────
TRIGGER_TOOL_ERROR   = "tool_error"   # 工具调用出错
TRIGGER_POST_TOOL    = "post_tool"    # 工具调用成功后（基于输出内容）
TRIGGER_USER_INTENT  = "user_intent"  # 用户消息进入时
TRIGGER_PATTERN      = "pattern"      # assistant 输出文本模式
# [具身改进 A3] 前馈控制：工具执行前触发，用于在危险操作发生前注入警示，
# 而不是等出错/出结果后再补救。条件字段复用 condition.tool_name；
# 暂不支持按参数内容匹配（tool_input 结构多变，先覆盖"按工具名预警"这一最常见场景）。
TRIGGER_PRE_TOOL     = "pre_tool"     # 工具调用前（前馈控制）
# [SYS-FORMAT-CORRECTION 统一化] 格式纠错检测器（perception/format_correction_detector.py）
# 判定命中某条规则（如 <tool_use> 未闭合、标签角色混淆、写大文件截断等）后触发。
# 与其它 trigger 的关键区别：这条路径命中后，调用方（agent/turn_loop.py）会
# 自动以 user 身份注入 reminder 内容，并让 agentic loop continue 到下一轮
# （而不仅仅是"注入一条提示"），本质是"检测+纠错续跑"，不是单纯的情境提示。
# condition 字段复用 issue_type（对应 FormatIssue.issue_type，如
# "unclosed_tool_use" / "write_file_truncated" 等，参见 format_correction_detector.py）。
TRIGGER_FORMAT_ISSUE = "format_issue"


@dataclass
class ReminderCondition:
    """reminder 触发条件。所有字段均为正则字符串（可选）。"""
    tool_name: Optional[str] = None       # 匹配工具名（正则）
    error_pattern: Optional[str] = None  # 匹配错误/输出内容（正则）
    output_pattern: Optional[str] = None # 匹配工具成功输出（正则）
    keyword: Optional[str] = None        # 用户消息关键词（正则）
    intent_pattern: Optional[str] = None # 用户消息模式（正则）
    text_pattern: Optional[str] = None   # assistant 输出模式（正则）
    # [Stage 7 / 15.2] 错误分类驱动恢复：按 error_category 精确路由
    # 对应 classify_error() 返回值枚举（observability.py）
    error_category: Optional[str] = None  # permission|not_found|timeout|network|…
    # [SYS-FORMAT-CORRECTION 统一化] 匹配 FormatIssue.issue_type（正则）
    # 如 "unclosed_tool_use" / "write_file_truncated" 等
    issue_type: Optional[str] = None


@dataclass
class Reminder:
    """一条 reminder 的完整描述。"""
    name: str
    trigger_event: str                       # TRIGGER_* 常量之一
    condition: ReminderCondition = field(default_factory=ReminderCondition)
    inject_as: str = "user"                  # "user" | "assistant"
    priority: int = 50                       # 0-100
    enabled: bool = True
    content: str = ""                        # reminder 正文（Markdown）
    source_path: Optional[Path] = None       # 来源文件路径（调试用）
    # 是否来自用户自定义目录（用于优先级裁决）
    is_custom: bool = False

    def __repr__(self) -> str:
        return (
            f"<Reminder name={self.name!r} event={self.trigger_event!r} "
            f"priority={self.priority} inject_as={self.inject_as!r} "
            f"custom={self.is_custom}>"
        )


# ── 解析工具 ──────────────────────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[Dict, str]:
    """
    从 Markdown 文本中分离 YAML frontmatter 和正文。
    返回 (meta_dict, body_str)。
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text.strip()

    raw_yaml = m.group(1)
    body = text[m.end():].strip()

    if _HAS_YAML:
        try:
            meta = _yaml.safe_load(raw_yaml) or {}
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.reminders.loader._parse_frontmatter')
            meta = {}
    else:
        # 极简 YAML 解析（仅支持顶层 key: value）
        meta = _simple_yaml_parse(raw_yaml)

    return meta, body


def _simple_yaml_parse(raw: str) -> Dict:
    """无 PyYAML 时的简单 YAML 解析（仅顶层 key: value，不支持嵌套 mapping）。"""
    result: Dict = {}
    current_key: Optional[str] = None
    current_block: List[str] = []

    def _flush():
        if current_key is None:
            return
        val = "\n".join(current_block).strip()
        # bool
        if val.lower() in ("true", "yes"):
            result[current_key] = True
        elif val.lower() in ("false", "no"):
            result[current_key] = False
        else:
            # int
            try:
                result[current_key] = int(val)
                return
            except ValueError:
                pass
            result[current_key] = val.strip('"').strip("'")

    for line in raw.splitlines():
        kv = re.match(r"^(\w[\w_]*):\s*(.*)", line)
        if kv:
            _flush()
            current_key = kv.group(1)
            current_block = [kv.group(2)]
        elif line.startswith("  ") and current_key:
            # 嵌套内容（condition 块）作为子字典
            sub = re.match(r"\s+(\w[\w_]*):\s*(.*)", line)
            if sub:
                if not isinstance(result.get(current_key), dict):
                    result[current_key] = {}
                result[current_key][sub.group(1)] = sub.group(2).strip('"').strip("'")
        else:
            if current_key:
                current_block.append(line)

    _flush()
    return result


def _build_condition(cond_raw) -> ReminderCondition:
    """将 frontmatter 中的 condition 字典转成 ReminderCondition。"""
    if not isinstance(cond_raw, dict):
        return ReminderCondition()
    return ReminderCondition(
        tool_name=cond_raw.get("tool_name"),
        error_pattern=cond_raw.get("error_pattern"),
        output_pattern=cond_raw.get("output_pattern"),
        keyword=cond_raw.get("keyword"),
        intent_pattern=cond_raw.get("intent_pattern"),
        text_pattern=cond_raw.get("text_pattern"),
        error_category=cond_raw.get("error_category"),  # [15.2]
        issue_type=cond_raw.get("issue_type"),  # [SYS-FORMAT-CORRECTION 统一化]
    )


# ── ReminderLoader ────────────────────────────────────────────────────────────

class ReminderLoader:
    """
    从一个或多个目录加载 reminder 文件。

    加载规则：
    - 同时加载系统默认目录和用户自定义目录；
    - 相同 name 的 reminder，用户自定义目录的优先（覆盖系统默认）；
    - 不同 name 的全部保留。
    """

    def __init__(
        self,
        system_dir: Optional[Path] = None,
        custom_dir: Optional[Path] = None,
        verbose: bool = False,
    ) -> None:
        self._system_dir = system_dir
        self._custom_dir = custom_dir
        self._verbose = verbose
        self._reminders: List[Reminder] = []

    # ── 公开接口 ──────────────────────────────────────────────────────────────

    def load(self) -> List[Reminder]:
        """扫描目录，加载所有 reminder，返回去重后的列表。"""
        system_map: Dict[str, Reminder] = {}
        custom_map: Dict[str, Reminder] = {}

        if self._system_dir and self._system_dir.is_dir():
            for r in self._scan_dir(self._system_dir, is_custom=False):
                system_map[r.name] = r

        if self._custom_dir and self._custom_dir.is_dir():
            for r in self._scan_dir(self._custom_dir, is_custom=True):
                custom_map[r.name] = r

        # 合并：custom 覆盖 system 同名条目
        merged: Dict[str, Reminder] = {**system_map, **custom_map}
        self._reminders = [r for r in merged.values() if r.enabled]

        if self._verbose:
            print(
                f"[ReminderLoader] 加载完成：system={len(system_map)} "
                f"custom={len(custom_map)} merged(enabled)={len(self._reminders)}"
            )

        return self._reminders

    def reload(self) -> List[Reminder]:
        """热重载（重新扫描目录）。"""
        return self.load()

    @property
    def reminders(self) -> List[Reminder]:
        return self._reminders

    # ── 内部方法 ──────────────────────────────────────────────────────────────

    def _scan_dir(self, directory: Path, *, is_custom: bool) -> List[Reminder]:
        """扫描目录下所有 .md 文件，解析成 Reminder 对象。"""
        results: List[Reminder] = []
        for fp in sorted(directory.glob("*.md")):
            r = self._parse_file(fp, is_custom=is_custom)
            if r is not None:
                results.append(r)
        return results

    def _parse_file(self, fp: Path, *, is_custom: bool) -> Optional[Reminder]:
        """解析单个 reminder 文件。解析失败则记录警告并返回 None。"""
        try:
            text = fp.read_text(encoding="utf-8")
        except OSError as e:
            print(f"[ReminderLoader] 读取文件失败 {fp}: {e}")
            return None

        meta, body = _parse_frontmatter(text)

        if not meta:
            if self._verbose:
                print(f"[ReminderLoader] 跳过（无 frontmatter）：{fp.name}")
            return None

        trigger_event = str(meta.get("trigger_event", "")).strip()
        if trigger_event not in (
            TRIGGER_TOOL_ERROR, TRIGGER_POST_TOOL,
            TRIGGER_USER_INTENT, TRIGGER_PATTERN, TRIGGER_PRE_TOOL,
            TRIGGER_FORMAT_ISSUE,
        ):
            if self._verbose:
                print(
                    f"[ReminderLoader] 跳过（trigger_event 无效 {trigger_event!r}）：{fp.name}"
                )
            return None

        name = str(meta.get("name", fp.stem)).strip()
        inject_as = str(meta.get("inject_as", "user")).strip().lower()
        if inject_as not in ("user", "assistant"):
            inject_as = "user"

        enabled_raw = meta.get("enabled", True)
        enabled = bool(enabled_raw) if not isinstance(enabled_raw, bool) else enabled_raw

        try:
            priority = int(meta.get("priority", 50))
        except (ValueError, TypeError):
            priority = 50

        condition = _build_condition(meta.get("condition"))

        reminder = Reminder(
            name=name,
            trigger_event=trigger_event,
            condition=condition,
            inject_as=inject_as,
            priority=priority,
            enabled=enabled,
            content=body,
            source_path=fp,
            is_custom=is_custom,
        )

        if self._verbose:
            print(f"[ReminderLoader] 加载：{reminder}")

        return reminder
