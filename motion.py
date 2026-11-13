"""Motion graphic dari kode: Claude menulis animasi canvas (HTML/JS) yang disinkronkan dengan narasi,
lalu tiap frame dirender di Edge headless (Playwright) dan digabung jadi MP4 dengan ffmpeg."""
import json, os, re, subprocess
import clipper

SIZES = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}
FPS = 30

STYLES = {
    "dokumenter": "Dokumenter sejarah: latar hitam/arang, aksen merah tua & krem, font serif (Georgia) untuk judul, "
                  "tekstur film grain halus, vignette, transisi pelan dan dramatis.",
    "modern": "Modern bersih: latar gelap navy, aksen gradien ungu-tosca, font Segoe UI / Bahnschrift tebal, "
              "teks kinetik memantul, ikon geometris sederhana, transisi geser cepat.",
    "finansial": "Finansial premium: latar hitam, aksen emas & hijau, angka besar yang menghitung naik, grafik garis/batang, "
                 "font Bahnschrift, nuansa mahal dan tenang.",
    "edukasi": "Edukasi ceria: latar terang krem, warna cerah (biru, kuning, oranye), font Segoe UI tebal, "
               "ilustrasi bentuk sederhana, gerakan memantul yang ramah.",
    "kartun": "Cerita kartun animasi: karakter 2D berkepala bulat besar putih (mata titik, alis tipis ekspresif, mulut kecil), "
              "badan ramping berkostum sesuai zaman, bayangan cel lembut; latar penuh warna hangat yang digambar detail "
              "(langit gradien, bangunan, alam) dengan lapisan parallax; SATU kata narasi besar putih ber-outline hitam di "
              "tengah layar mengikuti kata yang sedang diucapkan.",
    "stickman": "Stickman explainer: latar putih bersih (boleh tekstur kertas tipis), tokoh stickman garis hitam tebal "
                "dengan kepala lingkaran, hanya aksen merah & biru untuk penekanan, properti digambar garis sederhana "
                "ala papan tulis/whiteboard, teks kata kunci hitam tebal font Segoe UI.",
    "neon": "Neon cyber: latar hitam pekat, garis dan teks neon (magenta, cyan) dengan efek glow (shadowBlur), "
            "glitch ringan saat transisi, font Consolas/Bahnschrift.",
}

CONTRACT = """KONTRAK TEKNIS (wajib, kalau dilanggar video gagal dirender):
- Balas SATU dokumen HTML lengkap saja (<!DOCTYPE html> ... </html>), tanpa penjelasan, tanpa markdown.
- Hanya satu <canvas id="c" width="{W}" height="{H}"> dan satu <script> inline. Gambar SEMUA visual dengan Canvas 2D.
- Definisikan: window.DURATION = {DUR}; dan window.renderFrame = function(t) {{ ... }}
  renderFrame(t) menggambar ULANG seluruh frame untuk waktu t detik (0..DURATION), DETERMINISTIK:
  jangan pakai Math.random() tanpa seed (pakai PRNG sendiri dengan seed tetap), jangan Date.now(), jangan
  requestAnimationFrame/setTimeout/setInterval, jangan menyimpan state antar frame (frame bisa dipanggil acak).
- Tanpa akses jaringan: tanpa gambar/font/library eksternal, tanpa fetch. Font hanya: Segoe UI, Bahnschrift,
  Arial, Georgia, Impact, Consolas.
- Gunakan easing (easeOutCubic, easeInOutQuad, dll) untuk semua gerakan; sediakan helper sendiri.
- Teks harus muat di layar (ukur dengan ctx.measureText dan bungkus/kecilkan bila perlu), area aman 6% dari tepi."""


def _ask(prompt, api_key, model, log, label=None, est=600):
    """label: tampilkan waktu berjalan tiap 5 detik (log(msg, frac 0..0.95)) selama Claude bekerja;
    menulis kode animasi bisa 5-15 menit, jadi batas waktunya 30 menit."""
    if api_key and api_key.startswith("gemini:"):
        return _ask_gemini(prompt, api_key[7:], log, label, est)
    if api_key and api_key != "__claude_code__":
        import anthropic
        msg = anthropic.Anthropic(api_key=api_key).messages.create(
            model=model, max_tokens=16000, messages=[{"role": "user", "content": prompt}])
        return "".join(b.text for b in msg.content if b.type == "text")
    tick = (lambda s: log(f"{label}... {s // 60}:{s % 60:02d} berjalan (biasanya 5-15 menit)", min(s / est, 0.95))) if label else None
    return clipper._ask_claude_code(prompt, log, timeout=1800 if label else 900, progress=tick)


