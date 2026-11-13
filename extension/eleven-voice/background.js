// Jembatan ke server ShortsClips lokal (content script di elevenlabs.io tidak boleh langsung memanggil localhost).
const DEFAULT_SERVER = "http://localhost:5000";

async function server() {
  const { server } = await chrome.storage.local.get("server");
  return (server || DEFAULT_SERVER).replace(/\/+$/, "");
}

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  (async () => {
    try {
      const base = await server();
      const opt = msg.body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(msg.body) } : {};
      const r = await fetch(base + msg.path, opt);
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
      reply({ ok: true, data });
    } catch (e) {
      reply({ ok: false, error: String(e.message || e) + " (ShortsClips sudah jalan? run.bat)" });
    }
  })();
  return true;   // balasan async
});
