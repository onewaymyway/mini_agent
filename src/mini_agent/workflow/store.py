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
    # [P7-③2 workflow_mechanism_improvement_plan.md] 可复用 step 片段存放目录。
    SNIPPETS_DIR = ".agent/workflow_snippets"

    def __init__(self, project_root: Path) -> None:
        self._root = project_root
        self._dir = project_root / self.WORKFLOWS_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._snippets_dir = project_root / self.SNIPPETS_DIR

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
            self._expand_includes(data)
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

    # ── 可复用 step 片段（workflow_mechanism_improvement_plan.md P7-③2）──────
    #
    # 片段文件：<project_root>/.agent/workflow_snippets/<n>.yaml，格式是一段
    # `steps:` 列表（与 workflow YAML 的 steps 字段同构，但不包含 name/
    # description 等顶层 workflow 字段）。workflow YAML 里某个 step 写
    # `include: <snippet_name>` 时，_load_path() 在调用 WorkflowDef.from_dict()
    # 之前，先对原始 dict 做一次纯文本级展开：把这个 include 条目替换成片段
    # 里的实际 steps。这是纯加载期行为，展开后的 WorkflowDef 与手写完整
    # YAML 没有任何区别，不涉及 runner.py 执行逻辑改动。
    #
    # 命名空间化规则：片段里每个 step 的 id 都会被加上
    # `"{include_step_id}__"` 前缀（include_step_id 即引用处写的 id 字段），
    # 避免同一个片段被多次 include 时 id 冲突。片段内部 steps 互相引用的
    # depends_on 会同步改写为加前缀后的 id；片段第一层（片段内没有
    # depends_on 或依赖的 id 不在片段内）的 step 会自动接上 include 条目
    # 自己声明的 depends_on，把片段"接入"到外部依赖图里。workflow 中其它
    # step 若通过 depends_on 或 prompt 占位符 `{include_step_id.output}`
    # 引用了这个 include 条目本身，会被改写为指向片段展开后的最后一个
    # step（视为这段片段的"输出代表"）。

    def _snippet_path(self, name: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
        return self._snippets_dir / f"{safe}.yaml"

    def list_snippets(self) -> list[dict]:
        """列举所有已保存的可复用 step 片段：name, step_count, steps。"""
        result = []
        if not self._snippets_dir.exists():
            return result
        for yaml_file in sorted(self._snippets_dir.glob("*.yaml")):
            try:
                steps = self.load_snippet(yaml_file.stem)
            except Exception:
                continue
            result.append({
                "name": yaml_file.stem,
                "step_count": len(steps),
                "steps": [s.get("id", "") for s in steps],
            })
        return result

    def load_snippet(self, name: str) -> list[dict]:
        """加载一个片段的原始 steps 列表（未展开占位符，纯 dict）。"""
        path = self._snippet_path(name)
        if not path.exists():
            raise ValueError(f"可复用 step 片段不存在：{name!r}")
        text = path.read_text(encoding="utf-8")
        try:
            import yaml  # type: ignore
            data = yaml.safe_load(text) or {}
        except ImportError:
            import json
            data = json.loads(text)
        steps = data.get("steps", [])
        if not isinstance(steps, list):
            raise ValueError(f"片段 {name!r} 格式有误：steps 必须是列表")
        return steps

    def save_snippet(self, name: str, steps: list[dict]) -> Path:
        """保存一个可复用 step 片段（覆盖同名）。steps 为原始 dict 列表。"""
        self._snippets_dir.mkdir(parents=True, exist_ok=True)
        try:
            import yaml  # type: ignore
            content = yaml.dump({"steps": steps}, allow_unicode=True, sort_keys=False, indent=2)
        except ImportError:
            import json
            content = json.dumps({"steps": steps}, ensure_ascii=False, indent=2)
        path = self._snippet_path(name)
        path.write_text(content, encoding="utf-8")
        return path

    def delete_snippet(self, name: str) -> bool:
        path = self._snippet_path(name)
        if path.exists():
            path.unlink()
            return True
        return False

    def _expand_includes(self, data: dict) -> None:
        """
        原地展开 data["steps"] 里所有 `include: <snippet_name>` 条目。
        找不到的片段会抛 ValueError（加载失败会被 _load_path 的外层
        try/except 捕获，打印警告并返回 None，与其它加载失败场景一致）。
        """
        raw_steps = data.get("steps", [])
        if not isinstance(raw_steps, list) or not any(
            isinstance(s, dict) and s.get("include") for s in raw_steps
        ):
            return  # 没有 include 条目，快速跳过

        # id_rewrite: include 条目自身的 id -> 展开后代表它的"输出" step id
        # （取片段展开后最后一个 step 的 id），用于改写其它 step 的
        # depends_on / prompt 占位符引用。
        id_rewrite: dict[str, str] = {}
        expanded: list[dict] = []

        for entry in raw_steps:
            if not (isinstance(entry, dict) and entry.get("include")):
                expanded.append(entry)
                continue

            include_id = str(entry.get("id") or entry["include"])
            snippet_steps = self.load_snippet(str(entry["include"]))
            prefix = f"{include_id}__"

            # 片段内部 id 集合，用于判断 depends_on 引用是"片段内部"还是"外部"。
            inner_ids = {str(s.get("id", "")) for s in snippet_steps if isinstance(s, dict)}

            snippet_expanded: list[dict] = []
            for s in snippet_steps:
                if not isinstance(s, dict):
                    continue
                s2 = dict(s)
                orig_id = str(s2.get("id", ""))
                s2["id"] = f"{prefix}{orig_id}"
                deps = list(s2.get("depends_on") or [])
                new_deps = [f"{prefix}{d}" if str(d) in inner_ids else d for d in deps]
                # 片段"入口" step（内部没有依赖，或依赖项不在片段内）自动
                # 接上 include 条目自己声明的外部 depends_on，把片段接入
                # 外部依赖图。
                if not deps:
                    new_deps = list(entry.get("depends_on") or [])
                s2["depends_on"] = new_deps
                # 片段内部 prompt 里对其它片段内 step 的占位符引用
                # （如 {score.output}）同步加前缀，否则展开后指向的是
                # 不存在的裸 id。
                inner_prompt = s2.get("prompt")
                if isinstance(inner_prompt, str) and inner_prompt:
                    import re as _re

                    def _inner_sub(m, _inner_ids=inner_ids, _prefix=prefix):
                        key = m.group(1)
                        ref_id, _, ref_field = key.partition(".")
                        if ref_id in _inner_ids:
                            return "{" + _prefix + ref_id + ("." + ref_field if ref_field else "") + "}"
                        return m.group(0)
                    s2["prompt"] = _re.sub(r"\{([^}]+)\}", _inner_sub, inner_prompt)
                snippet_expanded.append(s2)

            if snippet_expanded:
                id_rewrite[include_id] = str(snippet_expanded[-1]["id"])
            expanded.extend(snippet_expanded)

        # 第二遍：改写其余（非 include 展开出来的）step 对被 include 的
        # id 的引用——depends_on 列表 + prompt 里的 `{include_id.output}` /
        # `{include_id.score}` 占位符。
        if id_rewrite:
            import re
            for s in expanded:
                if not isinstance(s, dict):
                    continue
                deps = s.get("depends_on")
                if isinstance(deps, list):
                    s["depends_on"] = [id_rewrite.get(str(d), d) for d in deps]
                prompt = s.get("prompt")
                if isinstance(prompt, str) and prompt:
                    def _sub(m):
                        key = m.group(1)
                        ref_id, _, ref_field = key.partition(".")
                        if ref_id in id_rewrite:
                            return "{" + id_rewrite[ref_id] + ("." + ref_field if ref_field else "") + "}"
                        return m.group(0)
                    s["prompt"] = re.sub(r"\{([^}]+)\}", _sub, prompt)

        data["steps"] = expanded

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
