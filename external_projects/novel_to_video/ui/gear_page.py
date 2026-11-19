"""ui/gear_page.py — 挡位、预算与批量队列（Phase 3）。"""

from __future__ import annotations

import streamlit as st

from novel_to_video import gates
from novel_to_video import project_store as ps
from novel_to_video import tool_api
from ui.common import DEFAULT_PROJECTS_ROOT
from ui.chapter_page import _pick_project

GEAR_HELP = {
    "G0": "手动：每个检查点都停下来等待确认",
    "G1": "引导：章节合成自动完成，其余手动",
    "G2": "半自动：剧本/分镜自动，小场景仍逐镜审核",
    "G3": "全自动：机械校验通过的镜头自动通过；必须设置预算上限",
    "G4": "托管：全自动，失败后换一次策略（收紧口播块）；必须设置预算上限",
    "G5": "不停机：全自动，失败后自动重置重试最多 3 次；必须设置预算上限；全书合成仍需人工确认",
    "G6": "全流程自动：按章自动补设定/性别、生成并通过定妆图，再场景图、配音、合成；失败的章节持续重试、不跳过（直到成功、手动停止或预算用尽）；一章完成自动开始下一章；必须设置预算上限；全书合成仍需人工确认",
}


def page_gear(pdir: str) -> None:
    """由驾驶舱的子导航调用：项目目录由驾驶舱统一选择，本页不再单独选择项目。"""
    st.header("设置与批量队列")
    if not pdir:
        return
    pol = tool_api.gear_policy()["data"]
    import json
    from pathlib import Path
    from novel_to_video import render_mode as RM
    _meta = json.loads((Path(pdir) / "project.json").read_text(encoding="utf-8"))
    cur_gear = _meta.get("gear", "G0")
    still_mode = RM.mode_of(_meta) == RM.STILL
    cur = st.selectbox("挡位", pol["gears"], index=pol["gears"].index(cur_gear),
                       format_func=lambda g: f"{g} · {GEAR_HELP[g]}", key="gear_pick")
    budget = {}
    # 上次保存的预算作为控件初值；未保存过的项才用默认值。key 带项目路径，切换项目时控件重新取值。
    saved = _meta.get("budget") or {}
    kpre = f"gear_budget_{pdir}"

    def _int(name: str, default: int) -> int:
        v = saved.get(name)
        return int(v) if v not in (None, "") else default

    def _float(name: str, default: float) -> float:
        v = saved.get(name)
        return float(v) if v not in (None, "") else default

    if cur in ("G3", "G4", "G5", "G6"):
        if still_mode:
            # 动态漫不调用视频接口：只设图像（场景图与素材）与耗时上限
            c2, c3 = st.columns(2)
            budget["max_image_calls"] = c2.number_input("图像调用上限（场景图与素材）", min_value=0,
                                                        value=_int("max_image_calls", 20), step=1,
                                                        key=f"{kpre}_img")
            budget["max_minutes"] = c3.number_input("耗时上限（分钟）", min_value=0.0,
                                                    value=_float("max_minutes", 120.0), step=10.0,
                                                    key=f"{kpre}_min")
        else:
            c1, c2, c3 = st.columns(3)
            budget["max_video_calls"] = c1.number_input("视频调用上限", min_value=0,
                                                        value=_int("max_video_calls", 20), step=1,
                                                        key=f"{kpre}_vid")
            budget["max_image_calls"] = c2.number_input("图像调用上限（场景图与素材）", min_value=0,
                                                        value=_int("max_image_calls", 20), step=1,
                                                        key=f"{kpre}_img")
            budget["max_minutes"] = c3.number_input("耗时上限（分钟）", min_value=0.0,
                                                    value=_float("max_minutes", 120.0), step=10.0,
                                                    key=f"{kpre}_min")
    if cur == "G6":
        budget[gates.CHAPTER_RETRY_KEY] = st.number_input(
            "单章最多重试次数（0 = 不限制）", min_value=0, value=_int(gates.CHAPTER_RETRY_KEY, 0), step=1,
            key=f"{kpre}_retry",
            help="G6 不会跳过失败的章节：失败后持续重试（间隔逐步拉长到最长 60 秒），直到成功、你点击停止或预算用尽。"
                 "填写大于 0 的数字则在重试这么多次后停下整个队列（同样不会去做后面的章节），修正原因后重新运行即可继续。")
    if st.button("保存挡位"):
        res = tool_api.set_gear(pdir, cur, budget or None)
        (st.success if res["ok"] else st.error)(res["data"] if res["ok"] else res["error"]["message"])

    proj_cfg = json.loads((Path(pdir) / "project.json").read_text(encoding="utf-8"))
    st.markdown("#### 校验失败后的自动修正")
    st.caption("剧本或镜头规划没通过校验时（例如对白不是剧本原文），自动把全部问题反馈给模型修正，"
               "而不是直接让整章失败。每次修正多一次文本模型调用；设为 0 则关闭、直接失败。"
               f"默认 {ps.DEFAULT_AUTO_FIX_RETRIES} 次，上限 {ps.MAX_AUTO_FIX_RETRIES} 次。"
               "已创建的项目保留它保存的次数，要改请在这里修改。")
    r1, r2 = st.columns(2)
    cur_s = int(proj_cfg.get("script_max_retries", ps.DEFAULT_AUTO_FIX_RETRIES))
    cur_p = int(proj_cfg.get("shots_max_retries", ps.DEFAULT_AUTO_FIX_RETRIES))
    new_s = r1.number_input("剧本（C-1/C-2）最多自动修正次数", min_value=0, max_value=ps.MAX_AUTO_FIX_RETRIES, value=cur_s, step=1,
                            key="gear_script_retry")
    new_p = r2.number_input("镜头规划（C-3/C-4）最多自动修正次数", min_value=0, max_value=ps.MAX_AUTO_FIX_RETRIES, value=cur_p, step=1,
                            key="gear_shots_retry")
    if (int(new_s) != cur_s or int(new_p) != cur_p) and st.button("保存自动修正次数", key="gear_retry_save"):
        res = [tool_api.set_script_retries(pdir, int(new_s)), tool_api.set_shots_retries(pdir, int(new_p))]
        bad = next((r for r in res if not r["ok"]), None)
        (st.error if bad else st.success)(bad["error"]["message"] if bad else "已保存")

    st.markdown("#### 角色多形象（默认关闭）")
    st.caption("开启后，镜头规划会在角色换装、受伤、伪装等外观明显变化时指定「形象」；库里没有的形象会在 C-5 起草，"
               "与补建角色一样需要确认设定与定妆图，之后该镜头的提示词与参考图使用这个形象。"
               "关闭时一切与之前相同。已规划的章节不会自动改变，需要时在章节页「重新规划本章镜头」。")
    looks_cur = bool(proj_cfg.get("character_looks_enabled", True))
    looks_new = st.checkbox("启用角色多形象", value=looks_cur, key="gear_looks")
    if looks_new != looks_cur and st.button("保存角色多形象开关", key="gear_looks_save"):
        r = tool_api.set_character_looks(pdir, looks_new)
        (st.success if r["ok"] else st.error)("已保存" if r["ok"] else r["error"]["message"])

    st.markdown("#### 地点细化（场景点，默认关闭）")
    st.caption("开启后，镜头规划会判断「现有地点是否真的是这场戏发生的场所」：只有「铁城」这样的区域级地点、"
               "戏却发生在市政厅办公室里时，会规划新的场景点（如「铁城·市政厅办公室」），"
               "C-5 起草设定（继承所属区域的风格），与补建地点一样需要确认设定与定妆图。"
               "关闭时一切与之前相同。已规划的章节不会自动改变，需要时在章节页「重新规划本章镜头」。")
    spots_cur = bool(proj_cfg.get("location_spots_enabled", True))
    spots_new = st.checkbox("启用地点细化", value=spots_cur, key="gear_spots")
    if spots_new != spots_cur and st.button("保存地点细化开关", key="gear_spots_save"):
        r = tool_api.set_location_spots(pdir, spots_new)
        (st.success if r["ok"] else st.error)("已保存" if r["ok"] else r["error"]["message"])

    st.markdown("#### 视频输出格式")
    from novel_to_video import video_format as vf
    vcur = vf.resolve(proj_cfg.get("video") or {})
    ori_keys = list(vf.ORIENTATIONS)
    ori = st.radio("画面方向", ori_keys, index=ori_keys.index(vcur.orientation), horizontal=True,
                   format_func=lambda k: vf.ORIENTATIONS[k]["label"] + "（默认）" if k == "landscape" else vf.ORIENTATIONS[k]["label"],
                   key="gear_v_ori", help="横屏 16:9 为默认。竖屏 9:16 适合手机竖屏观看。")
    f1, f2, f3 = st.columns(3)
    res = f1.selectbox("分辨率", list(vf.RESOLUTIONS), index=list(vf.RESOLUTIONS).index(vcur.resolution),
                       key="gear_v_res", help="720P 为 1280x720（横屏）；1080P 为 1920x1080（横屏）。")
    fps = f2.selectbox("帧率（fps）", list(vf.FPS_CHOICES), index=list(vf.FPS_CHOICES).index(vcur.fps),
                       key="gear_v_fps")
    poll = f3.number_input("轮询视频状态间隔（秒）", min_value=1.0, max_value=60.0, step=1.0,
                           value=float(vcur.poll_interval_sec), key="gear_v_poll",
                           help="提交视频后，每隔多少秒查询一次生成状态。")
    st.caption(f"当前：{vcur.describe}。"
               "修改后，之后运行的小场景视频会按新格式生成；已生成的地点定妆图与封面背景不会自动重做，需要时请重新生成。")
    if st.button("保存视频输出格式", key="gear_v_fmt_save"):
        r = tool_api.set_video_format(pdir, ori, res, int(fps), float(poll))
        (st.success if r["ok"] else st.error)("已保存" if r["ok"] else r["error"]["message"])

    st.markdown("#### 视频接口等待与重试")
    vcfg = {**ps.DEFAULT_VIDEO_WAIT, **(proj_cfg.get("video") or {})}
    c1, c2, c3, c4 = st.columns(4)
    v_wait = c1.number_input("等待视频完成上限（秒）", min_value=60, max_value=6 * 3600, step=60,
                             value=int(vcfg["max_wait_sec"]), key="gear_v_wait",
                             help="提交后轮询等待视频完成的最长时间。视频通常需要数分钟，默认 3600 秒。")
    v_retry = c2.number_input("普通错误重试次数", min_value=0, max_value=10, step=1,
                              value=int(vcfg["max_retries"]), key="gear_v_retry",
                              help="网络异常、5xx 等普通错误的重试次数。")
    v_queue = c3.number_input("排队满时最多等待（秒）", min_value=0, max_value=6 * 3600, step=60,
                              value=int(vcfg["queue_wait_sec"]), key="gear_v_queue",
                              help="服务端提示 please retry later（排队已满）时，持续等待的上限；这期间不计入重试次数。")
    v_interval = c4.number_input("排队重试间隔（秒）", min_value=1, max_value=60, step=1,
                                 value=int(vcfg["queue_retry_interval_sec"]), key="gear_v_interval",
                                 help="排队已满时，每次重试之间的固定等待时间，不递增。默认 5 秒。")
    if st.button("保存视频等待设置", key="gear_v_save"):
        res = tool_api.set_video_wait(pdir, int(v_wait), int(v_retry), int(v_queue), int(v_interval))
        (st.success if res["ok"] else st.error)("已保存" if res["ok"] else res["error"]["message"])

    st.markdown("#### 视频网络（创建 / 查询 / 下载）")
    cur_v_proxy = bool(vcfg.get("use_system_proxy", False))
    v_proxy = st.checkbox("使用系统代理", value=cur_v_proxy, key="gear_v_proxy",
                          help="默认关闭（直连）：视频创建、状态查询与视频下载都不读取系统代理。"
                               "若系统代理不允许访问视频下载域名，下载会返回 403（例如 platform-outputs 域名）。"
                               "只有确实需要通过代理访问外网时才开启。")
    if bool(v_proxy) != cur_v_proxy and st.button("保存视频网络设置", key="gear_v_proxy_save"):
        res = tool_api.set_video_proxy(pdir, bool(v_proxy))
        (st.success if res["ok"] else st.error)("已保存（下一次视频请求起生效）" if res["ok"] else res["error"]["message"])
    st.caption(f"当前：{'使用系统代理' if cur_v_proxy else '直连（默认）'}。配音的网络方式在下方“配音网络”中单独设置。")

    st.markdown("#### 图像网络（生图请求 / 图片下载）")
    cur_i_proxy = bool((proj_cfg.get("image") or {}).get("use_system_proxy", False))
    i_proxy = st.checkbox("使用系统代理", value=cur_i_proxy, key="gear_i_proxy",
                          help="默认关闭（直连）：生图请求与图片下载都不读取系统代理。"
                               "若本机代理软件已关闭但 HTTP_PROXY/HTTPS_PROXY 环境变量还残留，下载会失败；"
                               "只有确实需要通过代理访问外网时才开启。")
    if bool(i_proxy) != cur_i_proxy and st.button("保存图像网络设置", key="gear_i_proxy_save"):
        res = tool_api.set_image_proxy(pdir, bool(i_proxy))
        (st.success if res["ok"] else st.error)("已保存（下一次生图请求起生效）" if res["ok"] else res["error"]["message"])
    st.caption(f"当前：{'使用系统代理' if cur_i_proxy else '直连（默认）'}。")

    st.markdown("#### 字幕")
    subcfg = {**ps.DEFAULT_OPTIONS["subtitles"], **(proj_cfg.get("subtitles") or {})}
    sub_on = st.checkbox("章节合成时烧录字幕", value=bool(subcfg.get("enabled", True)), key="gear_sub_on",
                         help="旁白原样显示，对白前加说话人名，时间与配音对齐。关闭后输出无字幕的章节视频。")
    sub_size = st.number_input("字号（留空自动，像素高度按画面高度取值）", min_value=0, max_value=120, step=2,
                               value=int(subcfg.get("font_size") or 0), key="gear_sub_size")
    if st.button("保存字幕设置", key="gear_sub_save"):
        res = tool_api.set_subtitles(pdir, bool(sub_on), int(sub_size) or None)
        (st.success if res["ok"] else st.error)("已保存（重新运行本章后生效）" if res["ok"] else res["error"]["message"])

    st.markdown("#### 章节封面")
    from novel_to_video.media.cover import LAYOUTS as _COVER_LAYOUTS
    ccfg = {**ps.DEFAULT_OPTIONS["chapter_cover"], **(proj_cfg.get("chapter_cover") or {})}
    cc_on = st.checkbox("每章视频开头加章节封面", value=bool(ccfg.get("enabled")), key="gear_cc_on",
                        help="默认关闭（与以前一致）。开启后每章合成时专门生成一张无文字的封面背景图（调用一次生图接口，"
                             "已生成的会沿用），再由程序叠加小说名、第 N 章和章节标题，作为章节视频的开头静帧。")
    cc1, cc2, cc3 = st.columns(3)
    cc_title = cc1.text_input("小说名（留空用项目书名）", value=ccfg.get("book_title") or "", key="gear_cc_title")
    cc_layout = cc2.selectbox("布局", list(_COVER_LAYOUTS), index=list(_COVER_LAYOUTS).index(ccfg.get("layout", "center")),
                              key="gear_cc_layout")
    cc_sec = cc3.number_input("封面时长（秒）", min_value=1.0, max_value=8.0, step=0.5,
                              value=float(ccfg.get("duration_sec") or 3.0), key="gear_cc_sec")
    if st.button("保存章节封面设置", key="gear_cc_save"):
        res = tool_api.set_chapter_cover(pdir, bool(cc_on), book_title=cc_title, layout=cc_layout,
                                         duration_sec=float(cc_sec))
        (st.success if res["ok"] else st.error)(
            "已保存（下次合成章节时生效；已完成的章节点「重新合成本章」）" if res["ok"] else res["error"]["message"])

    st.markdown("#### 配音网络")
    cur_proxy = (proj_cfg.get("tts") or {}).get("edge_tts_proxy")
    modes = ["系统代理（默认）", "强制直连（不使用任何代理）"]
    idx = 1 if cur_proxy == "direct" else 0
    picked = st.radio("edge-tts 连接方式", modes, index=idx, horizontal=True, key="gear_tts_proxy",
                      help="如果系统里设置了代理，而代理不允许访问微软语音服务，会出现 403。选择强制直连可绕过系统代理。")
    if (picked == modes[1]) != (idx == 1) and st.button("保存配音网络", key="gear_tts_proxy_save"):
        res = tool_api.set_tts_proxy(pdir, "direct" if picked == modes[1] else None)
        (st.success if res["ok"] else st.error)("已保存" if res["ok"] else res["error"]["message"])
    st.caption("只影响 edge-tts 配音。若要使用指定代理地址，请在项目配置 tts.edge_tts_proxy 中填写。")

    st.markdown("#### 渲染方式与动态漫设置")
    mode_now = RM.mode_of(proj_cfg)
    st.caption("渲染方式：" + ("动态漫（场景图 + 动效 + 配音字幕）" if mode_now == RM.STILL else "视频（小场景视频）")
               + "。创建后不可更改。")
    if mode_now == RM.STILL:
        _still_settings_section(pdir, proj_cfg)

    st.markdown("#### 检查点策略")
    st.dataframe([{"检查点": cp, **{g: ("自动" if pol["table"][g][cp] == "auto" else "手动") for g in pol["gears"]}}
                  for cp in pol["table"]["G0"]], hide_index=True, width='stretch')
    st.caption("全书合成（F-1）在任何挡位下都需要人工确认。")

    st.markdown("#### 批量运行")
    spec = st.text_input("章节范围（留空 = 全部选中章节）", value="", key="gear_spec")
    if st.button("开始运行队列"):
        with st.spinner("运行中…（每章完成后返回）"):
            res = tool_api.run_queue(pdir, _parse(pdir, spec), gear=None)
        if not res["ok"]:
            st.error(res["error"]["message"])
        else:
            d = res["data"]
            if d["halted"]:
                st.warning(f"已因预算停止：{d['halted']}")
            st.json(d["results"])

    st.markdown("#### 全书状态")
    fs = tool_api.final_status(pdir)
    if fs["ok"]:
        d = fs["data"]
        st.write(f"已完成：{d['done']}　等待中：{d['pending']}　需重新合成：{d['stale']}")
        if d["final_stale"]:
            st.warning("全书成片已过期，需重新合成")


