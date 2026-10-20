"""流水线表格：自动滚动到当前步骤（运行中；没有运行中的步骤时为下一步）。

st.dataframe 不支持程序化滚动，所以这里用一个自定义组件渲染同样的表格。
数据未变化时不重绘、不改变滚动位置。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit.components.v1 as components

_COMPONENT_DIR = Path(__file__).resolve().parent / "step_table"
_table = components.declare_component("novel_step_table", path=str(_COMPONENT_DIR))


def render_step_table(rows: List[Dict[str, Any]], current_id: Optional[str], statuses: Dict[str, str]) -> None:
    """rows: 与原表格相同的展示列；statuses: 步骤 id → 状态（用于高亮运行中的步骤）。
    rows 中每行须带 "__id" 字段（步骤 id），由本函数写入并在显示前去掉。"""
    columns = [c for c in (rows[0].keys() if rows else []) if not c.startswith("__")]
    payload_rows = []
    for r in rows:
        sid = r.get("__id")
        payload_rows.append({**{c: r.get(c) for c in columns}, "__id": sid, "__status": statuses.get(sid, "")})
    _table(
        data={"columns": columns, "rows": payload_rows, "current": current_id},
        key="novel_step_table",
        default=None,
    )
