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
class SkillResource:
    """
    [渐进式加载] 一个可按需加载的子资源（通常对应 references/*.md 里的一个文件）。

    与 browse_paths 的区别：resource 是"结构化、可整段加载进 context"的子文档
    （体量可控、主题聚焦），会出现在资源清单里，受关键词/工具双通道管理，
    并计入独立的 token 预算。真正的大型文档库/示例集合不应注册为 resource，
    而应作为 browse_path 提示 agent 自行用文件工具检索。
    """
    id: str
    path: str                       # 相对 skill 所在目录的路径
    description: str = ""
    triggers: list[str] = field(default_factory=list)   # 可为空：留空则只能被 agent 主动加载


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
    # [platform_filter] 平台/tag 限制：空 = 不限制，见 mini_agent.platform_filter
    platforms: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    # [渐进式加载] 结构化子资源（可加载）与自助浏览提示（不受管理，agent 自己查）
    resources: list["SkillResource"] = field(default_factory=list)
    browse_paths: list[dict] = field(default_factory=list)  # [{"path":..., "description":...}]

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
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.skills')
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
        per_resource_tokens: int = 3_000,
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
        # [渐进式加载] 已加载的子资源内容缓存：skill_name -> {resource_id: content}
        # 复用同一个 tracker 记录调用历史（key 为 "skill_name/resource_id"），
        # 卸载资源时只清缓存，不 forget tracker 记录，保留 LRU/使用统计供 agent 参考。
        self._loaded_resources: dict[str, dict[str, str]] = {}
        self.per_resource_tokens = per_resource_tokens
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
                if skill and _skill_allowed(skill):
                    self._all[skill.name] = skill
            # Flat: skills/my-skill.md
            for skill_md in d.glob("*.md"):
                if skill_md.name == "SKILL.md":
                    continue
                skill = _parse_skill(skill_md)
                if skill and _skill_allowed(skill):
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
            # [渐进式加载] 父 skill 卸载后，其下已加载的子资源内容一并清出 context；
            # 但 tracker 里的调用记录（skill_name/resource_id）不清零，
            # 下次重新激活该 skill 时清单仍能展示"之前用过 N 次"作为参考信号。
            self._loaded_resources.pop(name, None)
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
        """
        Activate any skills whose trigger words match the query. Return newly activated names.

        [渐进式加载] 资源级 triggers 也参与第一轮激活：未激活 skill 名下某个
        resource 的 triggers 命中时，同样视为该 skill 命中，先激活父 skill——
        否则子资源的关键词永远等不到 auto_activate_resources() 那一轮扫描
        （那一轮只扫描已激活 skill），会出现"资源关键词写了但从不生效"的死角。
        资源级命中不改变 SkillResource 自身的加载状态，仍由后续
        auto_activate_resources() 按同一条 query 正常加载该资源。
        """
        newly = []
        q = (query or "").lower()
        for name, skill in self._all.items():
            if name in self._active:
                continue
            hit = skill.matches_query(query)
            if not hit:
                hit = any(
                    r.triggers and any(t in q for t in r.triggers)
                    for r in skill.resources
                )
            if hit:
                self._active.append(name)
                self.detector.update_fingerprint(skill)
                newly.append(name)
        return newly

    # ── [渐进式加载] 子资源管理 ──────────────────────────────────────────────────
    #
    # 两条通道并存，互不冲突：
    #   A. 关键词自动通道：auto_activate_resources() 与 skill 级 auto_activate() 同级调用
    #   B. Agent 主动通道：load_resource() / unload_resource()（供 tools/skill_manager.py 里
    #      的 skill_resource_load / skill_resource_unload 工具调用）
    # 两者最终都走同一份 _loaded_resources 状态和同一个 tracker，天然幂等。

    def _find_resource(self, skill_name: str, resource_id: str) -> Optional["SkillResource"]:
        skill = self._all.get(skill_name)
        if not skill:
            return None
        for r in skill.resources:
            if r.id == resource_id:
                return r
        return None

    def _tracker_key(self, skill_name: str, resource_id: str) -> str:
        return f"{skill_name}/{resource_id}"

    def list_resources(self, skill_name: str) -> list[dict]:
        """
        返回某个 skill 的资源清单（结构化 resources + 提示性 browse_paths），
        含加载状态与历史使用统计，供 skill_resource_list 工具 / build_context 清单渲染复用。
        """
        skill = self._all.get(skill_name)
        if not skill:
            return []
        loaded = self._loaded_resources.get(skill_name, {})
        out = []
        for r in skill.resources:
            rec = self.tracker.get_record(self._tracker_key(skill_name, r.id))
            out.append({
                "id":          r.id,
                "description": r.description,
                "path":        r.path,
                "loaded":      r.id in loaded,
                "call_count":  rec.call_count if rec else 0,
                "last_called": rec.last_called if rec else None,
            })
        return out

    def load_resource(self, skill_name: str, resource_id: str) -> tuple[bool, str]:
        """
        加载指定子资源内容（从磁盘读取，支持热编辑后拿到最新内容）。
        幂等：已加载时直接返回成功，仍会 touch tracker。
        Returns: (ok, message)
        """
        skill = self._all.get(skill_name)
        if not skill:
            return False, f"skill '{skill_name}' 不存在"
        resource = self._find_resource(skill_name, resource_id)
        if not resource:
            return False, f"skill '{skill_name}' 下没有 resource '{resource_id}'"
        file_path = skill.location.parent / resource.path
        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception as e:
            return False, f"读取失败：{file_path} ({e})"

        was_loaded = resource_id in self._loaded_resources.get(skill_name, {})
        self._loaded_resources.setdefault(skill_name, {})[resource_id] = content
        key = self._tracker_key(skill_name, resource_id)
        self.tracker.record(key)
        note = "重新加载" if was_loaded else "已加载"
        return True, f"{note}: {skill_name}/{resource_id}"

    def unload_resource(self, skill_name: str, resource_id: str) -> bool:
        """
        卸载已加载的子资源内容（只清 context 缓存，不清 tracker 调用记录，
        保留 LRU/使用次数供下次 list_resources 时展示）。
        """
        bucket = self._loaded_resources.get(skill_name)
        if not bucket or resource_id not in bucket:
            return False
        del bucket[resource_id]
        if not bucket:
            self._loaded_resources.pop(skill_name, None)
        return True

    def auto_activate_resources(self, text: str) -> list[str]:
        """
        [关键词自动通道] 对所有已激活 skill 下、triggers 非空的 resource 做关键词匹配，
        命中且未加载则自动加载。留空 triggers 的 resource 不参与此通道，只能被 agent
        主动调用 skill_resource_load 加载（对应设计里"完全靠 agent 自己判断"的场景）。

        Return: 本次新加载的 "skill_name/resource_id" 列表
        """
        if not text:
            return []
        q = text.lower()
        newly: list[str] = []
        for skill_name in self._active:
            skill = self._all.get(skill_name)
            if not skill:
                continue
            loaded = self._loaded_resources.get(skill_name, {})
            for r in skill.resources:
                if not r.triggers or r.id in loaded:
                    continue
                if any(t in q for t in r.triggers):
                    ok, _ = self.load_resource(skill_name, r.id)
                    if ok:
                        newly.append(self._tracker_key(skill_name, r.id))
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
            # [路径感知] SKILL.md 里经常写相对路径（引用同目录下的脚本/模板/
            # 参考资料），但这些路径是相对 SKILL.md 自己所在目录的，不是相对
            # agent 当前工作目录的——不告诉 agent 这一点，它大概率会拼错路径
            # 去当前工作目录下找一个根本不存在的文件。这里把 skill 目录路径
            # 显式注入进去，让 agent 有能力自己算出正确的绝对路径。
            skill_dir = skill.location.parent
            _path_note = (
                f"**Skill 所在目录**：`{skill_dir}`\n"
                f"以上内容中出现的相对路径（脚本、模板、参考资料等）均相对于这个目录解析，"
                f"不是相对于当前工作目录或项目根目录。引用时请自行拼接为绝对路径，"
                f"例如 `{skill_dir}/xxx`。"
            )
            _resource_block = self._render_resource_block(skill)
            parts.append(f"{_header}\n\n{_path_note}\n\n{_content}{_resource_block}")
        return "\n\n---\n\n".join(parts)

    def _render_resource_block(self, skill: "Skill") -> str:
        """
        [渐进式加载] 为一个 active skill 渲染：
          1. 结构化子资源清单（含加载状态 + 历史使用次数，永远展示，很轻量）
          2. 已加载子资源的完整内容（受 per_resource_tokens 预算截断）
          3. browse_paths 提示（纯文字，不受管理，agent 自行用文件工具查）
        没有 resources/browse_paths 时返回空字符串，不影响旧格式 skill。
        """
        if not skill.resources and not skill.browse_paths:
            return ""

        out = []

        if skill.resources:
            rows = []
            for info in self.list_resources(skill.name):
                status = "● 已加载" if info["loaded"] else "○ 未加载"
                extra = f"（历史使用 {info['call_count']} 次）" if info["call_count"] else ""
                rows.append(f"| {info['id']} | {info['description']} | {status}{extra} |")
            manifest = (
                f"\n\n### 可加载子资源（skill: {skill.name}）\n"
                f"| id | 说明 | 状态 |\n|---|---|---|\n" + "\n".join(rows) +
                f"\n\n> 需要时调用 skill_resource_load(skill_name=\"{skill.name}\", "
                f"resource_id=..., reason=...) 加载对应文档；用完可调用 "
                f"skill_resource_unload 释放 context。"
            )
            out.append(manifest)

            loaded = self._loaded_resources.get(skill.name, {})
            for resource_id, content in loaded.items():
                resource = self._find_resource(skill.name, resource_id)
                clipped = self.tracker._clip(content, self.per_resource_tokens)
                out.append(
                    f"\n\n#### Resource: {skill.name}/{resource_id}"
                    f"{' — ' + resource.description if resource else ''}\n\n{clipped}"
                )

        if skill.browse_paths:
            rows = [f"- `{bp.get('path','')}` — {bp.get('description','')}" for bp in skill.browse_paths]
            out.append(
                "\n\n### 参考资料库（需自行检索，不通过加载机制注入）\n" + "\n".join(rows) +
                "\n\n> 体量较大或需要按具体问题定位片段，请用 view/grep/bash 自行查找，"
                "不要整份加载。"
            )

        return "".join(out)

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
        def _with_path_note(name: str) -> str:
            skill = self._all[name]
            content = skill.content
            # 同 build_context：压缩重附时也要带上 skill 目录路径，否则 agent
            # 压缩后重新看到 skill 内容时又会丢失"相对路径相对于哪里"这个
            # 关键信息。路径提示放在内容最前面，即使后面被 tracker 按预算
            # 截断（_clip 保留头部截断尾部），这条提示也不会被截掉。
            skill_dir = skill.location.parent
            note = (
                f"**Skill 所在目录**：`{skill_dir}`"
                f"（相对路径相对于此目录解析，不是当前工作目录）\n\n"
            )
            return note + content

        if include_inactive:
            candidates = {
                name: _with_path_note(name)
                for name in self.tracker.recent_names()
                if name in self._all
            }
        else:
            candidates = {
                name: _with_path_note(name)
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
        每条记录包含 name / description / active / location 字段，不含全文内容。
        location 是 skill 所在目录（不是 SKILL.md 文件本身），方便 agent 在
        还没激活、只看到目录列表时就能定位到该 skill 下的其它文件。
        """
        return [
            {
                "name":        name,
                "description": skill.description,
                "active":      name in self._active,
                "location":    str(skill.location.parent),
            }
            for name, skill in sorted(self._all.items())
        ]

    def get_active_catalog(self) -> list[dict]:
        """仅返回当前激活的技能目录（用于 system prompt 中的简洁描述）。"""
        return [
            {
                "name":        name,
                "description": self._all[name].description,
                "location":    str(self._all[name].location.parent),
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
                if skill and _skill_allowed(skill):
                    new_all[skill.name] = skill
            for skill_md in d.glob("*.md"):
                if skill_md.name == "SKILL.md":
                    continue
                skill = _parse_skill(skill_md)
                if skill and _skill_allowed(skill):
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
    # [platform_filter] platforms / tags：逗号分隔，未声明 = 不限制（见 platform_filter.py）
    platforms = _parse_list(fields.get("platforms", "") if fm_match else "")
    skill_tags = _parse_list(fields.get("tags", "") if fm_match else "")
    confidence_score = 1.0
    if fm_match:
        try:
            confidence_score = float(fields.get("confidence_score", "1.0"))
            confidence_score = max(0.0, min(1.0, confidence_score))
        except (ValueError, KeyError):
            confidence_score = 1.0

    # [渐进式加载] 解析 resources / browse_paths 结构化列表块（旧格式 skill 没有
    # 这两个 key，_parse_yaml_list_block 直接返回空列表，行为与之前完全一致）
    resources: list[SkillResource] = []
    browse_paths: list[dict] = []
    if fm_match:
        for item in _parse_yaml_list_block(fm_text, "resources"):
            rid = item.get("id", "").strip()
            rpath = item.get("path", "").strip()
            if not rid or not rpath:
                continue
            raw_triggers = item.get("triggers", "")
            r_triggers = [t.strip().lower() for t in raw_triggers.split(",") if t.strip()]
            resources.append(SkillResource(
                id=rid,
                path=rpath,
                description=item.get("description", ""),
                triggers=r_triggers,
            ))
        for item in _parse_yaml_list_block(fm_text, "browse_paths"):
            if item.get("path"):
                browse_paths.append({
                    "path": item.get("path", ""),
                    "description": item.get("description", ""),
                })

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
        platforms=platforms,
        tags=skill_tags,
        resources=resources,
        browse_paths=browse_paths,
    )


def _skill_allowed(skill: Skill) -> bool:
    """[platform_filter] discover/rediscover 阶段的放行判定，不满足则整个 skill 不进入 _all。"""
    from mini_agent.platform_filter import get_load_policy
    allowed, _reason = get_load_policy().is_allowed(
        platforms=skill.platforms, tags=skill.tags, kind="skill", name=skill.name,
    )
    return allowed


def _parse_yaml_list_block(fm_text: str, key: str) -> list[dict]:
    """
    轻量解析形如以下结构的 frontmatter 列表块（不是完整 YAML 实现，
    只覆盖 resources / browse_paths 这种固定浅层结构，够用即可）：

        resources:
          - id: advanced
            path: references/advanced.md
            description: 复杂参数组合与边界情况
            triggers: 高级用法, edge case

    key 不存在时返回 []（旧格式 SKILL.md 天然兼容，无需任何改动）。
    """
    lines = fm_text.splitlines()
    items: list[dict] = []
    current: Optional[dict] = None
    in_block = False

    for line in lines:
        if not in_block:
            if line.strip() == f"{key}:":
                in_block = True
            continue

        if not line.strip():
            continue
        # 顶层新 key（无缩进）意味着列表块结束
        if not line[:1].isspace():
            break

        stripped = line.strip()
        if stripped.startswith("- "):
            if current is not None:
                items.append(current)
            current = {}
            rest = stripped[2:]
            if ":" in rest:
                k, v = rest.split(":", 1)
                current[k.strip()] = v.strip()
        elif current is not None and ":" in stripped:
            k, v = stripped.split(":", 1)
            current[k.strip()] = v.strip()

    if current is not None:
        items.append(current)
    return items


def _extract_triggers(name: str, description: str) -> list[str]:
    combined = (name + " " + description).lower()
    found = [t for t in _TRIGGER_VERBS if t in combined]
    # Also add the skill name itself
    if name and name not in found:
        found.insert(0, name.lower())
    return found
