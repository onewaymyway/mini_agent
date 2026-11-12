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
  let freshFromHash = false;
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
    freshFromHash = true;
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
    const host = document.createElement("div");
    host.id = "infohub-feedback-host";
    const root = host.attachShadow({ mode: "closed" });
    root.innerHTML = `
      <div class="bar">
        <div class="row">
          <strong>info_hub</strong>
          <span class="status"></span>
          <button class="close" title="收起">×</button>
        </div>
        <div class="row btns"></div>
        <details class="reasons"><summary>原因（可选）</summary><div class="rs"></div>
          <input class="note" maxlength="300" placeholder="备注（可选）">
        </details>
      </div>`;
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
    root.querySelector(".close").addEventListener("click", () => host.remove());
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

    // 只在第一次带标记打开时记录“打开”，避免 SPA 重新渲染时重复计数
    if (freshFromHash) {
      chrome.runtime.sendMessage({
        type: "open",
        body: { source_id: ctx.sourceId, item_key: ctx.key, view: ctx.view, mode: "dedicated" },
      }, () => void chrome.runtime.lastError);
    }
  }

  resolveContext();
})();
