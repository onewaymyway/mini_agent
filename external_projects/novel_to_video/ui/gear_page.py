"""ui/gear_page.py — 挡位、预算与批量队列（Phase 3）。"""

from __future__ import annotations

import streamlit as st

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

    cur_retry = int(json.loads((Path(pdir) / "project.json").read_text(encoding="utf-8")).get("script_max_retries") or 0)
    retry = st.number_input("剧本校验失败后自动带反馈重试次数（0 = 不重试，默认）", min_value=0, max_value=3,
                            value=cur_retry, step=1, key="gear_script_retry",
                            help="每次重试多一次文本模型调用。模型输出过长或对白不在原文中时，会把校验问题反馈给模型再生成。")
    if int(retry) != cur_retry and st.button("保存重试次数", key="gear_script_retry_save"):
        res = tool_api.set_script_retries(pdir, int(retry))
        (st.success if res["ok"] else st.error)("已保存" if res["ok"] else res["error"]["message"])

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
