// Panel ShortsClips di ElevenLabs: ambil naskah Motion (status NASKAH) -> tempel -> Generate -> kirim MP3 ke ShortsClips.
(() => {
  if (window.__scElevenPanel) return; window.__scElevenPanel = true;
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const api = (path, body) => new Promise((res, rej) => chrome.runtime.sendMessage({ path, body },
    r => r && r.ok ? res(r.data) : rej(new Error((r && r.error) || "Ekstensi tidak merespons, muat ulang halaman"))));

  // ---- audio tertangkap dari hook.js ----
  let lastAudio = null, waiters = [];
  window.addEventListener("message", e => {
    if (e.source !== window || !e.data || !e.data.__scEleven) return;
    lastAudio = { buf: e.data.buf, mime: e.data.mime, at: Date.now() };
    waiters.splice(0).forEach(w => w(lastAudio));
    setStatus(`🎧 Audio tertangkap (${Math.round(e.data.buf.byteLength / 1024)} KB)`);
  });
  const waitAudio = (since, ms) => new Promise((res, rej) => {
    if (lastAudio && lastAudio.at > since) return res(lastAudio);
    const t = setTimeout(() => rej(new Error("Audio tidak muncul. Kuota habis / tombol Generate tidak ditemukan?")), ms);
    waiters.push(a => { clearTimeout(t); res(a); });
  });
  const b64 = buf => new Promise(res => { const r = new FileReader(); r.onload = () => res(String(r.result).split(",")[1]); r.readAsDataURL(new Blob([buf])); });

  // ---- isi editor & klik Generate ----
  const visible = el => { const r = el.getBoundingClientRect(); return r.width > 150 && r.height > 40 && getComputedStyle(el).visibility !== "hidden"; };
  function editor() {
    const c = [...document.querySelectorAll("textarea, [contenteditable='true']")].filter(el => visible(el) && !el.closest("#sc-ev"));
    return c.sort((a, b) => b.getBoundingClientRect().height * b.getBoundingClientRect().width - a.getBoundingClientRect().height * a.getBoundingClientRect().width)[0];
  }
  async function fill(text) {
    const el = editor();
    if (!el) throw new Error("Kotak teks ElevenLabs tidak ditemukan. Buka menu Text to Speech.");
    el.focus();
    if (el.tagName === "TEXTAREA") {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(el, text);
      el.dispatchEvent(new Event("input", { bubbles: true }));
    } else {
      document.execCommand("selectAll", false); document.execCommand("insertText", false, text);
    }
    await sleep(700);
  }
  function genButton() {
    return [...document.querySelectorAll("button")].filter(b => !b.closest("#sc-ev") && b.offsetParent)
      .find(b => /^\s*(generate|regenerate|generate speech|buat)\b/i.test(b.innerText || b.getAttribute("aria-label") || ""));
  }
  async function generate() {
    for (let i = 0; i < 20; i++) {
      const b = genButton();
      if (b && !b.disabled && b.getAttribute("aria-disabled") !== "true") { b.click(); return; }
      await sleep(500);
    }
    throw new Error("Tombol Generate tidak ditemukan / tidak aktif (kuota habis?). Klik Generate manual, lalu 📤 Kirim audio terakhir.");
  }

  async function sendAudio(job, a) {
    setStatus(`📤 Mengirim suara "${job.name}" ke ShortsClips...`);
    await api(`/api/motion/${job.id}/eleven_audio`, { audio: await b64(a.buf), mime: a.mime });
    setStatus(`✅ "${job.name}" terkirim. ShortsClips lanjut membuat animasi & render.`);
  }
  async function voice(job) {
    setStatus(`✍️ Menempel naskah "${job.name}"...`);
    await fill(job.text);
    const t0 = Date.now();
    await generate();
    setStatus(`⏳ ElevenLabs membuat suara "${job.name}"...`);
    const a = await waitAudio(t0, 180000);
    await sleep(800);
    await sendAudio(job, lastAudio && lastAudio.at >= a.at ? lastAudio : a);
  }

  // ---- panel ----
  const box = document.createElement("div"); box.id = "sc-ev";
  box.innerHTML = `<div class="hd">🎙️ ShortsClips → ElevenLabs <span class="x" title="Kecilkan">–</span></div>
  <div class="bd"><div class="row"><button data-a="load">🔄 Ambil naskah</button> <button data-a="all" class="ghost">☑ Pilih semua</button></div>
  <div class="list"></div><div class="row"><button data-a="go">▶ Buat suara terpilih</button></div>
  <div class="row"><button data-a="last" class="ghost" title="Bila kamu klik Generate sendiri: kirim ke video yang dipilih">📤 Kirim audio terakhir</button></div>
  <div class="st">Pilih suara (mis. Dera) di ElevenLabs, klik Ambil naskah, pilih satu video, lalu ▶ Buat suara.</div></div>`;
  document.body.appendChild(box);
  const $ = s => box.querySelector(s);
  const setStatus = t => { $(".st").textContent = t; };
  let jobs = [], busy = false; const picked = new Set();
  $(".x").onclick = () => box.classList.toggle("min");

  function draw() {
    $(".list").innerHTML = jobs.length ? jobs.map((j, i) => `<div class="it"><label><input type="checkbox" name="sc-sel" value="${j.id}" ${picked.has(j.id) ? "checked" : ""}> ${j.wait ? "🎧 " : ""}${j.name.replace(/</g, "&lt;")}</label>
      <span class="n">${j.text.length} hrf</span></div>`).join("")
      : `<div class="n">Tidak ada video Motion di status NASKAH.</div>`;
  }
  async function load() {
    const d = await api("/api/eleven/queue"); jobs = d.jobs || []; draw();
    for (const id of [...picked]) if (!jobs.some(j => j.id === id)) picked.delete(id);
    if (!picked.size) jobs.filter(j => j.wait).forEach(j => picked.add(j.id));
    draw(); sum();
  }
  function sum() {
    const sel = jobs.filter(x => picked.has(x.id)), n = sel.reduce((a, x) => a + x.text.length, 0);
    setStatus(sel.length ? `Dipilih ${sel.length} video (${n} karakter kuota).` : `${jobs.length} naskah menunggu. Centang video.`);
  }
  box.addEventListener("change", e => {
    if (e.target.name !== "sc-sel") return;
    e.target.checked ? picked.add(e.target.value) : picked.delete(e.target.value); sum();
  });
  async function run(fn) {
    if (busy) return; busy = true; box.classList.add("busy");
    try { await fn(); } catch (e) { setStatus("⚠️ " + e.message); } finally { busy = false; box.classList.remove("busy"); }
  }
  box.addEventListener("click", e => {
    const b = e.target.closest("button"); if (!b) return;
    if (b.dataset.a === "load") run(load);
    else if (b.dataset.a === "all") { jobs.forEach(j => picked.add(j.id)); draw(); sum(); }
    else if (b.dataset.a === "go") run(async () => {
      const sel = jobs.filter(x => picked.has(x.id));
      if (!sel.length) throw new Error("Centang dulu video Motion-nya.");
      const fail = [];
      for (let i = 0; i < sel.length; i++) {
        const j = sel[i];
        try { setStatus(`(${i + 1}/${sel.length}) ${j.name}`); await voice(j); picked.delete(j.id); }
        catch (e) { fail.push(`${j.name}: ${e.message}`); if (/kuota|quota/i.test(e.message)) break; }
        await sleep(1500);
      }
      await load();
      setStatus(fail.length ? `⚠️ ${sel.length - fail.length}/${sel.length} terkirim. Gagal: ${fail.join(" | ")}` : `✅ ${sel.length} suara selesai & terkirim.`);
    });
    else if (b.dataset.a === "last") run(async () => {
      if (!lastAudio) throw new Error("Belum ada audio tertangkap. Klik Generate dulu.");
      const j = jobs.find(x => picked.has(x.id));
      if (!j || picked.size > 1) throw new Error("Centang tepat satu video tujuan.");
      await sendAudio(j, lastAudio); picked.delete(j.id); await load();
    });
  });
})();
