"""
orchestrator/agent_profiles.py — 用户自定义子 agent（预设角色）

类似 Claude Code 的 `.claude/agents/*.md`：用户在
  <project_root>/.agent/agents/*.md   （项目级）
  ~/.agent/agents/*.md                （全局级，项目级同名覆盖）
中预先定义好"专家子agent"，frontmatter 声明 name/description/model/
tools/inputs，正文是 system prompt 模板，支持 {参数名} 和 {context}
占位符。

主 agent 通过 spawn_named_agent 工具调用，传入结构化 inputs + 自由文本
context，由 render_profile_prompt 拼出最终 prompt。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class AgentInputSpec:
    name: str
    type: str = "string"
    description: str = ""
    required: bool = False
    default: Any = None


@dataclass
class AgentProfile:
    name: str
    description: str = ""
    model: Optional[str] = None
    provider: Optional[str] = None
    tools: list[str] = field(default_factory=list)        # 空 = 不限制
    tool_groups: list[str] = field(default_factory=list)  # 空 = 不限制
    inputs: list[AgentInputSpec] = field(default_factory=list)
    system_prompt: str = ""
    hooks: dict = field(default_factory=dict)   # 该 profile 自带的 hooks.json 内容
    source_path: Optional[Path] = None

    # ── Role Agent 扩展字段 ───────────────────────────────────────────────────
    # role_type: "evaluator" | "coach" | "custom" | "" (空=普通 sub-agent)
    role_type: str = ""
    # trigger_on: "output"(主 agent 输出后) | "tool_use:<tool_name>" | "turn_end"
    trigger_on: str = ""
    # 评估-修订循环最多几轮（evaluator 用）
    max_iterations: int = 1
    # 评估分高于此值视为通过（0-1 浮点）
    pass_threshold: float = 0.8
    # 反馈注入主 agent 的方式："user"(追加 user 消息) | "system_reminder"(追加到 system)
    inject_as: str = "user"


# ── 解析 ──────────────────────────────────────────────────────────────────

def _parse_inputs(raw: Any) -> list[AgentInputSpec]:
    specs: list[AgentInputSpec] = []
    if not isinstance(raw, list):
        return specs
    for item in raw:
        if not isinstance(item, dict) or "name" not in item:
            continue
        specs.append(AgentInputSpec(
            name=str(item["name"]),
            type=str(item.get("type", "string")),
            description=str(item.get("description", "")),
            required=bool(item.get("required", False)),
            default=item.get("default"),
        ))
    return specs


def _parse_simple_frontmatter(fm_text: str) -> dict:
    """极简 fallback：解析形如 `key: value` 的扁平 frontmatter（无 PyYAML 时使用）。
    不支持嵌套结构（inputs/hooks 等复杂字段需要 PyYAML）。"""
    out: dict = {}
    for line in fm_text.splitlines():
        if ":" not in line or line.strip().startswith("#"):
            continue
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip()
    return out


def _parse_profile(path: Path) -> Optional[AgentProfile]:
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
    tools = meta.get("tools")
    if isinstance(tools, str):
        tools = [t.strip() for t in tools.split(",") if t.strip()]
    elif not isinstance(tools, list):
        tools = []

    tool_groups = meta.get("tool_groups")
    if isinstance(tool_groups, str):
        tool_groups = [t.strip() for t in tool_groups.split(",") if t.strip()]
    elif not isinstance(tool_groups, list):
        tool_groups = []

    return AgentProfile(
        name=name,
        description=str(meta.get("description", "")),
        model=meta.get("model"),
        provider=meta.get("provider"),
        tools=tools,
        tool_groups=tool_groups,
        inputs=_parse_inputs(meta.get("inputs")),
        system_prompt=body.strip(),
        hooks=meta.get("hooks") if isinstance(meta.get("hooks"), dict) else {},
        source_path=path,
        role_type=str(meta.get("role_type", "")),
        trigger_on=str(meta.get("trigger_on", "")),
        max_iterations=int(meta.get("max_iterations", 1)),
        pass_threshold=float(meta.get("pass_threshold", 0.8)),
        inject_as=str(meta.get("inject_as", "user")),
    )


# ── Loader ───────────────────────────────────────────────────────────────

class AgentProfileLoader:
    """发现并管理所有自定义子 agent profile。后加载目录中的同名 profile 覆盖先加载的。"""

    def __init__(self, dirs: list[Path]) -> None:
        self._dirs = dirs
        self._all: dict[str, AgentProfile] = {}
        self._discover()

    def _discover(self) -> None:
        for d in self._dirs:
            if not d.is_dir():
                continue
            for md in sorted(d.glob("*.md")):
                profile = _parse_profile(md)
                if profile:
                    self._all[profile.name] = profile

    @property
    def available(self) -> list[str]:
        return sorted(self._all)

    def get(self, name: str) -> Optional[AgentProfile]:
        return self._all.get(name)

    def get_catalog(self) -> list[dict]:
        """供注入主 agent 的 system prompt：name/description/inputs schema。"""
        out = []
        for name, p in sorted(self._all.items()):
            out.append({
                "name": name,
                "description": p.description,
                "inputs": [
                    {
                        "name": i.name,
                        "type": i.type,
                        "required": i.required,
                        "description": i.description,
                    }
                    for i in p.inputs
                ],
            })
        return out

    def rediscover(self, dirs: Optional[list] = None) -> None:
        """
        [SYS-HOT-RELOAD] 重新扫描磁盘，增量更新 _all。
        新增的 profile 立即可用，修改的 profile 立即生效，
        删除的 profile 从目录中移除（进行中的 subagent 不受影响）。
        dirs 参数由 HotReloader 传入，忽略该参数，始终用 self._dirs。
        """
        new_all: dict[str, AgentProfile] = {}
        for d in self._dirs:
            if not d.is_dir():
                continue
            for md in sorted(d.glob("*.md")):
                profile = _parse_profile(md)
                if profile:
                    new_all[profile.name] = profile
        self._all = new_all


# ── 渲染 / 校验 ──────────────────────────────────────────────────────────

def _stringify(value: Any) -> str:
    if isinstance(value, (list, dict)):
        import json
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def validate_inputs(profile: AgentProfile, inputs: dict) -> Optional[str]:
    """检查 required 参数是否齐全。返回 None 表示通过，否则返回错误描述。"""
    missing = [
        spec.name for spec in profile.inputs
        if spec.required and inputs.get(spec.name) in (None, "")
    ]
    if missing:
        return f"missing required inputs for agent '{profile.name}': {missing}"
    return None


def render_profile_prompt(profile: AgentProfile, inputs: dict, context: str = "") -> str:
    """将 inputs 和 context 填充进 profile 的 system_prompt 模板。

    未在模板中出现的占位符会被静默忽略；模板中使用但未提供的参数（无 default）
    替换为空字符串。
    """
    text = profile.system_prompt

    for spec in profile.inputs:
        placeholder = "{" + spec.name + "}"
        if placeholder not in text:
            continue
        if spec.name in inputs:
            value = inputs[spec.name]
        elif spec.default is not None:
            value = spec.default
        else:
            value = ""
        text = text.replace(placeholder, _stringify(value))

    # 允许调用方传入未在 inputs schema 中声明、但模板里使用了的占位符
    for key, value in inputs.items():
        placeholder = "{" + key + "}"
        if placeholder in text:
            text = text.replace(placeholder, _stringify(value))

    if "{context}" in text:
        text = text.replace("{context}", context)
    elif context:
        text = text + "\n\n# Additional Context\n\n" + context

    return text.strip()


# ── 模块级单例（与 init_task_manager 同一模式） ──────────────────────────────

_profile_loader: Optional[AgentProfileLoader] = None


def init_agent_profiles(cfg) -> AgentProfileLoader:
    global _profile_loader
    from mini_agent.storage.paths import AgentPaths
    paths = AgentPaths(Path(getattr(cfg, "project_root", None) or Path.cwd()))
    # 全局先加载，项目级同名覆盖
    dirs = [paths.global_agents_dir, paths.project_agents_dir]
    _profile_loader = AgentProfileLoader(dirs)
    return _profile_loader


def get_profile_loader() -> Optional[AgentProfileLoader]:
    return _profile_loader
