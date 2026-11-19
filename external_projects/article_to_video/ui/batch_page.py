"""ui/batch_page.py — 「📦 批量生成」（R23）：持续的队列，可随时往里加文章，逐篇自动创建项目并生成视频。

三块：
  1. 共用参数：挡位（含 G3 完全无人值守）、运行到、重试、与「新建项目」相同的画幅/水印/署名等；对所有文章共享；
  2. 加入队列：可以一篇一篇传，也可以一次多选；每篇在表格里设置好标题和作者再加入；队列运行中同样可以加入；
  3. 队列与监控：每 3 秒局部刷新；开始 / 停止 / 重新排队 / 移除；队列日志。
队列本身（落盘、重试、停止、恢复）在 article_to_video/batch/ 中，这里只负责展示与操作。
"""

from __future__ import annotations

import streamlit as st

from article_to_video import app_settings as S
from article_to_video.batch import prep, runner, store
from article_to_video.batch.store import QueueError
from article_to_video.paths import projects_root
from article_to_video.pipeline.gates import GEARS, STAGES
from article_to_video.project_store import MODES, ORIENTATIONS, PERSONAS, RESOLUTIONS, WATERMARK_POSITIONS
from ui.import_page import (MODE_LABELS, ORIENTATION_LABELS, PERSONA_LABELS, POSITION_LABELS, _decode)
from ui.joblog_panel import render_job_panel

REFRESH_SECONDS = 3
GEAR_HELP = {
    "G0": "四个检查点都需人工确认——队列会停在每篇的大纲，不适合无人值守。",
    "G1": "大纲与终审需人工——队列会停在每篇的大纲，不适合无人值守。",
    "G2": "全自动，但终审需要在驾驶舱里人工确认才会导出；必须设置预算上限（生图次数或耗时）。",
    "G3": "完全无人值守：无人工检查点，终审自动通过并导出，**不设预算上限**（可能产生较多费用）。",
}


def _upload_key() -> str:
    return f"bq_up_{st.session_state.get('bq_up_n', 0)}"


# ---------- 1. 共用参数 ----------

def _params_panel(root) -> None:
    params = store.load(root)["params"]
    base_s = S._merge(S.inherited_group("settings"), params["settings"])
    base_s, _ = S.normalize_group("settings", base_s)
    base_b = S._merge(S.inherited_group("branding"), params["branding"])
    base_b, _ = S.normalize_group("branding", base_b)

    with st.expander("共用参数（对队列里所有文章生效）", expanded=False):
        st.caption("修改只影响**之后开始处理**的文章；正在运行的那一篇不受影响。")
        c1, c2, c3 = st.columns(3)
        gear = c1.radio("挡位", list(GEARS), index=list(GEARS).index(params["gear"]), key="bq_gear")
        until = c2.selectbox("运行到", list(STAGES), index=list(STAGES).index(params["until"]), key="bq_until")
        merge_all = c3.checkbox("多期时导出全集合并版", value=params["merge_all"], key="bq_merge")
        if gear == "G3":
            st.warning(GEAR_HELP["G3"])
        else:
            st.caption(GEAR_HELP[gear])
        max_images, max_minutes = 0, 0.0
        if gear == "G2":
            b1, b2 = st.columns(2)
            max_images = int(b1.number_input("预算：每篇最多生图次数（0 = 不限）", min_value=0,
                                             value=int(params["max_images"]), step=1, key="bq_mi"))
            max_minutes = float(b2.number_input("预算：每篇最长耗时（分钟，0 = 不限）", min_value=0.0,
                                                value=float(params["max_minutes"]), step=5.0, key="bq_mm"))
        r1, r2 = st.columns(2)
        retries = int(r1.number_input("失败后重试次数", min_value=0, max_value=10, value=int(params["max_retries"]),
                                      step=1, key="bq_retries", help="重试用尽仍失败则记为失败并继续下一篇"))
        delays_text = r2.text_input("每次重试前等待（秒，逗号分隔）", value=prep.format_delays(params["retry_delays"]),
                                    key="bq_delays", help="例如 30,60,120：第 1 次重试前等 30 秒，第 2 次 60 秒…；次数多于填写项时沿用最后一个")

        st.markdown("**视频参数**（与「新建项目」相同，所有文章共享；标题与作者在加入队列时逐篇设置）")
        d1, d2, d3 = st.columns(3)
        mode = d1.selectbox("模式", MODES, index=MODES.index(base_s["mode"]), format_func=MODE_LABELS.get, key="bq_mode")
        target = d2.slider("目标时长（分钟）", 3, 30, min(30, max(3, int(round(base_s["target_minutes"])))), key="bq_target")
        persona = d3.selectbox("解说视角", PERSONAS, index=PERSONAS.index(base_s["persona"]),
                               format_func=PERSONA_LABELS.get, key="bq_persona")
        e1, e2 = st.columns(2)
        orientation = e1.selectbox("画幅", ORIENTATIONS, index=ORIENTATIONS.index(base_s["video"]["orientation"]),
                                   format_func=ORIENTATION_LABELS.get, key="bq_orient")
        resolution = e2.selectbox("分辨率", RESOLUTIONS, index=RESOLUTIONS.index(base_s["video"]["resolution"]),
                                  key="bq_res")
        w1, w2, w3 = st.columns([3, 2, 2])
        wm_text = w1.text_input("水印文字（留空则不加）", value=base_b["watermark"]["text"], max_chars=60, key="bq_wm")
        wm_pos = w2.selectbox("水印位置", WATERMARK_POSITIONS,
                              index=WATERMARK_POSITIONS.index(base_b["watermark"]["position"]),
                              format_func=POSITION_LABELS.get, key="bq_wmpos")
        wm_op = w3.slider("透明度", 0.05, 1.0, float(base_b["watermark"]["opacity"]), 0.05, key="bq_wmop")
        k1, k2, k3, k4 = st.columns(4)
        cr_t = k1.checkbox("片头标题卡署名", value=base_b["credits"]["title_card"], key="bq_crt")
        cr_l = k2.checkbox("下三分之一条", value=base_b["credits"]["lower_third"], key="bq_crl")
        cr_e = k3.checkbox("片尾卡", value=base_b["credits"]["end_card"], key="bq_cre")
        ai = k4.checkbox("片尾 AI 生成声明", value=base_b["ai_disclosure"]["enabled"], key="bq_ai")

        if st.button("保存共用参数", key="bq_save"):
            try:
                delays = prep.parse_delays(delays_text)
                warnings = store.set_params(root, {
                    "gear": gear, "until": until, "merge_all": merge_all, "max_images": max_images,
                    "max_minutes": max_minutes, "max_retries": retries, "retry_delays": delays,
                    "settings": {"mode": mode, "target_minutes": target, "persona": persona,
                                 "video": {"orientation": orientation, "resolution": resolution}},
                    "branding": {"credits": {"title_card": cr_t, "lower_third": cr_l, "end_card": cr_e},
                                 "watermark": {"enabled": bool(wm_text.strip()), "text": wm_text,
                                               "position": wm_pos, "opacity": wm_op},
                                 "ai_disclosure": {"enabled": ai}},
                })
            except (ValueError, QueueError) as exc:
                st.error(str(exc))
            else:
                st.success("已保存")
                for w in warnings:
                    st.warning(w)


