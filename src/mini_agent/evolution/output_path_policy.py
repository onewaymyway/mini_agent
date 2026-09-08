"""
evolution/output_path_policy.py — 产出路径规范（用户可编辑）

[goal_cron_feedback_and_output_policy_plan.md 第5节]
[next_doc/output_projects_root_and_git_isolation_plan.md §3.3/§3.4]

规范文件路径：<project_root>/.agent/policies/output_path_policy.md

本模块只负责：
  - 首次不存在时幂等写入内置默认模板（仿照 cron_job_workspace.py 的
    "已存在文件不覆盖"模式，用户后续可以直接编辑这个文件，不需要改代码）；
  - 老用户已有 policy 文件时，检查是否缺失新增规则（特征字符串检测），
    缺失则在文件末尾追加，不覆盖/不改动用户已有的其余内容；
  - 读取当前内容（含用户改动），渲染 `{{output_projects_root}}` 等占位符，
    供各执行路径统一注入 prompt。

本轮不做路径规范的强制拦截（不在 hook 里硬拦截写 src/ 的工具调用），只做
prompt 层面的规则注入，避免误伤"用户确实特殊说明要改 src/ 下代码"的合法场景。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths


DEFAULT_POLICY = """# 产出路径规范

在没有特殊说明的情况下，执行任务时请遵守：

1. 禁止把产出的代码写入主项目 `src/` 目录。
2. 禁止把产出的代码写入 `tests/` 目录。
3. 和 skill 相关的产出，放到对应 skill 的目录下。
4. 任务本身已经说明了工作目录的，产出放到该工作目录下。
5. 如果任务描述里出现了"本轮产出请写入：<目录>"这一行（周期性 Goal/
   CronJob 每轮自动附加，见 .agent/daemon_run_outputs/ 目录规范），以该目录为准，优先级
   高于本规范其他各条。
6. 调研类、产出类项目（有独立文件结构，可能需要自己的代码/数据版本
   管理）一律新建到 `{{output_projects_root}}/<项目名>/` 下，禁止放进
   主项目 src/、tests/ 或仓库根目录。这类目录不受主项目 git 管理；如该
   产出项目自身需要版本控制，应在其自己的目录内单独执行 `git init`
   建独立仓库，禁止把它的改动 commit 到主项目仓库。
   （已经有专门产出机制覆盖的场景——周期性 Goal/CronJob 的
   `.agent/daemon_run_outputs/`、已注册的 external_projects——以那些
   机制已有的规则为准，不受本条影响。）

如果任务描述中明确要求修改 `src/`、`tests/` 或指定了其他路径，以任务描述的
明确说明为准，本规范不覆盖显式指令。
"""

# [next_doc/output_projects_root_and_git_isolation_plan.md §3.4] 老用户已有
# policy 文件的追加式迁移：用这个特征字符串判断第 6 条规则是否已经存在，
# 缺失则在文件末尾追加，不覆盖/不改动用户已有的其余内容（含用户自己编辑
# 过的 1~5 条规则原文）。将来再加第 7、8 条规则时，可以复用
# `_append_rule_if_missing()` 这个小函数，不用每次手写一遍分支判断。
_RULE_6_MARKER = "调研类、产出类项目"

_RULE_6_TEXT = """6. 调研类、产出类项目（有独立文件结构，可能需要自己的代码/数据版本
   管理）一律新建到 `{{output_projects_root}}/<项目名>/` 下，禁止放进
   主项目 src/、tests/ 或仓库根目录。这类目录不受主项目 git 管理；如该
   产出项目自身需要版本控制，应在其自己的目录内单独执行 `git init`
   建独立仓库，禁止把它的改动 commit 到主项目仓库。
   （已经有专门产出机制覆盖的场景——周期性 Goal/CronJob 的
   `.agent/daemon_run_outputs/`、已注册的 external_projects——以那些
   机制已有的规则为准，不受本条影响。）
"""


def policy_path(paths: "AgentPaths") -> Path:
    return Path(paths.project_root) / ".agent" / "policies" / "output_path_policy.md"


def _append_rule_if_missing(path: Path, marker: str, rule_text: str) -> None:
    """特征字符串检测 → 缺失则追加的可复用迁移逻辑。

    - `marker` 不在现有文件内容中时，在文件末尾追加一行简短注释
      （标注自动追加日期）+ `rule_text`。
    - 已包含 `marker`（新建文件本身已含该规则，或此前已迁移过）则不动。
    - 读/写异常一律静默跳过，不影响调用方主流程（迁移是锦上添花，不是
      关键路径）。
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return
    if marker in content:
        return
    try:
        import datetime

        today = datetime.date.today().isoformat()
        with path.open("a", encoding="utf-8") as f:
            if content and not content.endswith("\n"):
                f.write("\n")
            f.write(f"\n<!-- 以下为自动追加的新规则（{today}） -->\n")
            f.write(rule_text)
    except OSError:
        pass


def ensure_policy_file(paths: "AgentPaths") -> Path:
    """幂等创建规范文件：不存在则整份写入默认模板；已存在则不覆盖用户
    已有内容，只做"缺失新规则则追加"的迁移检查（§3.4）。"""
    path = policy_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(DEFAULT_POLICY, encoding="utf-8")
    else:
        _append_rule_if_missing(path, _RULE_6_MARKER, _RULE_6_TEXT)
    return path


def load_policy(paths: "AgentPaths", cfg=None) -> str:
    """读取当前规范内容（含用户改动）；文件不存在时先幂等创建再读取。

    `cfg` 可选传入 `AppConfig`：传入时用于把 `{{output_projects_root}}`
    占位符渲染成解析后的绝对路径；不传时退回直接用 `paths.project_root`
    读取一次 `agent_config.json`（见 evolution/output_projects_root.py::
    resolve_root 对两种入参的处理），保持未升级调用点也能拿到正确值。
    """
    path = ensure_policy_file(paths)
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        text = DEFAULT_POLICY.strip()

    # [next_doc/output_projects_root_and_git_isolation_plan.md §4 调用点]
    # `ensure_policy_file()` 本来就没有独立的初始化调用点（一直是
    # load_policy() 惰性触发），这里合并调用 ensure_output_projects_root()
    # ——幂等建目录 + 需要时追加 .gitignore，与 ensure_policy_file() 共用
    # 同一次"session/objective 开始前的规范文件确保"时机，不再散落两处。
    root: Optional[Path] = None
    try:
        from mini_agent.evolution.output_projects_root import ensure_output_projects_root

        root = ensure_output_projects_root(cfg if cfg is not None else Path(paths.project_root))
    except Exception:
        pass

    if "{{output_projects_root}}" in text:
        try:
            if root is None:
                from mini_agent.evolution.output_projects_root import resolve_root

                root = resolve_root(cfg if cfg is not None else Path(paths.project_root))
            text = text.replace("{{output_projects_root}}", str(root))
        except Exception:
            pass
    return text
