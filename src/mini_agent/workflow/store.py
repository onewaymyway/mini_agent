"""
workflow/store.py — 工作流持久化存储

工作流保存在 <project_root>/.agent/workflows/ 目录下，
每个工作流是一个 YAML 文件：<name>.yaml

支持：
  - 保存工作流（覆盖同名）
  - 加载工作流（按名称）
  - 列举所有工作流（含元信息）
  - 删除工作流
  - 导出为 YAML 字符串（用于展示/编辑）
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .schema import WorkflowDef


class WorkflowStore:
    """工作流文件存储管理器。"""

    WORKFLOWS_DIR = ".agent/workflows"

    def __init__(self, project_root: Path) -> None:
        self._root = project_root
        self._dir = project_root / self.WORKFLOWS_DIR
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        # 规范化名称：只允许字母数字下划线中划线
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
        return self._dir / f"{safe}.yaml"

    def _dir_path(self, name: str) -> Path:
        """[workflow_directory_mode_design.md 阶段2] 文件夹模式下工作流所在目录。"""
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
        return self._dir / safe

    def _dir_entry_path(self, name: str) -> Path:
        """文件夹模式的主入口文件：<workflows_dir>/<name>/workflow.yaml"""
        return self._dir_path(name) / "workflow.yaml"

    def _resolve_path(self, name: str) -> Optional[Path]:
        """
        [阶段2] 按优先级解析工作流实际所在文件：
          1. 文件夹模式：<workflows_dir>/<name>/workflow.yaml
          2. 单文件模式：<workflows_dir>/<name>.yaml（向后兼容）
        找不到返回 None。
        """
        dir_entry = self._dir_entry_path(name)
        if dir_entry.exists():
            return dir_entry
        flat = self._path(name)
        if flat.exists():
            return flat
        return None

    # ── CRUD ────────────────────────────────────────────────────────────────

    def save(self, wf: WorkflowDef, cfg=None, role_checker=None) -> Path:
        """
        保存工作流到 YAML 文件，返回文件路径。

        [workflow机制改进计划.md P6] 保存前引用完整性校验受 cfg 里的两个开关
        控制（cfg 为 None 时按默认值 True 全部开启，保持向后兼容）：
          - cfg.workflow.validate_placeholders_on_save
          - cfg.workflow.validate_role_refs_on_save（需配合 role_checker 使用，
            role_checker 为 None 时即使开关打开也无法真正校验，直接跳过）
        """
        wf_cfg = getattr(cfg, "workflow", None) if cfg is not None else None
        check_placeholders = bool(getattr(wf_cfg, "validate_placeholders_on_save", True))
        check_roles = bool(getattr(wf_cfg, "validate_role_refs_on_save", True))
        errors = wf.validate(
            check_placeholders=check_placeholders,
            role_checker=(role_checker if (check_roles and role_checker is not None) else None),
        )
        if errors:
            raise ValueError("工作流定义有误：\n" + "\n".join(f"  - {e}" for e in errors))

        try:
            import yaml  # type: ignore
            content = yaml.dump(
                wf.to_dict(),
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
                indent=2,
            )
        except ImportError:
            import json
            content = "# YAML not available, saved as JSON comment format\n"
            content += json.dumps(wf.to_dict(), ensure_ascii=False, indent=2)

        # [阶段2] 若该名字已经是文件夹模式（或调用方通过 source_dir 显式
        # 指定了文件夹模式），写入 <name>/workflow.yaml，而不是新建一个
        # 与目录同名的 <name>.yaml（避免两种模式并存导致加载歧义）。
        if self._dir_entry_path(wf.name).exists() or wf.source_dir is not None:
            dir_path = self._dir_path(wf.name)
            dir_path.mkdir(parents=True, exist_ok=True)
            path = self._dir_entry_path(wf.name)
        else:
            path = self._path(wf.name)
        path.write_text(content, encoding="utf-8")
        return path

    def save_as_dir(self, wf: WorkflowDef, cfg=None, role_checker=None) -> Path:
        """
        [workflow_directory_mode_design.md 阶段2] 强制以文件夹模式保存：
        建立 <workflows_dir>/<name>/{agents,skills,prompts}/ 空目录（已存在则跳过），
        主入口写到 <name>/workflow.yaml。返回 workflow.yaml 的路径。
        """
        dir_path = self._dir_path(wf.name)
        for sub in ("agents", "skills", "prompts"):
            (dir_path / sub).mkdir(parents=True, exist_ok=True)
        wf.source_dir = dir_path
        return self.save(wf, cfg=cfg, role_checker=role_checker)

    def to_dir(self, name: str) -> Path:
        """
        [阶段2] 把已有的单文件工作流升级为文件夹模式（CLI: workflow to-dir）。
        原 <name>.yaml 的内容原样迁移进 <name>/workflow.yaml，随后删除旧的
        单文件（避免两种模式同时存在造成后续加载歧义）。已经是文件夹模式则
        直接返回现有路径，不做任何改动。
        """
        dir_entry = self._dir_entry_path(name)
        if dir_entry.exists():
            return dir_entry
        flat = self._path(name)
        if not flat.exists():
            raise ValueError(f"工作流不存在：{name!r}")
        wf = self._load_path(flat)
        if wf is None:
            raise ValueError(f"工作流 {name!r} 加载失败，无法转换为文件夹模式")
        wf.source_dir = None  # 避免 save() 误判走旧目录逻辑前先清空
        path = self.save_as_dir(wf)
        flat.unlink()
        return path

    def load(self, name: str) -> Optional[WorkflowDef]:
        """按名称加载工作流，不存在返回 None。优先文件夹模式，其次单文件模式。"""
        path = self._resolve_path(name)
        if path is None:
            return None
        return self._load_path(path)

    def _load_path(self, path: Path) -> Optional[WorkflowDef]:
        try:
            text = path.read_text(encoding="utf-8")
            try:
                import yaml  # type: ignore
                data = yaml.safe_load(text) or {}
            except ImportError:
                import json
                data = json.loads(text)
            wf = WorkflowDef.from_dict(data)
            # [阶段2] 文件夹模式：path 形如 <name>/workflow.yaml，
            # source_dir 即其父目录；单文件模式则保持 None。
            if path.name == "workflow.yaml" and path.parent != self._dir:
                wf.source_dir = path.parent
            self._resolve_prompt_files(wf, path)
            return wf
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where='mini_agent.workflow.store.WorkflowStore._load_path')
            import mini_agent.ui.renderer as R
            R.print_warning(f"[WorkflowStore] 加载 {path.name} 失败: {e}")
            return None

    @staticmethod
    def _resolve_prompt_files(wf: WorkflowDef, entry_path: Path) -> None:
        """
        [workflow_directory_mode_design.md 阶段2] 把每个 step.prompt_file
        指向的模板文件读出来，覆盖填充 step.prompt。相对路径的基准目录：
        文件夹模式为 wf.source_dir，单文件模式为 entry_path 所在目录。
        文件缺失时保留 step.prompt 原值（通常为空串）并打印警告，不中断
        整体加载（工作流其它字段仍可用于展示/编辑）。
        """
        base_dir = wf.source_dir if wf.source_dir is not None else entry_path.parent
        for step in wf.steps:
            if not step.prompt_file:
                continue
            fpath = (base_dir / step.prompt_file).resolve()
            try:
                step.prompt = fpath.read_text(encoding="utf-8")
            except Exception as e:
                from mini_agent.errors import log_exception
                log_exception(e, where='mini_agent.workflow.store.WorkflowStore._resolve_prompt_files')
                import mini_agent.ui.renderer as R
                R.print_warning(
                    f"[WorkflowStore] 步骤 {step.id!r} 的 prompt_file 读取失败："
                    f"{step.prompt_file!r}（{e}）"
                )

    def delete(self, name: str) -> bool:
        """删除工作流，返回是否成功（文件夹模式下删除整个目录）。"""
        dir_entry = self._dir_entry_path(name)
        if dir_entry.exists():
            import shutil
            shutil.rmtree(self._dir_path(name))
            return True
        path = self._path(name)
        if path.exists():
            path.unlink()
            return True
        return False

    # ── 列举 ────────────────────────────────────────────────────────────────

    def list_all(self) -> list[dict]:
        """
        列举所有已保存工作流的元信息。
        返回 list[dict]，每项包含：name, description, version, step_count, path
        """
        result = []
        # 单文件模式
        for yaml_file in sorted(self._dir.glob("*.yaml")):
            wf = self._load_path(yaml_file)
            if wf:
                result.append({
                    "name": wf.name,
                    "description": wf.description,
                    "version": wf.version,
                    "step_count": len(wf.steps),
                    "steps": [s.id for s in wf.steps],
                    "path": str(yaml_file),
                })
        # [阶段2] 文件夹模式：<workflows_dir>/<subdir>/workflow.yaml
        for sub in sorted(p for p in self._dir.iterdir() if p.is_dir()):
            entry = sub / "workflow.yaml"
            if not entry.exists():
                continue
            wf = self._load_path(entry)
            if wf:
                result.append({
                    "name": wf.name,
                    "description": wf.description,
                    "version": wf.version,
                    "step_count": len(wf.steps),
                    "steps": [s.id for s in wf.steps],
                    "path": str(entry),
                })
        return result

    def exists(self, name: str) -> bool:
        return self._resolve_path(name) is not None

    # ── 导出 ────────────────────────────────────────────────────────────────

    def export_yaml(self, name: str) -> Optional[str]:
        """把工作流导出为 YAML 字符串（用于展示给用户编辑）。"""
        path = self._resolve_path(name)
        if path is None:
            return None
        return path.read_text(encoding="utf-8")

    # ── 内置模板库（workflow机制改进计划.md P6）───────────────────────────────
    #
    # 模板与用户已保存的工作流是两套独立存储：模板是随包分发的只读 YAML，
    # 放在 workflow/templates/ 下；instantiate_template 读取模板、替换
    # name 字段为用户指定的新名字后返回一个新的 WorkflowDef（此时还未落盘，
    # 调用方仍需自行调用 save() 才会真正写入 .agent/workflows/）。
    # 这样设计是为了让"从模板创建"复用与"手写 YAML 保存"完全相同的
    # save() 校验路径，不会绕过 P6 的引用完整性校验。

    @staticmethod
    def _templates_dir() -> Path:
        return Path(__file__).resolve().parent / "templates"

    def list_templates(self) -> list[dict]:
        """列举内置模板的元信息：name, description, step_count, steps。"""
        result = []
        tdir = self._templates_dir()
        if not tdir.exists():
            return result
        for yaml_file in sorted(tdir.glob("*.yaml")):
            wf = self._load_path(yaml_file)
            if wf:
                result.append({
                    "name": yaml_file.stem,
                    "description": wf.description,
                    "step_count": len(wf.steps),
                    "steps": [s.id for s in wf.steps],
                })
        return result

    def instantiate_template(self, template_name: str, new_name: str) -> WorkflowDef:
        """
        基于内置模板 template_name 创建一个新的 WorkflowDef（尚未保存）。
        只替换顶层 name 字段为 new_name，模板内部的 step 结构原样保留——
        模板里的 prompt 占位符（如 {input}）由 run_workflow 时传入的
        inputs 解析，与"创建工作流"这一步无关。
        """
        tdir = self._templates_dir()
        path = tdir / f"{template_name}.yaml"
        if not path.exists():
            available = [p.stem for p in sorted(tdir.glob("*.yaml"))] if tdir.exists() else []
            raise ValueError(f"模板不存在：{template_name!r}（可用模板：{available}）")
        wf = self._load_path(path)
        if wf is None:
            raise ValueError(f"模板 {template_name!r} 加载失败，请检查模板 YAML 是否损坏")
        wf.name = new_name
        return wf