MOTION_LABEL = {
    "auto_alternate": "推近与拉远交替（默认）", "push_in": "缓慢推近", "pull_out": "缓慢拉远", "none": "静止",
    "pan_left": "向左平移", "pan_right": "向右平移", "pan_up": "向上平移", "pan_down": "向下平移", "drift": "轻微晃动",
}
TRANSITION_LABEL = {"fade": "淡化（黑场过渡，默认）", "dissolve": "叠化（画面重叠）", "cut": "硬切"}
STRENGTH_LABEL = {"slow": "慢（默认）", "medium": "中"}


def _shots_with_stills(pdir) -> dict:
    """{显示名: (章节号, 镜头id)}：已有场景图的镜头。"""
    from pathlib import Path
    from novel_to_video import chapter_io as cio
    from novel_to_video import project_store as ps
    out = {}
    try:
        chapters = ps.load_project(Path(pdir))["chapters"]
    except Exception:  # noqa: BLE001 - 项目索引不可读时不列出镜头
        return out
    for ch in chapters:
        no = int(ch["no"])
        try:
            shots = cio.load_shots(Path(pdir), no).get("shots") or []
        except Exception:  # noqa: BLE001
            continue
        for sh in shots:
            if sh.get("still_file"):
                out[f"第 {no} 章 · {sh['id']}"] = (no, sh["id"])
    return out


