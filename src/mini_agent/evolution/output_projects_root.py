"""
evolution/output_projects_root.py — 调研/产出类项目根目录 + 主项目 git 隔离

[next_doc/output_projects_root_and_git_isolation_plan.md]

用户诉求：daemon/agent 在执行任务过程中临时搭建的"独立调研/产出项目"
（爬虫脚本集合、分析产出、示例代码库等）经常被误放进主项目 `src/`，还被
主项目 git 仓库连带管理。本模块提供：

  - resolve_root(cfg)            — 把 `AppConfig.output_projects_root`
                                    （相对/绝对路径）解析为绝对路径。
  - ensure_root(cfg)              — 幂等创建该目录（不存在则建，存在不动）。
  - maybe_append_gitignore(cfg)   — 如果该目录落在 project_root 内，
                                    幂等把它追加进主项目 `.gitignore`。
  - ensure_output_projects_root(cfg) — 组合以上三步，供调用方一次性调用。

本模块不做旧数据迁移、不做目录内部结构规范、不做磁盘清理策略（见方案
文档 §6 非目标）。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mini_agent.config.models import AppConfig


_DEFAULT_OUTPUT_PROJECTS_ROOT = "./output_projects"


def _raw_value_from_agent_config_json(project_root: Path) -> str:
    """在没有已加载的 AppConfig 时（比如只有 AgentPaths 的调用点），
    直接读一次 <project_root>/agent_config.json 里的
    `output_projects_root` 字段。文件不存在/解析失败/字段缺失，一律
    退回默认值——保持和 config/loader.py 里其余字段"缺失即用默认值"
    的一贯容错风格，不抛异常。
    """
    cfg_path = Path(project_root) / "agent_config.json"
    try:
        import json

        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        val = data.get("output_projects_root")
        if isinstance(val, str) and val.strip():
            return val
    except Exception:
        pass
    return _DEFAULT_OUTPUT_PROJECTS_ROOT


def resolve_root(cfg_or_project_root) -> Path:
    """解析 `output_projects_root` 为绝对路径。

    接受两种入参，覆盖两类调用场景：
      - `AppConfig` 实例（有 `project_root` + `output_projects_root` 字段）：
        已加载配置的调用点（如 objective_executor.py）直接传 cfg。
      - `Path`（project_root 本身）：只持有 `AgentPaths` 没有 cfg 的调用点
        （如 cron_job_workspace.py/cron_scheduler.py），退回直接读取
        `agent_config.json` 原始字段。
    """
    if isinstance(cfg_or_project_root, (str, Path)):
        project_root = Path(cfg_or_project_root)
        raw = _raw_value_from_agent_config_json(project_root)
    else:
        cfg = cfg_or_project_root
        project_root = Path(getattr(cfg, "project_root", Path.cwd()))
        raw = getattr(cfg, "output_projects_root", None) or _DEFAULT_OUTPUT_PROJECTS_ROOT

    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = project_root / p
    return p.resolve()


def ensure_root(cfg_or_project_root) -> Path:
    """幂等创建产出项目根目录：不存在则建，已存在不动、不清空。"""
    root = resolve_root(cfg_or_project_root)
    root.mkdir(parents=True, exist_ok=True)
    return root


def maybe_append_gitignore(cfg_or_project_root) -> Optional[Path]:
    """若产出项目根目录落在主项目 project_root 内，幂等把它相对
    project_root 的路径（末尾带 `/`）追加进主项目根目录 `.gitignore`。

    - 已经在 .gitignore 里（该行已存在）则不重复追加。
    - `.gitignore` 不存在则新建（只含这一行）。
    - 目录落在 project_root 之外（绝对路径/其他磁盘分区）则跳过，
      返回 None——本来就不在主仓库工作树内，不存在被误 git add 的可能。
    """
    if isinstance(cfg_or_project_root, (str, Path)):
        project_root = Path(cfg_or_project_root).resolve()
    else:
        project_root = Path(getattr(cfg_or_project_root, "project_root", Path.cwd())).resolve()

    root = resolve_root(cfg_or_project_root)

    try:
        rel = root.relative_to(project_root)
    except ValueError:
        # 不在 project_root 内，跳过。
        return None

    rel_line = rel.as_posix()
    if not rel_line.endswith("/"):
        rel_line += "/"

    gitignore_path = project_root / ".gitignore"
    existing_lines: list[str] = []
    if gitignore_path.exists():
        try:
            existing_lines = gitignore_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            existing_lines = []

    if rel_line in existing_lines or rel_line.rstrip("/") in existing_lines:
        return gitignore_path

    with gitignore_path.open("a", encoding="utf-8") as f:
        if existing_lines and not existing_lines[-1] == "":
            f.write("\n")
        f.write(rel_line + "\n")
    return gitignore_path


def ensure_output_projects_root(cfg_or_project_root) -> Path:
    """组合调用：建目录 + 有需要则追加 .gitignore。返回根目录绝对路径。"""
    root = ensure_root(cfg_or_project_root)
    maybe_append_gitignore(cfg_or_project_root)
    return root


# ── 项目介绍信息文件（output_projects_intro_and_kanban_plan.md）───────────────
# 每个产出项目目录下约定放一份固定文件名的介绍信息，供人/看板快速了解
# "这是个什么项目"，不用逐个打开翻源码。文件名固定，方便代码/prompt 双向
# 约定；内容格式不强制（纯文本/Markdown 均可），看板只做"有就展示、没有
# 就提示缺失"，不做格式校验。
PROJECT_INFO_FILENAME = "PROJECT_INFO.md"

# 看板列表页展示的介绍摘要最大字符数，避免一份很长的介绍信息把列表撑爆。
_INTRO_EXCERPT_MAX_CHARS = 2000


def project_info_path(project_dir: Path) -> Path:
    """给定某个产出项目的目录，返回其介绍信息文件应在的路径（不保证存在）。"""
    return Path(project_dir) / PROJECT_INFO_FILENAME


def list_projects(cfg_or_project_root) -> list[dict]:
    """扫描 `output_projects_root` 下的一级子目录，返回每个项目的概要信息。

    每个子目录视为一个"产出项目"（不递归更深层级），返回字段：
      - name           — 目录名（项目名）
      - path           — 绝对路径（字符串）
      - has_intro      — 是否存在 `PROJECT_INFO.md`
      - intro_excerpt  — 介绍文件内容（截断到 `_INTRO_EXCERPT_MAX_CHARS`
                         字符，超出则末尾追加省略标记）；不存在则为空字符串
      - intro_truncated — 介绍内容是否被截断
      - modified_at    — 目录本身的 mtime（epoch 秒），用于列表排序/展示
                         "最近更新"

    根目录不存在时返回空列表（不是异常——首次使用、还没产出过任何项目
    是正常状态）。跳过非目录条目（理论上不应该有，但兜底）。读取单个
    子目录/介绍文件失败不影响其余条目，异常项目会被跳过并静默忽略
    （扫描类只读功能，单条目失败不该拖垮整个列表）。
    """
    root = resolve_root(cfg_or_project_root)
    if not root.is_dir():
        return []

    results: list[dict] = []
    try:
        entries = sorted(root.iterdir(), key=lambda p: p.name)
    except OSError:
        return []

    for entry in entries:
        try:
            if not entry.is_dir():
                continue
            info_path = project_info_path(entry)
            has_intro = info_path.is_file()
            intro_excerpt = ""
            intro_truncated = False
            if has_intro:
                try:
                    raw = info_path.read_text(encoding="utf-8")
                except OSError:
                    raw = ""
                if len(raw) > _INTRO_EXCERPT_MAX_CHARS:
                    intro_excerpt = raw[:_INTRO_EXCERPT_MAX_CHARS]
                    intro_truncated = True
                else:
                    intro_excerpt = raw
            try:
                modified_at = entry.stat().st_mtime
            except OSError:
                modified_at = None
            results.append(
                {
                    "name": entry.name,
                    "path": str(entry.resolve()),
                    "has_intro": has_intro,
                    "intro_excerpt": intro_excerpt,
                    "intro_truncated": intro_truncated,
                    "modified_at": modified_at,
                }
            )
        except Exception:
            continue

    return results
