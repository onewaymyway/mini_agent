// 后台 service worker：负责与本地接收服务通信（内容脚本不直接跨域请求）。
const DEFAULTS = { port: 18765, token: "" };

async function loadConfig() {
  return chrome.storage.local.get(DEFAULTS);
}

async function post(path, payload) {
  const cfg = await loadConfig();
  if (!cfg.token) {
    return { ok: false, error: "尚未设置连接令牌，请在扩展选项中填写" };
  }
  try {
    const resp = await fetch(`http://127.0.0.1:${cfg.port}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-InfoHub-Token": cfg.token },
      body: JSON.stringify(payload),
    });
    const data = await resp.json().catch(() => ({}));
    return { ok: resp.ok, status: resp.status, error: resp.ok ? "" : (data.error || `HTTP ${resp.status}`) };
  } catch (e) {
    return { ok: false, error: `无法连接 info_hub 接收服务：${e.message}` };
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "feedback") {
    post("/v1/feedback", msg.body).then(sendResponse);
    return true; // 异步响应
  }
  if (msg && msg.type === "open") {
    post("/v1/open", msg.body).then(sendResponse);
    return true;
  }
  if (msg && msg.type === "dwell") {
    post("/v1/dwell", msg.body).then(sendResponse);
    return true;
  }
  return false;
});
