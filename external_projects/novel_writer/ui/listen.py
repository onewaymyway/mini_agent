"""🎧 听书：浏览器 speechSynthesis 自动连播（M7）。

数据组装在 novel_writer/listen.py（纯逻辑）；这里只负责取章节、嵌入播放器。
播放器完全在浏览器端运行，不生成音频文件，不调用 LLM。
"""

from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components

from novel_writer import tool_api
from novel_writer.listen import build_payload, payload_to_js
from ui.components import show_error

_PLAYER_HTML = """
<div style="font-family:system-ui,'Microsoft YaHei',sans-serif;font-size:14px;color:#222">
  <div id="now" style="font-weight:600;font-size:16px;margin-bottom:4px">准备就绪</div>
  <div id="prog" style="color:#666;margin-bottom:10px"></div>
  <div style="display:flex;gap:8px;flex-wrap:wrap">
    <button id="prev">⏮ 上一章</button>
    <button id="toggle" style="min-width:90px">▶ 播放</button>
    <button id="stop">⏹ 停止</button>
    <button id="next">⏭ 下一章</button>
  </div>
  <div style="margin-top:10px;display:flex;gap:12px;align-items:center;flex-wrap:wrap">
    <label>语速 <select id="rate">__RATE_OPTIONS__</select></label>
    <label>音色 <select id="voice" style="max-width:260px"></select></label>
  </div>
  <div style="color:#888;font-size:12px;margin-top:8px">
    语速与音色在下一段生效。切换标签页时浏览器可能暂停朗读，请保持本页可见。
  </div>
</div>
<script>
(function () {
  const DATA = __PAYLOAD__;
  const segs = DATA.segments, chs = DATA.chapters;
  const synth = window.speechSynthesis;
  const $ = id => document.getElementById(id);
  let idx = 0, state = "idle", token = 0, keepRef = null;

  function chapterIndexOf(i) {
    let k = 0;
    for (let j = 0; j < chs.length; j++) if (chs[j].start <= i) k = j;
    return k;
  }
  function render() {
    if (!segs.length) { $("now").textContent = "没有可朗读的正文"; return; }
    const i = Math.min(idx, segs.length - 1);
    const k = chapterIndexOf(i), c = chs[k];
    $("now").textContent = "第" + c.ch + "章 " + c.title;
    $("prog").textContent = "第 " + (k + 1) + "/" + chs.length + " 章 · 段落 " + (i + 1) + "/" + segs.length;
    $("toggle").textContent = state === "playing" ? "⏸ 暂停" : (state === "paused" ? "▶ 继续" : "▶ 播放");
  }
  function zhVoices() {
    return synth.getVoices().filter(v => (v.lang || "").toLowerCase().startsWith("zh"))
      .sort((a, b) => (/Natural/i.test(b.name) - /Natural/i.test(a.name)) || a.name.localeCompare(b.name));
  }
  function fillVoices() {
    const sel = $("voice"), prev = sel.value;
    sel.innerHTML = "";
    const list = zhVoices();
    if (!list.length) {
      const o = document.createElement("option"); o.value = ""; o.textContent = "浏览器默认（未找到中文语音）";
      sel.appendChild(o);
    }
    list.forEach(v => {
      const o = document.createElement("option"); o.value = v.name; o.textContent = v.name + " (" + v.lang + ")";
      sel.appendChild(o);
    });
    if (prev && [...sel.options].some(o => o.value === prev)) sel.value = prev;
  }
  function speakCurrent(my) {
    if (my !== token) return;
    const u = new SpeechSynthesisUtterance(segs[idx]);
    u.lang = "zh-CN";
    u.rate = parseFloat($("rate").value);
    const name = $("voice").value;
    const v = zhVoices().find(x => x.name === name);
    if (v) { u.voice = v; u.lang = v.lang; }
    u.onend = () => {
      if (my !== token) return;
      idx++;
      if (idx >= segs.length) { idx = 0; state = "idle"; keepRef = null; render(); return; }
      speakCurrent(my);
    };
    u.onerror = e => {
      if (my !== token) return;
      if (e.error === "interrupted" || e.error === "canceled") return;
      state = "idle"; keepRef = null; render();
    };
    keepRef = u;   // 保留引用，防止 Chrome 垃圾回收导致 onend 不触发
    state = "playing";
    render();
    synth.speak(u);
  }
  function speakFrom(i) {
    token++;
    synth.cancel();
    synth.resume();   // 从暂停状态跳转时，先解除暂停
    idx = i;
    speakCurrent(token);
  }
  function stopAll() {
    token++;
    synth.cancel();
    state = "idle"; idx = 0; keepRef = null;
    render();
  }
  function jumpChapter(delta) {
    if (!chs.length) return;
    const k = chapterIndexOf(Math.min(idx, segs.length - 1));
    const target = Math.max(0, Math.min(chs.length - 1, k + delta));
    speakFrom(chs[target].start);
  }

  $("toggle").onclick = () => {
    if (state === "playing") { synth.pause(); state = "paused"; render(); }
    else if (state === "paused") { synth.resume(); state = "playing"; render(); }
    else speakFrom(idx);
  };
  $("stop").onclick = stopAll;
  $("prev").onclick = () => jumpChapter(-1);
  $("next").onclick = () => jumpChapter(1);
  if (synth.onvoiceschanged !== undefined) synth.onvoiceschanged = fillVoices;
  fillVoices();
  render();
})();
</script>
"""


RATE_OPTIONS = [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]


def _rate_options_html() -> str:
    return "".join(
        f'<option value="{r}"{" selected" if r == 1.0 else ""}>{r:g}x</option>' for r in RATE_OPTIONS
    )


def _player_html(payload: dict) -> str:
    return (_PLAYER_HTML.replace("__RATE_OPTIONS__", _rate_options_html())
            .replace("__PAYLOAD__", payload_to_js(payload)))


def render_listen(ws: str, rows: list) -> None:
    """渲染听书区块。rows 为 tool_api.chapters 返回的章节行。"""
    readable = [r for r in rows if r.get("words")]
    with st.expander("🎧 听书（浏览器朗读，自动连播）", expanded=False):
        if not readable:
            st.info("还没有可朗读的正文。")
            return
        numbers = [r["chapter"] for r in readable]
        start = st.selectbox(
            "从第几章开始连播",
            numbers,
            format_func=lambda n: f"第{n}章 {next(r['title'] for r in readable if r['chapter'] == n)}",
            key="listen_start",
        )
        chapters = []
        for n in [x for x in numbers if x >= start]:
            detail = tool_api.chapter(ws, n)
            if not detail["ok"]:
                show_error(detail)
                return
            title = next(r["title"] for r in readable if r["chapter"] == n)
            chapters.append({"chapter": n, "title": title, "text": detail["data"]["text"] or ""})
        payload = build_payload(chapters)
        if not payload["segments"]:
            st.info("所选章节没有可朗读的正文。")
            return
        st.caption(f"共 {len(payload['chapters'])} 章 · {len(payload['segments'])} 段。播放会一直连到末章。")
        components.html(_player_html(payload), height=230)