# ---------- 2. 加入队列 ----------

def _rows_from_editor(edited) -> list:
    return edited.to_dict("records") if hasattr(edited, "to_dict") else list(edited)


def _add_panel(root) -> None:
    st.subheader("加入队列")
    st.caption("可以一篇一篇传，也可以一次多选；**每篇都要在下表里设置好标题和作者**。队列正在运行时也可以继续加入。")
    uploads = st.file_uploader("文章（Markdown / 文本，可多选）", type=["md", "markdown", "txt"],
                               accept_multiple_files=True, key=_upload_key())
    if not uploads:
        return
    decoded, errors = {}, []
    for up in uploads:
        try:
            decoded[up.name + f"#{up.size}"] = (up.name, _decode(up.getvalue()))
        except Exception as exc:  # noqa: BLE001 — 编码不对等，逐个文件提示
            errors.append(f"{up.name}：{exc}")
    for e in errors:
        st.error(e)
    if not decoded:
        return

    rows = []
    for key, (name, text) in decoded.items():
        meta = prep.suggest_meta(text, name)
        rows.append({"加入": True, "文件": name, "标题": meta["title"], "作者": meta["author"],
                     "汉字数": meta["chars"], "全文朗读约（分钟）": meta["minutes"]})
    sig = "|".join(decoded)
    edited = st.data_editor(
        rows, key=f"bq_editor_{sig}", hide_index=True, use_container_width=True, num_rows="fixed",
        disabled=["文件", "汉字数", "全文朗读约（分钟）"],
        column_config={"加入": st.column_config.CheckboxColumn("加入", width="small"),
                       "标题": st.column_config.TextColumn("标题（必填）"),
                       "作者": st.column_config.TextColumn("作者（可留空 = 不署名）")})
    chosen = [(r, list(decoded.values())[i][1]) for i, r in enumerate(_rows_from_editor(edited)) if r.get("加入")]
    if st.button(f"加入队列（{len(chosen)} 篇）", type="primary", disabled=not chosen, key="bq_add"):
        missing = [r["文件"] for r, _ in chosen if not str(r.get("标题") or "").strip()]
        if missing:
            st.error("以下文章还没有填写标题：" + "、".join(missing))
            return
        added = 0
        for r, text in chosen:
            try:
                res = store.enqueue(root, source_text=text, source_name=r["文件"],
                                    title=str(r["标题"]), author=str(r.get("作者") or ""))
                added += 1
                for w in res["warnings"]:
                    st.warning(f"《{r['标题']}》{w}")
            except QueueError as exc:
                st.error(f"《{r['标题']}》未加入：{exc}")
        if added:
            st.session_state["bq_up_n"] = st.session_state.get("bq_up_n", 0) + 1   # 清空上传框，便于继续加
            st.session_state["bq_flash"] = f"已加入 {added} 篇"
            st.rerun()


