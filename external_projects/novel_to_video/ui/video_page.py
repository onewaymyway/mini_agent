"""ui/video_page.py — 章节视频：所有章节的视频完成情况总览，以及已完成章节的连续播放（预览用）。

* 总览：每章的状态、成片是否存在/是否过期、时长、大小与失败原因，一眼看出哪些章节还没做完。
* 连续播放：把有成片的章节按章号排成播放列表，一章播完自动接着播下一章（可关闭自动连播）。
  成片是否「过期」由 tool_api.chapter_videos 判断（素材改动后还没重新合成）；默认只播放最新的成片。

自动连播的实现：Streamlit 自带的 st.video 没有「播完事件」，所以用一小段脚本监听页面里 <video> 的 ended 事件，
播完后点击页面上的「下一章」按钮（与手动点击完全等价，由同一个回调切换章节）。
脚本只在浏览器里运行，失败时手动点「下一章」依然可用。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st
import streamlit.components.v1 as components

from novel_to_video import tool_api
from ui.common import DEFAULT_PROJECTS_ROOT
from ui.chapter_page import _pick_project

STATUS_LABEL = {"done": "✅ 已完成", "failed": "❌ 失败", "waiting_assets": "⏸ 等待素材",
                "waiting_review": "⏸ 等待审核", "running": "🟢 运行中", "pending": "⚪ 未开始",
                "stopped": "⏹ 已停止"}
NEXT_BUTTON_KEY = "vid_next"        # 「下一章」按钮的 key；Streamlit 会给它的容器加上 CSS 类 st-key-vid_next


def fmt_duration(sec: Optional[float]) -> str:
    """秒 → 「m:ss」或「h:mm:ss」；未知返回「—」。"""
    if sec is None:
        return "—"
    total = int(round(float(sec)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def chapter_state_label(item: Dict[str, Any]) -> str:
    """总览表里的「状态」列：成片存在但已过期时单独标出，避免把旧成片当成已完成。"""
    if item.get("video") and not item.get("stale"):
        return "✅ 已完成"
    if item.get("stale"):
        return "🟡 成片已过期"
    return STATUS_LABEL.get(item.get("status"), str(item.get("status")))


def playlist_of(items: List[Dict[str, Any]], include_stale: bool = False) -> List[Dict[str, Any]]:
    """可连续播放的章节（按章号）：有成片；默认不含已过期的旧成片。"""
    return sorted((i for i in items if i.get("video") and (include_stale or not i.get("stale"))),
                  key=lambda i: i["no"])


def build_autoplay_script(auto: bool) -> str:
    """嵌入页面的脚本（HTML）。每 0.6 秒检查一次父页面里的 <video>，把 onended 指向「点下一章」。
    用 onended 赋值而不是 addEventListener：组件每次重新渲染都会覆盖旧的处理函数，不会重复或遗留已销毁的函数。"""
    return f"""<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:0">