GEMINI_TEXT_MODELS = ["gemini-pro-latest", "gemini-3.1-pro-preview", "gemini-flash-latest", "gemini-3.8-flash"]   # dicoba berurutan
GEMINI_PRICE = {"flash": (0.30, 2.50), "pro": (1.25, 10.0)}   # perkiraan $/juta token (input, output)


def _ask_gemini(prompt, key, log, label=None, est=600, images=None):
    """Gemini API (pakai API key Gemini yang sama dengan menu Dokumenter). Token berpikir dibatasi supaya hemat.
    images: [(teks label, path jpg/png), ...] disisipkan setelah prompt (untuk narasi dari isi video)."""
    import time, threading, urllib.request, urllib.error, base64
    parts = [{"text": prompt}]
    for lbl, path in images or []:
        if lbl: parts.append({"text": lbl})
        with open(path, "rb") as fh:
            parts.append({"inlineData": {"mimeType": "image/png" if path.lower().endswith(".png") else "image/jpeg",
                                         "data": base64.b64encode(fh.read()).decode()}})
    t0, done, last = time.time(), threading.Event(), ""
    if label:
        def tick():
            while not done.wait(5):
                s = int(time.time() - t0)
                log(f"{label} (Gemini) {s // 60}:{s % 60:02d} berjalan", min(s / est, 0.95))
        threading.Thread(target=tick, daemon=True).start()
    try:
        url0 = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"
        def call(model, thinking):
            gc = {"maxOutputTokens": 65536, "temperature": 0.7}
            if thinking: gc["thinkingConfig"] = thinking
            req = urllib.request.Request(url0.format(model), json.dumps({"contents": [{"parts": parts}],
                                         "generationConfig": gc}).encode(),
                                         {"Content-Type": "application/json", "x-goog-api-key": key})
            with urllib.request.urlopen(req, timeout=900) as r:
                return json.load(r)
        for model in GEMINI_TEXT_MODELS:
            data = None   # batasi token berpikir (hemat); model lama pakai thinkingBudget, model baru thinkingLevel
            thinkings, busy = [{"thinkingLevel": "low"}, {"thinkingBudget": 8192 if label else 1024}, None], 0
            while thinkings:
                try:
                    data = call(model, thinkings[0]); break
                except urllib.error.HTTPError as e:
                    last = f"{model}: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:200]}"
                    if e.code in (400, 403) and "API key" in last:
                        raise RuntimeError("API key Gemini tidak valid") from None
                    if e.code == 400: thinkings.pop(0); continue           # format thinking tidak didukung: coba format lain
                    if e.code in (500, 503) and busy < 3:                   # server Gemini sibuk: tunggu lalu ulangi
                        busy += 1; log(f"Gemini {model} sedang sibuk, coba lagi dalam {20 * busy} detik ({busy}/3)...")
                        time.sleep(20 * busy); continue
                    break   # 404/429/5xx berulang: coba model berikutnya
                except (TimeoutError, OSError) as e:
                    last = f"{model}: {e}"; break
            if data is None:
                why = "kuota gratis 0 / perlu billing" if "HTTP 429" in last else last.split("{")[0].strip()
                log(f"Gemini {model} tidak tersedia ({why}), coba model berikutnya...")
                continue
            text = "".join(p.get("text", "") for c in data.get("candidates") or []
                           for p in (c.get("content") or {}).get("parts") or [] if not p.get("thought"))
            u = data.get("usageMetadata") or {}
            inp, out = u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)
            pi, po = GEMINI_PRICE["flash" if "flash" in model else "pro"]
            clipper.record_usage(log, f"gemini:{model}", inp, out, cost=(inp * pi + out * po) / 1e6, secs=time.time() - t0)
            if text.strip():
                return text
            last = f"{model}: jawaban kosong ({(data.get('candidates') or [{}])[0].get('finishReason')})"
        raise RuntimeError(f"Gemini gagal: {last}")
    finally:
        done.set()


