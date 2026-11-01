"""ui/import_page.py — 导入 novel_writer 导出目录。

流程：填写路径 → 检查（只读，不落盘）→ 确认报告 → 创建项目。
检查结果保存在 session_state 中，便于在创建前反复查看问题清单。
"""

from __future__ import annotations

import streamlit as st

from novel_to_video import project_store as ps
from novel_to_video import tool_api

from .common import DEFAULT_PROJECTS_ROOT, LEVEL_LABEL


def _render_report(report: dict) -> None:
    stats = report["stats"]
    cols = st.columns(5)
    cols[0].metric("章节", stats["chapters"])
    cols[1].metric("角色", stats["characters"])
    cols[2].metric("地点", stats["locations"])
    cols[3].metric("阻断错误", stats["errors"])
    cols[4].metric("提示", stats["warnings"])

    if report["issues"]:
        st.markdown("#### 导入问题")
        st.dataframe(
            [{"级别": LEVEL_LABEL.get(i["level"], i["level"]), "代码": i["code"],
              "说明": i["message"], "位置": i.get("path") or ""} for i in report["issues"]],
            width='stretch', hide_index=True,
        )
    else:
        st.success("没有导入问题。")

    with st.expander(f"章节清单（{len(report['chapters'])} 章）"):
        st.dataframe(
            [{"章号": c["no"], "章名": c["title"], "字数": c["char_count"],
              "有总结": "是" if c["summary_file"] else "否", "源文件": c["source_file"]}
             for c in report["chapters"]],
            width='stretch', hide_index=True,
        )
    with st.expander(f"角色库（{len(report['characters'])} 个，来自人设卡）"):
        st.dataframe(
            [{"id": c["id"], "姓名": "、".join(c["names"]), "角色": c["role"],
              "年龄段": c["age_range"], "性别": c["gender"] or "（缺失，G-2 补全）",
              "首次出场章": c["first_appear"], "人设卡": "有" if c["persona_card"] else "无"}
             for c in report["characters"]],
            width='stretch', hide_index=True,
        )
    with st.expander(f"地点库（{len(report['locations'])} 个）"):
        st.dataframe(
            [{"id": l["id"], "名称": l["name"], "类型": l["location_type"],
              "首次出现章": l["first_appear"]} for l in report["locations"]],
            width='stretch', hide_index=True,
        )


def page_import() -> None:
    st.header("导入 novel_writer 导出目录")
    st.caption("导入只读取导出目录，不会修改它。检查通过后再创建项目。")

    col1, col2 = st.columns([3, 2])
    with col1:
        src = st.text_input("导出目录路径（含 正文/、设定/、README.md 的那一层）", key="imp_src")
        title = st.text_input("书名（导出包不含书名，留空则取目录名）", key="imp_title")
    with col2:
        spec = st.text_input("章节范围（留空 = 全部；示例 1-10,12,20-）", key="imp_spec")
        unlimited = st.checkbox("不限制时长（按原文实际情况生成，不限制字数）", key="imp_unlimited")
        duration = st.number_input("单章目标时长（秒）", min_value=30, max_value=300, value=90, step=10,
                                   key="imp_dur", disabled=unlimited)
        duration = 0 if unlimited else duration
        r_s = st.number_input("剧本自动修正次数", min_value=0, max_value=ps.MAX_AUTO_FIX_RETRIES,
                              value=ps.DEFAULT_AUTO_FIX_RETRIES, step=1, key="imp_retry_s")
        r_p = st.number_input("镜头规划自动修正次数", min_value=0, max_value=ps.MAX_AUTO_FIX_RETRIES,
                              value=ps.DEFAULT_AUTO_FIX_RETRIES, step=1, key="imp_retry_p")
        retry_opts = {"script_max_retries": int(r_s), "shots_max_retries": int(r_p)}
        root = st.text_input("项目存放目录", value=DEFAULT_PROJECTS_ROOT, key="imp_root")

    if st.button("检查导出目录", type="primary", disabled=not src.strip(), key="imp_check"):
        res = tool_api.create_project_from_source(
            root, src.strip(), title=title.strip() or None,
            chapter_spec=spec.strip() or None, options={"target_chapter_duration_sec": int(duration), **retry_opts},
            dry_run=True,
        )
        st.session_state["imp_result"] = res

    res = st.session_state.get("imp_result")
    if not res:
        return
    if not res["ok"]:
        st.error(res["error"]["message"])
        if res["data"] and res["data"].get("issues"):
            _render_report(res["data"])
        return

    data = res["data"]
    st.info(f"将选中 {len(data['selected_chapters'])} 章：{', '.join(map(str, data['selected_chapters']))}")
    _render_report(data["report"])

    if st.button("创建项目", type="primary", key="imp_create"):
        created = tool_api.create_project_from_source(
            root, src.strip(), title=title.strip() or None,
            chapter_spec=spec.strip() or None, options={"target_chapter_duration_sec": int(duration), **retry_opts},
        )
        if created["ok"]:
            st.success(f"项目已创建：{created['data']['project_dir']}")
            st.session_state.pop("imp_result", None)
        else:
            st.error(created["error"]["message"])