<script>
(function(){{
  var AUTO = {str(bool(auto)).lower()};
  function nextButton(doc){{
    var box = doc.querySelector('.st-key-{NEXT_BUTTON_KEY}');
    var btn = box ? box.querySelector('button') : null;
    if (btn) return btn;
    var all = doc.querySelectorAll('button');
    for (var i = 0; i < all.length; i++) {{ if ((all[i].textContent || '').indexOf('下一章') >= 0) return all[i]; }}
    return null;
  }}
  function bind(){{
    var doc;
    try {{ doc = window.parent.document; }} catch (e) {{ return; }}
    var v = doc.querySelector('video');
    if (!v) return;
    v.onended = function(){{
      if (!AUTO) return;
      var b = nextButton(doc);
      if (b && !b.disabled) b.click();
    }};
  }}
  bind();
  setInterval(bind, 600);
}})();
</script></body></html>"""


def _step(cur_key: str, go_key: str, nos: List[int], delta: int) -> None:
    """上一章 / 下一章的回调：在播放列表里移动一格，并让之后的播放自动开始。"""
    cur = st.session_state.get(cur_key)
    if cur in nos:
        j = nos.index(cur) + delta
        if 0 <= j < len(nos):
            st.session_state[cur_key] = nos[j]
    st.session_state[go_key] = True


def _jumped(go_key: str) -> None:
    st.session_state[go_key] = True


def page_videos() -> None:
    st.header("章节视频")
    st.caption("查看所有章节的视频完成情况，并把已完成的章节连续播放，方便整体预览。")
    root = st.text_input("项目存放目录", value=DEFAULT_PROJECTS_ROOT, key="vid_root")
    pdir = _pick_project(root)
    if not pdir:
        return
    res = tool_api.chapter_videos(pdir)
    if not res["ok"]:
        st.error(res["error"]["message"])
        return
    items: List[Dict[str, Any]] = res["data"]["chapters"]
    sm = res["data"]["summary"]

    cols = st.columns(5)
    cols[0].metric("章节总数", sm["total"], help=f"其中选中 {sm['selected']} 章")
    cols[1].metric("已完成", f"{sm['done']} / {sm['total']}")
    cols[2].metric("失败", sm["failed"])
    cols[3].metric("等待素材/审核", sm["waiting"])
    cols[4].metric("已完成总时长", fmt_duration(sm["total_duration_sec"]))
    if sm["stale"]:
        st.warning(f"有 {sm['stale']} 章的成片已过期（素材或设置改动后还没重新合成），下表以「🟡 成片已过期」标出。")

    st.markdown("#### 完成情况")
    view = st.radio("显示", ["全部", "未完成", "已完成"], horizontal=True, key="vid_filter")

    def _done(i: Dict[str, Any]) -> bool:
        return bool(i["video"]) and not i["stale"]

    shown = [i for i in items if view == "全部" or (view == "已完成") == _done(i)]
    if shown:
        st.dataframe([{
            "章": i["no"], "标题": i["title"], "状态": chapter_state_label(i),
            "时长": fmt_duration(i["duration_sec"]), "大小(MB)": f"{i['size_mb']:.2f}" if i["size_mb"] is not None else "—",
            "选中": "是" if i["selected"] else "否", "更新时间": i["updated_at"],
            "说明": i["message"] if not _done(i) else "",
        } for i in shown], hide_index=True, width='stretch')
    else:
        st.info("没有符合条件的章节。")

    st.markdown("#### 连续播放")
    include_stale = st.checkbox("也播放已过期的旧成片", value=False, key="vid_stale",
                                disabled=not sm["stale"], help="默认只播放与当前素材一致的最新成片。")
    play = playlist_of(items, include_stale)
    if not play:
        st.info("还没有可播放的章节视频。章节完成合成后会出现在这里。")
        return
    nos = [i["no"] for i in play]
    by_no = {i["no"]: i for i in play}
    cur_key, go_key = f"vid_cur:{pdir}", f"vid_go:{pdir}"
    if st.session_state.get(cur_key) not in nos:
        st.session_state[cur_key] = nos[0]

    c_prev, c_pick, c_next, c_auto = st.columns([1, 4, 1, 2])
    idx = nos.index(st.session_state[cur_key])
    c_prev.button("⏮ 上一章", key="vid_prev", disabled=idx == 0, on_click=_step, args=(cur_key, go_key, nos, -1))
    c_pick.selectbox("当前章节（可从任意一章开始，播完后接着往后播）", nos, key=cur_key,
                     format_func=lambda n: f"第 {n} 章　{by_no[n]['title']}（{fmt_duration(by_no[n]['duration_sec'])}）",
                     on_change=_jumped, args=(go_key,))
    c_next.button("⏭ 下一章", key=NEXT_BUTTON_KEY, disabled=idx >= len(nos) - 1,
                  on_click=_step, args=(cur_key, go_key, nos, 1))
    auto = c_auto.checkbox("播完自动连播下一章", value=True, key="vid_auto")

    cur = by_no[st.session_state[cur_key]]
    total = sum(i["duration_sec"] or 0 for i in play)
    st.caption(f"正在播放：第 {cur['no']} 章「{cur['title']}」　·　列表第 {idx + 1} / {len(nos)} 章　·　"
               f"列表总时长 {fmt_duration(total)}" + ("　·　⚠️ 这是已过期的旧成片" if cur["stale"] else ""))
    # 首次进入页面不自动播放；用户切换过章节（或自动连播触发了「下一章」）之后，新章节自动开始播放
    st.video(str(Path(pdir) / cur["video"]), autoplay=bool(st.session_state.get(go_key)))
    if idx >= len(nos) - 1:
        st.caption("这是播放列表的最后一章。")
    components.html(build_autoplay_script(auto), height=0)
