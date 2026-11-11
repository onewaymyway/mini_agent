/* info_hub 看板。纯原生 JS，无构建步骤。
   听报使用浏览器 speechSynthesis，不生成音频文件。 */
(function () {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const state = { reports: [], sources: [], categories: [], currentReport: null, plans: [] };

  // ---------- 通用 ----------
  function flash(msg, isError) {
    const el = $("#flash");
    el.textContent = msg;
    el.className = "flash" + (isError ? " error" : "");
    el.hidden = false;
    clearTimeout(flash._t);
    flash._t = setTimeout(() => (el.hidden = true), 3500);
  }

  async function api(path, opts) {
    const init = Object.assign({ headers: {} }, opts || {});
    if (init.body !== undefined) {
      init.method = init.method || "POST";
      init.headers = Object.assign({ "Content-Type": "application/json", "X-Info-Hub": "1" }, init.headers);
      init.body = JSON.stringify(init.body);
    }
    const res = await fetch(path, init);
    let data = {};
    try { data = await res.json(); } catch (e) { /* 非 json 响应 */ }
    if (!res.ok) throw new Error(data.error || ("HTTP " + res.status));
    return data;
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  // Markdown 渲染：先转义，再只转换有限语法，避免注入
  function renderMarkdown(md) {
    const out = [];
    let inList = false;
    for (const raw of md.split("\n")) {
      const line = raw.replace(/\s+$/, "");
      const isItem = /^\s*- /.test(line);
      const isSub = /^\s{2,}- /.test(line);
      if (inList && !isItem) { out.push("</ul>"); inList = false; }
      let m;
      if ((m = line.match(/^(#{1,3})\s+(.*)$/))) {
        out.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`);
      } else if (isItem) {
        if (!inList) { out.push("<ul>"); inList = true; }
        out.push(`<li class="${isSub ? "sub" : ""}">${inline(line.replace(/^\s*- /, ""))}</li>`);
      } else if (line.trim() === "") {
        continue;
      } else {
        out.push(`<p>${inline(line)}</p>`);
      }
    }
    if (inList) out.push("</ul>");
    return out.join("\n");
  }

  function inline(text) {
    let t = esc(text);
    t = t.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
      (_, label, url) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`);
    return t;
  }

  // ---------- 标签页 ----------
  function showTab(name) {
    document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
    document.querySelectorAll(".panel").forEach((p) => (p.hidden = p.id !== "tab-" + name));
    location.hash = name;
    ({ reports: loadReports, sources: loadSources, runs: loadRuns, login: loadLogin })[name]();
  }

  // ---------- 报告 ----------
  async function loadReports() {
    const [list, sources] = await Promise.all([api("/api/reports"), api("/api/sources")]);
    state.reports = list.reports;
    const sel = $("#plan-select");
    if (!sel.options.length) {
      (list.plans || []).forEach((id) => sel.add(new Option(id, id)));
    }
    const ul = $("#report-list");
    ul.innerHTML = "";
    if (!state.reports.length) {
      ul.innerHTML = '<li class="muted">还没有报告。点击"生成报告"或等待调度。</li>';
      return;
    }
    state.reports.forEach((r, idx) => {
      const li = document.createElement("li");
      const s = r.summary || {};
      li.innerHTML = `<div><strong>${esc(r.plan_id)}</strong></div>
        <div class="meta">${esc((r.generated_at || "").replace("T", " ").slice(0, 16))}</div>
        <div class="meta">新增 ${s.new_items || 0} 条 · 异常 ${s.error_count || 0}</div>`;
      li.addEventListener("click", () => openReport(r, li));
      ul.appendChild(li);
      if (idx === 0 && !state.currentReport) openReport(r, li);
    });
  }

  async function openReport(r, li) {
    document.querySelectorAll("#report-list li").forEach((x) => x.classList.remove("active"));
    if (li) li.classList.add("active");
    state.currentReport = r;
    const [{ markdown }, { json }] = await Promise.all([
      api("/api/report?path=" + encodeURIComponent(r.markdown)),
      api("/api/report?path=" + encodeURIComponent(r.json)),
    ]);
    const view = $("#report-view");
    view.innerHTML = `<div class="toolbar"><button id="btn-listen-report">▶ 听报</button></div>`
      + renderMarkdown(markdown) + "<h2>单条朗读</h2><div id=\"report-items\"></div>";
    $("#btn-listen-report").addEventListener("click", () => listenReport(r));
    renderItemButtons(json);
  }

  // 单条朗读：按报告 json 的分节列出条目，每条一个按钮
  function renderItemButtons(digest) {
    const box = $("#report-items");
    let count = 0;
    (digest.sections || []).forEach((sec) => (sec.sources || []).forEach((src) => (src.items || []).forEach((it) => {
      count += 1;
      const row = document.createElement("div");
      row.className = "item-row";
      row.innerHTML = `<button title="朗读这条">🔈</button><span>${esc(it.title)}</span>`;
      row.querySelector("button").addEventListener("click", () => listenItem(src.source_id, it.id));
      box.appendChild(row);
    })));
    if (!count) box.innerHTML = '<p class="muted">本报告没有条目。</p>';
  }

  async function listenReport(r) {
    const { segments } = await api("/api/report/speech?path=" + encodeURIComponent(r.json));
    player.load(segments, `${r.plan_id} · ${(r.generated_at || "").replace("T", " ").slice(0, 16)}`);
    player.play();
  }

  async function listenItem(sourceId, itemId) {
    const { segments, item } = await api(`/api/item/speech?source=${encodeURIComponent(sourceId)}&id=${encodeURIComponent(itemId)}`);
    player.load(segments, item.title || "条目");
    player.play();
  }

  // ---------- 信息源 ----------
  async function loadSources() {
    const data = await api("/api/sources");
    state.sources = data.sources;
    state.categories = data.categories;
    const filter = $("#category-filter");
    if (filter.options.length <= 1) {
      state.categories.forEach((c) => filter.add(new Option(c.name, c.id)));
      filter.addEventListener("change", renderSources);
    }
    renderSources();
  }

  function renderSources() {
    const cat = $("#category-filter").value;
    const tbody = $("#source-rows");
    tbody.innerHTML = "";
    const errs = [];
    state.sources.forEach((s) => {
      if (s.config_errors && s.config_errors.length) { errs.push(s); return; }
      if (cat && s.category !== cat) return;
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${esc(s.name)}<div class="meta muted">${esc(s.id)}</div></td>
        <td>${esc(catName(s.category))}</td>
        <td>${esc(s.kind)} / ${esc(s.adapter)}${s.browser_site ? " · 需登录" : ""}</td>
        <td>${esc(s.every)}</td>
        <td>${esc(fmtTime(s.last_run))}</td>
        <td>${s.last_item_count == null ? "-" : esc(s.last_item_count)}</td>
        <td><input type="checkbox" ${s.enabled ? "checked" : ""} aria-label="启用"></td>
        <td><button>立即抓取</button></td>`;
      tr.querySelector("input").addEventListener("change", async (e) => {
        try {
          await api(`/api/sources/${encodeURIComponent(s.id)}/enabled`, { body: { enabled: e.target.checked } });
          flash(`${s.name} 已${e.target.checked ? "启用" : "停用"}`);
          s.enabled = e.target.checked;
        } catch (err) { flash(err.message, true); e.target.checked = !e.target.checked; }
      });
      tr.querySelector("button").addEventListener("click", async (e) => {
        e.target.disabled = true;
        try {
          const r = await api(`/api/sources/${encodeURIComponent(s.id)}/run`, { body: {} });
          flash(r.ok ? `${s.name}：抓取 ${r.item_count} 条，新增 ${r.new_count} 条` : `${s.name} 失败：${r.error}`, !r.ok);
          loadSources();
        } catch (err) { flash(err.message, true); } finally { e.target.disabled = false; }
      });
      tbody.appendChild(tr);
    });
    $("#config-errors").innerHTML = errs.length
      ? `<h3>配置错误</h3><ul>${errs.map((s) => `<li><strong>${esc(s.id)}</strong>：${esc(s.config_errors.join("；"))}</li>`).join("")}</ul>`
      : "";
  }

  function catName(id) {
    const c = state.categories.find((x) => x.id === id);
    return c ? c.name : id;
  }

  function fmtTime(iso) {
    return iso ? iso.replace("T", " ").slice(0, 16) : "从未";
  }

  // ---------- 运行记录 ----------
  async function loadRuns() {
    const input = $("#runs-day");
    if (!input.value) input.value = new Date().toISOString().slice(0, 10);
    input.onchange = loadRuns;
    const data = await api("/api/runs?day=" + encodeURIComponent(input.value));
    const tbody = $("#run-rows");
    tbody.innerHTML = "";
    if (!data.runs.length) {
      tbody.innerHTML = '<tr><td colspan="4" class="muted">当天没有运行记录</td></tr>';
      return;
    }
    data.runs.forEach((r) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${esc(r.time)}</td><td>${esc(r.source_id)}</td>
        <td class="status-${r.status === "成功" ? "ok" : "失败"}">${esc(r.status)}</td><td>${esc(r.detail)}</td>`;
      tbody.appendChild(tr);
    });
  }

  // ---------- 登录 ----------
  async function loadLogin() {
    const data = await api("/api/login-status");
    const tbody = $("#login-rows");
    tbody.innerHTML = "";
    if (!data.sites.length) {
      tbody.innerHTML = '<tr><td colspan="4" class="muted">config/sites.yaml 中没有登记站点</td></tr>';
      return;
    }
    data.sites.forEach((s) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${esc(s.name)}<div class="meta muted">${esc(s.site)}</div></td>
        <td class="status-${esc(s.status)}">${esc(statusText(s.status))}${s.last_error ? `<div class="meta">${esc(s.last_error)}</div>` : ""}</td>
        <td>${esc(fmtTime(s.last_login_at))}</td>
        <td><code>${esc(s.login_command)}</code></td>`;
      tbody.appendChild(tr);
    });
  }

  function statusText(s) {
    return { logged_in: "已登录", expired: "需要重新登录", unknown: "未登录过" }[s] || s;
  }

  // ---------- 顶部按钮 ----------
  async function generate() {
    const plan = $("#plan-select").value;
    try {
      await api("/api/reports/generate", { body: { plan_id: plan } });
      flash(`已生成报告：${plan}`);
      loadReports();
    } catch (err) { flash(err.message, true); }
  }

  async function tickNow() {
    const btn = $("#btn-tick");
    btn.disabled = true;
    try {
      const r = await api("/api/tick", { body: {} });
      flash(`调度完成：运行 ${r.ran_sources.length} 个源，生成报告 ${r.generated_reports.length} 份`);
      showTab(currentTab());
    } catch (err) { flash(err.message, true); } finally { btn.disabled = false; }
  }

  function currentTab() {
    const h = (location.hash || "#reports").slice(1);
    return ["reports", "sources", "runs", "login"].includes(h) ? h : "reports";
  }

  // ---------- 听报播放器 ----------
  const player = (function () {
    const synth = window.speechSynthesis;
    const st = { segments: [], index: 0, playing: false, title: "", rate: 1, voice: "" };
    const saved = JSON.parse(localStorage.getItem("info_hub_speech") || "{}");
    st.rate = saved.rate || 1;
    st.voice = saved.voice || "";

    function persist() {
      localStorage.setItem("info_hub_speech", JSON.stringify({ rate: st.rate, voice: st.voice }));
    }

    function refreshVoices() {
      if (!synth) return;
      const voices = synth.getVoices();
      const sel = $("#p-voice");
      sel.innerHTML = "";
      const zh = voices.filter((v) => /^zh/i.test(v.lang));
      const list = zh.concat(voices.filter((v) => !/^zh/i.test(v.lang)));
      list.forEach((v) => sel.add(new Option(`${v.name} (${v.lang})`, v.name)));
      if (st.voice && list.some((v) => v.name === st.voice)) sel.value = st.voice;
      else if (zh.length) { sel.value = zh[0].name; st.voice = zh[0].name; }
    }

    function current() { return st.segments[st.index]; }

    function speakCurrent() {
      const seg = current();
      if (!seg) { stop(); return; }
      synth.cancel();
      const u = new SpeechSynthesisUtterance(seg.text);
      u.rate = st.rate;
      u.lang = "zh-CN";
      const voice = synth.getVoices().find((v) => v.name === st.voice);
      if (voice) { u.voice = voice; u.lang = voice.lang; }
      u.onend = () => {
        if (!st.playing) return;
        st.index += 1;
        if (st.index >= st.segments.length) { stop(); flash("播报完成"); return; }
        speakCurrent();
      };
      u.onerror = (e) => { if (e.error !== "interrupted" && e.error !== "canceled") { stop(); flash("语音播放出错：" + e.error, true); } };
      synth.speak(u);
      render();
    }

    function render() {
      $("#p-progress").textContent = st.segments.length ? `${st.index + 1} / ${st.segments.length}` : "";
      $("#p-toggle").textContent = st.playing ? "❚❚" : "▶";
      $("#player-title").textContent = st.title;
    }

    function load(segments, title) {
      stop(false);
      st.segments = segments || [];
      st.index = 0;
      st.title = title || "";
      $("#player").hidden = false;
      render();
    }

    function play() {
      if (!synth) { $("#p-warn").hidden = false; return; }
      if (!st.segments.length) { flash("没有可朗读的内容", true); return; }
      st.playing = true;
      speakCurrent();
    }

    function pause() {
      if (!synth) return;
      st.playing = false;
      synth.pause();
      render();
    }

    function resume() {
      if (!synth) return;
      if (synth.paused) { st.playing = true; synth.resume(); render(); }
      else play();
    }

    function stop(hide = true) {
      st.playing = false;
      if (synth) synth.cancel();
      if (hide) render();
    }

    function move(delta) {
      if (!st.segments.length) return;
      st.index = Math.min(Math.max(st.index + delta, 0), st.segments.length - 1);
      if (st.playing) speakCurrent(); else render();
    }

    $("#p-toggle").addEventListener("click", () => (st.playing ? pause() : resume()));
    $("#p-stop").addEventListener("click", () => stop());
    $("#p-prev").addEventListener("click", () => move(-1));
    $("#p-next").addEventListener("click", () => move(1));
    $("#p-rate").addEventListener("input", (e) => {
      st.rate = parseFloat(e.target.value);
      $("#p-rate-val").textContent = st.rate.toFixed(1);
      persist();
      if (st.playing) speakCurrent();
    });
    $("#p-voice").addEventListener("change", (e) => { st.voice = e.target.value; persist(); if (st.playing) speakCurrent(); });
    if (synth) {
      refreshVoices();
      synth.onvoiceschanged = refreshVoices;
    } else {
      $("#p-warn").hidden = false;
    }
    $("#p-rate").value = st.rate;
    $("#p-rate-val").textContent = Number(st.rate).toFixed(1);

    return { load, play, pause, resume, stop, move };
  })();

  // ---------- 启动 ----------
  document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
  $("#btn-generate").addEventListener("click", generate);
  $("#btn-tick").addEventListener("click", tickNow);
  window.addEventListener("hashchange", () => showTab(currentTab()));
  showTab(currentTab());
  window.__infoHub = { renderMarkdown, esc, player };  // 便于调试与测试
})();
