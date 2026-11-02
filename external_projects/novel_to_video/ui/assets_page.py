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
    from ui.llm_trace_panel import render_llm_trace
    render_llm_trace(pdir, scopes=["assets"], title="G-2 模型调用记录（输入 / 输出）", key_prefix="trace_assets")


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
        "类型": {"character": "角色", "location": "地点", "prop": "道具", "look": "角色形象"}.get(r["kind"], r["kind"]),
        "id": r["id"], "名称": r["name"],
        "缺失字段": "、".join(r["missing_fields"]) or "无",
        "设定": STATUS_LABEL.get(r["text_status"], r["text_status"]),
        "定妆图": STATUS_LABEL.get(r["portrait_status"], r["portrait_status"]),
        "版本": r["portrait_version"], "错误": r["last_error"] or "",
    } for r in status["rows"]], width='stretch', hide_index=True)


def _presplit_panel(pdir: str) -> None:
    """地点细化 L4：为区域级地点提议具体场景点。候选勾选后确认才写入地点库（待审核）。"""
    from novel_to_video.steps.location_presplit import presplit_enabled
    from novel_to_video.project_store import read_json as _read_json
    st.markdown("#### 区域预拆解（地点细化）")
    st.caption("为区域级地点提议具体场景点（如「铁城·市政厅办公室」），让视频有具体场所可画。"
               "候选不会直接入库：勾选后确认写入，写入的场景点仍需逐条审核定妆图。")
    flash = st.session_state.pop("presplit_flash", None)
    if flash:
        st.success(flash)
    enabled = presplit_enabled(Path(pdir))
    on = st.checkbox("启用区域预拆解", value=enabled, key="presplit_on")
    if on != enabled:
        tool_api.set_location_presplit(pdir, on)
        st.rerun()
    if not on:
        st.info("区域预拆解已关闭。")
        return
    try:
        locs = _read_json(Path(pdir) / "global" / "locations.json").get("locations", [])
    except (OSError, ValueError):
        locs = []
    areas = [l for l in locs if (l.get("level") or "area") == "area"]
    if not areas:
        st.info("没有区域级地点。")
        return
    area = st.selectbox("选择区域", areas, format_func=lambda l: f"{l['name']}（{l['id']}）", key="presplit_area")
    existing = [l["name"] for l in locs if l.get("level") == "spot" and l.get("parent_id") == area["id"]]
    st.caption("该区域已有场景点：" + ("、".join(existing) if existing else "无"))
    cand_key = f"presplit_cands_{pdir}_{area['id']}"
    if st.button("提议场景点", key="presplit_propose", type="primary"):
        with st.spinner("正在请模型提议场景点…"):
            res = tool_api.presplit_propose(pdir, area["id"])
        if res["ok"]:
            st.session_state[cand_key] = res["data"]["candidates"]
            if not res["data"]["candidates"]:
                st.info("模型没有提议新的场景点（可能已覆盖或剧本中没有该区域的具体场所）。")
        else:
            st.error(res["error"]["message"])
    cands = st.session_state.get(cand_key) or []
    if not cands:
        return
    st.markdown(f"**候选场景点（{len(cands)} 个，勾选要写入的）**")
    chosen = []
    kinds = {"interior": "内景", "exterior": "外景"}
    for i, c in enumerate(cands):
        label = f"{c['name']}（{kinds.get(c.get('spot_kind'), '未定')}）— {c.get('reason') or '无理由'}"
        if st.checkbox(label, value=True, key=f"presplit_pick_{area['id']}_{i}"):
            chosen.append(c)
    if st.button("确认写入所选场景点", key="presplit_confirm", disabled=not chosen):
        with st.spinner("正在为场景点起草视觉锚点并写入…"):
            res = tool_api.presplit_confirm(pdir, area["id"], chosen)
        if res["ok"]:
            st.session_state.pop(cand_key, None)
            st.session_state["presplit_flash"] = f"已写入 {len(res['data']['added'])} 个场景点（待审核，见下方逐条审核）。"
            st.rerun()
        else:
            st.error(res["error"]["message"])


def _gallery(pdir: str, status: dict) -> None:
    """逐条审核。地点细化开启并存在场景点时：场景点按所属区域分组，并提示同区域内名字相近的场景点。"""
    from pathlib import Path as _Path
    from novel_to_video.project_store import read_json as _read_json
    from novel_to_video.steps.location_spots import near_name_pairs
    st.markdown("#### 逐条审核")
    try:
        locs = _read_json(_Path(pdir) / "global" / "locations.json").get("locations", [])
    except (OSError, ValueError):
        locs = []
    by_id = {l["id"]: l for l in locs}
    if any(l.get("level") == "spot" for l in locs):
        for pair in near_name_pairs(locs):
            st.info(f"同一区域内名字相近：「{pair['a_name']}」与「{pair['b_name']}」。"
                    "如果是同一个场所，请合并（或在全书素材页把其中一个的名称改为别名），避免重复生成定妆图。")
    last_group = object()
    for r in status["rows"]:
        loc = by_id.get(r.get("id")) if r.get("kind") == "location" else None
        if loc is not None:
            if loc.get("level") == "spot":
                area = by_id.get(loc.get("parent_id")) or {}
                group = f"区域「{area.get('name', '未指定')}」的场景点"
            else:
                group = "区域地点"
            if group != last_group:
                st.markdown(f"**{group}**")
                last_group = group
        entity_card(pdir, r)