# ---------- 3. 队列与监控 ----------

def _rerun_monitor() -> None:
    """只刷新监控区；不在片段重跑里（例如整页重跑时点击）则退回整页刷新。"""
    try:
        st.rerun(scope="fragment")
    except st.errors.StreamlitAPIException:
        st.rerun()


@st.fragment(run_every=REFRESH_SECONDS)
def _monitor(root_str: str) -> None:
    from pathlib import Path
    root = Path(root_str)
    data = store.load(root)
    summ = store.summary(root)
    counts = summ["counts"]
    alive = summ["runner_alive"]

    if alive:
        st.info("⏳ 队列运行中" + ("（已请求停止，当前这一篇结束后停下）" if summ["stop_requested"] else ""))
    else:
        st.caption("队列空闲。点「开始」运行；队列只在你点击开始后才会处理文章。")
    n, chars, minutes = prep.pending_estimate(data)
    c = st.columns(6)
    c[0].metric("排队中", counts["queued"])
    c[1].metric("运行中", counts["running"])
    c[2].metric("完成", counts["done"])
    c[3].metric("失败", counts["failed"])
    c[4].metric("已停止/等待", counts["stopped"] + counts["waiting"] + counts["budget_exceeded"])
    c[5].metric("待处理体量", f"{chars:,} 字", help=f"{n} 篇，全文朗读约 {minutes} 分钟（只是体量估算，不是费用）")

    b1, b2, b3, _ = st.columns([1, 1, 1.4, 3])
    if b1.button("▶ 开始", disabled=alive or not counts["queued"], key="bq_start", type="primary"):
        runner.start_background(root)
        _rerun_monitor()
    if b2.button("⏹ 停止", disabled=not alive or summ["stop_requested"], key="bq_stop"):
        runner.request_stop(root)
        _rerun_monitor()
    redo = counts["failed"] + counts["stopped"] + counts["waiting"] + counts["budget_exceeded"]
    if b3.button(f"🔁 重新排队未完成项（{redo}）", disabled=not redo, key="bq_requeue"):
        store.requeue(root)
        _rerun_monitor()

    rows = prep.queue_rows(root, data)
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("队列是空的。在上面加入文章。")

    removable = [i for i in data["items"] if i["state"] != "running"]
    if removable:
        r1, r2 = st.columns([3, 1])
        pick = r1.selectbox("选择条目", [i["id"] for i in removable], key="bq_pick",
                            format_func=lambda i: next(f"{x['id']} {x['title']}（{prep.STATE_LABELS[x['state']]}）"
                                                       for x in removable if x["id"] == i))
        item = next(i for i in removable if i["id"] == pick)
        if r2.button("🗑 移出队列", key="bq_remove", help="已生成的项目不受影响"):
            try:
                store.remove_item(root, pick)
            except QueueError as exc:
                st.error(str(exc))
            _rerun_monitor()
        if item["state"] in store.REQUEUE_FROM and st.button("🔁 只重新排队这一篇", key="bq_requeue_one"):
            store.requeue(root, pick)
            _rerun_monitor()
        if item.get("error"):
            st.caption(f"说明：{item['error']}")
        if item.get("project_id"):
            st.caption(f"项目：{item['project_id']}（可到「🧭 驾驶舱」选择该项目查看或手动处理）")

    done = [i for i in data["items"] if i["state"] == "done" and i.get("outputs")]
    if done:
        with st.expander(f"成片文件（{len(done)} 篇）"):
            for i in done:
                st.write(f"**{i['title']}**")
                for o in i["outputs"]:
                    st.write(f"📦 {o}")


def render_batch_page() -> None:
    st.header("📦 批量生成")
    st.caption("持续的队列：逐篇导入、自动运行流水线；失败自动重试，仍失败则记录后继续下一篇。")
    root = projects_root()
    flash = st.session_state.pop("bq_flash", None)
    if flash:
        st.success(flash)
    _params_panel(root)
    _add_panel(root)
    st.subheader("队列与监控")
    _monitor(str(root))
    render_job_panel(store.queue_dir(root), ["queue"], title="队列日志", height=200)
