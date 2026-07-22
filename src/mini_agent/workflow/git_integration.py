"""
workflow/git_integration.py — [P9-2 workflow_system_next_directions.md §2]
workflow 定义文件的 git 集成。

设计原则（见 next_doc/workflow_system_next_directions.md §2.3
"为什么强调不重新发明"）：`.agent/workflows/` 本来就没有被 .gitignore
排除，说明 workflow 定义文件天然应该被 git 追踪——真正的缺口不是"没有
版本历史"，而是 save_workflow 完全不 aware 它在一个 git 仓库里。所以这
个模块只做两件事，都不涉及"自己造一套版本历史机制"：
  1. save_workflow 成功后，如果检测到 project_root 是 git 仓库，提示用户
     `git commit`（只提示，不自动 commit——自动 commit 属于"代用户做决定"）。
  2. 提供 `git_log_for_workflow` / `git_diff_for_workflow` 两个只读查询，
     直接复用 git 已有的历史/diff 能力，`git_diff_for_workflow` 额外在原始
     unified diff 之上包一层"step 级别"的结构化摘要（比如"gate_candidate
     从 false 改成了 true"），方便人类阅读，但原始 diff 始终一并附上。

全部函数只读或只打印提示，不写入任何 git 状态，探测/执行失败一律优雅
降级（返回 None 或一句提示文字），不抛异常——这是个锦上添花的功能，不应
该因为环境里没装 git 就影响主流程（保存工作流）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional


def is_git_repo(project_root: Path) -> bool:
    """轻量探测 project_root 是否在一个 git 工作区内。"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(project_root), capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"
    except Exception:
        return False


def save_hint(project_root: Path, workflow_path: Path) -> Optional[str]:
    """save_workflow 成功后展示的提示文案；不在 git 仓库里返回 None（不打扰用户）。"""
    if not is_git_repo(project_root):
        return None
    try:
        rel = workflow_path.relative_to(project_root)
    except ValueError:
        rel = workflow_path
    return (
        f"💡 检测到当前项目是 git 仓库：建议 `git add {rel} && git commit` 记录这次改动"
        f"（workflow 的版本历史直接用 git 就够了，用 `/workflow history <name>` "
        f"可以直接看这个 workflow 的提交历史）。"
    )


def _relative_workflow_paths(project_root: Path, name: str) -> list[str]:
    """一个 workflow 名字在磁盘上可能对应单文件（<name>.yaml）或文件夹
    （<name>/），两种候选路径都传给 git 查询——不存在的路径 git log/diff
    也能正常处理（只是没有结果），不需要提前判断当前是哪种模式。"""
    from mini_agent.workflow.store import WorkflowStore
    store = WorkflowStore(project_root)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    base = f"{store.WORKFLOWS_DIR}/{safe}"
    return [f"{base}.yaml", base]


def git_log_for_workflow(project_root: Path, name: str, limit: int = 20) -> str:
    """`git log --oneline -- <workflow路径>`：直接复用 git 已有的历史，
    不重新存一份 workflow 专属版本记录。"""
    if not is_git_repo(project_root):
        return "当前项目不是 git 仓库，无法查看历史。"
    paths = _relative_workflow_paths(project_root, name)
    try:
        result = subprocess.run(
            ["git", "log", f"-{limit}", "--oneline", "--", *paths],
            cwd=str(project_root), capture_output=True, text=True, timeout=10,
        )
    except Exception as e:
        return f"git log 执行失败：{e}"
    if result.returncode != 0:
        return f"git log 执行失败：{result.stderr.strip()}"
    out = result.stdout.strip()
    return out if out else f"工作流 {name!r} 还没有任何 git 提交记录（可能是新建但还没 commit）。"


def git_diff_for_workflow(project_root: Path, name: str) -> str:
    """
    `git diff -- <workflow路径>` 的结构化包装：在原始 unified diff 之上，
    额外解析出"哪些 step 的哪些字段变了"这样对人类更友好的摘要，原始 diff
    仍然附在后面供需要细节时查看。结构化解析失败（YAML 解析出错等）不影响
    原始 diff 的展示，只是没有摘要这一段。
    """
    if not is_git_repo(project_root):
        return "当前项目不是 git 仓库，无法查看 diff。"
    paths = _relative_workflow_paths(project_root, name)
    try:
        result = subprocess.run(
            ["git", "diff", "--", *paths],
            cwd=str(project_root), capture_output=True, text=True, timeout=10,
        )
    except Exception as e:
        return f"git diff 执行失败：{e}"
    if result.returncode != 0:
        return f"git diff 执行失败：{result.stderr.strip()}"
    raw_diff = result.stdout
    if not raw_diff.strip():
        return f"工作流 {name!r} 相对上次 commit 没有未提交的改动。"

    structured = _structured_step_diff(project_root, paths)
    sections = []
    if structured:
        sections.append("**step 级别差异**：\n" + structured)
    sections.append("**原始 diff**：\n```diff\n" + raw_diff + "\n```")
    return "\n\n".join(sections)


def _load_yaml_from_ref(project_root: Path, ref: str, rel_path: str) -> Optional[dict]:
    """`git show <ref>:<rel_path>` 读某个引用下的文件内容并解析为 dict，
    读不到/解析失败返回 None（当作"这个版本没有这个文件"处理）。"""
    try:
        result = subprocess.run(
            ["git", "show", f"{ref}:{rel_path}"],
            cwd=str(project_root), capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None
        import yaml  # type: ignore
        return yaml.safe_load(result.stdout) or {}
    except Exception:
        return None


def _structured_step_diff(project_root: Path, candidate_rel_paths: list[str]) -> str:
    """
    对 HEAD 版本 vs 工作区当前版本做一次"step 级别"的粗粒度对比：新增/
    删除的 step id、以及同一个 step id 下发生变化的字段（condition/role/
    depends_on/prompt 等）。不追求逐字符 diff 的精确度，只求比原始 YAML
    diff 更容易一眼看出"改了什么业务含义"。
    """
    for rel_path in candidate_rel_paths:
        old = _load_yaml_from_ref(project_root, "HEAD", rel_path)
        if old is None:
            continue
        new_path = project_root / rel_path
        if not new_path.exists():
            continue
        try:
            import yaml  # type: ignore
            new = yaml.safe_load(new_path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue

        old_steps = {s.get("id"): s for s in (old.get("steps") or []) if isinstance(s, dict)}
        new_steps = {s.get("id"): s for s in (new.get("steps") or []) if isinstance(s, dict)}

        lines: list[str] = []
        for step_id in sorted(set(old_steps) - set(new_steps)):
            lines.append(f"  - 删除了步骤 {step_id!r}")
        for step_id in sorted(set(new_steps) - set(old_steps)):
            lines.append(f"  - 新增了步骤 {step_id!r}")
        for step_id in sorted(set(old_steps) & set(new_steps)):
            old_s, new_s = old_steps[step_id], new_steps[step_id]
            for field in sorted(set(old_s) | set(new_s)):
                if field == "id":
                    continue
                if old_s.get(field) != new_s.get(field):
                    lines.append(
                        f"  - 步骤 {step_id!r} 的 {field} 从 {old_s.get(field)!r} "
                        f"改成了 {new_s.get(field)!r}"
                    )
        return "\n".join(lines) if lines else ""
    return ""
