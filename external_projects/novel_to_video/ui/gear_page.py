"""ui/gear_page.py — 挡位、预算与批量队列（Phase 3）。"""

from __future__ import annotations

import streamlit as st

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
}


def page_gear() -> None:
    st.header("挡位与批量队列")
    root = st.text_input("项目存放目录", value=DEFAULT_PROJECTS_ROOT, key="gear_root")
    pdir = _pick_project(root)
    if not pdir:
        return
    pol = tool_api.gear_policy()["data"]
    import json
    from pathlib import Path
    cur_gear = json.loads((Path(pdir) / "project.json").read_text(encoding="utf-8")).get("gear", "G0")
    cur = st.selectbox("挡位", pol["gears"], index=pol["gears"].index(cur_gear),
                       format_func=lambda g: f"{g} · {GEAR_HELP[g]}", key="gear_pick")
    budget = {}
    if cur in ("G3", "G4", "G5"):
        c1, c2, c3 = st.columns(3)
        budget["max_video_calls"] = c1.number_input("视频调用上限", min_value=0, value=20, step=1)
        budget["max_image_calls"] = c2.number_input("图像调用上限", min_value=0, value=20, step=1)
        budget["max_minutes"] = c3.number_input("耗时上限（分钟）", min_value=0.0, value=120.0, step=10.0)
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

    st.markdown("#### 视频接口等待与重试")
    vcfg = {**ps.DEFAULT_VIDEO_WAIT, **(proj_cfg.get("video") or {})}
    c1, c2, c3 = st.columns(3)
    v_wait = c1.number_input("等待视频完成上限（秒）", min_value=60, max_value=6 * 3600, step=60,
                             value=int(vcfg["max_wait_sec"]), key="gear_v_wait",
                             help="提交后轮询等待视频完成的最长时间。视频通常需要数分钟，默认 3600 秒。")
    v_retry = c2.number_input("普通错误重试次数", min_value=0, max_value=10, step=1,
                              value=int(vcfg["max_retries"]), key="gear_v_retry",
                              help="网络异常、5xx 等普通错误的重试次数。")
    v_queue = c3.number_input("排队满时最多等待（秒）", min_value=0, max_value=6 * 3600, step=60,
                              value=int(vcfg["queue_wait_sec"]), key="gear_v_queue",
                              help="服务端提示 please retry later（排队已满）时，持续等待的上限；这期间不计入重试次数。")
    if st.button("保存视频等待设置", key="gear_v_save"):
        res = tool_api.set_video_wait(pdir, int(v_wait), int(v_retry), int(v_queue))
        (st.success if res["ok"] else st.error)("已保存" if res["ok"] else res["error"]["message"])

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


def _parse(pdir, spec):
    if not spec.strip():
        return None
    from pathlib import Path
    from novel_to_video import project_store as ps
    return ps.parse_chapter_spec(spec, [c["no"] for c in ps.load_project(Path(pdir))["chapters"]])
