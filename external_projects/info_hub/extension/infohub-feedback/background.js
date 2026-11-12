// 后台 service worker：负责与本地接收服务通信（内容脚本不直接跨域请求）。
const DEFAULTS = { port: 18765, token: "" };

async function loadConfig() {
  return chrome.storage.local.get(DEFAULTS);
}

// 配对：看板“配对插件”开启 5 分钟窗口后，向接收服务取一次令牌并保存；窗口之外会被拒绝（无副作用）
async function tryPair(port) {
  try {
    const resp = await fetch(`http://127.0.0.1:${port}/v1/pair`, { method: "POST" });
    if (!resp.ok) return "";
    const data = await resp.json().catch(() => ({}));
    if (data.token) {
      await chrome.storage.local.set({ token: data.token });
      return data.token;
    }
  } catch (e) { /* 服务未运行等，视为未配对 */ }
  return "";
}

const NOT_PAIRED = "尚未配对：请在看板“服务管理”点击“配对插件”，再刷新此页面（也可在扩展选项中手动填写令牌）";

async function post(path, payload, quiet, retried) {
  const cfg = await loadConfig();
  if (!cfg.token) {
    const got = await tryPair(cfg.port);
    if (!got) {
      if (quiet) return { found: false, reason: NOT_PAIRED };
      return { ok: false, error: NOT_PAIRED };
    }
    return post(path, payload, quiet, true);
  }
  try {
    const resp = await fetch(`http://127.0.0.1:${cfg.port}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-InfoHub-Token": cfg.token },
      body: JSON.stringify(payload),
    });
    const data = await resp.json().catch(() => ({}));
    if (resp.status === 401 && !retried) {
      // 令牌已失效（例如重新生成了令牌）：清掉旧令牌，若配对窗口开着就自动重新配对
      await chrome.storage.local.set({ token: "" });
      return post(path, payload, quiet, true);
    }
    if (quiet) return resp.ok ? data : { found: false, reason: `接收服务返回 HTTP ${resp.status}${resp.status === 404 ? "（服务版本过旧，缺少 /v1/context）" : ""}${data.error ? "：" + data.error : ""}` };
    return { ok: resp.ok, status: resp.status, error: resp.ok ? "" : (data.error || `HTTP ${resp.status}`) };
  } catch (e) {
    if (quiet) return { found: false, reason: `无法连接接收服务：${e.message}` };
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
  if (msg && msg.type === "ensure") {
    // 工具条出现时顺便检查是否已配对；未配对则尝试一次（看板配对窗口开启时才会成功）
    loadConfig().then(async (cfg) => { if (!cfg.token) await tryPair(cfg.port); sendResponse({ ok: true }); });
    return true;
  }
  if (msg && msg.type === "context") {
    // 按页面地址向接收服务查询看板登记的条目上下文；未配置令牌或服务不可用时静默返回未找到
    post("/v1/context", { url: msg.url }, true).then(sendResponse);
    return true;
  }
  if (msg && msg.type === "dwell") {
    post("/v1/dwell", msg.body).then(sendResponse);
    return true;
  }
  return false;
});
