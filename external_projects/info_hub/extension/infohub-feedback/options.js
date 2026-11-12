const $ = (id) => document.getElementById(id);
const show = (text) => { $("msg").textContent = text; };

chrome.storage.local.get({ port: 18765, token: "", showAll: false }).then((cfg) => {
  $("port").value = cfg.port;
  $("token").value = cfg.token;
  $("showAll").checked = !!cfg.showAll;
});

$("save").addEventListener("click", async () => {
  const port = Number($("port").value) || 18765;
  await chrome.storage.local.set({ port, token: $("token").value.trim(), showAll: $("showAll").checked });
  show("已保存。");
});

$("test").addEventListener("click", async () => {
  const port = Number($("port").value) || 18765;
  const token = $("token").value.trim();
  try {
    const ping = await fetch(`http://127.0.0.1:${port}/v1/ping`);
    if (!ping.ok) return show(`接收服务返回 ${ping.status}`);
    // 用一个无害的请求验证令牌：发送空反馈，服务端会因校验失败返回 400，但 401 表示令牌错误
    const probe = await fetch(`http://127.0.0.1:${port}/v1/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-InfoHub-Token": token },
      body: "{}",
    });
    if (probe.status === 401) return show("令牌不正确。");
    // 校验接收服务是否支持 /v1/context（旧版本服务没有该接口，小红书等丢失 #ihk 的页面会因此没有工具条）
    const ctx = await fetch(`http://127.0.0.1:${port}/v1/context`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-InfoHub-Token": token },
      body: JSON.stringify({ url: "https://example.invalid/probe" }),
    });
    if (ctx.status === 404) return show("连接正常，但接收服务版本过旧（缺少 /v1/context）。请在看板“服务管理”中停止并重新启动接收服务，同时重启看板。");
    show("连接正常（服务已响应，令牌有效，支持上下文查询）。");
  } catch (e) {
    show("无法连接接收服务，请确认已在项目目录运行 python -m server.feedback_receiver。");
  }
});
