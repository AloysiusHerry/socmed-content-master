// Jembatan ke server ShortsClips lokal (content script di flow.google.com tidak boleh langsung memanggil localhost),
// plus klik "asli" lewat chrome.debugger untuk mode otomatis penuh.
const DEFAULT_SERVER = "http://localhost:5000";
const attached = new Set();

async function server() {
  const { server } = await chrome.storage.local.get("server");
  return (server || DEFAULT_SERVER).replace(/\/+$/, "");
}

async function click(tabId, x, y) {
  const t = { tabId };
  if (!attached.has(tabId)) {
    await chrome.debugger.attach(t, "1.3");
    attached.add(tabId);
  }
  const ev = (type, extra = {}) => chrome.debugger.sendCommand(t, "Input.dispatchMouseEvent", { type, x, y, button: "left", clickCount: 1, ...extra });
  await ev("mouseMoved", { button: "none", clickCount: 0 });
  await ev("mousePressed");
  await new Promise(r => setTimeout(r, 60 + Math.random() * 90));
  await ev("mouseReleased");
}

async function enter(tabId) {
  const t = { tabId };
  if (!attached.has(tabId)) { await chrome.debugger.attach(t, "1.3"); attached.add(tabId); }
  const k = { key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 };
  await chrome.debugger.sendCommand(t, "Input.dispatchKeyEvent", { type: "rawKeyDown", ...k });
  await chrome.debugger.sendCommand(t, "Input.dispatchKeyEvent", { type: "char", text: "\r", ...k });
  await new Promise(r => setTimeout(r, 50 + Math.random() * 60));
  await chrome.debugger.sendCommand(t, "Input.dispatchKeyEvent", { type: "keyUp", ...k });
}

async function detach(tabId) {
  if (attached.delete(tabId)) await chrome.debugger.detach({ tabId }).catch(() => {});
}
chrome.debugger.onDetach.addListener(src => attached.delete(src.tabId));
chrome.tabs.onRemoved.addListener(id => attached.delete(id));

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  (async () => {
    try {
      if (msg.cmd === "click") { await click(sender.tab.id, msg.x, msg.y); return reply({ ok: true }); }
      if (msg.cmd === "enter") { await enter(sender.tab.id); return reply({ ok: true }); }
      if (msg.cmd === "detach") { await detach(sender.tab.id); return reply({ ok: true }); }
      const base = await server();
      const opt = msg.body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(msg.body) } : {};
      const r = await fetch(base + msg.path, opt);
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
      reply({ ok: true, data });
    } catch (e) {
      reply({ ok: false, error: String(e.message || e) + (msg.cmd ? "" : " (ShortsClips sudah jalan? run.bat)") });
    }
  })();
  return true;   // balasan async
});
