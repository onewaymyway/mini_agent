"""
skills/usage_detector.py — Skill 实际使用检测

判断 agent 在一次推理中是否真正"用到"了某个 skill，而不是仅仅"加载了"它。

检测策略（双轨）：
  Track A — 显式声明（高置信度）
    模型在回复中嵌入 <skill_used>name</skill_used> 标签主动声明。
    完全可靠，但依赖模型遵守 system prompt 中的约定。

  Track B — 关键词证据匹配（自动，无侵入性）
    从每个 skill 的内容中提取「指纹词」（专有名词、API/函数名、特定步骤词），
    在 assistant 回复中检测命中情况。
    命中数 >= threshold 则判定该 skill 被实际使用。

两条 track 互为补充：
  - 模型忘记声明时，Track B 兜底
  - 关键词稀少时（通用 skill），Track A 兜底

设计约束：
  - 纯文本处理，无外部依赖，O(n) 复杂度
  - 检测结果不影响主流程，只在 _append_assistant_response 后作为副作用更新 tracker
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from skills import Skill


# ── 标签解析（Track A）────────────────────────────────────────────────────────

# 匹配 <skill_used>name1,name2</skill_used> 或逐个 <skill_used>name</skill_used>
_TAG_RE = re.compile(
    r"<skill_used>\s*(.*?)\s*</skill_used>",
    re.IGNORECASE | re.DOTALL,
)


def extract_declared_skills(text: str) -> list[str]:
    """
    从 assistant 回复中提取显式声明的 skill 名称（Track A）。

    支持格式：
      <skill_used>docx</skill_used>
      <skill_used>docx, pdf</skill_used>
      <skill_used>docx,pdf,excel</skill_used>
    """
    names: list[str] = []
    for match in _TAG_RE.finditer(text):
        raw = match.group(1)
        for part in re.split(r"[,\s]+", raw):
            name = part.strip().lower()
            if name:
                names.append(name)
    return names


def strip_skill_tags(text: str) -> str:
    """从 assistant 回复中移除 <skill_used>...</skill_used> 标签，避免输出污染。"""
    return _TAG_RE.sub("", text).strip()


# ── 指纹词提取（Track B 预处理）─────────────────────────────────────────────

# 过滤掉太通用的停用词（保留技术词汇）
_STOP_WORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "this", "that", "it", "its", "use", "used", "using", "can", "will",
    "should", "must", "may", "when", "then", "if", "you", "your", "we",
    "our", "all", "any", "each", "file", "path", "note", "see", "also",
    "make", "set", "add", "get", "put", "new", "old", "run", "call",
})

# 优先提取的模式：函数名、类名、CLI命令、特定技术词
_HIGH_VALUE_RE = re.compile(
    r"""
    (?:
        [A-Z][a-zA-Z]{2,}[A-Z][a-zA-Z]+   # CamelCase 类名/组件名
        | [a-z][a-z_]{2,}\.[a-z_]{2,}\(   # method.call(
        | --[a-z][-a-z]+                   # --cli-flag
        | \b[A-Z][A-Z_]{2,}\b             # ALL_CAPS 常量
        | (?:import|from|require)\s+\S+    # import 语句
        | \.[a-z]{2,4}\b                   # .docx .pdf .xlsx
    )
    """,
    re.VERBOSE,
)


@dataclass
class SkillFingerprint:
    """
    单个 skill 的指纹，用于 Track B 匹配。

    包含两类词：
      high_value: 高置信度词（CamelCase、API名、文件扩展名）每个命中得 2 分
      normal:     普通关键词（4+ 字符的技术词）每个命中得 1 分
    """
    skill_name:  str
    high_value:  set[str] = field(default_factory=set)
    normal:      set[str] = field(default_factory=set)

    @property
    def total_terms(self) -> int:
        return len(self.high_value) + len(self.normal)


def build_fingerprint(skill: "Skill") -> SkillFingerprint:
    """
    从 skill 内容中提取指纹词，构建 SkillFingerprint。

    提取策略：
      1. 先用高值正则抓取专有名词
      2. 再提取 4+ 字符的普通词，过滤停用词
      3. 取频率最高的 top-N 词（避免指纹过大）
    """
    content = skill.content

    # Track 1：高价值词
    high_value: set[str] = set()
    for m in _HIGH_VALUE_RE.finditer(content):
        term = m.group(0).strip()
        if len(term) >= 3:
            high_value.add(term.lower())

    # Track 2：普通词频统计
    words = re.findall(r"\b[a-zA-Z][a-zA-Z_]{3,}\b", content)
    freq: dict[str, int] = {}
    for w in words:
        lw = w.lower()
        if lw not in _STOP_WORDS and lw not in high_value:
            freq[lw] = freq.get(lw, 0) + 1

    # 只保留出现 2 次以上且频率最高的 40 个词（指纹大小上限）
    normal = {
        w for w, c in sorted(freq.items(), key=lambda x: -x[1])[:40]
        if c >= 2
    }

    return SkillFingerprint(
        skill_name=skill.name,
        high_value=high_value,
        normal=normal,
    )


# ── 相似度评分（Track B 匹配）────────────────────────────────────────────────

@dataclass
class UsageEvidence:
    """单次检测结果，记录证据细节便于调试和日志。"""
    skill_name:       str
    detected:         bool
    track:            str          # "declared" | "fingerprint" | "none"
    score:            float        # 0.0 ~ 1.0+（fingerprint 分数）
    matched_terms:    list[str]    # Track B 命中的词
    declared:         bool         # Track A 是否显式声明


def score_response(
    response_text: str,
    fingerprint:   SkillFingerprint,
    threshold:     float = 0.15,
) -> UsageEvidence:
    """
    对单个 skill 计算「被使用」的置信度分数。

    评分公式：
      score = (high_value_hits * 2 + normal_hits * 1) / max(total_terms, 1)

    threshold 默认 0.15（命中 15% 的特征词认为使用了该 skill）。
    高价值词权重 2 倍，确保即使命中少量 CamelCase/API 名也能触发。
    """
    if not response_text:
        return UsageEvidence(
            skill_name=fingerprint.skill_name,
            detected=False, track="none",
            score=0.0, matched_terms=[], declared=False,
        )

    text_lower = response_text.lower()
    matched: list[str] = []
    raw_score = 0

    for term in fingerprint.high_value:
        if term in text_lower:
            matched.append(term)
            raw_score += 2

    for term in fingerprint.normal:
        if term in text_lower:
            matched.append(term)
            raw_score += 1

    total = max(fingerprint.total_terms, 1)
    score = raw_score / total
    detected = score >= threshold

    return UsageEvidence(
        skill_name=fingerprint.skill_name,
        detected=detected,
        track="fingerprint" if detected else "none",
        score=round(score, 3),
        matched_terms=matched,
        declared=False,
    )


# ── 主检测器 ──────────────────────────────────────────────────────────────────

class SkillUsageDetector:
    """
    统一入口：Track A（声明）+ Track B（指纹）双轨检测。

    使用方式：
      detector = SkillUsageDetector(threshold=0.15)
      detector.build_fingerprints(skill_loader)          # 初始化/更新指纹
      used = detector.detect(response_text, active_skills)
    """

    def __init__(self, threshold: float = 0.15) -> None:
        self.threshold   = threshold
        self._fps: dict[str, SkillFingerprint] = {}

    def build_fingerprints(self, skills: "dict[str, Skill]") -> None:
        """为所有 skill 构建（或更新）指纹，应在 skill 集合变化时调用。"""
        for name, skill in skills.items():
            self._fps[name] = build_fingerprint(skill)

    def update_fingerprint(self, skill: "Skill") -> None:
        """单个 skill 的指纹更新（激活新 skill 时调用）。"""
        self._fps[skill.name] = build_fingerprint(skill)

    def detect(
        self,
        response_text: str,
        active_skills: list[str],
    ) -> dict[str, UsageEvidence]:
        """
        检测 active_skills 中哪些被本次回复实际使用。

        流程：
          1. Track A：从回复中提取 <skill_used> 声明
          2. Track B：对每个 active skill 做指纹评分
          3. 合并：Track A 命中直接判定为 detected；
                   Track B 命中且 Track A 未命中也判定 detected

        Args:
            response_text: assistant 的完整回复文本（含可能的 skill_used 标签）
            active_skills: 当前激活的 skill 名称列表

        Returns:
            {skill_name: UsageEvidence}，只包含 active_skills 中的项
        """
        declared_names = set(extract_declared_skills(response_text))
        results: dict[str, UsageEvidence] = {}

        for name in active_skills:
            fp = self._fps.get(name)

            # Track A 优先
            if name in declared_names or name.lower() in declared_names:
                ev = UsageEvidence(
                    skill_name=name, detected=True,
                    track="declared", score=1.0,
                    matched_terms=[], declared=True,
                )
                results[name] = ev
                continue

            # Track B 补充
            if fp and fp.total_terms > 0:
                ev = score_response(response_text, fp, self.threshold)
                ev.declared = False
                results[name] = ev
            else:
                # 指纹为空（skill 内容太少或尚未构建），保守判为未使用
                results[name] = UsageEvidence(
                    skill_name=name, detected=False,
                    track="none", score=0.0,
                    matched_terms=[], declared=False,
                )

        return results

    def detect_used_names(
        self,
        response_text: str,
        active_skills: list[str],
    ) -> list[str]:
        """简化接口：只返回被判定为「实际使用」的 skill 名称列表。"""
        return [
            name
            for name, ev in self.detect(response_text, active_skills).items()
            if ev.detected
        ]