def _extract_html(raw):
    m = re.search(r"<!DOCTYPE html.*?</html>|<html.*?</html>", raw, re.S | re.I)
    if not m:
        raise RuntimeError("AI tidak mengembalikan HTML animasi")
    return m.group(0)


STORY_RULE = """- Format CERITA dengan twist, bukan daftar fakta: kalimat pertama langsung aksi yang aneh (mis. "Dia sengaja membuat
  celah di rel kereta setiap 12 meter."), lalu kisah kala-kini dengan satu tokoh cerdik, satu orang yang meremehkan dengan
  1-2 dialog pendek dalam tanda kutip, dan saingan yang tampak menang; twist di 15 detik terakhir membuktikan sains/sejarah
  di baliknya; kalimat terakhir menyebut fakta aslinya. Tanpa sapaan, tanpa "tahukah kamu", tanpa ajakan subscribe.
- "visual": tulis aksi karakter (siapa, ekspresi, gerakan) + latar adegan; tokoh yang sama memakai deskripsi yang sama."""


def write_script(topic, lang="id", seconds=30, api_key=None, model="claude-sonnet-5-5", log=print, own_script="", style=""):
    """own_script: naskah milik sendiri; kata-katanya dipertahankan, AI hanya membagi adegan & ide visual."""
    words = int(seconds * clipper.WORDS_PER_SEC)
    bahasa = {"id": "Bahasa Indonesia", "en": "English", "ms": "Bahasa Melayu"}.get(lang, "Bahasa Indonesia")
    log("Claude menulis naskah & storyboard...")
    if own_script:
        rule = f"""Naskah sudah ditulis pemilik channel. Bagi naskah berikut menjadi adegan TANPA mengubah, menambah,
atau menghapus kata-katanya (gabungan semua "text" harus sama persis dengan naskah):
\"\"\"{own_script}\"\"\""""
    else:
        if seconds and seconds > 0:
            rule = f"- Total {int(words * 0.9)}-{words} kata narasi (sekitar {seconds} detik)."
        else:   # otomatis: panjang mengikuti kebutuhan cerita, maks 1 menit
            mx = int(60 * clipper.WORDS_PER_SEC * 0.92)
            rule = (f"- Panjang narasi kamu tentukan sendiri sesuai kebutuhan cerita/topik (biasanya 25-55 detik), "
                    f"MAKSIMAL {mx} kata (= 1 menit). Lebih pendek lebih baik bila ceritanya sudah tuntas.")
    raw = _ask(f"""Tulis naskah video motion graphic pendek dalam {bahasa}.
Topik / brief: {topic or '(lihat naskah)'}
{rule}
{STORY_RULE if style == "kartun" else '- "visual": tulis aksi stickman (pose, ekspresi) + properti yang digambar.' if style == "stickman" else ""}
- 4-8 adegan. Adegan 1 = hook yang memancing rasa penasaran. Adegan terakhir = penutup yang membekas.
- Fakta harus akurat. Utamakan angka, tanggal, perbandingan, lokasi yang bisa divisualkan.
- "visual": deskripsi animasi untuk adegan itu (teks kinetik, angka menghitung naik, peta/garis yang tergambar,
  grafik, ikon, kalender, garis waktu, dsb).
Balas HANYA JSON: {{"title": "...", "description": "...", "hashtags": ["#..."],
"scenes": [{{"text": "narasi adegan", "visual": "deskripsi animasi"}}]}}""", api_key, model, log)
    m = re.search(r"\{.*\}", raw, re.S)
    s = json.loads(m.group(0)) if m else None
    if not s or not s.get("scenes"):
        raise RuntimeError("Naskah tidak valid dari AI")
    return s


