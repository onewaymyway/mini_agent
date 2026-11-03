"""ui/chapter_page.py — 章节生产：运行流水线、停止点提示、小场景逐镜审核。"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from novel_to_video import tool_api

from .common import DEFAULT_PROJECTS_ROOT
from .joblog_panel import render_job_panel

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


SPEAKER_NOTE = {
    "unplanned": "还没做镜头规划：规划时会先尝试归入已有角色，归不进去的才补建",
    "mapped": "镜头规划已归入已有角色，无需补建",
    "pending": "库中没有对应角色，运行到 C-5 会自动起草，起草后在下方确认",
    "auto_added": "已补建，需要在下方确认设定与定妆图",
    "unknown": "没能把这个说话人的台词对应到镜头（可能被改动），请查看镜头列表",
}


def _auto_added_section(pdir: str, no: int, v: dict) -> None:
    """补建角色：以镜头规划的实际结果为准，逐个说话人说明是否需要补建；需要确认的卡片直接在本页操作。"""
    from ui.auto_added_page import render_auto_added
    all_speakers = [s for s in (v.get("speakers") or []) if s["status"] != "library"]
    open_speakers = [s for s in all_speakers if s["status"] in ("unplanned", "pending")]
    handled = [s for s in all_speakers if s["status"] not in ("unplanned", "pending")]
    queue = tool_api.auto_added_queue(pdir)
    rows = queue["data"] if queue["ok"] else []
    mine_open = [r for r in rows if r["chapter"] == no
                 and not (r["text_status"] == "approved" and r["portrait_status"] == "approved")]
    if not all_speakers and not mine_open:
        return
    st.markdown("#### 补建角色（需要你确认）")
    waiting = v.get("status") == "waiting_assets"
    if waiting:
        st.warning("本章正在等待补建素材确认：请在下面完成角色、地点、道具的设定与定妆图确认，然后点上方「运行第 {0} 章流水线」继续。".format(no))
        if v.get("message"):
            st.caption("流水线报告的等待项：" + v["message"])
    if open_speakers:
        st.caption("以下说话人不在角色库中，且镜头规划后仍需要人确认：")
        st.dataframe([{"说话人": s["name"], "台词句数": s["lines"],
                       "处理结果": SPEAKER_NOTE.get(s["status"], s["status"])} for s in open_speakers],
                     width='stretch', hide_index=True)
        st.info(f"剧本本身没有问题，无需修改。点击上方「运行第 {no} 章流水线」，会在 C-5 自动起草设定，起草完成后在这里确认。")
    if mine_open:
        from ui.auto_added_page import render_batch_bar
        render_batch_bar(pdir, chapter=no, key_prefix=f"batch_ch{no}")
        open_n = render_auto_added(pdir, chapter=no, key_prefix=f"aa_ch{no}", only_open=True)
        if open_n == 0:
            st.success("本章的补建角色已全部确认，可以重新运行本章继续。")
    elif not open_speakers and not waiting:
        st.success("本章没有需要确认的补建素材。")
    if handled:
        # 已经处理完的说话人收进折叠区，只作为说明，不占用待确认区域
        with st.expander(f"已自动归入已有角色（{len(handled)}）", expanded=False):
            st.dataframe([{"说话人": s["name"], "台词句数": s["lines"],
                           "处理结果": SPEAKER_NOTE.get(s["status"], s["status"]) +
                           (f"：{s['target']}" if s["status"] == "mapped" and s.get("target") else "")}
                          for s in handled], width='stretch', hide_index=True)


def _missing_locations(v: dict) -> None:
    """缺失素材：镜头里提到、但全局地点库中没有的地点。补充到全局库的流程在下一阶段实现，这里先明确列出。"""
    names = v.get("new_locations") or []
    if not names:
        return
    specs = {sp["name"]: sp for sp in v.get("new_location_specs") or []}
    if specs:    # 地点细化：显示所属区域与规划理由
        locs = {l["id"]: l["name"] for l in v.get("all_locations") or []}
        lines = []
        for n in names:
            sp = specs.get(n) or {}
            where = f"（位于{locs.get(sp.get('parent_id'), sp.get('parent_id'))}）" if sp.get("parent_id") else ""
            kind = {"interior": "内景", "exterior": "外景"}.get(sp.get("spot_kind") or "", "")
            why = f"：{sp['reason']}" if sp.get("reason") else ""
            lines.append(f"- **{n}**{where}{'，' + kind if kind else ''}{why}")
        st.warning("镜头规划发现现有地点不能直接使用，规划了以下新场景点。运行本章（C-5）时会起草设定并写入全局地点库，"
                   "之后需要在「全书素材」中确认文本与定妆图：\n" + "\n".join(lines))
        return
    st.warning("以下地点还没有对应的全局地点：{0}。运行本章（C-5）时会为它们起草设定并写入全局地点库，"
               "之后需要在「全书素材」中确认文本与定妆图。".format("、".join(names)))


def _missing_looks(v: dict) -> None:
    """镜头里需要、但角色还没有的形象（角色多形象开启时）：C-5 会起草，需在「全书素材」确认。"""
    items = v.get("new_looks") or []
    if not items:
        return
    names = {c["id"]: c["name"] for c in v.get("all_characters") or []}
    st.warning("以下角色形象还不存在：{0}。运行本章（C-5）时会为它们起草设定，"
               "之后需要在「全书素材」中确认设定与定妆图。".format(
                   "、".join(f"{names.get(i['character_id'], i['character_id'])}·{i['label']}" for i in items)))


def _missing_props(v: dict) -> None:
    """镜头里提到、但全局道具库中没有的道具：C-5 会起草并写入全局道具库。"""
    names = v.get("new_props") or []
    if not names:
        return
    st.warning("以下道具还没有对应的全局道具：{0}。运行本章（C-5）时会为它们起草设定并写入全局道具库，"
               "之后需要在「全书素材」中确认文本与定妆图。".format("、".join(names)))


def _shots_review(v: dict) -> None:
    """C-3 镜头规划校验：自动修正次数与未解决的问题（失败时章节停在这里，这里能看到全部原因）。"""
    rv = v.get("shots_review")
    if not rv:
        return
    ok = rv.get("status") == "pass"
    if ok and rv.get("attempts", 1) <= 1:
        return
    st.markdown("#### 镜头规划校验（C-3）")
    if ok:
        st.success(f"通过：自动修正 {rv['attempts'] - 1} 次后通过")
        return
    st.error(f"未通过：已生成 {rv.get('attempts', 1)} 次仍有 {len(rv.get('issues') or [])} 个问题。"
             "完整的输入与模型原始输出见下方「模型调用记录」；也可以在「设置与队列」页调高自动修正次数后重新运行。")
    st.dataframe([{"问题": i["message"]} for i in rv.get("issues") or []], width='stretch', hide_index=True)


def _plan_warnings(pdir: str, no: int, v: dict) -> None:
    """旧版本的镜头规划质量提示：旧版代码会把超长口播按时长机械拆成多个镜头，这些镜头共用同一条画面描述。
    新版本不再这样拆（超长镜头由模型自行拆分并写不同画面），所以只有旧文件才会显示这个提示。"""
    warns = v.get("plan_warnings") or []
    # 地点细化等新提示只有 message，没有拆分字段：直接展示
    for w in warns:
        if "parts" not in w and w.get("message"):
            st.warning(w["message"])
    warns = [w for w in warns if "parts" in w]     # 旧版拆分提示：带 parts 字段
    if warns:
        total = len(v["shots"])
        split_shots = sum(w["parts"] for w in warns)
        st.warning(f"旧版镜头规划提示：本章有 {len(warns)} 段口播被旧版代码机械拆成了 {split_shots} 个镜头，"
                   f"这些镜头共用同一条画面描述，生成的视频会很相似（当前共 {total} 个镜头）。"
                   "建议重新规划本章镜头，新规划会让每镜头口播约 4–12 秒（差几秒可以接受），相邻镜头画面各不相同。")
        with st.expander("查看哪些镜头被拆分"):
            st.dataframe([{"原始镜头": w["raw_index"], "场次": w["scene_no"], "口播约（秒）": w["seconds"],
                           "拆成镜头数": w["parts"]} for w in warns], width='stretch', hide_index=True)
    # 重新规划对任何章节都可用（不只在有旧版提示时）
    sure = st.checkbox("我确认丢弃本章当前镜头（及其配音、视频、逐镜审核）并重新规划", key=f"replan_ok_{no}")
    if st.button("重新规划本章镜头", key=f"replan_{no}", disabled=not sure):
        res = tool_api.replan_shots(pdir, no)
        if res["ok"]:
            st.toast("已丢弃当前镜头规划，请点上方「运行本章流水线」重新规划")
            st.rerun()
        else:
            st.error(res["error"]["message"])


def _script_review(v: dict) -> None:
    """C-2 剧本校验结果：状态、字数与问题清单（含对白在原文中找不到的具体行）。"""
    rv = v.get("script_review")
    if not rv:
        return
    st.markdown("#### 剧本校验（C-2）")
    ok = rv.get("status") == "pass"
    (st.success if ok else st.error)(
        f"{'通过' if ok else '未通过'}　场次 {rv.get('scenes', 0)}　口播 {rv.get('spoken_chars', '-')} 字"
        f"（目标 {rv.get('target_chars', '-')} 字）　生成次数 {rv.get('attempts', 1)}")
    issues = rv.get("issues") or []
    if issues:
        st.dataframe([{"级别": "错误" if i["level"] == "error" else "提示", "说明": i["message"]} for i in issues],
                     width='stretch', hide_index=True)


def _cast_row(pdir: str, sh: dict) -> None:
    """镜头里的出镜角色：定妆图 + 名字，便于审核视频里的人物对不对。"""
    cast = sh.get("characters") or []
    if not cast:
        st.caption("出镜角色：无（纯环境镜头）")
    else:
        cols = st.columns(min(len(cast), 6) or 1)
        for col, c in zip(cols, cast):
            with col:
                p = Path(pdir) / c["asset"] if c.get("asset") else None
                if p and p.is_file():
                    st.image(str(p), width=64)
                st.caption(f"**{c['name']}**")
    if sh.get("missing_mentions"):
        st.warning("画面描述里提到了 " + "、".join(sh["missing_mentions"]) +
                   "，但出镜角色里没有：视频提示词不会带上他的外观与参考图。请在「编辑镜头内容」里补上出镜角色。")


def _render_big_scenes(pdir: str, no: int, v: dict) -> None:
    """一章 → 多个大场景 → 每个大场景的镜头。大场景块内显示场次范围、地点、共用的角色与道具。"""
    big = v.get("big_scenes") or []
    shots = v.get("shots") or []
    if not big:                       # 旧数据或划分失败：沿用逐镜列表
        for sh in shots:
            _shot_card(pdir, no, sh, v.get("all_characters") or [])
        return
    names_c = {c["id"]: c["name"] for c in v.get("all_characters") or []}
    names_l = {l["id"]: l["name"] for l in v.get("all_locations") or []}
    st.caption(f"本章划分为 {len(big)} 个大场景，每个大场景包含若干镜头；同一大场景内的地点、角色、道具可以共用。")
    known = {g["big_no"] for g in big}
    for g in big:
        mine = [sh for sh in shots if sh.get("big_scene_no") == g["big_no"]]
        with st.container(border=True):
            st.markdown(f"##### 大场景 {g['big_no']}：{g['title_zh']}")
            loc = names_l.get(g.get("location_id")) or g.get("place_zh") or "-"
            st.caption(f"场次 {g['from_scene']}–{g['to_scene']}　·　地点 {loc}　·　时间 {g.get('time_zh') or '未标注'}　·　镜头 {len(mine)} 个")
            if g.get("summary_zh"):
                st.write(g["summary_zh"])
            chars = "、".join(names_c.get(cid, cid) for cid in g.get("character_ids") or []) or "无"
            props = "、".join(g.get("prop_names") or []) or "无"
            st.caption(f"共用素材：出镜角色 {chars}　｜　道具 {props}")
            refs = g.get("refs") or []
            if refs:
                st.caption("场景参考图（本大场景镜头生成视频时使用的参考图）")
                cols = st.columns(min(4, len(refs)))
                for i, r in enumerate(refs):
                    with cols[i % len(cols)]:
                        if r.get("exists") and r.get("path"):
                            from pathlib import Path as _P
                            st.image(str(_P(pdir) / r["path"]), caption=r["label"], width="stretch")
                        else:
                            st.caption(f"{r['label']}（尚未生成）")
            else:
                st.caption("场景参考图：无（地点尚未生成定妆图或设定图）")
            if not mine:
                st.warning("这个大场景没有对应的镜头，请重新运行本章 C-3 镜头规划。")
            for sh in mine:
                _shot_card(pdir, no, sh, v.get("all_characters") or [])
    orphans = [sh for sh in shots if sh.get("big_scene_no") not in known]
    if orphans:
        with st.container(border=True):
            st.markdown("##### 未归入大场景的镜头")
            for sh in orphans:
                _shot_card(pdir, no, sh, v.get("all_characters") or [])


def _shot_card(pdir: str, no: int, sh: dict, all_chars: list) -> None:
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
            ref = sh.get("ref_image")
            if ref and ref.get("exists") and ref.get("path"):
                st.image(str(root / ref["path"]), caption=f"参考图：{ref['label']}", width="stretch")
            elif ref:
                st.caption(f"参考图：{ref['label']}（尚未生成）")
            else:
                st.caption("参考图：无（镜头没有地点）")
        with right:
            dur = sh.get("duration_sec")
            st.markdown(f"**{sh['id']}**　场次 {sh['scene_no']}　地点 {sh.get('location_name') or sh.get('location_id') or '-'}"
                        f"　时长 {dur or '-'} 秒　状态：**{SHOT_LABEL.get(sh['state'], sh['state'])}**")
            if (sh.get("split_total") or 1) > 1:
                st.caption(f"由同一个原始镜头拆出（第 {sh['split_part']}/{sh['split_total']} 段），与同组镜头共用画面描述")
            _cast_row(pdir, sh)
            if sh.get("look_ids") or sh.get("look_labels"):
                nm = {c["id"]: c["name"] for c in all_chars or []}
                st.caption("角色形象：" + "、".join(
                    [f"{nm.get(c, c)}→{l}" for c, l in (sh.get("look_ids") or {}).items()]
                    + [f"{nm.get(c, c)}→{l}（待补建）" for c, l in (sh.get("look_labels") or {}).items()]))
            if sh.get("props") or sh.get("prop_names"):
                names = [p["name"] for p in sh.get("props") or []] + [f"{n}（待补建）" for n in sh.get("prop_names") or []]
                st.caption("道具：" + "、".join(names))
            if sh.get("error") or sh.get("clip_error"):
                st.error(sh.get("error") or sh.get("clip_error"))
            for b in sh["blocks"]:
                who = "旁白" if b["type"] == "narration" else (b.get("speaker_label") or b.get("speaker_id") or "?")
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
                names = {c["id"]: c["name"] for c in all_chars}
                cast_ids = st.multiselect("出镜角色（决定视频提示词里的角色外观与参考图）", list(names),
                                          default=[c for c in (sh.get("character_ids") or []) if c in names],
                                          format_func=lambda i: f"{names[i]}（{i}）", key=f"cast_{no}_{sh['id']}")
                texts = {}
                for b in sh["blocks"]:
                    texts[b["idx"]] = st.text_input(
                        f"口播 {b['idx']}（{'旁白' if b['type'] == 'narration' else '对白，须逐字来自剧本'}）",
                        value=b["text"], key=f"bt_{no}_{sh['id']}_{b['idx']}")
                if st.button("保存修改（修改后请重新运行本章）", key=f"save_{no}_{sh['id']}"):
                    changed = {i: t for i, t in texts.items()
                               if t != next(x["text"] for x in sh["blocks"] if x["idx"] == i)}
                    res = tool_api.edit_shot(pdir, no, sh["id"], visual_hint_en=en, visual_hint_zh=zh,
                                             block_texts=changed or None,
                                             character_ids=cast_ids if cast_ids != (sh.get("character_ids") or []) else None)
                    if res["ok"]:
                        st.success("已保存。下次运行本章时会自动重算音频、提示词并重新生成视频。")
                    else:
                        st.error(res["error"]["message"])


def _recompose_section(pdir: str, no: int, v: dict) -> None:
    """单章重新合成：只重做声音、画面与字幕，不重新生成视频，也不调用配音接口。"""
    st.markdown("#### 重新合成（不重新生成视频）")
    st.caption("用已生成的原始视频与现有配音重新拼合声音、画面，并重新烧录字幕。"
               "不调用视频或配音接口，不消耗生成次数。修改字幕设置或视频格式后，用它让改动生效。")
    if not v.get("shots"):
        st.caption("本章还没有镜头，先运行本章流水线。")
        return
    if st.button("重新合成本章", key=f"recomp_{no}", help="需要所有小场景已通过审核。"):
        r = tool_api.start_recompose_background(pdir, [no])
        (st.success if r["ok"] else st.error)(
            "已开始重新合成，进度见下方日志。" if r["ok"] else r["error"]["message"])
    render_job_panel(pdir, ["chapter"], title="重新合成日志", height=220, limit=120)


def _recompose_all(pdir: str, overview: list) -> None:
    """批量重新合成：所有已合成过的章节（done / stale）。"""
    targets = [r["no"] for r in overview if r.get("status") in ("done", "stale")]
    with st.expander(f"批量重新合成（{len(targets)} 章已有成片）"):
        st.caption("对所有已有章节成片的章节逐章重新合成声音、画面与字幕，不重新生成视频。单章失败不影响其它章。")
        if st.button("重新合成所有已完成章节", key="recomp_all", disabled=not targets):
            r = tool_api.start_recompose_background(pdir, targets)
            (st.success if r["ok"] else st.error)("已开始批量重新合成。" if r["ok"] else r["error"]["message"])


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
    _recompose_all(pdir, overview)

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
    _auto_added_section(pdir, no, v)
    if v.get("output"):
        st.markdown("#### 章节成片")
        st.video(str(Path(pdir) / v["output"]))
    _recompose_section(pdir, no, v)
    _script_review(v)
    _missing_locations(v)
    _missing_props(v)
    _missing_looks(v)
    _shots_review(v)
    if v.get("script"):
        with st.expander("剧本（模型输出整理后的文本）"):
            st.code(v["script"], language="markdown")
    from ui.llm_trace_panel import render_llm_trace
    render_llm_trace(pdir, chapter=no, key_prefix=f"trace_ch{no}")
    if v["shots"]:
        st.markdown("#### 小场景逐镜审核")
        _plan_warnings(pdir, no, v)
        _render_big_scenes(pdir, no, v)
    else:
        st.caption("还没有镜头规划，先运行本章流水线。")
