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


@dataclass
class Skill:
    name: str
    description: str
    location: Path
    content: str           # full SKILL.md text
    trigger_words: list[str] = field(default_factory=list)

    def matches_query(self, query: str) -> bool:
        """Heuristic: does the user query seem to need this skill?"""
        q = query.lower()
        return any(t in q for t in self.trigger_words)


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

    def __init__(self, skills_dirs: list[Path]) -> None:
        self._dirs = skills_dirs
        self._all: dict[str, Skill] = {}
        self._active: list[str] = []
        self._discover()

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
        if name in self._all and name not in self._active:
            self._active.append(name)
            return True
        return False

    def deactivate(self, name: str) -> bool:
        if name in self._active:
            self._active.remove(name)
            return True
        return False

    def auto_activate(self, query: str) -> list[str]:
        """Activate any skills whose trigger words match the query. Return newly activated names."""
        newly = []
        for name, skill in self._all.items():
            if name not in self._active and skill.matches_query(query):
                self._active.append(name)
                newly.append(name)
        return newly

    def build_context(self, query: str = "") -> str:
        """
        Return skill context for active skills.

        If query is provided and skill_chunking mode is active (caller sets query),
        only the most relevant sections of each skill are returned.
        Without query, the full SKILL.md content is returned.
        """
        if not self._active:
            return ""
        parts = []
        for name in self._active:
            skill = self._all[name]
            if query:
                content = self._relevant_chunks(skill.content, query)
            else:
                content = skill.content
            parts.append(f"## Skill: {skill.name}\n\n{content}")
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

    return Skill(
        name=name,
        description=description,
        location=path,
        content=content,
        trigger_words=trigger_words,
    )


def _extract_triggers(name: str, description: str) -> list[str]:
    combined = (name + " " + description).lower()
    found = [t for t in _TRIGGER_VERBS if t in combined]
    # Also add the skill name itself
    if name and name not in found:
        found.insert(0, name.lower())
    return found
