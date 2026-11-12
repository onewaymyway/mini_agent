// 内容脚本（document_start 注入）：在页面自身脚本运行之前读取 #ihk=... 标记。
// 小红书等单页应用加载后常用 history.replaceState 改写地址，片段会被删掉，
// 所以标记在最早时刻保存下来，并存入当前标签页的 sessionStorage，之后即使地址被改写也能继续识别。
// 若标记在脚本运行前就已丢失（站点重定向/改写），则凭当前地址向接收服务查询看板登记的上下文（/v1/context）。
// 仍找不到时：只有在扩展选项中开启“在所有页面显示”，才以“手动反馈”模式显示工具条（默认关闭）。
(function () {
  if (window.top !== window) return;
  const STORE = "infohub-feedback-ctx";
  const KEY_RE = /^[0-9a-f]{16}$/;

  let ctx = null;
  const hash = new URLSearchParams(location.hash.slice(1));
  if (KEY_RE.test(hash.get("ihk") || "")) {
    ctx = {
      key: hash.get("ihk"),
      sourceId: hash.get("ihs") || "",
      view: hash.get("ihv") || "",
      title: hash.get("iht") || "",
      url: location.href.split("#")[0],
      pathname: location.pathname,
    };
    try { sessionStorage.setItem(STORE, JSON.stringify(ctx)); } catch (e) { /* 忽略存储不可用 */ }
  } else {
    try {
      const saved = JSON.parse(sessionStorage.getItem(STORE) || "null");
      if (saved && saved.pathname === location.pathname && KEY_RE.test(saved.key)) ctx = saved;
    } catch (e) { /* 忽略 */ }
  }
  const log = (...a) => console.info("[info_hub]", ...a);
  log("内容脚本已加载", location.href.split("#")[0], ctx ? "（片段/会话已有上下文）" : "（需要查询看板登记）");
  const MANUAL_SOURCE = "manual";  // 手动模式使用的信息源 id（不属于任何看板信息源）
  const pageUrl = () => location.href.split("#")[0];

  function resolveContext() {
    if (ctx) return begin();
    const ask = { type: "context", url: pageUrl() };
    chrome.runtime.sendMessage(ask, (resp) => {
      if (!chrome.runtime.lastError && resp && resp.found && resp.context && KEY_RE.test(resp.context.item_key || "")) {
        const c = resp.context;
        ctx = { key: c.item_key, sourceId: c.source_id || "", view: c.view || "", title: c.title || "",
                url: pageUrl(), pathname: location.pathname };
        try { sessionStorage.setItem(STORE, JSON.stringify(ctx)); } catch (e) { /* 忽略 */ }
        log("已通过看板登记恢复条目上下文：", c.source_id, c.item_key);
        return begin();
      }
      log("未找到条目上下文：", resp && resp.reason ? resp.reason : "接收服务已响应但没有匹配的看板登记", resp);
      chrome.storage.local.get({ showAll: false }).then((cfg) => {
        if (!cfg.showAll) return;
        ctx = { manual: true, key: "", sourceId: MANUAL_SOURCE, view: "", title: "", url: "", pathname: location.pathname };
        begin();
      }, () => {});
    });
  }

  function begin() {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", start, { once: true });
    } else {
      start();
    }
  }

  function start() {
    if (document.getElementById("infohub-feedback-host")) return;
    chrome.runtime.sendMessage({ type: "ensure" }, () => void chrome.runtime.lastError);
    const host = document.createElement("div");
    host.id = "infohub-feedback-host";
    // 宿主元素不受页面样式影响；面板用 position:fixed 悬浮在视口右侧，不随页面滚动，
    // 小红书等“页面高度固定、不可滚动”的站点也能看到。
    host.style.cssText = "all:initial;position:fixed;top:0;right:0;width:0;height:0;z-index:2147483647;";
    const root = host.attachShadow({ mode: "closed" });
    root.innerHTML = `
      <style>
        :host { all: initial; }
        .wrap { position: fixed; right: 0; top: 35%; z-index: 2147483647;
                font: 13px/1.4 system-ui, "Microsoft YaHei", sans-serif; color: #1f2328; }
        .wrap[data-collapsed="1"] .bar { display: none; }
        .wrap[data-collapsed="0"] .tab { display: none; }
        .tab { all: unset; box-sizing: border-box; cursor: pointer; padding: 10px 6px; color: #fff;
               background: #2d6cdf; border-radius: 8px 0 0 8px; writing-mode: vertical-rl;
               letter-spacing: 2px; font-size: 12px; box-shadow: -2px 2px 8px rgba(0,0,0,.25); opacity: .9; }
        .tab:hover { opacity: 1; background: #1f56b8; color: #fff; }
        .bar { box-sizing: border-box; width: 172px; padding: 8px 10px 10px; background: #fff;
               border: 1px solid #d0d7de; border-right: 0; border-radius: 10px 0 0 10px;
               box-shadow: -2px 2px 12px rgba(0,0,0,.22); }
        .row { display: flex; align-items: center; gap: 6px; }
        .head { cursor: ns-resize; user-select: none; touch-action: none; margin-bottom: 6px; }
        .head strong { flex: 1; }
        .status { display: block; min-height: 18px; margin: 0 0 4px; font-size: 12px; color: #57606a; word-break: break-all; }
        .status[data-ok="0"] { color: #cf222e; }
        .status[data-ok="1"] { color: #1a7f37; }
        .btns { flex-direction: column; align-items: stretch; gap: 6px; }
        button { font: inherit; cursor: pointer; padding: 6px 8px; border: 1px solid #d0d7de; border-radius: 6px;
                 background: #f6f8fa; color: inherit; text-align: left; }
        button:hover { background: #eaeef2; }
        .close { padding: 0 6px; border: 0; background: transparent; font-size: 16px; line-height: 1; }
        details { margin-top: 8px; }
        summary { cursor: pointer; color: #57606a; font-size: 12px; }
        .rs label { display: block; margin: 4px 0; font-size: 12px; }
        .note { box-sizing: border-box; width: 100%; margin-top: 4px; padding: 4px 6px; font: inherit;
                border: 1px solid #d0d7de; border-radius: 6px; }
      </style>
      <div class="wrap" data-collapsed="0">
        <button class="tab" title="展开 info_hub 反馈">info_hub 反馈</button>
        <div class="bar">
          <div class="row head" title="上下拖动可调整位置">
            <strong>info_hub</strong>
            <button class="close" title="收起到侧边">×</button>
          </div>
          <span class="status"></span>
          <div class="row btns"></div>
          <details class="reasons"><summary>原因（可选）</summary><div class="rs"></div>
            <input class="note" maxlength="300" placeholder="备注（可选）">
          </details>
        </div>
      </div>`;
    const wrap = root.querySelector(".wrap");
    // 页面自己的快捷键（小红书的 J/K、空格等）不应被备注输入框触发
    for (const ev of ["keydown", "keyup", "keypress"]) root.addEventListener(ev, (e) => e.stopPropagation());
    const UI = { collapsed: false, top: null };
    const applyUi = () => {
      wrap.dataset.collapsed = UI.collapsed ? "1" : "0";
      if (UI.top !== null) wrap.style.top = Math.max(0, Math.min(UI.top, window.innerHeight - 40)) + "px";
    };
    const saveUi = () => { try { chrome.storage.local.set({ ui: UI }); } catch (e) { /* 忽略 */ } };
    try {
      chrome.storage.local.get({ ui: UI }).then((cfg) => {
        if (cfg.ui && typeof cfg.ui === "object") {
          UI.collapsed = !!cfg.ui.collapsed;
          UI.top = typeof cfg.ui.top === "number" ? cfg.ui.top : null;
        }
        applyUi();
      }, () => {});
    } catch (e) { /* 忽略 */ }
    root.querySelector(".tab").addEventListener("click", () => { UI.collapsed = false; applyUi(); saveUi(); });
    // 垂直拖动标题栏调整位置
    const head = root.querySelector(".head");
    head.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".close")) return;
      const startY = e.clientY, startTop = wrap.getBoundingClientRect().top;
      head.setPointerCapture(e.pointerId);
      const move = (ev) => { UI.top = startTop + ev.clientY - startY; applyUi(); };
      const up = () => { head.removeEventListener("pointermove", move); head.removeEventListener("pointerup", up); saveUi(); };
      head.addEventListener("pointermove", move);
      head.addEventListener("pointerup", up);
    });
    const btnRow = root.querySelector(".btns");
    const rsBox = root.querySelector(".rs");
    const status = root.querySelector(".status");
    const note = root.querySelector(".note");

    const REASONS = [
      ["off_topic", "话题不符"],
      ["shallow", "内容太浅"],
      ["seen", "已经看过"],
      ["source_dislike", "不喜欢这个来源"],
    ];
    const ACTIONS = [
      ["interested", "👍 感兴趣"],
      ["not_interested", "👎 不感兴趣"],
      ["later", "⏰ 稍后看"],
    ];
    for (const [id, label] of REASONS) {
      const lab = document.createElement("label");
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.value = id;
      lab.append(cb, " " + label);
      rsBox.append(lab);
    }
    const selectedReasons = () => [...rsBox.querySelectorAll("input:checked")].map((c) => c.value);
    if (ctx.manual) {
      status.textContent = "手动反馈（未从看板打开）";
      status.dataset.ok = "1";
    }
    const say = (text, ok) => { status.textContent = text; status.dataset.ok = ok ? "1" : "0"; };

    for (const [action, label] of ACTIONS) {
      const btn = document.createElement("button");
      btn.textContent = label;
      btn.addEventListener("click", () => {
        say("发送中…", true);
        chrome.runtime.sendMessage({
          type: "feedback",
          body: {
            action,
            source_id: ctx.sourceId,
            url: ctx.manual ? pageUrl() : ctx.url,
            title: ctx.manual ? document.title : ctx.title,
            reasons: selectedReasons(),
            note: note.value.trim(),
            view: ctx.view,
            open_mode: "dedicated",
          },
        }, (resp) => {
          if (chrome.runtime.lastError) return say("扩展通信失败", false);
          if (resp && resp.ok) {
            say("已记录：" + label.replace(/^\S+\s/, ""), true);
            for (const cb of rsBox.querySelectorAll("input")) cb.checked = false;
            note.value = "";
          } else {
            say(resp && resp.error ? resp.error : "记录失败", false);
          }
        });
      });
      btnRow.append(btn);
    }
    root.querySelector(".close").addEventListener("click", () => { UI.collapsed = true; applyUi(); saveUi(); });
    document.documentElement.append(host);

    if (ctx.manual) return;  // 手动模式没有看板条目 key，不统计停留与打开

    // 停留时长：只统计页面可见时间，发送增量（可多次累加，不重复计数）
    let accumulated = 0;
    let lastSent = 0;
    let visibleSince = document.visibilityState === "visible" ? Date.now() : null;
    const totalMs = () => accumulated + (visibleSince !== null ? Date.now() - visibleSince : 0);
    function sendDwell() {
      const total = totalMs();
      const seconds = Math.round((total - lastSent) / 1000);
      if (seconds <= 0) return;
      lastSent = total;
      chrome.runtime.sendMessage({
        type: "dwell",
        body: { source_id: ctx.sourceId, item_key: ctx.key, view: ctx.view, seconds },
      }, () => void chrome.runtime.lastError);
    }
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") {
        visibleSince = Date.now();
      } else {
        if (visibleSince !== null) accumulated += Date.now() - visibleSince;
        visibleSince = null;
        sendDwell();
      }
    });
    window.addEventListener("pagehide", sendDwell);

    // “打开”记录由看板在服务端统一写入（open_in_dedicated_browser），插件不再重复上报，避免重复计数
  }

  resolveContext();
})();
