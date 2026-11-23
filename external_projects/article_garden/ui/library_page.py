"""ui/library_page.py — 📚 文章库。P0：只列出库内 Markdown 文件；导入、摘要、过滤、预览在 P1 提供。"""

from __future__ import annotations

import streamlit as st

from ui import state
from ui.topbar import render_topbar


def render_library_page() -> None:
    ctx = state.current_ctx()
    if ctx is None:
        return
    render_topbar(ctx, "📚 文章库")
    rows = []
    for origin, d in (("导入", ctx.imported_dir), ("生成", ctx.generated_dir)):
        if d.is_dir():
            for p in sorted(d.rglob("*.md")):
                rows.append({"来源": origin, "文件": p.relative_to(ctx.root).as_posix(),
                             "大小(KB)": round(p.stat().st_size / 1024, 1)})
    if not rows:
        st.info("库里还没有文章。导入功能（上传 md / zip / 目录，含导入预览）在 **P1** 提供。")
        st.caption(f"现在也可以手动把 Markdown 文件放进：`{ctx.imported_dir}`，刷新本页即可看到。")
        return
    st.dataframe(rows, use_container_width=True, hide_index=True)
    st.caption("摘要状态、所属节点、概念数、过滤与预览在 P1–P3 提供。")
