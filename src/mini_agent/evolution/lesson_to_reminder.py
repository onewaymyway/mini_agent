"""
evolution/lesson_to_reminder.py — Lesson → Reminder 自动闭环（具身改进 v3 B2）

具身来源：前馈控制 + 经验内化——重复的经验不只是被记住（lesson memory 只
影响"语言层面认知"，要等到下次相关上下文被检索到才会被模型看见），更应该
被内化为条件反射式的前馈警示，在同类操作发生前主动跳出来提醒，而不是被动
等待检索命中。

实现取舍（复用已有基础设施，不重新发明聚类逻辑）：
  - 聚类直接复用 `perception/lesson_review.py::group_lessons()`——它已经实现
    了"按 trigger 文本关键词 Jaccard 相似度分组"，T1/T2/T3 门槛判定也在那里。
    本模块只负责"分组结果 → reminder 文件"这一步转换，避免维护两套相似的
    聚类/阈值逻辑。
  - reminder 触发类型固定为 pre_tool（依赖 A3 新增的触发类型）：lesson 描述
    的几乎都是"做某个操作时要注意 XX"，本质就是前馈警示，pre_tool 语义最贴切。
    工具名通过 trigger 文本里的反引号标注（如"工具 `bash` 调用..."）正则提取，
    提取不到时不限定 tool_name（对所有工具调用都生效）。
  - 激活策略两档：
      * 分组内含 human_feedback 来源的条目 → 直接激活（写入 reminder_dir，
        enabled: true，ReminderManager 下次 reload 即可加载生效）。
      * 其余分组需要达到 lesson_review 的 T1 门槛（occurrence 总和 ≥3 且
        来自 ≥2 个 session）→ 写成草稿（写入 reminder_dir/drafts/，
        enabled: false）。ReminderLoader 只 `glob("*.md")`（不递归子目录），
        drafts/ 下的文件不会被自动加载，需要用户手动审阅后挪到上一级目录
        （或后续接入晨报 /digest 展示 + 一键 promote 命令，本次未实现，
        见模块末尾的 promote_draft() 工具函数）。
  - 幂等性：用分组 key 生成的文件名做去重判断（两个目录都要检查），重复扫描
    不会重复生成同一分组的文件。但分组结果本身依赖于当前已有的全部 lesson
    条目，下次扫描时同一主题如果有新增条目会被归入同一组——已存在的文件不
    会被覆盖更新（保持简单：避免用户手动编辑过的 reminder 被静默覆盖）。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from mini_agent.perception.lesson_review import LessonGroup, group_lessons

# trigger 文本里反引号包裹的标识符，启发式地当作工具名（如"工具 `bash` 调用..."）
_TOOL_NAME_RE = re.compile(r"`([a-zA-Z_][\w.\-]*)`")

# 单个分组正文最多展示几条来源 entry 的 suggested_action（避免正文过长）
_MAX_BODY_ITEMS = 3


@dataclass
class GeneratedReminder:
    """一次 scan() 产出的待写入文件描述。"""
    group: LessonGroup
    filename: str
    markdown: str
    activated: bool  # True → 写入 reminder_dir 直接生效；False → 写入 drafts/ 待确认


def _extract_tool_name(group: LessonGroup) -> Optional[str]:
    for e in group.entries:
        m = _TOOL_NAME_RE.search(e.trigger or "")
        if m:
            return m.group(1)
    return None


def _slugify(key: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
    return (slug[:40] if slug else fallback) or fallback


class LessonToReminderBridge:
    """
    把 lesson memory 扫描结果转化为 reminder 文件。

    用法：
        bridge = LessonToReminderBridge(reminder_dir)
        entries = memory_store.all_entries()
        written_paths = bridge.scan_and_write(entries)
    """

    def __init__(self, reminder_dir: Path) -> None:
        self.reminder_dir = Path(reminder_dir)
        self.drafts_dir = self.reminder_dir / "drafts"

    # ── 公开接口 ──────────────────────────────────────────────────────────────

    def scan(self, entries: list) -> List[GeneratedReminder]:
        """纯函数式扫描：不做任何磁盘写入，只返回待生成列表（便于单测）。"""
        generated: List[GeneratedReminder] = []
        for group in group_lessons(entries):
            activated = group.has_human_feedback
            if not activated and not group.meets_t1_threshold:
                continue  # 既无人类反馈来源，又没达到 T1 聚合门槛，跳过

            filename = f"auto_{_slugify(group.key, group.entries[0].entry_id)}.md"
            if (self.reminder_dir / filename).exists() or (self.drafts_dir / filename).exists():
                continue  # 已生成过同名文件，跳过（不覆盖，见模块文档"幂等性"一节）

            markdown = self._render(group, filename, activated=activated)
            generated.append(GeneratedReminder(
                group=group, filename=filename, markdown=markdown, activated=activated,
            ))
        return generated

    def write(self, generated: List[GeneratedReminder]) -> List[Path]:
        """把 scan() 的结果实际写入磁盘，返回写入的文件路径列表。"""
        written: List[Path] = []
        for gr in generated:
            target_dir = self.reminder_dir if gr.activated else self.drafts_dir
            target_dir.mkdir(parents=True, exist_ok=True)
            fp = target_dir / gr.filename
            fp.write_text(gr.markdown, encoding="utf-8")
            written.append(fp)
        return written

    def scan_and_write(self, entries: list) -> List[Path]:
        """scan() + write() 的便捷组合，是最常用的调用方式。"""
        return self.write(self.scan(entries))

    # ── 内部渲染 ──────────────────────────────────────────────────────────────

    def _render(self, group: LessonGroup, filename: str, *, activated: bool) -> str:
        tool_name = _extract_tool_name(group)
        rep = group.entries[0]
        priority = 70 if group.has_human_feedback else 55

        cond_lines = f'  tool_name: "{tool_name}"\n' if tool_name else ""

        body_items = [
            f"- {e.suggested_action}"
            for e in group.entries[:_MAX_BODY_ITEMS]
            if e.suggested_action
        ]
        if not body_items and rep.suggested_action:
            body_items.append(f"- {rep.suggested_action}")
        body = "\n".join(body_items) or "（无具体建议，请参见来源 lesson 详情）"

        entry_ids = ",".join(e.entry_id for e in group.entries)
        group_title = group.key[:60] if group.key else filename

        return (
            "---\n"
            f"name: {filename[:-3]}\n"
            "trigger_event: pre_tool\n"
            "condition:\n"
            f"{cond_lines}"
            "inject_as: user\n"
            f"priority: {priority}\n"
            f"enabled: {'true' if activated else 'false'}\n"
            "---\n\n"
            f"**[自动生成的 reminder，来自 lesson 聚类「{group_title}」]**\n\n"
            f"触发场景：{rep.trigger}\n\n"
            f"建议：\n{body}\n\n"
            f"<!-- source_entry_ids: {entry_ids} -->\n"
            f"<!-- generated_at: {time.strftime('%Y-%m-%d %H:%M:%S')} -->\n"
        )


def promote_draft(reminder_dir: Path, filename: str) -> Optional[Path]:
    """
    把 drafts/ 下的草稿 reminder 提升为正式生效（移动到 reminder_dir 并把
    enabled 改为 true）。供未来 CLI/晨报命令调用，本次改进先提供工具函数，
    暂未接入交互命令（见模块文档说明）。

    返回新文件路径；草稿不存在时返回 None。
    """
    reminder_dir = Path(reminder_dir)
    src = reminder_dir / "drafts" / filename
    if not src.exists():
        return None
    text = src.read_text(encoding="utf-8")
    text = re.sub(r"(?m)^enabled:\s*false\s*$", "enabled: true", text, count=1)
    dst = reminder_dir / filename
    dst.write_text(text, encoding="utf-8")
    src.unlink()
    return dst


def run_lesson_to_reminder_scan(memory_store, reminder_dir: Path) -> List[Path]:
    """便捷入口：从 MemoryStore 读取全部条目并完成一次扫描+写入。

    供 巩固循环 周期扫描或 CLI 命令直接调用，避免调用方重复 boilerplate
    （`entries = memory_store.all_entries(); bridge = LessonToReminderBridge(...)`）。
    """
    entries = memory_store.all_entries()
    bridge = LessonToReminderBridge(reminder_dir)
    return bridge.scan_and_write(entries)


__all__ = [
    "GeneratedReminder",
    "LessonToReminderBridge",
    "promote_draft",
    "run_lesson_to_reminder_scan",
]
