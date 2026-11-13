// Panel kecil di Google Flow: salin batch prompt dari proyek Dokumenter ShortsClips, lalu kirim gambar hasilnya
// kembali ke proyek. Tombol Generate di Flow tetap ditekan sendiri oleh pengguna.
(() => {
  if (window.__scfi) return;
  window.__scfi = true;

  const api = (path, body) => new Promise((ok, fail) =>
    chrome.runtime.sendMessage({ path, body }, r => (r && r.ok ? ok(r.data) : fail(new Error(r ? r.error : "ekstensi tidak merespons")))));
  const STALE = "Ekstensi baru di-reload. Tekan F5 di tab Flow ini dulu.";
  const stale = () => { RUN = false; const s = document.querySelector("#scfi-auto-st, #scfi-msg"); if (s) { s.className = "st err"; s.textContent = STALE; } };
  const store = {   // setelah ekstensi di-reload, skrip lama di tab ini terputus: beri pesan jelas, jangan error merah
    get: async k => { try { return (await chrome.storage.local.get(k))[k]; } catch { stale(); } },
    set: async (k, v) => { try { await chrome.storage.local.set({ [k]: v }); } catch { stale(); } },
  };
  const esc = t => String(t ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  let PID = null, ITEMS = [], ASPECT = "16:9", PAIR = null, FOUND = [];

  const el = document.createElement("div");
  el.id = "scfi";
  el.innerHTML = `
    <div class="h" id="scfi-h">🎬 <span>ShortsClips Flow Importer</span><b id="scfi-tg">–</b></div>
    <div class="b">
      <div class="row"><select id="scfi-p" style="flex:1;min-width:0;max-width:300px"><option>Memuat proyek...</option></select><button class="g" id="scfi-r" title="Muat ulang">⟳</button></div>
      <div class="st" id="scfi-info"></div>
      <div class="sec">⚡ Mode otomatis</div>
      <div class="row st"><label>Jeda <input id="scfi-dmin" type="number" min="10" style="width:52px">–<input id="scfi-dmax" type="number" min="10" style="width:52px"> dtk</label>
        <label>Maks/sesi <input id="scfi-max" type="number" min="1" style="width:52px"></label>
        <label title="Setelah Maks/sesi tercapai: istirahat sekian menit lalu lanjut sendiri. 0 = berhenti.">Istirahat <input id="scfi-rest" type="number" min="0" style="width:46px"> mnt</label>
        <label>Prompt/kiriman <select id="scfi-per"><option value="1">1 (paling aman)</option><option>5</option><option>8</option><option>10</option></select></label></div>
      <label class="row st"><input type="checkbox" id="scfi-pair" checked> Setelah selesai, lanjut otomatis ke proyek pasangannya (Panjang ↔ Shorts)</label>
      <div class="row"><button id="scfi-auto">▶ Mulai otomatis</button><span class="st" id="scfi-auto-st"></span></div>
      <div class="st">Mengetik & mengirim prompt satu per satu dengan jeda acak, gambar langsung terpasang ke adegannya. Berhenti sendiri kalau muncul captcha/error. Biarkan tab ini terbuka.</div>
      <div class="sec">✋ Manual</div>
      <div class="row"><label class="st">Per batch <select id="scfi-n"><option>5</option><option selected>8</option><option>10</option></select></label>
        <button id="scfi-copy">📋 Salin batch berikutnya</button></div>
      <div class="st">Tempel ke kotak "Apa yang ingin Anda buat?", lalu klik tombol buat di Flow.</div>
      <div class="row"><button class="g" id="scfi-scan">🔍 Pindai gambar di project ini</button></div>
      <div id="scfi-list"></div>
      <div class="row"><button id="scfi-send" disabled>⬆ Kirim ke ShortsClips</button><span class="st" id="scfi-msg"></span></div>
    </div>`;
  document.body.appendChild(el);
  const $ = id => el.querySelector("#" + id);
  const msg = (t, cls = "") => { $("scfi-msg").className = "st " + cls; $("scfi-msg").textContent = t; };

  $("scfi-h").onclick = async () => { el.classList.toggle("min"); $("scfi-tg").textContent = el.classList.contains("min") ? "+" : "–"; store.set("min", el.classList.contains("min")).catch(() => {}); };
  store.get("min").then(m => { if (m) { el.classList.add("min"); $("scfi-tg").textContent = "+"; } });

  async function loadProjects() {
    try {
      const ps = await api("/api/flow/projects");
      const last = await store.get("pid");
      $("scfi-p").innerHTML = ps.length ? ps.map(p => `<option value="${esc(p.id)}" ${p.id === last ? "selected" : ""}>${esc(p.topic)} (${p.done}/${p.total})</option>`).join("")
        : "<option value=''>Belum ada proyek Dokumenter dengan prompt</option>";
      await loadManifest();
    } catch (e) { $("scfi-info").innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  }
  async function loadManifest() {
    PID = $("scfi-p").value || null;
    if (!PID) return;
    store.set("pid", PID).catch(() => {});
    const m = await api(`/api/flow/${encodeURIComponent(PID)}/manifest`);
    ITEMS = m.items; ASPECT = m.aspect || "16:9"; PAIR = m.pair || null;
    const todo = ITEMS.filter(x => !x.has_image).length;
    $("scfi-info").textContent = `${ITEMS.length - todo}/${ITEMS.length} gambar sudah terpasang · sisa ${todo} prompt · rasio ${ASPECT}`
      + (PAIR ? " · punya pasangan " + (ASPECT === "9:16" ? "🖥️ Panjang" : "📱 Shorts") : "");
  }
  $("scfi-p").onchange = () => loadManifest().catch(e => msg(e.message, "err"));
  $("scfi-r").onclick = () => loadProjects();

  // ---------- salin batch berikutnya (prompt yang belum punya gambar)
  $("scfi-copy").onclick = async () => {
    try {
      await loadManifest();
      const n = +$("scfi-n").value, todo = ITEMS.filter(x => !x.has_image).slice(0, n);
      if (!todo.length) return msg("Semua prompt sudah punya gambar 🎉", "ok");
      const text = `Generate one ${ASPECT} image for each prompt below, in order. Name/number them exactly as listed.\n\n`
        + todo.map(x => `${String(x.no).padStart(2, "0")}. [${x.scene ? "scene " + x.scene : "THUMBNAIL"}] ${x.prompt}`).join("\n");
      await navigator.clipboard.writeText(text);
      msg(`Batch prompt ${todo[0].no}–${todo[todo.length - 1].no} tersalin. Tempel di Flow.`, "ok");
    } catch (e) { msg(e.message, "err"); }
  };

  // ---------- cocokkan gambar dengan prompt (judul kartu Flow vs isi prompt)
  const STOP = new Set("the and with for from into onto over under that this its are was his her their a an of in on at to by as or is be one two three four five".split(" "));
  const toks = t => (t || "").toLowerCase().replace(/…/g, " ").split(/[^a-z0-9]+/).filter(w => w.length >= 3 && !STOP.has(w)).map(w => w.slice(0, 5));
  function stripStyle(prompts) {   // buang awalan gaya yang sama di semua prompt agar tidak ikut dicocokkan
    if (prompts.length < 2) return prompts;
    let pre = prompts[0];
    for (const p of prompts) while (!p.startsWith(pre)) pre = pre.slice(0, -1);
    return prompts.map(p => p.slice(pre.length));
  }
  function score(label, prompt) {
    const a = toks(label), b = new Set(toks(prompt));
    if (!a.length) return 0;
    return a.filter(w => b.has(w)).length / a.length;
  }

  function match(found, open) {   // pasangkan greedy: skor tertinggi dulu, satu prompt untuk satu gambar
    const bodies = stripStyle(open.map(x => x.prompt));
    const pairs = [];
    found.forEach((f, fi) => open.forEach((o, oi) => pairs.push([score(f.label, bodies[oi]), fi, o.no])));
    pairs.sort((x, y) => y[0] - x[0]);
    const usedF = new Set(), usedP = new Set();
    for (const [s, fi, no] of pairs) {
      if (s <= 0 || usedF.has(fi) || usedP.has(no)) continue;
      found[fi].no = no; found[fi].score = s; usedF.add(fi); usedP.add(no);
    }
    return found;
  }

  $("scfi-scan").onclick = async () => {
    try {
      await loadManifest();
      const sent = (await store.get("sent_" + PID)) || [];
      FOUND = [...document.querySelectorAll("flow-grid-tile-container")].map(t => {
        const img = t.querySelector("img"), mid = t.querySelector("[data-media-id]");
        return img && img.src.includes("/asb/") ? { id: mid ? mid.getAttribute("data-media-id") : img.src, label: t.getAttribute("aria-label") || "", src: img.src } : null;
      }).filter(Boolean).filter(x => !sent.includes(x.id));
      match(FOUND, ITEMS.filter(x => !x.has_image));
      const opts = no => `<option value="">— lewati —</option>` + ITEMS.map(x =>
        `<option value="${x.no}" ${x.no === no ? "selected" : ""}>${String(x.no).padStart(2, "0")} · ${x.scene ? "adegan " + x.scene : "thumbnail"}${x.has_image ? " (ganti)" : ""}</option>`).join("");
      $("scfi-list").innerHTML = FOUND.length ? FOUND.map((f, i) => `<div class="it" data-i="${i}">
          <input type="checkbox" ${f.no != null ? "checked" : ""}><img src="${esc(f.src)}"><select>${opts(f.no)}</select>
          <div class="lb">${esc(f.label)}${f.no != null ? ` · cocok ${Math.round(f.score * 100)}%` : " · tidak yakin, pilih manual"}</div></div>`).join("")
        : `<div class="st">Tidak ada gambar baru di project ini.</div>`;
      $("scfi-send").disabled = !FOUND.length;
      msg(FOUND.length ? `${FOUND.length} gambar ditemukan. Cek pasangannya, lalu kirim.` : "", "");
    } catch (e) { msg(e.message, "err"); }
  };

  const toDataURL = blob => new Promise(r => { const fr = new FileReader(); fr.onload = () => r(fr.result); fr.readAsDataURL(blob); });

  $("scfi-send").onclick = async () => {
    const rows = [...el.querySelectorAll(".it")].map(r => ({ r, f: FOUND[+r.dataset.i], on: r.querySelector("input").checked, no: r.querySelector("select").value === "" ? null : +r.querySelector("select").value }))
      .filter(x => x.on && x.no !== null);
    const nos = rows.map(x => x.no);
    if (new Set(nos).size !== nos.length) return msg("Ada nomor prompt yang dipilih dua kali.", "err");
    if (!rows.length) return msg("Belum ada gambar yang dicentang.", "err");
    $("scfi-send").disabled = true;
    const sent = (await store.get("sent_" + PID)) || [];
    let ok = 0;
    for (const x of rows) {
      try {
        msg(`Mengirim ${ok + 1}/${rows.length}...`);
        const full = x.f.src.replace(/=[^=/]*$/, "") + "=s0";   // resolusi asli
        const blob = await (await fetch(full, { credentials: "include" })).blob();
        await api(`/api/flow/${encodeURIComponent(PID)}/import`, { no: x.no, type: blob.type, data: await toDataURL(blob) });
        sent.push(x.f.id); ok++; x.r.classList.add("sent"); x.r.querySelector("input").checked = false;
      } catch (e) { msg(`Gagal di prompt ${x.no}: ${e.message}`, "err"); break; }
    }
    await store.set("sent_" + PID, sent);
    await loadManifest();
    if (ok === rows.length) msg(`✅ ${ok} gambar terkirim & terpasang ke adegannya.`, "ok");
    $("scfi-send").disabled = false;
  };

  // ---------- mode otomatis: ketik prompt -> klik buat -> tunggu gambar baru -> kirim -> jeda acak
  const DEF = { dmin: 25, dmax: 60, max: 25, per: 1, rest: 10 };
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  let RUN = false, CLICK_ERR = "";
  (async () => {
    const c = { ...DEF, ...((await store.get("auto")) || {}) };
    $("scfi-dmin").value = c.dmin; $("scfi-dmax").value = c.dmax; $("scfi-max").value = c.max; $("scfi-per").value = c.per; $("scfi-rest").value = c.rest;
  })();
  const autoSt = (t, cls = "") => { $("scfi-auto-st").className = "st " + cls; $("scfi-auto-st").textContent = t; };
  const tiles = () => [...document.querySelectorAll("flow-grid-tile-container")].map(t => {
    const img = t.querySelector("img"), mid = t.querySelector("[data-media-id]");
    return img && img.src.includes("/asb/") ? { id: mid ? mid.getAttribute("data-media-id") : img.src, label: t.getAttribute("aria-label") || "", src: img.src } : null;
  }).filter(Boolean);
  const visible = e => e && e.getBoundingClientRect().width > 0 && getComputedStyle(e).visibility !== "hidden";
  function blocked() {   // captcha challenge atau pesan batas/eror yang terlihat
    if ([...document.querySelectorAll("iframe[src*='recaptcha'][src*='bframe'], iframe[title*='challenge' i]")].some(visible)) return "Captcha muncul. Selesaikan dulu, lalu klik Mulai lagi.";
    const txt = document.body.innerText.slice(-4000).toLowerCase();
    const hit = ["terlalu banyak permintaan", "unusual traffic", "too many requests", "kuota", "quota", "batas harian", "daily limit"].find(w => txt.includes(w));
    return hit ? `Flow menampilkan "${hit}". Mode otomatis dijeda.` : "";
  }
  async function typePrompt(text) {
    const box = document.querySelector('div.ProseMirror[contenteditable="true"]');
    const btn = () => document.querySelector('button[type=submit]');
    if (!box || !btn()) throw new Error("Kotak prompt Flow tidak ditemukan. Buka project Flow dulu.");
    box.focus();
    document.execCommand("selectAll"); document.execCommand("delete");
    for (const part of text.match(/.{1,40}(\s|$)|.{1,40}/g)) {   // ketik bertahap seperti tempel/ketik manusia
      document.execCommand("insertText", false, part);
      await sleep(40 + Math.random() * 120);
    }
    await sleep(600 + Math.random() * 900);
    for (let i = 0; i < 20 && btn().disabled; i++) await sleep(300);
    if (btn().disabled) throw new Error("Tombol buat Flow tidak aktif.");
    const sent = () => !box.isConnected || !box.innerText.trim();
    const b = btn();
    b.scrollIntoView({ block: "center" });
    const r = b.getBoundingClientRect();
    el.style.visibility = "hidden";   // panel ini menutupi tombol kirim Flow: sembunyikan sebentar supaya klik kena tombolnya
    await sleep(150);
    CLICK_ERR = "";
    const x = r.x + r.width * (0.35 + Math.random() * 0.3), y = r.y + r.height * (0.35 + Math.random() * 0.3);
    const hit = document.elementFromPoint(x, y);
    if (!hit || !(b === hit || b.contains(hit))) CLICK_ERR = `tombol tertutup oleh <${hit ? hit.tagName.toLowerCase() + (hit.className ? "." + String(hit.className).split(" ")[0] : "") : "?"}>`;
    try {   // klik asli lewat chrome.debugger (Chrome menampilkan bar "sedang men-debug" selama mode otomatis)
      await new Promise((ok, fail) => chrome.runtime.sendMessage({ cmd: "click", x, y },
        res => (res && res.ok ? ok() : fail(new Error(res ? res.error : "ekstensi tidak merespons")))));
    } catch (e) { console.warn("[ShortsClips] klik debugger gagal:", e.message); CLICK_ERR = e.message; }
    await sleep(300);
    el.style.visibility = "";
    for (let i = 0; i < 10 && !sent(); i++) await sleep(500);
    if (sent()) return;
    // cadangan: tombol Enter "asli" di kotak prompt (kursor ditaruh di akhir teks dulu)
    box.focus();
    const sel = getSelection(), rg = document.createRange(); rg.selectNodeContents(box); rg.collapse(false); sel.removeAllRanges(); sel.addRange(rg);
    try {
      await new Promise((ok, fail) => chrome.runtime.sendMessage({ cmd: "enter" }, res => (res && res.ok ? ok() : fail(new Error(res ? res.error : "ekstensi tidak merespons")))));
    } catch (e) { CLICK_ERR = (CLICK_ERR ? CLICK_ERR + "; " : "") + "enter: " + e.message; }
    for (let i = 0; i < 10 && !sent(); i++) await sleep(500);
    if (sent()) return;
    if (!CLICK_ERR) CLICK_ERR = `klik di (${Math.round(x)},${Math.round(y)}) & Enter terkirim tapi Flow tidak bereaksi; zoom ${Math.round(devicePixelRatio * 100)}%`;
    throw new Error(`Kirim otomatis gagal [${CLICK_ERR}]. Mode otomatis dijeda.`);
  }
  async function waitNew(before, need = 1) {
    const t0 = Date.now(), timeout = (3 + need) * 60000;
    let found = [], stableSince = 0;
    while (Date.now() - t0 < timeout && RUN) {
      await sleep(2500);
      const b = blocked(); if (b) throw new Error(b);
      const now = tiles().filter(x => !before.has(x.id));
      if (now.length !== found.length) { found = now; stableSince = Date.now(); }
      else if (found.length >= need && Date.now() - stableSince > 6000) return found;   // tunggu semua varian selesai
      else if (found.length && Date.now() - stableSince > 90000) return found;          // sebagian saja, tidak bertambah lagi
      if (need > 1) autoSt(`${found.length}/${need} gambar batch ini sudah muncul...`);
    }
    if (!RUN) return found;
    if (found.length) return found;
    throw new Error(`Gambar tidak muncul dalam ${Math.round(timeout / 60000)} menit. Mode otomatis dijeda.`);
  }
  async function importTile(f, no) {
    const blob = await (await fetch(f.src.replace(/=[^=/]*$/, "") + "=s0", { credentials: "include" })).blob();
    await api(`/api/flow/${encodeURIComponent(PID)}/import`, { no, type: blob.type, data: await toDataURL(blob) });
  }

  $("scfi-auto").onclick = async () => {
    if (RUN) { RUN = false; autoSt("Berhenti setelah langkah ini..."); return; }
    const c = { dmin: Math.max(10, +$("scfi-dmin").value || DEF.dmin), dmax: Math.max(10, +$("scfi-dmax").value || DEF.dmax), max: Math.max(1, +$("scfi-max").value || DEF.max), per: +$("scfi-per").value || 1,
      rest: Math.max(0, +($("scfi-rest").value === "" ? DEF.rest : $("scfi-rest").value)) };
    if (c.dmax < c.dmin) c.dmax = c.dmin;
    store.set("auto", c).catch(() => {});
    RUN = true; $("scfi-auto").textContent = "⏸ Jeda"; let made = 0, total = 0, sesi = 1; const doneP = new Set();
    try {
      while (RUN && made < c.max) {
        await loadManifest();
        const batch = ITEMS.filter(x => !x.has_image).slice(0, Math.min(c.per, c.max - made));
        if (!batch.length) {
          // antrean: proyek ini selesai -> pindah ke pasangannya (Panjang <-> Shorts) kalau masih ada prompt kosong
          if ($("scfi-pair").checked && PAIR && !doneP.has(PAIR)) {
            doneP.add(PID);
            const nxt = PAIR, opt = [...$("scfi-p").options].find(o => o.value === nxt);
            if (opt) {
              $("scfi-p").value = nxt; await loadManifest();
              if (ITEMS.some(x => !x.has_image)) {
                autoSt(`Proyek pertama selesai 🎉 Lanjut ke ${ASPECT === "9:16" ? "📱 Shorts" : "🖥️ Panjang"} (rasio ${ASPECT})...`, "ok");
                await sleep(4000); continue;
              }
            }
          }
          autoSt("Semua prompt sudah punya gambar 🎉", "ok"); break;
        }
        const b = blocked(); if (b) throw new Error(b);
        const before = new Set(tiles().map(x => x.id));
        if (batch.length === 1) {
          autoSt(`Prompt ${batch[0].no} (adegan ${batch[0].scene}) sedang dibuat...`);
          await typePrompt(`Aspect ratio ${ASPECT}. ${batch[0].prompt}`);
          const fresh = await waitNew(before);
          if (!fresh.length) break;
          await importTile(fresh[fresh.length - 1], batch[0].no);
          made++;
          await store.set("sent_" + PID, ((await store.get("sent_" + PID)) || []).concat(fresh.map(x => x.id)));
        } else {
          // satu pesan berisi beberapa prompt bernomor; gambar dipasangkan lewat judul kartu Flow
          autoSt(`Batch prompt ${batch[0].no}–${batch[batch.length - 1].no} sedang dibuat...`);
          await typePrompt(`Generate one ${ASPECT} image for each prompt below, in order. Name/number them exactly as listed.\n\n`
            + batch.map(x => `${String(x.no).padStart(2, "0")}. [${x.scene ? "scene " + x.scene : "THUMBNAIL"}] ${x.prompt}`).join("\n"));
          const fresh = await waitNew(before, batch.length);
          if (!fresh.length) break;
          match(fresh, batch);
          const left = batch.filter(x => !fresh.some(f => f.no === x.no)), loose = fresh.filter(f => f.no == null);
          if (left.length === 1 && loose.length === 1) loose[0].no = left[0].no;   // sisa satu-satunya
          let got = 0;
          for (const f of fresh.filter(f => f.no != null)) { await importTile(f, f.no); got++; made++; }
          await store.set("sent_" + PID, ((await store.get("sent_" + PID)) || []).concat(fresh.filter(f => f.no != null).map(x => x.id)));
          if (!got) throw new Error("Gambar batch ini tidak bisa dipasangkan otomatis. Pakai 🔍 Pindai gambar (manual).");
          if (got < batch.length) autoSt(`${got}/${batch.length} terpasang; sisanya dicoba lagi di batch berikutnya.`);
        }
        if (made >= c.max) {
          total += made;
          if (!c.rest) { autoSt(`✅ ${total} gambar selesai (batas sesi). Istirahat dulu, lalu Mulai lagi.`, "ok"); break; }
          // istirahat otomatis lalu lanjut sesi berikutnya (pakai jam dinding: timer tab latar bisa diperlambat Chrome)
          const until = Date.now() + c.rest * 60000 * (0.9 + Math.random() * 0.2);
          while (RUN && Date.now() < until) {
            const s2 = Math.ceil((until - Date.now()) / 1000);
            autoSt(`☕ Sesi ${sesi} selesai (${total} gambar). Istirahat, lanjut otomatis dalam ${Math.floor(s2 / 60)}:${String(s2 % 60).padStart(2, "0")}`, "ok");
            await sleep(1000);
          }
          if (!RUN) break;
          made = 0; sesi++; continue;
        }
        const wait = (c.dmin + Math.random() * (c.dmax - c.dmin)) * 1000;
        for (let left = wait; left > 0 && RUN; left -= 1000) { autoSt(`✅ ${made} terkirim · kiriman berikutnya dalam ${Math.ceil(left / 1000)} dtk`, "ok"); await sleep(1000); }
      }
      if (!RUN) autoSt(`Dijeda · ${total + made} gambar terkirim.`);
    } catch (e) { autoSt(e.message, "err"); }
    RUN = false; $("scfi-auto").textContent = "▶ Mulai otomatis";
    try { chrome.runtime.sendMessage({ cmd: "detach" }, () => void chrome.runtime.lastError); } catch { stale(); }
    loadManifest().catch(() => {});
  };

  loadProjects();
})();
