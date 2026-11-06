"""「读取文件」上下文模式用的只读工具（B7）。

模型不把全部内容塞进提示词，而是通过三个只读工具自己去项目目录里读取：
  studio_list(path)                 列出目录
  studio_read(path, start, end)     读取文件（可选行范围）
  studio_grep(pattern, path)        在项目内搜索文本（正则）

访问范围：整个构思项目目录。路径经 resolve() 后必须落在项目根之内，
`..` 越界、指向项目外的绝对路径、符号链接指向外部，一律拒绝。
没有写入工具，也没有读取大小或调用次数的限制。修改仍只能通过提议，作者确认后写入。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from mini_agent.tools import ToolRegistry

from novel_writer.brainstorm.workspace import BrainstormError

TOOL_NAMES = ("studio_list", "studio_read", "studio_grep")


def _resolve(root: Path, path: Optional[str]) -> Path:
    raw = (path or "").strip()
    target = (root / raw).resolve() if raw else root.resolve()
    base = root.resolve()
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise BrainstormError(f"路径超出构思项目目录：{path}") from exc
    return target


def _rel(root: Path, p: Path) -> str:
    return "." if p == root.resolve() else p.relative_to(root.resolve()).as_posix()


def build_registry(root: Path) -> ToolRegistry:
    """返回只含三个只读工具、且绑定到 root 的私有注册表。"""
    root = Path(root)
    reg = ToolRegistry(namespace="brainstorm_read")

    def studio_list(path: str = "") -> str:
        """列出项目内某个目录的文件与子目录。"""
        target = _resolve(root, path)
        if not target.is_dir():
            raise BrainstormError(f"不是目录：{path or '.'}")
        lines = []
        for child in sorted(target.iterdir(), key=lambda c: (not c.is_dir(), c.name)):
            if child.is_dir():
                lines.append(f"[目录] {_rel(root, child)}/")
            else:
                lines.append(f"[文件] {_rel(root, child)}  {child.stat().st_size} 字节")
        return "\n".join(lines) or "（空目录）"

    def studio_read(path: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> str:
        """读取项目内的文件内容，可选行范围（从 1 开始，含两端）。"""
        target = _resolve(root, path)
        if not target.is_file():
            raise BrainstormError(f"不是文件：{path}")
        text = target.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        lo = max(int(start_line or 1), 1)
        hi = int(end_line) if end_line else len(lines)
        body = lines[lo - 1:hi]
        return "\n".join(f"{i}\t{ln}" for i, ln in enumerate(body, lo))

    def studio_grep(pattern: str, path: str = "") -> str:
        """在项目内搜索匹配正则的行，返回「文件:行号: 内容」。"""
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            raise BrainstormError(f"正则不合法：{exc}") from exc
        start = _resolve(root, path)
        files = [start] if start.is_file() else sorted(p for p in start.rglob("*") if p.is_file())
        out = []
        for f in files:
            try:
                lines = f.read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            for i, ln in enumerate(lines, 1):
                if rx.search(ln):
                    out.append(f"{_rel(root, f)}:{i}: {ln}")
        return "\n".join(out) or "（没有匹配）"

    obj = {"type": "object"}
    reg.register_fn(studio_list, name="studio_list", requires_approval=False, group="brainstorm_read",
                    description="列出构思项目内某个目录的文件与子目录（path 为空则是项目根目录）。",
                    input_schema={**obj, "properties": {"path": {"type": "string"}}, "required": []})
    reg.register_fn(studio_read, name="studio_read", requires_approval=False, group="brainstorm_read",
                    description="读取构思项目内的文件，可用 start_line/end_line 指定行范围（从 1 开始）。",
                    input_schema={**obj, "properties": {"path": {"type": "string"},
                                                        "start_line": {"type": "integer"},
                                                        "end_line": {"type": "integer"}},
                                  "required": ["path"]})
    reg.register_fn(studio_grep, name="studio_grep", requires_approval=False, group="brainstorm_read",
                    description="在构思项目内搜索匹配正则的行，返回文件、行号和内容。",
                    input_schema={**obj, "properties": {"pattern": {"type": "string"},
                                                        "path": {"type": "string"}},
                                  "required": ["pattern"]})
    return reg


def open_registry(root: Path) -> ToolRegistry:
    """「联网与执行权限」模式的工具表：项目只读工具 + 框架的 web_search + bash。"""
    import mini_agent.tools.builtin  # noqa: F401 - 内置工具在导入时才注册到默认注册表
    from mini_agent.tools import get_default_registry

    reg = build_registry(root)
    base = get_default_registry()
    for name in ("bash", "web_search"):
        tool = base.get(name)
        if tool is None:
            raise BrainstormError(f"框架缺少内置工具：{name}")
        reg.register(tool)
    return reg


def _open_skill_dirs(cfg: Any) -> list:
    """框架的 skill 目录（主项目 .claude/skills，含浏览器 skill）+ 配置里额外指定的目录。"""
    from novel_writer import config

    dirs = []
    main_root = config._detect_in_place_main_project_root() or getattr(cfg, "project_root", None)
    if main_root:
        d = Path(main_root) / ".claude" / "skills"
        if d.is_dir():
            dirs.append(d)
    extra = getattr(cfg, "skills_dir", None)
    if extra:
        dirs.append(Path(extra))
    return dirs


def build_open_agent(root: Path) -> Any:
    """「联网与执行权限」模式的 Agent：项目只读工具 + web_search + bash + framework skill（含浏览器 skill）。
    自动批准所有工具调用（不弹确认），不开沙箱，并保留框架的扩展工具。仅在作者手动开启该开关后使用。"""
    import copy as _copy

    from mini_agent.agent import Agent
    from mini_agent.permissions import PermissionGuard
    from mini_agent.skills import SkillLoader

    from novel_writer import config
    from novel_writer.runner import _ensure_framework_on_path

    _ensure_framework_on_path()
    cfg = _copy.copy(config.load_llm_cfg())
    cfg.stream = False
    try:
        cfg.turn_judge.enabled = False
    except AttributeError:
        pass
    skill_dirs = _open_skill_dirs(cfg)
    loader = SkillLoader(skill_dirs) if skill_dirs else None
    guard = PermissionGuard(auto_approve=True, sandbox=False,
                            project_root=getattr(cfg, "project_root", config.PROJECT_ROOT))
    return Agent(cfg=cfg, guard=guard, registry=open_registry(root), skill_loader=loader,
                 extra_tools_enabled=True)


def build_read_agent(root: Path) -> Any:
    """「读取文件」模式的 Agent：只挂载上面三个只读工具，不设轮次上限。"""
    import copy as _copy

    from mini_agent.agent import Agent
    from mini_agent.permissions import PermissionGuard

    from novel_writer import config
    from novel_writer.runner import _ensure_framework_on_path

    _ensure_framework_on_path()
    cfg = _copy.copy(config.load_llm_cfg())
    cfg.stream = False
    try:
        cfg.turn_judge.enabled = False
    except AttributeError:
        pass
    guard = PermissionGuard(auto_approve=True, sandbox=getattr(cfg, "sandbox", False),
                            project_root=getattr(cfg, "project_root", config.PROJECT_ROOT))
    return Agent(cfg=cfg, guard=guard, registry=build_registry(root), skill_loader=None,
                 extra_tools_enabled=False)
