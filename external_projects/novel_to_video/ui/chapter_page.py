"""ui/chapter_page.py — 章节生产：运行流水线、停止点提示、小场景逐镜审核。"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from novel_to_video import tool_api

from .common import DEFAULT_PROJECTS_ROOT

STATUS_LABEL = {"pending": "待生成", "running": "运行中", "waiting_assets": "等待素材确认",
                "waiting_review": "等待审核", "done": "已完成", "failed": "失败"}
SHOT_LABEL = {"missing": "未生成", "pending": "待审", "approved": "已通过", "stale": "需重审",
              "failed": "失败"}
STAGE_LABEL = {"script": "C-1/2 剧本", "shots": "C-3/4 镜头规划", "assets": "C-5 素材补全",
               "audio": "C-6 配音", "prompts": "C-7 提示词与一致性", "clips": "C-8 小场景视频",
               "compose": "C-9 章节合成"}


def _pick_project(root: str):
    listed = tool_api.list_projects(root)
    if not listed["ok"] or not listed["data"]:
        st.info("还没有项目。请先在「导入导出目录」页创建。")
        return None
    dirs = [r["dir"] for r in listed["data"] if "error" not in r]
    return st.selectbox("项目", dirs, format_func=lambda p: Path(p).name, key="chap_pick")


def _run_panel(pdir: str, overview: list) -> None:
    from ui.joblog_panel import render_job_panel
    selected = [r["no"] for r in overview if r.get("selected")]
    c1, c2 = st.columns(2)
    no = c1.selectbox("章节", selected, key="chap_no") if selected else None
    if no is not None and c1.button(f"运行第 {no} 章流水线", type="primary", key="chap_run"):
        _queue_result(tool_api.start_queue_background(pdir, [no]))
    if selected and c2.button("运行全部已选章节", key="chap_run_all"):
        _queue_result(tool_api.start_queue_background(pdir))
    render_job_panel(pdir, ["chapter", "queue"], title="章节运行日志", height=320)


def _queue_result(res: dict) -> None:
    if res["ok"]:
        st.toast("已在后台开始运行，可在下方查看进度")
    else:
        st.error(res["error"]["message"])


def _show_run(res: dict) -> None:
    if not res["ok"]:
        st.error(res["error"]["message"])
        return
    d = res["data"]
    label = STATUS_LABEL.get(d["status"], d["status"])
    if d["status"] == "done":
        st.success(f"{label}：{d['message']}")
    elif d["status"] == "failed":
        st.error(f"{label}：{d['message']}")
    else:
        st.warning(f"{label}：{d['message']}")
    if d["status"] == "waiting_assets":
        st.info("请到「全书素材」页确认补建角色的设定与定妆图，然后回到这里重新运行。")
    if d.get("issues"):
        st.dataframe([i for i in d["issues"]], width='stretch', hide_index=True)


def _stages(view: dict) -> None:
    rows = []
    for key, label in STAGE_LABEL.items():
        st_info = view["stages"].get(key, {})
        rows.append({"阶段": label, "状态": st_info.get("status", "未运行"),
                     "说明": st_info.get("error") or ""})
    st.dataframe(rows, width='stretch', hide_index=True)
    if view.get("counts"):
        c = view["counts"]
        cols = st.columns(5)
        cols[0].metric("已通过", c.get("approved", 0))
        cols[1].metric("待审", c.get("pending", 0))
        cols[2].metric("需重审", c.get("stale", 0))
        cols[3].metric("未生成", c.get("missing", 0))
        cols[4].metric("失败", c.get("failed", 0))


def _shot_card(pdir: str, no: int, sh: dict) -> None:
    root = Path(pdir)
    with st.container(border=True):
        left, right = st.columns([1, 2])
        with left:
            if sh.get("clip") and (root / sh["clip"]).is_file():
                st.video(str(root / sh["clip"]))
            else:
                st.caption("暂无视频")
            if sh.get("audio") and (root / sh["audio"]).is_file():
                st.audio(str(root / sh["audio"]))
        with right:
            dur = sh.get("duration_sec")
            st.markdown(f"**{sh['id']}**　场次 {sh['scene_no']}　地点 {sh.get('location_id') or '-'}"
                        f"　时长 {dur or '-'} 秒　状态：**{SHOT_LABEL.get(sh['state'], sh['state'])}**")
            if sh.get("error") or sh.get("clip_error"):
                st.error(sh.get("error") or sh.get("clip_error"))
            for b in sh["blocks"]:
                who = "旁白" if b["type"] == "narration" else (b.get("speaker_id") or b.get("speaker_name") or "?")
                st.write(f"· **{who}**：{b['text']}")
            st.caption(f"画面：{sh.get('visual_hint_zh') or ''}　|　{sh.get('visual_hint_en') or ''}")

            b1, b2 = st.columns([1, 2])
            if b1.button("通过", key=f"ok_{no}_{sh['id']}", disabled=sh["state"] not in ("pending", "stale")):
                res = tool_api.review_shot(pdir, no, sh["id"], "approve")
                st.rerun() if res["ok"] else st.error(res["error"]["message"])
            note = b2.text_input("驳回意见（驳回后重新生成视频）", key=f"note_{no}_{sh['id']}")
            if st.button("驳回并重生成", key=f"rj_{no}_{sh['id']}",
                         disabled=sh["state"] not in ("pending", "stale", "approved") or not note.strip()):
                res = tool_api.review_shot(pdir, no, sh["id"], "reject", note)
                st.rerun() if res["ok"] else st.error(res["error"]["message"])

            with st.expander("编辑镜头内容"):
                en = st.text_area("画面描述（英文）", value=sh.get("visual_hint_en") or "", key=f"en_{no}_{sh['id']}")
                zh = st.text_input("画面描述（中文）", value=sh.get("visual_hint_zh") or "", key=f"zh_{no}_{sh['id']}")
                texts = {}
                for b in sh["blocks"]:
                    texts[b["idx"]] = st.text_input(
                        f"口播 {b['idx']}（{'旁白' if b['type'] == 'narration' else '对白，须逐字来自剧本'}）",
                        value=b["text"], key=f"bt_{no}_{sh['id']}_{b['idx']}")
                if st.button("保存修改（修改后请重新运行本章）", key=f"save_{no}_{sh['id']}"):
                    changed = {i: t for i, t in texts.items()
                               if t != next(x["text"] for x in sh["blocks"] if x["idx"] == i)}
                    res = tool_api.edit_shot(pdir, no, sh["id"], visual_hint_en=en, visual_hint_zh=zh,
                                             block_texts=changed or None)
                    if res["ok"]:
                        st.success("已保存。下次运行本章时会自动重算音频、提示词并重新生成视频。")
                    else:
                        st.error(res["error"]["message"])


def page_chapters() -> None:
    st.header("章节生产（C-1 … C-9）")
    st.caption("每章：剧本 → 镜头 → 补建角色确认 → 配音 → 提示词 → 小场景视频 → 逐镜审核 → 章节合成。")
    root = st.text_input("项目存放目录", value=DEFAULT_PROJECTS_ROOT, key="chap_root")
    pdir = _pick_project(root)
    if not pdir:
        return
    ov = tool_api.chapters_overview(pdir)
    if not ov["ok"]:
        st.error(ov["error"]["message"])
        return
    overview = ov["data"]
    st.dataframe([{"章号": r["no"], "章名": r["title"], "选中": r["selected"],
                   "状态": STATUS_LABEL.get(r["status"], r["status"]), "说明": r.get("message") or ""}
                  for r in overview], width='stretch', hide_index=True)
    _run_panel(pdir, overview)

    no = st.session_state.get("chap_no")
    if no is None:
        return
    view = tool_api.chapter_view(pdir, no)
    if not view["ok"]:
        st.error(view["error"]["message"])
        return
    v = view["data"]
    st.subheader(f"第 {no} 章 · {v['title']}")
    _stages(v)
    if v.get("output"):
        st.markdown("#### 章节成片")
        st.video(str(Path(pdir) / v["output"]))
    if v.get("script"):
        with st.expander("剧本"):
            st.code(v["script"], language="markdown")
    if v["shots"]:
        st.markdown("#### 小场景逐镜审核")
        for sh in v["shots"]:
            _shot_card(pdir, no, sh)
    else:
        st.caption("还没有镜头规划，先运行本章流水线。")