def entity_card(pdir: str, r: dict, *, with_generate: bool = False, allow_merge: bool = False) -> None:
    """单个角色/地点的审核卡片：定妆图、设定编辑、通过/驳回按钮。章节页与补建审核页复用。
    with_generate：设定已通过但还没有定妆图时，显示“生成定妆图”按钮。
    allow_merge：补建角色显示“其实已经存在？合并到已有角色”（见 merge_character）。"""
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
            if r.get("last_error"):
                st.error(r["last_error"])
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
            if allow_merge:
                _merge_panel(pdir, r)
            if r["text_status"] == "pending" and r["missing_fields"]:
                st.warning("「通过设定」暂时点不了：还缺 " + "、".join(r["missing_fields"])
                           + "。请展开上方「查看与编辑设定」补全——性别在下拉框里选（无法判断就选 unknown），"
                           "再点「保存修改」，之后即可通过设定。")
            if with_generate and r["text_status"] == "approved" and r["portrait_status"] in ("missing", "failed"):
                if st.button("生成定妆图", key=f"gen_{r['key']}", type="primary"):
                    _start_result(tool_api.start_portraits_background(pdir, ids=[r["id"]]))


def _merge_panel(pdir: str, r: dict) -> None:
    """补建角色其实就是库里已有的人：选择对应的已有角色合并，不再单独审核。"""
    cands = tool_api.merge_candidates(pdir, r["id"])
    with st.expander("这个角色其实已经存在？合并到已有角色"):
        if not cands["ok"] or not cands["data"]:
            st.caption("库里没有可合并的角色。")
            return
        by_id = {c["id"]: c for c in cands["data"]}
        target = st.selectbox("合并到已有角色", list(by_id), key=f"mg_sel_{r['key']}",
                              format_func=lambda i: f"{by_id[i]['name']}（{i}）"
                              + ("　· 也是补建角色" if by_id[i]["source"] == "auto_added" else ""))
        st.caption(f"合并后：“{r['name']}”成为所选角色的别名，各章镜头里这个说话人改归所选角色（沿用其音色与形象）；"
                   "本补建角色从库中删除，不再需要审核。已经为它生成过的音频、提示词、视频会被清理，需要重新生成；"
                   "定妆图文件保留在磁盘上。")
        sure = st.checkbox("我确认这是同一个角色，合并", key=f"mg_ok_{r['key']}")
        if st.button("合并", key=f"mg_btn_{r['key']}", disabled=not sure):
            res = tool_api.merge_auto_added(pdir, r["id"], target)
            if res["ok"]:
                st.toast(f"已把“{r['name']}”合并到“{by_id[target]['name']}”")
                st.rerun()
            else:
                st.error(res["error"]["message"])


def _vkey(prefix: str, rkey: str, value) -> str:
    """控件 key 包含存储值的指纹。Streamlit 会保留同一 key 的控件状态，忽略新的 value=；
    值变化时换一个 key，控件才会以最新内容重新创建（否则刷新前会一直显示旧值）。"""
    import hashlib
    fp = hashlib.md5(str(value if value is not None else "").encode("utf-8")).hexdigest()[:8]
    return f"{prefix}_{rkey}_{fp}"


def _entity_detail(pdir: str, r: dict) -> None:
    if r["kind"] == "look":                      # 角色形象：嵌在角色的 looks 里，id 形如 char_001:look_01
        cid, _, lid = r["id"].partition(":")
        chars = ps.read_json(Path(pdir) / "global" / "characters.json")["characters"]
        owner = next((c for c in chars if c["id"] == cid), {})
        ent = next((l for l in owner.get("looks") or [] if l["id"] == lid), {})
    else:
        fname, lkey = {"character": ("characters.json", "characters"), "location": ("locations.json", "locations"),
                       "prop": ("props.json", "props")}[r["kind"]]
        info = ps.read_json(Path(pdir) / "global" / fname)
        ent = next((e for e in info[lkey] if e["id"] == r["id"]), {})
    with st.expander("查看与编辑设定"):
        anchor = st.text_area("visual_anchor_en（定妆图提示核心）", value=ent.get("visual_anchor_en") or "",
                              key=_vkey("anc", r["key"], ent.get("visual_anchor_en")))
        fields = {"visual_anchor_en": anchor}
        if r["kind"] == "character":
            voice = st.text_input("voice_profile（配音音色设定）", value=ent.get("voice_profile") or "",
                                  key=_vkey("voi", r["key"], ent.get("voice_profile")))
            gender = st.selectbox("性别", ["", "male", "female", "unknown"],
                                  index=["", "male", "female", "unknown"].index(ent.get("gender") or "")
                                  if (ent.get("gender") or "") in ("", "male", "female", "unknown") else 0,
                                  key=_vkey("gen", r["key"], ent.get("gender")))
            fields.update(voice_profile=voice, gender=gender or None)
        src = {k: ent.get(f"{k}_source") for k in ("visual_anchor", "voice_profile", "gender")
               if ent.get(f"{k}_source")}
        if src:
            st.caption("来源：" + "，".join(f"{k}={v}" for k, v in src.items()))
        if st.button("保存修改", key=f"save_{r['key']}"):
            clean = {k: v for k, v in fields.items() if v is not None and ent.get(k) != v}
            # 模型起草/推断的性别不算确认：值相同也要写入，使来源变为 user（否则「通过设定」永远点不了）
            if (r["kind"] == "character" and fields.get("gender")
                    and ent.get("gender_source") not in ("user", "persona")):
                clean["gender"] = fields["gender"]
            if not clean:
                st.info("没有改动。")
            else:
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
    _presplit_panel(pdir)
    _gallery(pdir, data)
