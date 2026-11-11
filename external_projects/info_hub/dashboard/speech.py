"""听报播放器：生成一段自包含的 HTML，在 Streamlit 的 components.html 中运行。

朗读完全由浏览器的 speechSynthesis 完成，服务端不生成音频文件。
段落文本以 JSON 嵌入脚本，并转义 "</"，防止报告内容闭合 <script> 标签。
"""
from __future__ import annotations

import html
import json
from typing import Any


def speech_player_html(segments: list[dict[str, Any]], title: str = "") -> str:
    texts = [s.get("text", "") for s in segments if s.get("text")]
    payload = json.dumps(texts, ensure_ascii=False).replace("</", "<\\/")
    safe_title = html.escape(title or "听报")
    return f"""
<div id="ih-player" style="font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;
     border:1px solid #d0d7de;border-radius:8px;padding:10px 14px;background:#fff;color:#1d2330;">
  <div style="font-weight:600;margin-bottom:6px;">🔈 {safe_title}</div>
  <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
    <button id="ih-prev" title="上一段">⏮</button>
    <button id="ih-toggle" title="播放/暂停">▶ 播放</button>
    <button id="ih-next" title="下一段">⏭</button>
    <button id="ih-stop" title="停止">⏹</button>
    <label style="font-size:12px;color:#6b7280;">语速
      <input id="ih-rate" type="range" min="0.5" max="2" step="0.1" value="1" style="vertical-align:middle;">
      <span id="ih-rate-val">1.0</span></label>
    <label style="font-size:12px;color:#6b7280;">语音 <select id="ih-voice" style="max-width:260px;"></select></label>
    <span id="ih-progress" style="font-size:12px;color:#6b7280;"></span>
  </div>
  <div id="ih-warn" style="display:none;color:#b45309;font-size:12px;margin-top:6px;">
    当前浏览器不支持语音朗读，请使用 Chrome 或 Edge。</div>
</div>
<script>
(function () {{
  const segs = {payload};
  const synth = window.speechSynthesis;
  const $ = (id) => document.getElementById(id);
  const st = {{ i: 0, playing: false, rate: 1, voice: "" }};
  if (!synth || !window.SpeechSynthesisUtterance) {{
    $("ih-warn").style.display = "block";
    return;
  }}
  function voices() {{ return synth.getVoices() || []; }}
  function fillVoices() {{
    const v = voices();
    const sel = $("ih-voice");
    sel.innerHTML = "";
    const zh = v.filter((x) => /^zh/i.test(x.lang));
    const rest = v.filter((x) => !/^zh/i.test(x.lang));
    zh.concat(rest).forEach((x) => {{
      const o = document.createElement("option");
      o.value = x.name; o.textContent = x.name + " (" + x.lang + ")";
      sel.appendChild(o);
    }});
    if (!st.voice && zh.length) st.voice = zh[0].name;
    if (st.voice) sel.value = st.voice;
  }}
  function render() {{
    $("ih-progress").textContent = segs.length ? (st.i + 1) + " / " + segs.length : "没有可朗读的内容";
    $("ih-toggle").textContent = st.playing ? "❚❚ 暂停" : "▶ 播放";
  }}
  function speakCurrent() {{
    if (st.i >= segs.length) {{ stop(); return; }}
    synth.cancel();
    const u = new SpeechSynthesisUtterance(segs[st.i]);
    u.rate = st.rate; u.lang = "zh-CN";
    const v = voices().find((x) => x.name === st.voice);
    if (v) {{ u.voice = v; u.lang = v.lang; }}
    u.onend = () => {{
      if (!st.playing) return;
      st.i += 1;
      if (st.i >= segs.length) {{ stop(); return; }}
      speakCurrent();
    }};
    u.onerror = (e) => {{
      if (e.error !== "interrupted" && e.error !== "canceled") {{ stop(); }}
    }};
    synth.speak(u);
    render();
  }}
  function play() {{
    if (!segs.length) return;
    if (synth.paused) {{ synth.resume(); st.playing = true; render(); return; }}
    st.playing = true; speakCurrent();
  }}
  function pause() {{ st.playing = false; synth.pause(); render(); }}
  function stop() {{ st.playing = false; synth.cancel(); render(); }}
  function move(d) {{
    st.i = Math.min(Math.max(st.i + d, 0), Math.max(segs.length - 1, 0));
    if (st.playing) speakCurrent(); else render();
  }}
  $("ih-toggle").onclick = () => (st.playing ? pause() : play());
  $("ih-stop").onclick = stop;
  $("ih-prev").onclick = () => move(-1);
  $("ih-next").onclick = () => move(1);
  $("ih-rate").oninput = (e) => {{
    st.rate = parseFloat(e.target.value);
    $("ih-rate-val").textContent = st.rate.toFixed(1);
    if (st.playing) speakCurrent();
  }};
  $("ih-voice").onchange = (e) => {{
    st.voice = e.target.value;
    if (st.playing) speakCurrent();
  }};
  synth.onvoiceschanged = fillVoices;
  fillVoices();
  render();
}})();
</script>
"""
