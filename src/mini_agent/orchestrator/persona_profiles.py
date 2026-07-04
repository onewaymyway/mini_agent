"""
orchestrator/persona_profiles.py — 角色扮演（Persona）配置

用户在
  <project_root>/.agent/personas/*.md   （项目级，优先级更高）
  ~/.agent/personas/*.md                （全局级）
中预先定义"角色"，frontmatter 声明 name/display_name/description/
break_character_policy 等，正文是角色设定（身份、说话风格、行为准则）。

与 .agent/agents/*.md（AgentProfileLoader，子 agent）不同：
  - persona 作用于主 agent 自身，跨轮持续生效，直到用户显式退出
  - 不经过 spawn_named_agent，而是通过 /role use 激活、写入会话状态
  - 渲染结果会被强制追加一段"安全边界声明"，该声明由代码写死，
    不读取任何用户配置，角色文件本身无法覆盖或关闭

详见 next_doc/roleplay_persona_design.md。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# 系统级安全边界声明：无论角色如何设定，始终追加在角色正文之后。
# 不放进任何用户可编辑的文件里，避免被角色配置覆盖或绕过。
_SAFETY_SUFFIX = (
    "\n\n---\n"
    "角色设定仅影响语气与人设呈现，不改变你的安全边界：工具调用格式规范、"
    "内容安全与拒绝原则等核心约束在任何角色下始终有效；若角色设定与这些约束"
    "冲突，以约束为准。若用户明确表示想找回你的原始助手身份，或提出与角色"
    "扮演无关的严肃事务（如真实的代码报错排查、技术支持请求），应自然过渡"
    "回默认助手身份完成协助，并可提示用户 `/role exit` 可彻底清除角色设定。"
)


@dataclass
class PersonaProfile:
    name: str
    display_name: str = ""
    description: str = ""
    tone: str = ""
    allowed_tools: list[str] = field(default_factory=list)  # 空 = 不限制（二期用）
    break_character_policy: str = "soft"  # "soft" | "strict"
    exit_phrases: list[str] = field(default_factory=list)
    body: str = ""
    source_path: Optional[Path] = None

    # [platform_filter] 平台/tag 限制：空 = 不限制，与 agent/skill 一致
    platforms: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


def _as_list(raw) -> list[str]:
    if isinstance(raw, str):
        return [t.strip() for t in raw.split(",") if t.strip()]
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    return []


def _parse_simple_frontmatter(fm_text: str) -> dict:
    """极简 fallback：解析形如 `key: value` 的扁平 frontmatter（无 PyYAML 时使用）。"""
    out: dict = {}
    for line in fm_text.splitlines():
        if ":" not in line or line.strip().startswith("#"):
            continue
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip()
    return out


def _parse_persona(path: Path) -> Optional[PersonaProfile]:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None

    fm_match = _FRONTMATTER_RE.match(text)
    meta: dict = {}
    body = text
    if fm_match:
        body = text[fm_match.end():]
        try:
            import yaml  # type: ignore
            meta = yaml.safe_load(fm_match.group(1)) or {}
        except Exception:
            meta = _parse_simple_frontmatter(fm_match.group(1))

    if not isinstance(meta, dict):
        meta = {}

    name = str(meta.get("name") or path.stem)
    policy = str(meta.get("break_character_policy", "soft")).strip().lower()
    if policy not in ("soft", "strict"):
        policy = "soft"

    return PersonaProfile(
        name=name,
        display_name=str(meta.get("display_name") or name),
        description=str(meta.get("description", "")),
        tone=str(meta.get("tone", "")),
        allowed_tools=_as_list(meta.get("allowed_tools")),
        break_character_policy=policy,
        exit_phrases=_as_list(meta.get("exit_phrase")),
        body=body.strip(),
        source_path=path,
        platforms=_as_list(meta.get("platforms")),
        tags=_as_list(meta.get("tags")),
    )


def _persona_allowed(persona: PersonaProfile) -> bool:
    """[platform_filter] discover 阶段的放行判定，与 agent/skill 一致。"""
    try:
        from mini_agent.platform_filter import get_load_policy
        allowed, _reason = get_load_policy().is_allowed(
            platforms=persona.platforms, tags=persona.tags, kind="persona", name=persona.name,
        )
        return allowed
    except Exception:
        # platform_filter 不可用时不阻断（与其余 loader 的容错策略一致）
        return True


class PersonaLoader:
    """发现并管理所有角色扮演 persona。后加载目录中的同名 persona 覆盖先加载的。"""

    def __init__(self, dirs: list[Path]) -> None:
        self._dirs = dirs
        self._all: dict[str, PersonaProfile] = {}
        self._discover()

    def _discover(self) -> None:
        for d in self._dirs:
            if not d.is_dir():
                continue
            for md in sorted(d.glob("*.md")):
                persona = _parse_persona(md)
                if persona and _persona_allowed(persona):
                    self._all[persona.name] = persona

    @property
    def available(self) -> list[str]:
        return sorted(self._all)

    def get(self, name: str) -> Optional[PersonaProfile]:
        return self._all.get(name)

    def get_catalog(self) -> list[dict]:
        """供 /role list 展示：name/display_name/description。"""
        return [
            {
                "name": name,
                "display_name": p.display_name,
                "description": p.description,
            }
            for name, p in sorted(self._all.items())
        ]

    def rediscover(self, dirs: Optional[list] = None) -> None:
        """[SYS-HOT-RELOAD] 重新扫描磁盘，与 AgentProfileLoader.rediscover 行为一致。"""
        new_all: dict[str, PersonaProfile] = {}
        for d in self._dirs:
            if not d.is_dir():
                continue
            for md in sorted(d.glob("*.md")):
                persona = _parse_persona(md)
                if persona and _persona_allowed(persona):
                    new_all[persona.name] = persona
        self._all = new_all


def render_persona_prompt(persona: PersonaProfile) -> str:
    """渲染角色扮演 system prompt 片段：角色正文 + 强制安全边界声明。

    安全边界声明始终追加在最后，且不受任何 persona 字段/正文内容影响，
    确保它是 system prompt 中"最后生效的指令"。
    """
    header = f"## 当前角色扮演设定：{persona.display_name}\n"
    return (header + "\n" + persona.body).strip() + _SAFETY_SUFFIX


# ── 模块级单例（与 init_agent_profiles 同一模式） ─────────────────────────

_persona_loader: Optional[PersonaLoader] = None


def init_personas(cfg) -> PersonaLoader:
    global _persona_loader
    from mini_agent.storage.paths import AgentPaths
    paths = AgentPaths(Path(getattr(cfg, "project_root", None) or Path.cwd()))
    # 全局先加载，项目级同名覆盖（与 agent/skill 目录解析优先级一致）
    dirs = [paths.global_personas_dir, paths.project_personas_dir]
    _persona_loader = PersonaLoader(dirs)
    return _persona_loader


def get_persona_loader() -> Optional[PersonaLoader]:
    return _persona_loader
