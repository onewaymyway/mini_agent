"""
Skill system.
Discovers SKILL.md files from the skills directory, parses metadata,
and injects relevant skill context into the system prompt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .tracker import SkillUsageTracker
from .usage_detector import SkillUsageDetector


@dataclass
class Skill:
    name: str
    description: str
    location: Path
    content: str           # full SKILL.md text
    trigger_words: list[str] = field(default_factory=list)
    # [Stage 7 / 14.2] Skill 依赖与冲突图
    requires: list[str] = field(default_factory=list)      # 依赖 skill 列表
    conflicts_with: list[str] = field(default_factory=list)  # 互斥 skill 列表
    activation_conditions: list[str] = field(default_factory=list)  # 额外激活条件（正则）
    # [Stage 7 / 14.3] 知识可信度
    confidence_score: float = 1.0  # 0.0-1.0，影响注入时的语气强度
    positive_count: int = 0        # 正向印证次数（lesson/使用确认）
    negative_count: int = 0        # 反例计数（人工纠正/revert）

    def matches_query(self, query: str) -> bool:
        """Heuristic: does the user query seem to need this skill?"""
        q = query.lower()
        # 基础触发词匹配
        if any(t in q for t in self.trigger_words):
            return True
        # [14.2] activation_conditions 额外匹配
        if self.activation_conditions:
            import re as _re
            for cond in self.activation_conditions:
                try:
                    if _re.search(cond, q, _re.I):
                        return True
                except Exception:
                    pass
        return False


class SkillLoader:
    """
    Discovers and manages skills from one or more skill directories.

    Directory layout:
        skills/
          docx/
            SKILL.md       ← content
          pdf/
            SKILL.md
          my-skill.md      ← flat layout also supported
    """

    def __init__(
        self,
        skills_dirs: list[Path],
        per_skill_tokens: int = 5_000,
        total_budget:     int = 25_000,
    ) -> None:
        self._dirs = skills_dirs
        self._all: dict[str, Skill] = {}
        self._active: list[str] = []
        # [SYS-SKILL-TRACK] 调用追踪器：记录每个 skill 最近的调用时间，
        # 用于压缩时按 LRU 顺序重附 skill 上下文
        self.tracker = SkillUsageTracker(
            per_skill_tokens=per_skill_tokens,
            total_budget=total_budget,
        )
        # [SYS-SKILL-DETECT] 实际使用检测器：通过指纹匹配判断 skill 是否真正被用到
        self.detector = SkillUsageDetector()
        self._discover()
        # 发现结束后为所有 skill 构建初始指纹
        self.detector.build_fingerprints(self._all)

    @property
    def dirs(self) -> list:
        """
        构造时传入的 skill 目录列表（只读）。
        daemon 多用户架构 Phase 3：SessionAgentPool 需要给每个 SessionAgent
        构造独立的 SkillLoader（不能跨 session 共享同一个实例，见
        api/session_pool.py 模块 docstring 第 5 点），但应该用同一批目录——
        这个属性让它不需要重新从 cfg 推导一遍 skill_dirs 计算逻辑。
        """
        return self._dirs

    # ── Discovery ──────────────────────────────────────────────────────────────

    def _discover(self) -> None:
        for d in self._dirs:
            if not d.is_dir():
                continue
            # Nested: skills/docx/SKILL.md
            for skill_md in d.rglob("SKILL.md"):
                skill = _parse_skill(skill_md)
                if skill:
                    self._all[skill.name] = skill
            # Flat: skills/my-skill.md
            for skill_md in d.glob("*.md"):
                if skill_md.name == "SKILL.md":
                    continue
                skill = _parse_skill(skill_md)
                if skill:
                    self._all[skill.name] = skill

    # ── Public API ─────────────────────────────────────────────────────────────

    @property
    def available(self) -> list[str]:
        return sorted(self._all)

    @property
    def active(self) -> list[str]:
        return list(self._active)

    def activate(self, name: str) -> bool:
        """激活 skill；[14.2] 若触发冲突则拒绝激活并打印警告。"""
        if name not in self._all or name in self._active:
            return False
        skill = self._all[name]
        # [Stage 7 / 14.2] 冲突检查：若 conflicts_with 里的某个 skill 已激活，拒绝
        for conflicting in skill.conflicts_with:
            if conflicting in self._active:
                import mini_agent.ui.renderer as _R
                _R.print_warning(
                    f"[skill] 无法激活 '{name}'：与已激活的 '{conflicting}' 存在冲突 (conflicts_with)"
                )
                return False
        # [14.2] requires 检查：依赖的 skill 若不存在则打印提示（不阻塞，允许继续激活）
        for dep in skill.requires:
            if dep not in self._all:
                import mini_agent.ui.renderer as _R
                _R.print_warning(
                    f"[skill] '{name}' 依赖 '{dep}' 但该 skill 不存在（requires）"
                )
        self._active.append(name)
        # 激活只是"加载"，在 tracker 里以 load_count 区分；
        # 真正的使用通过 record_usage() 在推理结束后更新
        self.detector.update_fingerprint(skill)
        return True

    def deactivate(self, name: str) -> bool:
        if name in self._active:
            self._active.remove(name)
            return True
        return False

    def exclude(self, name: str) -> bool:
        """
        [Phase D / 3.2] 把某个 skill 从"可用集合"中临时移除：既不能被 auto_activate()
        自动命中，也不能被 activate() 显式激活（与 deactivate() 的区别——deactivate
        只是取消激活，skill 仍在 _all 里，关键词命中时会被 auto_activate 重新拉起）。

        用于 `mini-agent eval --without-skill <name>` 场景：需要严格保证该 skill
        在本次评测中完全不参与，而不是"默认不激活但仍可能被关键词触发"。

        返回是否真的移除了（name 不存在则返回 False，调用方可借此判断拼写错误）。
        """
        if name not in self._all:
            return False
        del self._all[name]
        if name in self._active:
            self._active.remove(name)
        return True

    def auto_activate(self, query: str) -> list[str]:
        """Activate any skills whose trigger words match the query. Return newly activated names."""
        newly = []
        for name, skill in self._all.items():
            if name not in self._active and skill.matches_query(query):
                self._active.append(name)
                self.detector.update_fingerprint(skill)
                newly.append(name)
        return newly

    def update_confidence(
        self,
        name: str,
        positive: bool = True,
        delta_positive: float = 0.05,
        delta_negative: float = 0.20,
    ) -> bool:
        """[Stage 7 / 14.3] 更新 skill 置信度（设计文档开放问题 9 的反例计数机制）。

        positive=True  → 正向印证（使用成功 / lesson 确认），confidence 小幅上升
        positive=False → 反例（人工纠正 / revert record），confidence 大幅下降

        正向使 confidence 向 1.0 靠拢，负向使其向 0 靠拢。
        仅更新内存中的 Skill 对象，不写回 SKILL.md（需要调用方显式持久化）。
        """
        if name not in self._all:
            return False
        skill = self._all[name]
        if positive:
            skill.positive_count += 1
            skill.confidence_score = min(1.0, skill.confidence_score + delta_positive)
        else:
            skill.negative_count += 1
            skill.confidence_score = max(0.0, skill.confidence_score - delta_negative)
        return True

    def record_usage(self, response_text: str) -> list[str]:
        """
        [SYS-SKILL-DETECT] 在 assistant 回复生成后调用，检测哪些 skill 被真正使用。

        通过 Track A（显式声明标签）和 Track B（指纹关键词匹配）双轨判定，
        只有被判定为「实际使用」的 skill 才更新 tracker（影响 LRU 排序和保护权重）。

        Args:
            response_text: assistant 完整回复文本

        Returns:
            实际被使用的 skill 名称列表（用于日志/调试）
        """
        if not self._active or not response_text:
            return []

        used_names = self.detector.detect_used_names(response_text, self._active)
        for name in used_names:
            self.tracker.record(name)   # 只有真正使用了才更新 tracker

        return used_names

    def build_context(self, query: str = "") -> str:
        """
        Return skill context for active skills.

        If query is provided and skill_chunking mode is active (caller sets query),
        only the most relevant sections of each skill are returned.
        Without query, the full SKILL.md content is returned.

        注意：build_context 不再自动调用 tracker.record()，
        tracker 的更新只通过 record_usage() 在推理后完成。
        """
        if not self._active:
            return ""
        parts = []
        for name in self._active:
            skill = self._all[name]
            if query:
                _content = self._relevant_chunks(skill.content, query)
            else:
                _content = skill.content
            # [Stage 7 / 14.3] 置信度标注：confidence < 0.7 时添加语气修饰
            _header = f"## Skill: {skill.name}"
            if skill.confidence_score < 0.5:
                _header += "  ⚠ 置信度较低，请结合实际情况判断"
            elif skill.confidence_score < 0.7:
                _header += "  ℹ 置信度中等"
            parts.append(f"{_header}\n\n{_content}")
        return "\n\n---\n\n".join(parts)

    def _relevant_chunks(self, content: str, query: str, max_chunks: int = 3) -> str:
        """按 ## 标题分段，返回与 query 最相关的 top-N 段。"""
        import re
        chunks = re.split(r"(?=^## )", content, flags=re.MULTILINE)
        if len(chunks) <= max_chunks:
            return content
        # 简单词重叠评分
        q_words = set(query.lower().split())
        def score(chunk: str) -> int:
            return sum(1 for w in q_words if w in chunk.lower())
        ranked = sorted(chunks, key=score, reverse=True)
        return "\n\n".join(ranked[:max_chunks])

    def build_compact_context(
        self,
        include_inactive: bool = False,
    ) -> tuple[str, list[str], list[str]]:
        """
        压缩时重建 skill 上下文：按 LRU 顺序、受 budget 约束填充 skill 内容。

        保护规则（自动从当前 active skill 推断）：
          - 当前激活的 skill 不受 per_skill_tokens 截断
          - 当前激活的 skill 不受 total_budget 限制
          - 若候选集合只有 1 个 skill，自动豁免截断

        Args:
            include_inactive: True = 同时考虑曾经被追踪但当前未激活的 skill
                              False = 只处理当前激活的 skill（默认）

        Returns:
            (compact_text, included_names, dropped_names)
        """
        if include_inactive:
            candidates = {
                name: self._all[name].content
                for name in self.tracker.recent_names()
                if name in self._all
            }
        else:
            candidates = {
                name: self._all[name].content
                for name in self._active
                if name in self._all
            }

        # 当前激活的 skill 作为受保护集合传入 tracker
        protected = set(self._active)

        return self.tracker.build_compact_context(candidates, protected=protected)

    def get(self, name: str) -> Optional[Skill]:
        return self._all.get(name)

    def list_skills(self) -> str:
        if not self._all:
            return "No skills found."
        lines = []
        for name, skill in sorted(self._all.items()):
            active_marker = "✓" if name in self._active else " "
            lines.append(f"  [{active_marker}] {name:<20}  {skill.description[:60]}")
        return "\n".join(lines)

    def get_catalog(self) -> list[dict]:
        """
        返回所有可用技能的目录（供注入 system prompt 或工具结果使用）。
        每条记录包含 name / description / active 三个字段，不含全文内容。
        """
        return [
            {
                "name":        name,
                "description": skill.description,
                "active":      name in self._active,
            }
            for name, skill in sorted(self._all.items())
        ]

    def get_active_catalog(self) -> list[dict]:
        """仅返回当前激活的技能目录（用于 system prompt 中的简洁描述）。"""
        return [
            {
                "name":        name,
                "description": self._all[name].description,
            }
            for name in self._active
            if name in self._all
        ]

    def describe(self, name: str) -> str:
        """返回指定技能的 description 字段（不含全文），用于工具返回值。"""
        skill = self._all.get(name)
        return skill.description if skill else ""

    def rediscover(self, dirs: Optional[list] = None) -> None:
        """
        [SYS-HOT-RELOAD] 重新扫描磁盘，增量更新 _all。
        - 新增的 skill：加入 _all
        - 修改的 skill：更新 _all（若已激活则保持激活状态）
        - 消失的 skill：从 _all 和 _active 中移除
        dirs 参数由 HotReloader 传入（与初始化时的 _dirs 一致），忽略该参数，
        始终用 self._dirs（保持一致性）。
        """
        old_names = set(self._all)
        new_all: dict[str, "Skill"] = {}

        for d in self._dirs:
            if not d.is_dir():
                continue
            for skill_md in d.rglob("SKILL.md"):
                skill = _parse_skill(skill_md)
                if skill:
                    new_all[skill.name] = skill
            for skill_md in d.glob("*.md"):
                if skill_md.name == "SKILL.md":
                    continue
                skill = _parse_skill(skill_md)
                if skill:
                    new_all[skill.name] = skill

        new_names = set(new_all)
        removed = old_names - new_names

        self._all = new_all
        # 清理已消失的 skill 的激活状态
        self._active = [n for n in self._active if n in self._all]
        # 重建指纹（新 skill 加入，旧 skill 内容可能变化）
        self.detector.build_fingerprints(self._all)


