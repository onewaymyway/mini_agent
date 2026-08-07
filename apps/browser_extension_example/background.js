/**
 * background.js — 示例：只上报"域名 + 停留时长"，不上报完整 URL/查询参数/页面标题内容，
 * 呼应设计方案里的隐私边界（脱敏由服务端 redact_url_path 决定，这里客户端也先做一层）。
 *
 * 默认关闭：需要在 popup 里手动勾选"开启上报"并填入 mini_agent 生成的 report token
 * （在 mini_agent 里执行 /behavior token 获取），否则 background 不会发起任何请求。
 */

const REPORT_URL = "http://127.0.0.1:8765/v1/perception/report"; // 按实际 mini_agent HTTP 端口调整

let current = null; // { domain, since }

async function getSettings() {
  const { enabled, token } = await chrome.storage.local.get(["enabled", "token"]);
  return { enabled: !!enabled, token: token || "" };
}

async function reportEvent(domain, sinceTs) {
  const { enabled, token } = await getSettings();
  if (!enabled || !token) return;

  const duration_sec = (Date.now() - sinceTs) / 1000;
  try {
    await fetch(REPORT_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source: "browser_ext",
        token,
        events: [
          {
            event_type: "page_visit",
            domain,
            duration_sec: Math.round(duration_sec * 10) / 10,
          },
        ],
      }),
    });
  } catch (e) {
    // 本机服务未启动等情况，静默失败，不重试、不缓存明文数据到本地。
  }
}

function domainOf(url) {
  try {
    return new URL(url).hostname;
  } catch {
    return null;
  }
}

async function onActiveTabChanged(url) {
  const domain = domainOf(url || "");
  const now = Date.now();
  if (current && current.domain) {
    await reportEvent(current.domain, current.since);
  }
  current = domain ? { domain, since: now } : null;
}

chrome.tabs.onActivated.addListener(async ({ tabId }) => {
  const { enabled } = await getSettings();
  if (!enabled) return;
  chrome.tabs.get(tabId, (tab) => onActiveTabChanged(tab && tab.url));
});

chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  const { enabled } = await getSettings();
  if (!enabled) return;
  if (changeInfo.status === "complete" && tab.active) {
    onActiveTabChanged(tab.url);
  }
});
