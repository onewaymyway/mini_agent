async function load() {
  const { enabled, token } = await chrome.storage.local.get(["enabled", "token"]);
  document.getElementById("enabled").checked = !!enabled;
  document.getElementById("token").value = token || "";
}

document.getElementById("save").addEventListener("click", async () => {
  const enabled = document.getElementById("enabled").checked;
  const token = document.getElementById("token").value.trim();
  await chrome.storage.local.set({ enabled, token });
  window.close();
});

load();
