// Berjalan di halaman ElevenLabs (MAIN world): tangkap audio hasil Generate dari respons jaringan,
// lalu teruskan ke content.js lewat window.postMessage. Tidak bergantung pada tombol Download.
(() => {
  const isTTS = u => /text-to-speech|speech-synthesis|\/stream|history\/[^/]+\/audio/i.test(u || "");
  const send = (buf, mime, url) => window.postMessage({ __scEleven: true, buf, mime, url }, "*", [buf]);

  const of = window.fetch;
  window.fetch = async function (...args) {
    const res = await of.apply(this, args);
    try {
      const url = typeof args[0] === "string" ? args[0] : (args[0] && args[0].url) || "";
      const ct = res.headers.get("content-type") || "";
      if (ct.startsWith("audio/") || (isTTS(url) && /octet-stream|mpeg/.test(ct))) {
        res.clone().arrayBuffer().then(b => b.byteLength > 2000 && send(b, ct || "audio/mpeg", url)).catch(() => {});
      }
    } catch (e) {}
    return res;
  };

  const oo = XMLHttpRequest.prototype.open, os = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m, u, ...r) { this.__u = u; return oo.call(this, m, u, ...r); };
  XMLHttpRequest.prototype.send = function (...a) {
    this.addEventListener("load", () => {
      try {
        const ct = this.getResponseHeader("content-type") || "";
        if (!ct.startsWith("audio/")) return;
        const r = this.response;
        if (r instanceof ArrayBuffer && r.byteLength > 2000) send(r.slice(0), ct, this.__u);
        else if (r instanceof Blob && r.size > 2000) r.arrayBuffer().then(b => send(b, ct, this.__u));
      } catch (e) {}
    });
    return os.apply(this, a);
  };
})();
