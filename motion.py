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
    if api_key and api_key != "__claude_code__":
        import anthropic
        msg = anthropic.Anthropic(api_key=api_key).messages.create(
            model=model, max_tokens=16000, messages=[{"role": "user", "content": prompt}])
        return "".join(b.text for b in msg.content if b.type == "text")
    tick = (lambda s: log(f"{label}... {s // 60}:{s % 60:02d} berjalan (biasanya 5-15 menit)", min(s / est, 0.95))) if label else None
    return clipper._ask_claude_code(prompt, log, timeout=1800 if label else 900, progress=tick)


def _extract_html(raw):
    m = re.search(r"<!DOCTYPE html.*?</html>|<html.*?</html>", raw, re.S | re.I)
    if not m:
        raise RuntimeError("AI tidak mengembalikan HTML animasi")
    return m.group(0)


def write_script(topic, lang="id", seconds=30, api_key=None, model="claude-sonnet-5-5", log=print, own_script=""):
    """own_script: naskah milik sendiri; kata-katanya dipertahankan, AI hanya membagi adegan & ide visual."""
    words = int(seconds * clipper.WORDS_PER_SEC)
    bahasa = {"id": "Bahasa Indonesia", "en": "English", "ms": "Bahasa Melayu"}.get(lang, "Bahasa Indonesia")
    log("Claude menulis naskah & storyboard...")
    if own_script:
        rule = f"""Naskah sudah ditulis pemilik channel. Bagi naskah berikut menjadi adegan TANPA mengubah, menambah,
atau menghapus kata-katanya (gabungan semua "text" harus sama persis dengan naskah):
\"\"\"{own_script}\"\"\""""
    else:
        rule = f"- Total {int(words * 0.9)}-{words} kata narasi (sekitar {seconds} detik)."
    raw = _ask(f"""Tulis naskah video motion graphic pendek dalam {bahasa}.
Topik / brief: {topic or '(lihat naskah)'}
{rule}
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


def write_animation(title, timeline, duration, aspect="9:16", style="modern", notes="",
                    api_key=None, model="claude-sonnet-5-5", log=print):
    W, H = SIZES.get(aspect, SIZES["9:16"])
    log("Claude menulis kode animasi (1-3 menit)...")
    prompt = f"""Kamu motion designer kelas atas yang membuat animasi dengan kode (gaya video viral "motion graphics made with code").
Buat motion graphic {aspect} ({W}x{H}) berdurasi {duration} detik.
Judul: {title}
Gaya visual: {STYLES.get(style, style)}
Catatan tambahan: {notes or '-'}

Timeline adegan (detik) beserta narasi dan ide visual. Visual tiap adegan HARUS sinkron dengan waktu ini;
kata kunci/angka penting dimunculkan tepat saat diucapkan (lihat "words" bila ada):
{json.dumps(timeline, ensure_ascii=False)[:14000]}

Kualitas yang diharapkan: teks kinetik (kata muncul, memantul, berganti warna mengikuti narasi), angka yang menghitung naik,
peta/garis/rute yang tergambar sendiri bila relevan, grafik, ikon geometris, transisi geser/zoom yang mulus antar adegan,
palet & tipografi konsisten, detail halus (grain, partikel, glow) agar terlihat mahal. Jangan hanya slide teks statis.
Tidak perlu subtitle penuh; tampilkan kata kunci saja.

{CONTRACT.format(W=W, H=H, DUR=duration)}"""
    return _extract_html(_ask(prompt, api_key, model, log, label="Claude menulis kode animasi"))


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
    return _extract_html(_ask(prompt, api_key, model, log, label="Claude merevisi kode animasi"))


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


def _open(p, html):
    m = re.search(r'width="(\d+)" height="(\d+)"', html)
    W, H = (int(m.group(1)), int(m.group(2))) if m else (1080, 1920)
    b = _browser(p)
    pg = b.new_page(viewport={"width": W, "height": H})
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.route("**/*", lambda r: r.abort())   # animasi tidak boleh mengakses jaringan
    pg.set_content(guard(html))
    return b, pg, errors, (W, H)


def check(html, fps=FPS):
    """Coba gambar SETIAP frame yang nanti dirender (menggambar hanya ~3 ms/frame, jadi beberapa detik saja);
    error bisa muncul hanya di satu frame, jadi cek per 0,1 detik tidak cukup.
    kembalikan pesan error pertama atau None."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b, pg, errors, _ = _open(p, html)
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


def render(html, out_path, audio=None, fps=FPS, log=print):
    """Render tiap frame (PNG dari canvas) lalu kirim ke ffmpeg."""
    from playwright.sync_api import sync_playwright
    import base64
    with sync_playwright() as p:
        b, pg, errors, (W, H) = _open(p, html)
        dur = float(pg.evaluate("window.DURATION"))
        n = int(dur * fps)
        # JPEG kualitas tinggi: encode di browser ~6x lebih cepat dari PNG, beda kualitas tak terlihat setelah H.264
        cmd = ["ffmpeg", "-y", "-v", "error", "-f", "image2pipe", "-framerate", str(fps), "-c:v", "mjpeg", "-i", "-"]
        if audio:
            cmd += ["-i", audio, "-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "160k", "-shortest"]
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
    return out_path