def timeline_from_tts(scenes, voice, out_dir, log=print):
    """Bacakan tiap adegan (edge-tts) lalu gabungkan; kembalikan (timeline, path audio, durasi)."""
    parts, t0, tl = [], 0.0, []
    voice = clipper.eleven_preflight(voice, " ".join(sc["text"] for sc in scenes), log)
    for k, sc in enumerate(scenes):
        log(f"Suara adegan {k + 1}/{len(scenes)}...")
        a = os.path.join(out_dir, f"_n{k}.mp3")
        dur, words = clipper.tts(sc["text"], voice, a)
        dur += 0.35
        tl.append({"start": round(t0, 2), "end": round(t0 + dur, 2), "text": sc["text"], "visual": sc.get("visual", ""),
                   "words": [{"w": w["word"], "t": round(w["start"] + t0, 2)} for w in words]})
        parts.append((a, dur)); t0 += dur
    # gabung dengan jeda: tiap potongan diberi panjang tetap = dur adegan
    inputs, filt = [], []
    for i, (a, d) in enumerate(parts):
        inputs += ["-i", a]; filt.append(f"[{i}:a]apad,atrim=0:{d:.2f}[a{i}]")
    filt.append("".join(f"[a{i}]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=0:a=1[out]")
    audio = os.path.join(out_dir, "narasi.m4a")
    clipper.run(["ffmpeg", "-y"] + inputs + ["-filter_complex", ";".join(filt), "-map", "[out]", "-c:a", "aac", "-b:a", "160k", audio])
    for a, _ in parts:
        os.remove(a)
    return tl, audio, round(t0, 2)


def timeline_estimate(scenes):
    """Tanpa suara: durasi adegan dari jumlah kata."""
    t0, tl = 0.0, []
    for sc in scenes:
        d = max(2.5, len(sc["text"].split()) / clipper.WORDS_PER_SEC + 0.4)
        tl.append({"start": round(t0, 2), "end": round(t0 + d, 2), "text": sc["text"], "visual": sc.get("visual", "")})
        t0 += d
    return tl, round(t0, 2)


def timeline_from_audio(audio, lang, log=print):
    """Narasi milik sendiri: transkripsi Whisper, segmen digabung jadi adegan >= 3 detik."""
    tr = clipper.transcribe(audio, "small", lang, log)
    tl, cur = [], None
    for s in tr["segments"]:
        if cur is None:
            cur = {"start": s["start"], "end": s["end"], "text": s["text"]}
        else:
            cur["end"], cur["text"] = s["end"], cur["text"] + " " + s["text"]
        if cur["end"] - cur["start"] >= 3:
            tl.append(cur); cur = None
    if cur:
        tl.append(cur)
    words = tr["words"]
    for sc in tl:
        sc["start"], sc["end"] = round(sc["start"], 2), round(sc["end"], 2)
        sc["words"] = [{"w": w["word"], "t": round(w["start"], 2)} for w in words if sc["start"] <= w["start"] < sc["end"]]
    return tl, round(clipper.get_duration(audio), 2)


def timeline_from_audio_scenes(audio, scenes, lang, log=print):
    """Narasi luar (mis. ElevenLabs web) yang membacakan naskah adegan: transkripsi untuk waktu per kata, lalu kata-kata
    dibagi ke adegan sesuai jumlah kata naskah tiap adegan (visual adegan tetap dipakai)."""
    tr = clipper.transcribe(audio, "small", lang, log)
    words, dur = tr["words"], round(clipper.get_duration(audio), 2)
    counts = [max(1, len(sc["text"].split())) for sc in scenes]
    scale = len(words) / sum(counts) if words else 0
    tl, i, cum = [], 0, 0
    for k, (sc, n) in enumerate(zip(scenes, counts)):
        cum += n
        j = len(words) if k == len(scenes) - 1 else min(len(words), round(cum * scale))
        ws = words[i:j]
        start = tl[-1]["end"] if tl else 0.0
        end = dur if k == len(scenes) - 1 else (words[j]["start"] if j < len(words) else dur)
        tl.append({"start": round(start, 2), "end": round(max(end, start + 0.5), 2), "text": sc["text"],
                   "visual": sc.get("visual", ""), "words": [{"w": w["word"], "t": round(w["start"], 2)} for w in ws]})
        i = j
    return tl, dur


CARTOON = """INI ANIMASI KARAKTER (bukan infografis). Wajib:
- Buat fungsi drawCharacter(ctx, x, y, scale, opts) yang dipakai ulang: kepala bulat besar putih dengan outline, mata titik
  yang BERKEDIP berkala, alis yang berubah sesuai emosi (marah/kaget/senang/sombong), mulut kecil yang bergerak saat tokoh itu
  berdialog, badan & tangan-kaki garis tebal yang berayun (idle sway, berjalan dengan siklus kaki, menunjuk, menyilangkan tangan).
  Topi/kostum tiap tokoh berbeda warna supaya mudah dibedakan; tokoh yang sama selalu tampil sama.
- Setiap adegan punya latar sendiri yang digambar detail (gradien langit, bangunan/alam, lantai berperspektif, 2-3 lapis parallax
  yang bergeser pelan) dan properti cerita (rel, kapal, guci, mesin, dsb) yang ikut bergerak/beraksi.
- Kamera hidup: zoom pelan ke tokoh, pan ke properti saat disebut, potong cepat antar sudut kira-kira tiap 1,5-3 detik.
- Ekspresi dramatis saat emosi (wajah memerah ungu ketika marah, uap dari kepala, tanda seru), efek momen twist (shake, kilatan).
- Caption: FRASA 3-5 kata (pakai "words"; kelompokkan kata berurutan, ganti frasa sekaligus, JANGAN per kata), ~64px,
  putih tebal outline hitam tebal, maks 2 baris, di BAGIAN BAWAH layar (pusat teks ±72% tinggi, di atas area tombol Shorts),
  efek pop kecil saat frasa berganti; kata yang sedang diucapkan boleh berwarna kuning. Jangan tampilkan kalimat penuh.
"""


STICKMAN = """INI ANIMASI STICKMAN (bukan infografis). Wajib:
- Buat fungsi drawStickman(ctx, x, y, scale, pose, opts) yang dipakai ulang: kepala lingkaran outline hitam tebal, badan dan
  tangan-kaki garis hitam tebal dengan ujung bulat (lineCap round) dan SENDI (siku, lutut) yang dihitung dari sudut, sehingga
  pose bisa diinterpolasi halus: idle bernapas, berjalan (siklus kaki & ayunan tangan), berlari, menunjuk, mengangkat bahu,
  melompat kaget, menepuk dahi, duduk. Wajah minimal: mata titik yang berkedip, alis & mulut garis untuk emosi.
  Bedakan tokoh dengan satu aksesori kecil berwarna (topi merah, dasi biru, dsb); tokoh yang sama selalu tampil sama.
- Latar putih, properti digambar garis seperti spidol (efek "tergambar" dengan lineDashOffset saat muncul): rumah, uang,
  grafik, mesin, peta, panah. Merah = bahaya/salah, biru = benar/penting.
- Kamera hidup: zoom pelan, pan ke properti saat disebut, potong antar adegan kira-kira tiap 2-3 detik, efek shake saat twist.
- Caption: FRASA 3-5 kata (pakai "words"; kelompokkan kata berurutan, ganti frasa sekaligus, JANGAN per kata), hitam tebal
  ~64px, maks 2 baris, highlight stabilo kuning di belakang kata yang sedang diucapkan, di BAGIAN BAWAH layar (pusat teks ±72%
  tinggi, di atas area tombol Shorts). Jangan tampilkan kalimat penuh.
"""


HOOK = """HOOK 3 DETIK PERTAMA (paling krusial, penonton global scroll sangat cepat). Wajib:
- Frame 0 (t=0) SUDAH penuh aksi: objek/tokoh utama besar di tengah, tidak ada layar kosong, fade-in, atau judul pelan.
- 0-3 detik: minimal 3 perubahan visual (potong/zoom/whip) kira-kira tiap 0,7-1 detik. Keyframe zoom dramatis:
  punch-in cepat 1.0->1.25 dengan ease-out (~0,25 dtk) tepat pada kata kunci pertama, lalu zoom lambat kontinu (Ken Burns) agar tak pernah diam.
- Transisi cepat 0,15-0,3 dtk: whip-pan dengan motion blur (gambar ulang beberapa kali bergeser, alpha rendah), flash putih 2-3 frame,
  atau zoom-through. Tambah camera shake kecil (2-4px) saat momen kejutan.
- Pertanyaan/klaim hook tampil sebagai teks besar kontras tinggi yang 'meninju' masuk (scale 1.4->1 + overshoot) di detik 0-1.
- Color grading tajam sepanjang video: kontras tinggi, saturasi kaya tapi bersih, hitam pekat, highlight terang; aksen 1-2 warna
  mencolok (kuning/merah) untuk hal penting; vignette tipis di tepi agar mata ke tengah. Garis & teks tajam (tanpa blur pada objek utama).
- PENTING: gaya cepat (punch-in, whip, flash, shake, perubahan tiap <1 dtk) HANYA untuk 0-3 detik pertama.
- Setelah detik 3: kembali ke tempo normal & tenang. Satu adegan = satu visual yang dibangun perlahan mengikuti narasi;
  transisi antar adegan halus 0,4-0,6 dtk (crossfade/slide), tanpa whip/flash/shake; boleh zoom lambat halus & gerak kecil
  supaya tidak beku, tapi jangan ada potongan cepat beruntun.
"""


def photo_rule(photos):
    lst = "\n".join(f"  window.PHOTOS[{p['i']}] = \"{p['name']}\" ({p['w']}x{p['h']}px)" for p in photos)
    return f"""FOTO DARI PENGGUNA (WAJIB dipakai, ini inti videonya):
{lst}
- PENGECUALIAN kontrak "tanpa gambar eksternal": window.PHOTOS disediakan sistem. Foto sudah dimuat sebelum renderFrame dipanggil: gambar dengan ctx.drawImage(window.PHOTOS[i], ...). Jangan membuat
  <img>/fetch sendiri. Nama file adalah petunjuk isi foto; cocokkan foto dengan adegan yang paling relevan, urutan default
  = urutan daftar. Setiap foto tampil minimal sekali dan cukup lama (>= 2 detik).
- Perlakukan foto secara sinematik: crop "cover" sesuai rasio layar (atau bingkai/kartu foto dengan bayangan), gerak Ken Burns
  (zoom/pan pelan), masuk-keluar dengan transisi (slide, mask lingkaran/wipe, polaroid jatuh), dan GABUNGKAN dengan elemen
  motion di atasnya: teks kinetik, panah/lingkaran sorotan yang tergambar, angka, ikon, overlay gradien agar teks terbaca.
- Jangan mendistorsi rasio foto (hitung skala dari naturalWidth/naturalHeight)."""


def write_animation(title, timeline, duration, aspect="9:16", style="modern", notes="",
                    api_key=None, model="claude-sonnet-5-5", log=print, photos=None):
    """photos: [{"i": 0, "name": "...", "w": .., "h": ..}] foto upload pengguna (tersedia sebagai window.PHOTOS[i])."""
    W, H = SIZES.get(aspect, SIZES["9:16"])
    log("Menulis kode animasi...")
    if photos:
        notes = (notes or "") + "\n\n" + photo_rule(photos)
    prompt = f"""Kamu motion designer kelas atas yang membuat animasi dengan kode (gaya video viral "motion graphics made with code").
Buat motion graphic {aspect} ({W}x{H}) berdurasi {duration} detik.
Judul: {title}
Gaya visual: {STYLES.get(style, style)}
Catatan tambahan: {notes or '-'}

Timeline adegan (detik) beserta narasi dan ide visual. Visual tiap adegan HARUS sinkron dengan waktu ini;
kata kunci/angka penting dimunculkan tepat saat diucapkan (lihat "words" bila ada):
{json.dumps(timeline, ensure_ascii=False)[:14000]}

{CARTOON if style == "kartun" else STICKMAN if style == "stickman" else ""}{HOOK}Kualitas yang diharapkan: teks kinetik (kata muncul, memantul, berganti warna mengikuti narasi), angka yang menghitung naik,
peta/garis/rute yang tergambar sendiri bila relevan, grafik, ikon geometris, transisi geser/zoom yang mulus antar adegan,
palet & tipografi konsisten, detail halus (grain, partikel, glow) agar terlihat mahal. Jangan hanya slide teks statis.
Tidak perlu subtitle penuh; tampilkan kata kunci saja.

{CONTRACT.format(W=W, H=H, DUR=duration)}"""
    return _extract_html(_ask(prompt, api_key, model, log, label="AI menulis kode animasi"))


def revise_animation(html, instruction, api_key=None, model="claude-sonnet-5-5", log=print, error=None):
    log("Claude merevisi animasi...")
    why = f"Perbaiki ERROR berikut saat render:\n{error}\n" if error else f"Revisi sesuai permintaan: {instruction}\n"
    m = re.search(r'width="(\d+)" height="(\d+)"', html)
    W, H = (m.group(1), m.group(2)) if m else (1080, 1920)
    d = re.search(r"DURATION\s*=\s*([\d.]+)", html)
    prompt = f"""{why}
Pertahankan semua yang tidak diminta berubah. Kode saat ini:
{html}

{CONTRACT.format(W=W, H=H, DUR=d.group(1) if d else 30)}"""
    if "PHOTOS" in html:
        prompt += ("\n- PENGECUALIAN: window.PHOTOS (array HTMLImageElement foto pengguna) sudah disediakan & dimuat oleh "
                   "sistem sebelum renderFrame; tetap pakai dengan ctx.drawImage, jangan didefinisikan ulang.")
    return _extract_html(_ask(prompt, api_key, model, log, label="AI merevisi kode animasi"))


def _browser(p):
    try:
        return p.chromium.launch(channel="msedge")
    except Exception:
        return p.chromium.launch()   # fallback: Chromium bawaan Playwright (perlu "playwright install chromium")


# Pengaman canvas: kode animasi buatan AI sering menghasilkan radius negatif / NaN sesaat
# (mis. easing yang melewati 0), dan browser langsung melempar IndexSizeError. Radius dijepit ke >= 0.
CANVAS_GUARD = """<script>(()=>{const P=CanvasRenderingContext2D.prototype,pos=v=>typeof v==='number'?Math.max(0,v||0):v;
const fix=(n,idx)=>{const o=P[n];if(o)P[n]=function(...a){for(const i of idx)a[i]=pos(a[i]);return o.apply(this,a)}};
fix('arc',[2]);fix('ellipse',[2,3]);fix('arcTo',[4]);fix('createRadialGradient',[2,5]);
const rr=P.roundRect;if(rr)P.roundRect=function(x,y,w,h,r){r=Array.isArray(r)?r.map(pos):pos(r);return rr.call(this,x,y,w,h,r)};})()</script>"""


def guard(html):
    """Sisipkan pengaman canvas sebelum script animasi."""
    i = html.lower().find("<script")
    return html[:i] + CANVAS_GUARD + html[i:] if i >= 0 else CANVAS_GUARD + html


def photo_script(photos):
    """Foto upload pengguna -> window.PHOTOS (HTMLImageElement, data URI tersemat, tanpa jaringan);
    window.PHOTOS_READY = true setelah semua selesai dimuat."""
    import base64
    if not photos:
        return ""
    srcs = []
    for f in photos:
        with open(f, "rb") as fh:
            srcs.append("data:image/jpeg;base64," + base64.b64encode(fh.read()).decode())
    return ("<script>(()=>{const S=" + json.dumps(srcs) + ";let n=0;window.PHOTOS_READY=false;"
            "window.PHOTOS=S.map(s=>{const i=new Image();i.onload=i.onerror=()=>{if(++n===S.length)window.PHOTOS_READY=true};"
            "i.src=s;return i});})()</script>")


def with_photos(html, photos):
    """Sisipkan pengaman canvas + foto sebelum script animasi."""
    html = guard(html)
    ps = photo_script(photos)
    i = html.lower().find("<script")
    return html[:i] + ps + html[i:] if i >= 0 else ps + html


def prepare_photo(src, dst, max_side=1440):
    """Kecilkan & jadikan JPEG (hemat memori browser saat render)."""
    clipper.run(["ffmpeg", "-y", "-v", "error", "-i", src, "-vf",
                 f"scale='if(gt(iw,ih),min({max_side},iw),-2)':'if(gt(iw,ih),-2,min({max_side},ih))'",
                 "-frames:v", "1", "-q:v", "3", dst])
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                        "-of", "csv=p=0", dst], capture_output=True, text=True)
    w, h = (int(x) for x in r.stdout.strip().split(",")[:2])
    return w, h


