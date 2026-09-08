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
