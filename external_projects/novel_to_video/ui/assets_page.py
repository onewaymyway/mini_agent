"""ui/assets_page.py — 全书级素材（G-2 / G-3）：补全、逐条审核、人工编辑、定妆图画廊。"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from novel_to_video import project_store as ps
from novel_to_video import tool_api

from .common import DEFAULT_PROJECTS_ROOT

STATUS_LABEL = {"missing": "缺失", "pending": "待审", "approved": "已通过", "stale": "需重生成",
                "failed": "失败"}


def _pick_project(root: str):
    listed = tool_api.list_projects(root)
    if not listed["ok"] or not listed["data"]:
        st.info("还没有项目。请先在「导入导出目录」页创建。")
        return None
    rows = [r for r in listed["data"] if "error" not in r]
    dirs = [r["dir"] for r in rows]
    return st.selectbox("项目", dirs, format_func=lambda p: Path(p).name, key="assets_pick")


def _run_panel(pdir: str) -> None:
    from ui.joblog_panel import render_job_panel
    st.markdown("#### 生成")
    c1, c2, c3 = st.columns(3)
    if c1.button("① 补全视觉与声音设定（G-2）", key="assets_text", type="primary"):
        _start_result(tool_api.start_visual_text_background(pdir))
    if c2.button("② 生成缺失的定妆图（G-3）", key="assets_portrait"):
        _start_result(tool_api.start_portraits_background(pdir))
    with c3:
        ids = st.text_input("重生成的实体 id（逗号分隔）", key="assets_regen_ids", placeholder="char_001")
        if st.button("重新生成所选定妆图", key="assets_regen", disabled=not ids.strip()):
            _start_result(tool_api.start_portraits_background(
                pdir, ids=[x.strip() for x in ids.split(",") if x.strip()], regenerate=True))
    render_job_panel(pdir, ["assets"], title="素材生成日志")


def _start_result(res: dict) -> None:
    if res["ok"]:
        st.toast(f"已开始：{res['data']['label']}")
    else:
        st.error(res["error"]["message"])


def _show_result(label: str, res: dict) -> None:
    if not res["ok"]:
        st.error(f"{label} 失败：{res['error']['message']}")
        return
    data = res["data"]
    st.success(f"{label} 完成：成功 {len(data.get('done', []))}，失败 {len(data.get('failed', []))}，"
               f"跳过 {len(data.get('skipped', []))}")
    if data.get("stopped"):
        st.error(data["stopped"])
    for f in data.get("failed", []):
        st.warning(f"{f['id']}：{f['error']}")


def _status_table(status: dict) -> None:
    st.markdown("#### 审核状态")
    cnt = status["counts"]
    st.progress(cnt["approved"] / cnt["total"] if cnt["total"] else 0.0,
                text=f"已通过 {cnt['approved']} / {cnt['total']}")
    if status["ready"]:
        st.success("全书级素材已全部通过，可以进入章节生产（Phase 2）。")
    st.dataframe([{
        "类型": "角色" if r["kind"] == "character" else "地点",
        "id": r["id"], "名称": r["name"],
        "缺失字段": "、".join(r["missing_fields"]) or "无",
        "设定": STATUS_LABEL.get(r["text_status"], r["text_status"]),
        "定妆图": STATUS_LABEL.get(r["portrait_status"], r["portrait_status"]),
        "版本": r["portrait_version"], "错误": r["last_error"] or "",
    } for r in status["rows"]], width='stretch', hide_index=True)


def _gallery(pdir: str, status: dict) -> None:
    st.markdown("#### 逐条审核")
    for r in status["rows"]:
        with st.container(border=True):
            left, right = st.columns([1, 2])
            with left:
                path = Path(pdir) / r["asset_path"] if r["asset_path"] else None
                if path and path.is_file():
                    st.image(str(path), caption=f"{r['id']} · {r['name']} · v{r['portrait_version']}",
                             width='stretch')
                else:
                    st.caption(f"{r['id']} · {r['name']} · 暂无定妆图")
            with right:
                st.markdown(f"**{r['name']}**（{r['id']}）　设定：{STATUS_LABEL.get(r['text_status'])}"
                            f"　定妆图：{STATUS_LABEL.get(r['portrait_status'])}")
                _entity_detail(pdir, r)
                b1, b2, b3 = st.columns(3)
                if b1.button("通过设定", key=f"ok_t_{r['key']}", disabled=r["text_status"] != "pending"
                             or bool(r["missing_fields"])):
                    tool_api.review_asset(pdir, r["kind"], r["id"], "approve", "text")
                    st.rerun()
                if b2.button("通过定妆图", key=f"ok_p_{r['key']}",
                             disabled=r["portrait_status"] not in ("pending",)):
                    tool_api.review_asset(pdir, r["kind"], r["id"], "approve", "portrait")
                    st.rerun()
                if b3.button("驳回并重生成", key=f"rj_{r['key']}",
                             disabled=r["portrait_status"] not in ("pending", "approved", "stale")):
                    _start_result(tool_api.start_portraits_background(pdir, ids=[r["id"]], regenerate=True))
                    st.rerun()


def _entity_detail(pdir: str, r: dict) -> None:
    info = ps.read_json(Path(pdir) / "global" / ("characters.json" if r["kind"] == "character"
                                                 else "locations.json"))
    ent = next((e for e in info["characters" if r["kind"] == "character" else "locations"]
                if e["id"] == r["id"]), {})
    with st.expander("查看与编辑设定"):
        anchor = st.text_area("visual_anchor_en（定妆图提示核心）", value=ent.get("visual_anchor_en") or "",
                              key=f"anc_{r['key']}")
        fields = {"visual_anchor_en": anchor}
        if r["kind"] == "character":
            voice = st.text_input("voice_profile（配音音色设定）", value=ent.get("voice_profile") or "",
                                  key=f"voi_{r['key']}")
            gender = st.selectbox("性别", ["", "male", "female", "unknown"],
                                  index=["", "male", "female", "unknown"].index(ent.get("gender") or "")
                                  if (ent.get("gender") or "") in ("", "male", "female", "unknown") else 0,
                                  key=f"gen_{r['key']}")
            fields.update(voice_profile=voice, gender=gender or None)
        src = {k: ent.get(f"{k}_source") for k in ("visual_anchor", "voice_profile", "gender")
               if ent.get(f"{k}_source")}
        if src:
            st.caption("来源：" + "，".join(f"{k}={v}" for k, v in src.items()))
        if st.button("保存修改", key=f"save_{r['key']}"):
            clean = {k: v for k, v in fields.items() if v is not None and ent.get(k) != v}
            if clean:
                res = tool_api.edit_asset(pdir, r["kind"], r["id"], clean)
                if res["ok"]:
                    st.success("已保存。改动视觉字段时，相关定妆图会标记为需重生成。")
                    st.rerun()
                else:
                    st.error(res["error"]["message"])


def page_assets() -> None:
    st.header("全书级素材（G-2 / G-3）")
    st.caption("角色与地点设定对全书共用。每一条都需要人工通过后才算完成。")
    root = st.text_input("项目存放目录", value=DEFAULT_PROJECTS_ROOT, key="assets_root")
    pdir = _pick_project(root)
    if not pdir:
        return
    _run_panel(pdir)
    status = tool_api.get_visual_status(pdir)
    if not status["ok"]:
        st.error(status["error"]["message"])
        return
    data = status["data"]
    st.caption(f"API 调用次数：文本 {data['api_calls']['text']}，图像 {data['api_calls']['image']}")
    _status_table(data)
    _gallery(pdir, data)