def _open(p, html, photos=None):
    m = re.search(r'width="(\d+)" height="(\d+)"', html)
    W, H = (int(m.group(1)), int(m.group(2))) if m else (1080, 1920)
    b = _browser(p)
    pg = b.new_page(viewport={"width": W, "height": H})
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.route("**/*", lambda r: r.abort())   # animasi tidak boleh mengakses jaringan
    pg.set_content(with_photos(html, photos))
    if photos:
        pg.wait_for_function("window.PHOTOS_READY === true", timeout=60000)
    return b, pg, errors, (W, H)


def check(html, fps=FPS, photos=None):
    """Coba gambar SETIAP frame yang nanti dirender (menggambar hanya ~3 ms/frame, jadi beberapa detik saja);
    error bisa muncul hanya di satu frame, jadi cek per 0,1 detik tidak cukup.
    kembalikan pesan error pertama atau None."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b, pg, errors, _ = _open(p, html, photos)
        try:
            ok = pg.evaluate("typeof window.renderFrame === 'function' && typeof window.DURATION === 'number'")
            if not ok:
                return "window.renderFrame atau window.DURATION tidak didefinisikan"
            # waktu dihitung PERSIS seperti saat render (k / fps); t += step atau k * step bisa beda di digit terakhir
            err = pg.evaluate("""fps => { const n = Math.floor(window.DURATION * fps);
                    for (let k = 0; k < n; k++) { const t = k / fps;
                    try { window.renderFrame(t); } catch (e) { return `renderFrame(${t.toFixed(2)}) error: ${e && e.stack || e}`; } }
                    try { window.renderFrame(window.DURATION - 0.001); } catch (e) { return `renderFrame(akhir) error: ${e}`; }
                    return null; }""", fps)
            return err or (errors[0] if errors else None)
        finally:
            b.close()


def _label_filter(label, out_path, H):
    """drawtext: label channel di paling bawah tengah, putih 30%. Teks lewat file agar tanda baca aman."""
    tf = out_path + ".label.txt"
    with open(tf, "w", encoding="utf-8") as f: f.write(label)
    esc = lambda p: p.replace("\\", "/").replace(":", "\\:")
    font = "C:/Windows/Fonts/arialbd.ttf"
    fs = max(18, int(H * 0.022))
    return tf, (f"drawtext=fontfile='{esc(font)}':textfile='{esc(tf)}':fontsize={fs}:fontcolor=white@0.3:"
                f"borderw=3:bordercolor=black@0.3:x=(w-text_w)/2:y=h-text_h-{int(H * 0.018)}")


def render(html, out_path, audio=None, fps=FPS, log=print, photos=None, label=""):
    """Render tiap frame (PNG dari canvas) lalu kirim ke ffmpeg. label = teks channel di paling bawah (opacity 30%)."""
    from playwright.sync_api import sync_playwright
    import base64
    with sync_playwright() as p:
        b, pg, errors, (W, H) = _open(p, html, photos)
        dur = float(pg.evaluate("window.DURATION"))
        n = int(dur * fps)
        # JPEG kualitas tinggi: encode di browser ~6x lebih cepat dari PNG, beda kualitas tak terlihat setelah H.264
        cmd = ["ffmpeg", "-y", "-v", "error", "-f", "image2pipe", "-framerate", str(fps), "-c:v", "mjpeg", "-i", "-"]
        if audio:
            cmd += ["-i", audio, "-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "160k", "-shortest"]
        tf = None
        if (label or "").strip():
            tf, vf = _label_filter(label.strip()[:60], out_path, H)
            cmd += ["-vf", vf]
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out_path]
        ff = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            batch = 15   # beberapa frame per panggilan agar lebih cepat
            for i in range(0, n, batch):
                ts = [k / fps for k in range(i, min(i + batch, n))]
                urls = pg.evaluate("""ts => ts.map(t => { window.renderFrame(t);
                                        return document.getElementById('c').toDataURL('image/jpeg', 0.95); })""", ts)
                for u in urls:
                    ff.stdin.write(base64.b64decode(u.split(",", 1)[1]))
                log(f"Render frame {min(i + batch, n)}/{n}", min(i + batch, n) / n)
            ff.stdin.close()
            err = ff.stderr.read().decode("utf-8", "ignore")
            if ff.wait() != 0:
                raise RuntimeError("ffmpeg gagal: " + err[-800:])
        finally:
            if ff.poll() is None:
                ff.kill()
            b.close()
            if tf and os.path.exists(tf): os.remove(tf)
    return out_path
