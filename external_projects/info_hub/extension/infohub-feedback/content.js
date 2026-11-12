// 内容脚本：只在 URL 片段含 #ihk=... 时出现反馈工具条（由 info_hub 看板打开时附加）。
(function () {
  const params = new URLSearchParams(location.hash.slice(1));
  const key = params.get("ihk");
  if (!key || !/^[0-9a-f]{16}$/.test(key)) return;
  if (window.top !== window) return;

  const sourceId = params.get("ihs") || "";
  const view = params.get("ihv") || "";
  const title = params.get("iht") || document.title || "";
  const pageUrl = location.href.split("#")[0];
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

  for (const [id, label] of REASONS) {
    const lab = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.value = id;
    lab.append(cb, " " + label);
    rsBox.append(lab);
  }

  function selectedReasons() {
    return [...rsBox.querySelectorAll("input:checked")].map((c) => c.value);
  }

  function say(text, ok) {
    status.textContent = text;
    status.dataset.ok = ok ? "1" : "0";
  }

  for (const [action, label] of ACTIONS) {
    const btn = document.createElement("button");
    btn.textContent = label;
    btn.addEventListener("click", () => {
      say("发送中…", true);
      chrome.runtime.sendMessage({
        type: "feedback",
        body: {
          action,
          source_id: sourceId,
          url: pageUrl,
          title,
          reasons: selectedReasons(),
          note: note.value.trim(),
          view,
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

  // 停留时长：只统计页面可见的时间。accumulated 为已结束的可见时段合计（毫秒），
  // lastSent 为已发送给接收服务的合计；每次发送的是两者之差，因此可以多次累加、不会重复计数。
  let accumulated = 0;
  let lastSent = 0;
  let visibleSince = document.visibilityState === "visible" ? Date.now() : null;
  function totalMs() {
    return accumulated + (visibleSince !== null ? Date.now() - visibleSince : 0);
  }
  function sendDwell() {
    const total = totalMs();
    const seconds = Math.round((total - lastSent) / 1000);
    if (seconds <= 0) return;
    lastSent = total;
    chrome.runtime.sendMessage({
      type: "dwell",
      body: { source_id: sourceId, item_key: key, view, seconds },
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

  // 打开即记录一次（不论之后是否反馈），用于看板上的点击统计。
  chrome.runtime.sendMessage({
    type: "open",
    body: { source_id: sourceId, item_key: key, view, mode: "dedicated" },
  }, () => void chrome.runtime.lastError);
})();