def _still_settings_section(pdir, proj_cfg) -> None:
    from novel_to_video import render_mode as RM
    from novel_to_video.media import motion as MO
    cfg = RM.still_settings(proj_cfg)
    m_keys = list(MO.MOTIONS)
    s_keys = list(MO.STRENGTHS)
    t_keys = list(RM.TRANSITIONS)
    c1, c2 = st.columns(2)
    motion = c1.selectbox("镜内动效", m_keys, index=m_keys.index(cfg["motion"]),
                          format_func=lambda k: MOTION_LABEL.get(k, k), key="still_motion")
    strength = c2.selectbox("动效强度", s_keys, index=s_keys.index(cfg["motion_strength"]),
                            format_func=lambda k: STRENGTH_LABEL.get(k, k), key="still_strength")
    c3, c4, c5 = st.columns(3)
    trans = c3.selectbox("镜间转场", t_keys, index=t_keys.index(cfg["transition"]),
                         format_func=lambda k: TRANSITION_LABEL.get(k, k), key="still_trans")
    tsec = c4.number_input("转场时长（秒）", min_value=0.1, max_value=float(RM.MAX_TRANSITION_SEC), step=0.1,
                           value=float(cfg["transition_sec"]), key="still_tsec",
                           help=f"淡化最长 {RM.MAX_TRANSITION_SEC} 秒；叠化最长 {RM.MAX_DISSOLVE_SEC} 秒（镜头配音后的静音余量）。")
    silent = c5.number_input("无配音镜头时长（秒）", min_value=1.0, max_value=60.0, step=0.5,
                             value=float(cfg["silent_shot_sec"]), key="still_silent")
    st.caption("动效、强度与时长变化后，已生成的动效片段会按新设置重建；转场只影响章节合成。"
               "修改后请重新运行章节，或重新合成章节。")
    if st.button("保存动态漫设置", key="still_save"):
        r = tool_api.set_still_settings(pdir, motion=motion, motion_strength=strength, transition=trans,
                                        transition_sec=float(tsec), silent_shot_sec=float(silent))
        (st.success if r["ok"] else st.error)("已保存" if r["ok"] else r["error"]["message"])

    st.markdown("#### 预览动效")
    st.caption("选一张已生成场景图的镜头，用上面的设置（未保存也可以）生成约 3 秒的预览，用于挑选动效。"
               "预览不写入章节，也不影响审核。")
    shots_map = _shots_with_stills(pdir)
    if not shots_map:
        st.caption("还没有场景图。先运行章节，场景图生成并进入审核后即可预览。")
        return
    pick = st.selectbox("镜头", list(shots_map), key="still_prev_pick")
    if st.button("预览动效", key="still_prev_go"):
        from pathlib import Path
        no, sid = shots_map[pick]
        r = tool_api.preview_motion(pdir, no, sid, motion=motion, motion_strength=strength, transition=trans,
                                    transition_sec=float(tsec))
        if r["ok"]:
            st.video(str(Path(pdir) / r["data"]["path"]))
            st.caption(f"预览时长约 {r['data']['seconds']} 秒")
        else:
            st.error(r["error"]["message"])


def _parse(pdir, spec):
    if not spec.strip():
        return None
    from pathlib import Path
    from novel_to_video import project_store as ps
    return ps.parse_chapter_spec(spec, [c["no"] for c in ps.load_project(Path(pdir))["chapters"]])
