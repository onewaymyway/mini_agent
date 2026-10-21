"""数据迁移面板：旧版单文件 JSON → 小文件存储（蓝图 / 人物 / 伏笔）。

待迁移的项目才显示。流程：干跑 →（可选）LLM 转换 → 人工核对 → 确认提交。旧文件始终改名为 .bak 保留。
"""

from __future__ import annotations

import streamlit as st

from novel_writer import tool_api
from ui.components import show_error

_LABELS = {"blueprint": "蓝图", "characters": "人物档案", "plot": "伏笔账本"}
_RUN = {
    "blueprint": tool_api.migrate_blueprint,
    "characters": tool_api.migrate_characters,
    "plot": tool_api.migrate_plot_ledger,
}
_HINTS = {
    "dry_run": ("info", "干跑通过，未写入任何文件。"),
    "migrated": ("success", "迁移完成，旧文件已改名为 .bak。"),
    "needs_llm": ("warning", "旧文件损坏，无法确定性迁移。请先勾选上方「允许 LLM 转换」，再点击「检查」或「迁移」。"),
    "review_required": ("warning", "LLM 已生成转换结果，尚未提交。请核对结果后点击「确认提交」。"),
    "already_migrated": ("info", "已是小文件格式，无需迁移。"),
    "no_legacy": ("info", "没有旧版文件，无需迁移。"),
}


def _show(result: dict) -> None:
    if not result.get("ok"):
        show_error(result)
        return
    data = result["data"]
    kind, text = _HINTS.get(data.get("status"), ("error", f"迁移未完成：{data.get('status')}"))
    getattr(st, kind)(text)
    st.json(data)


def render_migration(ws: str, scope: str) -> None:
    """scope：调用方的唯一标识（如 codex / threads），同一次运行里多处渲染时用来区分控件 key。"""
    res = tool_api.migration_status(ws)
    if not res["ok"]:
        show_error(res)
        return
    pending = [i for i in res["data"]["items"] if i["pending"]]
    if not pending:
        return
    with st.expander(f"⚠️ 数据迁移：{len(pending)} 项旧版数据待迁移", expanded=True):
        st.caption("旧版单文件 JSON 已改为每个条目一个文件的小文件存储。建议先在副本上验证；"
                   "迁移不会删除旧文件，只改名为 .bak。")
        use_llm = st.checkbox("旧文件损坏时允许 LLM 直接转换（结果需人工复核）", key=f"{scope}_mig_use_llm")
        for item in pending:
            name = item["name"]
            st.markdown(f"**{_LABELS[name]}**　旧文件：`{item['legacy']}`")
            c1, c2, c3 = st.columns(3)
            if c1.button("干跑检查", key=f"{scope}_mig_dry_{name}"):
                _show(_RUN[name](ws, dry_run=True, use_llm=use_llm))
            if c2.button("迁移", key=f"{scope}_mig_run_{name}",
                         help="合法文件直接确定性迁移；勾选 LLM 转换后，损坏的文件由 LLM 转换并等待复核。"):
                _show(_RUN[name](ws, use_llm=use_llm))
            if use_llm and c3.button("确认提交 LLM 转换结果", key=f"{scope}_mig_confirm_{name}",
                                       help="复用上一次「检查」生成的转换结果，不再调用 LLM；若旧文件已变化或尚未检查，会拒绝提交。"):
                _show(_RUN[name](ws, confirm=True, use_llm=True))
