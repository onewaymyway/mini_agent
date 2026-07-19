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

    # ── CRUD ────────────────────────────────────────────────────────────────

    def save(self, wf: WorkflowDef) -> Path:
        """保存工作流到 YAML 文件，返回文件路径。"""
        errors = wf.validate()
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

        path = self._path(wf.name)
        path.write_text(content, encoding="utf-8")
        return path

    def load(self, name: str) -> Optional[WorkflowDef]:
        """按名称加载工作流，不存在返回 None。"""
        path = self._path(name)
        if not path.exists():
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
            return WorkflowDef.from_dict(data)
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where='mini_agent.workflow.store.WorkflowStore._load_path')
            import mini_agent.ui.renderer as R
            R.print_warning(f"[WorkflowStore] 加载 {path.name} 失败: {e}")
            return None

    def delete(self, name: str) -> bool:
        """删除工作流，返回是否成功。"""
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
        return result

    def exists(self, name: str) -> bool:
        return self._path(name).exists()

    # ── 导出 ────────────────────────────────────────────────────────────────

    def export_yaml(self, name: str) -> Optional[str]:
        """把工作流导出为 YAML 字符串（用于展示给用户编辑）。"""
        path = self._path(name)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")
