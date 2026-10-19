"""output_registry — 产出项目注册表（管理项目 / 产出项目模型，P0 只读盘点）。

对应 next_doc/management_output_project_model_plan.md 第 9 章 P0 阶段。

公共 API：
    from mini_agent.output_registry import build_inventory, write_inventory_draft

P0 的全部行为都是只读的：
  - 不写 output_projects_root 下的任何文件或目录；
  - 不调用 GoalBacklog.load()（该方法会回填字段并可能写盘），而是直接读取
    .agent/goals.json 的原始 JSON；
  - 唯一写入的是草稿文件 .agent/output_registry/inventory_draft.json，
    位于 .agent 内部、且不在 output_projects_root 之下。
"""

from .inventory import (  # noqa: F401
    DRAFT_FILENAME,
    INVENTORY_SCHEMA,
    build_inventory,
    write_inventory_draft,
)
from .registry import (  # noqa: F401
    ROLE_BUILD,
    ROLE_ITERATE,
    ROLE_REFERENCE,
    RegistryError,
    bind_floating_project,
    derive_management_from_project,
    fill_management_titles,
    link_management_output,
    list_links,
    load_index,
    mark_management_root,
    rebuild_index,
    register_project,
    unlink_management_output,
)

__all__ = [
    "DRAFT_FILENAME", "INVENTORY_SCHEMA", "build_inventory", "write_inventory_draft",
    "ROLE_BUILD", "ROLE_ITERATE", "ROLE_REFERENCE", "RegistryError",
    "bind_floating_project", "derive_management_from_project", "fill_management_titles",
    "link_management_output", "list_links", "load_index", "mark_management_root",
    "rebuild_index", "register_project", "unlink_management_output",
]
