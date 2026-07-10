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
    body = (header + "\n" + persona.body).strip()
    if persona.allowed_tools:
        body += (
            "\n\n> [二期] 该角色限制了可用工具，仅可调用："
            f"{', '.join(persona.allowed_tools)}。其余工具调用会被系统拒绝。"
        )
    return body + _SAFETY_SUFFIX


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


# ── 使用统计（全局，跨项目，跨会话累计） ──────────────────────────────────
#
# 与 skills/tracker.py 的 SkillUsageTracker（服务于 compact 时的 LRU 预算重建）
# 不同，这里只是最朴素的"哪些角色被激活过多少次"计数，用于回答"这些默认内置
# 角色有没有人用"这类问题，不参与 system prompt 组装或压缩逻辑。
#
# 存储格式：~/.agent/persona_usage.jsonl，每行一条 {"name": str, "ts": float}。
# 只追加、不改写，容错优先于精确——单行读取失败跳过即可，不影响整体统计。


@dataclass
class PersonaUsageStat:
    name: str
    call_count: int = 0
    last_used: float = 0.0


def record_persona_usage(name: str, project_root: Optional[Path] = None) -> None:
    """记录一次 persona 激活事件（/role use 时调用）。失败静默吞掉，不阻断主流程。"""
    try:
        import json
        import time
        from mini_agent.storage.paths import AgentPaths
        from mini_agent.time_utils import ts_to_str
        paths = AgentPaths(project_root or Path.cwd())
        log_path = paths.global_persona_usage_log
        log_path.parent.mkdir(parents=True, exist_ok=True)
        _now = time.time()
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"name": name, "ts": _now, "ts_str": ts_to_str(_now)}, ensure_ascii=False) + "\n")
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.orchestrator.persona_profiles')
        pass


def summarize_persona_usage(project_root: Optional[Path] = None) -> list[PersonaUsageStat]:
    """读取全局使用日志，按调用次数降序返回每个 persona 的统计。

    单行解析失败会被跳过，不影响其余行的统计（与其余 loader 的容错策略一致）。
    """
    from mini_agent.storage.paths import AgentPaths
    paths = AgentPaths(project_root or Path.cwd())
    log_path = paths.global_persona_usage_log
    stats: dict[str, PersonaUsageStat] = {}
    if not log_path.exists():
        return []
    try:
        import json
        with log_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    name = str(entry.get("name", ""))
                    ts = float(entry.get("ts", 0.0))
                except Exception:
                    continue
                if not name:
                    continue
                if name not in stats:
                    stats[name] = PersonaUsageStat(name=name)
                stats[name].call_count += 1
                stats[name].last_used = max(stats[name].last_used, ts)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.orchestrator.persona_profiles')
        pass
    return sorted(stats.values(), key=lambda s: s.call_count, reverse=True)