# ── Parsing ────────────────────────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_FIELD_RE = re.compile(r"^(\w+)\s*:\s*(.+)$", re.MULTILINE)

# Fallback: extract from description text
_TRIGGER_VERBS = [
    "word", "docx", ".docx", "pdf", ".pdf", "excel", "xlsx", "powerpoint",
    "pptx", "spreadsheet", "presentation", "slide", "skill", "image",
    "data", "chart", "table", "report", "email", "calendar",
]


def _parse_skill(path: Path) -> Optional[Skill]:
    try:
        content = path.read_text(encoding="utf-8")
    except Exception:
        return None

    # Try YAML-like front matter
    name = description = ""
    trigger_words: list[str] = []

    fm_match = _FRONTMATTER_RE.match(content)
    if fm_match:
        fm_text = fm_match.group(1)
        fields = dict(_FIELD_RE.findall(fm_text))
        name = fields.get("name", "").strip()
        description = fields.get("description", "").strip()
        triggers_raw = fields.get("triggers", fields.get("trigger_words", ""))
        if triggers_raw:
            trigger_words = [t.strip().lower() for t in triggers_raw.split(",") if t.strip()]

    # Fallback name from directory / filename
    if not name:
        if path.name == "SKILL.md":
            name = path.parent.name
        else:
            name = path.stem

    # Fallback description: first non-empty non-frontmatter line
    if not description:
        body = content if not fm_match else content[fm_match.end():]
        for line in body.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                description = line[:120]
                break
        if not description:
            # First heading
            for line in body.splitlines():
                if line.startswith("#"):
                    description = line.lstrip("#").strip()[:120]
                    break

    # Fallback trigger words from name + description
    if not trigger_words:
        trigger_words = _extract_triggers(name, description)

    # [Stage 7 / 14.2+14.3] 解析扩展字段
    def _parse_list(raw: str) -> list[str]:
        return [s.strip() for s in raw.split(",") if s.strip()] if raw else []

    requires = _parse_list(fields.get("requires", "") if fm_match else "")
    conflicts_with = _parse_list(fields.get("conflicts_with", "") if fm_match else "")
    activation_conditions = _parse_list(fields.get("activation_conditions", "") if fm_match else "")
    confidence_score = 1.0
    if fm_match:
        try:
            confidence_score = float(fields.get("confidence_score", "1.0"))
            confidence_score = max(0.0, min(1.0, confidence_score))
        except (ValueError, KeyError):
            confidence_score = 1.0

    return Skill(
        name=name,
        description=description,
        location=path,
        content=content,
        trigger_words=trigger_words,
        requires=requires,
        conflicts_with=conflicts_with,
        activation_conditions=activation_conditions,
        confidence_score=confidence_score,
    )


def _extract_triggers(name: str, description: str) -> list[str]:
    combined = (name + " " + description).lower()
    found = [t for t in _TRIGGER_VERBS if t in combined]
    # Also add the skill name itself
    if name and name not in found:
        found.insert(0, name.lower())
    return found
