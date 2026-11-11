"""Inti pemrosesan: download, transkripsi, pilih highlight, potong 9:16, subtitle."""
import os, re, sys, json, subprocess, math

HOOK_WORDS = [
    # Indonesia
    "rahasia", "ternyata", "penting", "jangan", "kenapa", "bagaimana", "cara", "tips",
    "pertama", "masalah", "solusi", "gila", "serius", "wow", "banyak orang", "kesalahan",
    "harus", "uang", "sukses", "gagal", "fakta", "bayangkan", "intinya", "kuncinya",
    # English
    "secret", "never", "always", "why", "how", "mistake", "important", "truth",
    "actually", "crazy", "imagine", "problem", "money", "best", "worst", "the key",
]


def run(cmd, cwd=None):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="ignore")
    if p.returncode != 0:
        raise RuntimeError(f"Perintah gagal: {' '.join(cmd[:3])}...\n{p.stderr[-1500:]}")
    return p.stdout


# Panggil lewat python venv: yt-dlp.exe ada di venv\Scripts yang tidak ada di PATH.
# -N 8 = unduh 8 fragmen paralel.
YTDLP = [sys.executable, "-m", "yt_dlp", "-N", "8", "--no-playlist"]
# Video terbaik + audio, dibatasi lewat urutan: "res" = sisi TERPENDEK, jadi vertikal 1080x1920 tetap boleh.
# (Filter lama height<=1080 membuang semua video vertikal HD dan gagal di Instagram.)
VIDEO_FMT = "bv*+ba/b"
VIDEO_SORT = ["-S", "res:1080"]


# Platform yang dikenali untuk label di dashboard. yt-dlp sendiri mendukung >1000 situs,
# jadi link dari situs lain tetap dicoba.
PLATFORMS = [
    ("youtube", ("youtube.com", "youtu.be")), ("tiktok", ("tiktok.com",)),
    ("instagram", ("instagram.com",)), ("facebook", ("facebook.com", "fb.watch")),
    ("x", ("twitter.com", "x.com")), ("twitch", ("twitch.tv",)), ("vimeo", ("vimeo.com",)),
    ("reddit", ("reddit.com",)), ("dailymotion", ("dailymotion.com", "dai.ly")),
    ("bilibili", ("bilibili.com", "b23.tv")), ("kick", ("kick.com",)), ("threads", ("threads.net",)),
]


SHORT_MAX_SEC = 180   # video sampai 3 menit dianggap video pendek: dibuat 1 klip penuh


def is_short_url(url):
    """Sumber berjenis video pendek: Instagram, TikTok, Threads, atau link YouTube Shorts."""
    return platform_of(url) in ("instagram", "tiktok", "threads") or "/shorts/" in (url or "")


def platform_of(url):
    host = re.sub(r"^https?://", "", (url or "").lower()).split("/")[0]
    for name, domains in PLATFORMS:
        if any(host == d or host.endswith("." + d) for d in domains):
            return name
    return "lainnya" if url else "upload"


# cookies.txt (format Netscape, diekspor sendiri dari browser) = sesi login, untuk konten yang dibatasi
# (Instagram 18+/wilayah, akun privat yang kamu ikuti). Tidak dipakai untuk YouTube.
COOKIES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")


def resolve_url(url):
    """Link pendek yang dialihkan ke halaman login (mis. xhslink.com -> xiaohongshu.com/login?redirectPath=...)
    dibuka jadi URL konten aslinya agar extractor yt-dlp yang tepat dipakai."""
    if not re.search(r"^https?://(www\.)?xhslink\.com/", url or "", re.I):
        return url
    try:
        import urllib.request
        from urllib.parse import urlparse, parse_qs
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        final = urllib.request.urlopen(req, timeout=15).geturl()
        q = parse_qs(urlparse(final).query)
        return (q.get("redirectPath") or [final])[0]
    except Exception as e:
        try:
            return getattr(e, "url", None) or url
        except Exception:
            return url


def _cookie_args(url):
    return ["--cookies", COOKIES] if os.path.exists(COOKIES) and platform_of(url) != "youtube" else []


def _explain(err, url):
    """Tambah petunjuk untuk error konten terbatas/butuh login."""
    if re.search(r"isn't available to everyone|login required|log in|/login|private|age|cookies", err, re.I) or (
            "xiaohongshu" in (url or "") + err and "No video formats" in err):
        tip = ("cookies.txt sudah dipakai, tapi akun itu tetap tidak bisa melihat konten ini "
               "(atau cookies sudah kedaluwarsa: ekspor ulang)." if _cookie_args(url) else
               "Konten ini butuh login. Ekspor cookies browser (ekstensi 'Get cookies.txt LOCALLY') saat login, "
               "simpan sebagai cookies.txt di folder ShortsClips, lalu coba lagi.")
        return err + "\n\n💡 " + tip
    return err


def _mb(b):
    return f"{b / 1048576:.1f} MB"


def _ytdlp(args, label, log=print):
    """Jalankan yt-dlp dengan progress. log(msg, frac) dipanggil dengan frac 0..1.
    Mengembalikan path file hasil."""
    args = args[:-1] + [resolve_url(args[-1])]
    cmd = YTDLP + _cookie_args(args[-1]) + args[:-1] + ["--newline", "--progress", "--progress-template",
                               "download:PROG %(progress.downloaded_bytes)s %(progress.total_bytes,progress.total_bytes_estimate)s",
                               "--print", "after_move:filepath", args[-1]]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         encoding="utf-8", errors="ignore")
    path = None
    for line in p.stdout:
        line = line.strip()
        if line.startswith("PROG "):
            try:
                done, total = (float(x) for x in line.split()[1:3])
                log(f"{label}... {_mb(done)} / {_mb(total)} ({done / total * 100:.0f}%)", done / total)
            except (ValueError, ZeroDivisionError):
                pass
        elif line:
            path = line
    err = p.stderr.read()
    if p.wait() != 0 or not path:
        raise RuntimeError(f"Gagal: {label.lower()}\n{_explain(err[-1500:], args[-1])}")
    return path


def download_video(url, out_dir, log=print, name="source"):
    """Video penuh (dipakai untuk TikTok/Instagram/dll yang videonya pendek)."""
    out = os.path.join(out_dir, name + ".%(ext)s")
    return _ytdlp(["-f", VIDEO_FMT] + VIDEO_SORT + ["--merge-output-format", "mp4", "-o", out, url],
                  "Mengunduh video", log)


def download_audio(url, out_dir, log=print, name="audio", mp3=False):
    """Audio saja (jauh lebih kecil) untuk transkripsi & pencarian highlight."""
    out = os.path.join(out_dir, name + ".%(ext)s")
    extra = ["-x", "--audio-format", "mp3"] if mp3 else []
    return _ytdlp(["-f", "ba/b"] + extra + ["-o", out, url], "Mengunduh audio", log)


def video_info(url):
    """(judul, nama channel/pemilik video)."""
    url = resolve_url(url)
    try:
        out = run(YTDLP + _cookie_args(url) + ["--skip-download", "--print", "%(title)s|||%(channel,uploader,creator)s", url])
        title, _, owner = out.strip().splitlines()[0].partition("|||")
        return title[:120], ("" if owner == "NA" else owner)
    except Exception:
        return url, ""


def video_title(url):
    return video_info(url)[0]


def download_section(url, start, end, out_dir, idx):
    """Unduh hanya potongan [start, end] detik. Dipotong tepat di keyframe agar waktu akurat."""
    out = os.path.join(out_dir, f"part_{idx:02d}.%(ext)s")
    run(YTDLP + _cookie_args(url) + ["-f", VIDEO_FMT] + VIDEO_SORT + ["--merge-output-format", "mp4",
                 "--download-sections", f"*{start:.2f}-{end:.2f}", "--force-keyframes-at-cuts",
                 "-o", out, url])
    return os.path.join(out_dir, f"part_{idx:02d}.mp4")


def get_duration(path):
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", path])
    return float(out.strip())


def get_size(path):
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
               "-show_entries", "stream=width,height", "-of", "json", path])
    s = json.loads(out)["streams"][0]
    return s["width"], s["height"]


def _patch_av():
    """faster-whisper 1.2.x memanggil av.open(..., metadata_errors=...), argumen yang
    sudah dihapus di PyAV versi baru. Buang argumen itu kalau ditolak."""
    import av
    if getattr(av.open, "_shortsclips", False):
        return
    orig = av.open
    def open_(*a, **kw):
        try:
            return orig(*a, **kw)
        except TypeError as e:
            if "metadata_errors" not in str(e):
                raise
            kw.pop("metadata_errors", None)
            return orig(*a, **kw)
    open_._shortsclips = True
    av.open = open_


_model = None
def transcribe(path, model_size="small", language=None, log=print):
    """Transkripsi dengan faster-whisper, hasil per kata."""
    global _model
    _patch_av()
    from faster_whisper import WhisperModel
    if _model is None or _model[0] != model_size:
        log(f"Memuat model Whisper '{model_size}' (pertama kali agak lama)...")
        try:
            m = WhisperModel(model_size, device="cuda", compute_type="float16")
        except Exception:
            m = WhisperModel(model_size, device="cpu", compute_type="int8")
        _model = (model_size, m)
    model = _model[1]
    segments, info = model.transcribe(path, language=language or None,
                                      word_timestamps=True, vad_filter=True)
    words, segs = [], []
    for s in segments:
        # float() karena faster-whisper bisa mengembalikan numpy.float64 (tidak bisa disimpan ke JSON)
        segs.append({"start": float(s.start), "end": float(s.end), "text": s.text.strip()})
        for w in (s.words or []):
            words.append({"start": float(w.start), "end": float(w.end), "word": w.word.strip()})
        frac = float(min(s.end / info.duration, 1)) if info.duration else 0
        log(f"Transkripsi... {_fmt(s.end)} / {_fmt(info.duration)} ({frac * 100:.0f}%)", frac)
    return {"language": info.language, "segments": segs, "words": words}


def score_text(text, duration):
    t = text.lower()
    score = 0.0
    score += sum(2.0 for h in HOOK_WORDS if h in t)
    score += t.count("?") * 1.5 + t.count("!") * 1.0
    score += re.findall(r"\d+", t).__len__() * 0.5          # angka = konkret
    wps = len(t.split()) / max(duration, 1)
    score += min(wps, 4) * 2                                  # padat bicara
    return score


def find_highlights(transcript, n_clips=5, min_len=20, max_len=60):
    """Gabungkan segmen jadi kandidat klip, beri skor, ambil yang terbaik tanpa tumpang tindih."""
    segs = transcript["segments"]
    cands = []
    for i in range(len(segs)):
        text, start = "", segs[i]["start"]
        for j in range(i, len(segs)):
            text += " " + segs[j]["text"]
            dur = segs[j]["end"] - start
            if dur > max_len:
                break
            if dur >= min_len and re.search(r"[.!?]$", segs[j]["text"].strip()):
                sc = score_text(text, dur)
                # bonus jika kalimat pertama berupa hook
                first = segs[i]["text"].lower()
                if "?" in first or any(h in first for h in HOOK_WORDS):
                    sc += 4
                cands.append({"start": start, "end": segs[j]["end"],
                              "text": text.strip(), "score": round(sc, 2)})
    cands.sort(key=lambda c: c["score"], reverse=True)
    picked = []
    for c in cands:
        if all(c["end"] <= p["start"] or c["start"] >= p["end"] for p in picked):
            picked.append(c)
        if len(picked) >= n_clips:
            break
    # skor viral 0-100 relatif
    if picked:
        top = picked[0]["score"] or 1
        for p in picked:
            p["viral"] = int(60 + 39 * p["score"] / top)
    return sorted(picked, key=lambda c: c["start"])


def face_track(path, start, end, width, step=0.5):
    """Posisi X wajah terbesar tiap `step` detik (OpenCV). None jika tidak ada wajah sama sekali.
    Hasil: daftar (detik_relatif, x) yang sudah dihaluskan."""
    try:
        import cv2
        cap = cv2.VideoCapture(path)
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        pts, n = [], max(2, int((end - start) / step) + 1)
        for k in range(n):
            t = min(start + k * step, end - 0.05)
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok:
                pts.append(None); continue
            small = cv2.resize(frame, None, fx=0.5, fy=0.5)
            faces = cascade.detectMultiScale(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), 1.1, 5, minSize=(40, 40))
            pts.append((lambda f: (f[0] + f[2] / 2) * 2)(max(faces, key=lambda f: f[2] * f[3])) if len(faces) else None)
        cap.release()
    except Exception:
        return None
    return _smooth_track(pts, width, step)


def _smooth_track(pts, width, step):
    found = [x for x in pts if x is not None]
    if len(found) < max(2, len(pts) * 0.2):
        return None
    # isi frame tanpa wajah dengan posisi terdekat, lalu median 5 titik agar tidak loncat-loncat
    last = found[0]
    for i, x in enumerate(pts):
        pts[i] = last = x if x is not None else last
    med = [sorted(pts[max(0, i - 2):i + 3])[len(pts[max(0, i - 2):i + 3]) // 2] for i in range(len(pts))]
    # zona mati: kamera virtual baru bergeser jika wajah pindah > 8% lebar video
    cam, out = med[0], []
    for i, x in enumerate(med):
        if abs(x - cam) > width * 0.08:
            cam = x
        out.append((i * step, cam))
    return out


def subject_track(path, start, end, width, step=0.5):
    """Untuk video tanpa wajah manusia (hewan, objek): ikuti subjek utama lewat gerakan antar-frame
    + saliency (spectral residual). Hasil sama seperti face_track; None jika tidak ada subjek jelas."""
    try:
        import cv2
        import numpy as np
        cap = cv2.VideoCapture(path)
        pts, n = [], max(2, int((end - start) / step) + 1)
        for k in range(n):
            t = min(start + k * step, end - 0.2)
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, f1 = cap.read()
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000 + 120)
            ok2, f2 = cap.read()
            if not ok:
                pts.append(None); continue
            g1 = cv2.cvtColor(cv2.resize(f1, (160, 90)), cv2.COLOR_BGR2GRAY).astype(np.float32)
            # saliency spectral residual
            sp = np.fft.fft2(g1)
            la = np.log(np.abs(sp) + 1e-6)
            sal = np.abs(np.fft.ifft2(np.exp(la - cv2.blur(la, (3, 3)) + 1j * np.angle(sp)))) ** 2
            sal = cv2.GaussianBlur(sal, (9, 9), 3)
            sal /= sal.max() + 1e-9
            if ok2:   # gerakan, dikurangi gerak kamera rata-rata
                g2 = cv2.cvtColor(cv2.resize(f2, (160, 90)), cv2.COLOR_BGR2GRAY).astype(np.float32)
                mo = cv2.GaussianBlur(np.abs(g2 - g1), (9, 9), 3)
                mo = np.clip(mo - np.median(mo) * 2, 0, None)
                if mo.max() > 4:
                    sal = sal + 1.5 * mo / mo.max()
            sal[:, :8] *= 0.3; sal[:, -8:] *= 0.3   # tepi frame jarang jadi subjek
            sal[sal < np.percentile(sal, 85)] = 0
            col = sal.sum(axis=0)
            if col.sum() <= 0:
                pts.append(None); continue
            # pusat massa di sekitar puncak (abaikan titik-titik ramai yang tersebar)
            c = int(np.argmax(np.convolve(col, np.ones(31), "same")))
            win = np.zeros_like(col); win[max(0, c - 30):c + 31] = 1
            cw = col * win
            pts.append(float((cw * np.arange(len(cw))).sum() / cw.sum()) / len(col) * width)
        cap.release()
    except Exception:
        return None
    return _smooth_track(pts, width, step)


def face_center_x(path, start, end, width):
    """Posisi X wajah (median) di rentang klip. Fallback: tengah."""
    tr = face_track(path, start, end, width, max((end - start) / 12, 0.5))
    if not tr:
        return width / 2
    xs = sorted(x for _, x in tr)
    return xs[len(xs) // 2]


def _track_expr(track, crop_w, w, ease=0.6):
    """Ekspresi FFmpeg x(t) untuk crop: diam di tiap posisi, bergeser halus `ease` detik saat wajah pindah."""
    clamp = lambda x: int(min(max(x - crop_w / 2, 0), w - crop_w))
    keys = [(0.0, clamp(track[0][1]))]
    for t, x in track[1:]:
        if clamp(x) != keys[-1][1]:
            keys.append((t, clamp(x)))
    if len(keys) == 1:
        return str(keys[0][1])
    terms = [str(keys[0][1])]
    for (t, x), (_, px) in zip(keys[1:], keys):
        a = max(t - ease, 0)   # mulai bergeser sebelum titik wajah pindah, smoothstep
        terms.append(f"({x - px})*clip((t-{a:.2f})/{ease},0,1)*clip((t-{a:.2f})/{ease},0,1)*(3-2*clip((t-{a:.2f})/{ease},0,1))")
    return "+".join(terms)


def frame_filter(src, start, end, w, h, mode):
    """Filter video 9:16 (1080x1920). mode: auto (ikuti wajah, tanpa wajah -> blur), track, center, blur."""
    crop_w = min(w, int(h * 9 / 16)) // 2 * 2
    if crop_w >= w - 2:   # sumber sudah vertikal: tidak ada yang perlu di-crop
        return "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"
    if mode in ("auto", "track"):
        tr = face_track(src, start, end, w) or subject_track(src, start, end, w)
        if tr:
            return f"crop={crop_w}:{h}:'{_track_expr(tr, crop_w, w)}':0,scale=1080:1920,setsar=1"
        mode = "blur" if mode == "auto" else "center"
    if mode == "blur" and w / h > 0.7:
        return ("split=2[bg][fg];[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
                "boxblur=30:2,eq=brightness=-0.08[bg];[fg]scale=1080:-2[fg];[bg][fg]overlay=0:(H-h)/2,setsar=1")
    return f"crop={crop_w}:{h}:{(w - crop_w) // 2}:0,scale=1080:1920,setsar=1"


def frame_mode(opts):
    """Pengaturan lama: face True/False -> auto/center."""
    return opts.get("frame") or ("auto" if opts.get("face", True) else "center")


def hook_lines(text, max_chars=16):
    """Pecah hook jadi baris pendek (maks 3 baris) untuk layar 9:16."""
    words, lines = text.split(), [""]
    for wd in words:
        if lines[-1] and len(lines[-1]) + 1 + len(wd) > max_chars:
            lines.append(wd)
        else:
            lines[-1] = (lines[-1] + " " + wd).strip()
    return lines[:3]


def _ass_time(t):
    t = max(t, 0)
    h, m = int(t // 3600), int(t % 3600 // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def credit_alpha(opacity):
    """Opacity 0-100 (%) -> alpha ASS (00 = pekat, FF = transparan)."""
    return f"{round((1 - max(0, min(100, opacity)) / 100) * 255):02X}"


def make_ass(words, start, end, out_path, style, credit=None, hook=None, hook_sec=3.0):
    """credit = {"text", "opacity" (0-100), "pos" ("atas"/"bawah")}: label sumber video,
    tampil sepanjang klip dengan transparansi. hook = teks besar di sepertiga atas selama `hook_sec` detik pertama."""
    cpos = (credit or {}).get("pos")
    color = {"kuning": "&H0000FFFF", "putih": "&H00FFFFFF", "hijau": "&H0000FF7F"}.get(style, "&H0000FFFF")
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial Black,84,&H00FFFFFF,{color},&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,6,2,2,60,60,520,1
Style: Hook,Arial Black,82,&H0000E5FF,&H0000E5FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,3,22,0,8,70,70,330,1
Style: Credit,Arial,40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,2,1,{8 if cpos == "atas" else 2},60,60,{150 if cpos == "atas" else 90},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ws = [w for w in words if w["start"] >= start - 0.05 and w["end"] <= end + 0.05]
    lines = []
    for i in range(0, len(ws), 3):
        chunk = ws[i:i + 3]
        cs, ce = chunk[0]["start"] - start, chunk[-1]["end"] - start
        # kata yang sedang diucapkan disorot (karaoke)
        for k, w in enumerate(chunk):
            ws_, we_ = w["start"] - start, (chunk[k + 1]["start"] - start if k + 1 < len(chunk) else ce)
            txt = " ".join(
                (f"{{\\c{color}\\fscx110\\fscy110}}{x['word'].upper()}{{\\r}}" if j == k else x["word"].upper())
                for j, x in enumerate(chunk))
            lines.append(f"Dialogue: 0,{_ass_time(ws_)},{_ass_time(we_)},Default,,0,0,0,,{txt}")
    if credit and credit.get("text"):
        txt = credit["text"].replace("{", "(").replace("}", ")").replace("\n", " ")
        lines.append(f"Dialogue: 1,{_ass_time(0)},{_ass_time(end - start + 1)},Credit,,0,0,0,,"
                     f"{{\\alpha&H{credit_alpha(credit.get('opacity', 60))}&}}{txt}")
    if hook and hook.strip():
        txt = "\\N".join(hook_lines(hook.replace("{", "(").replace("}", ")").upper()))
        hs = min(hook_sec, max(end - start - 0.5, 1))
        # muncul dengan efek pop, hilang memudar
        lines.append(f"Dialogue: 2,{_ass_time(0)},{_ass_time(hs)},Hook,,0,0,0,,"
                     f"{{\\fad(0,250)\\fscx60\\fscy60\\t(0,180,\\fscx108\\fscy108)\\t(180,300,\\fscx100\\fscy100)"
                     f"\\t(300,{int(hs * 1000)},\\fscx112\\fscy112)}}{txt}")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(lines))


# Suara narasi (edge-tts, gratis, butuh internet). Satu suara dipakai untuk semua klip.
# Kunci "id|<suara>" = suara multibahasa yang dipakai membaca naskah Indonesia
# (edge-tts hanya punya 2 suara Indonesia asli: Ardi & Gadis).
VOICES = {
    # Indonesia
    "id-ID-ArdiNeural": "🇮🇩 Ardi (pria, Indonesia asli)",
    "id-ID-GadisNeural": "🇮🇩 Gadis (wanita, Indonesia asli)",
    "id|en-US-BrianMultilingualNeural": "🇮🇩 Brian (pria, santai, multibahasa)",
    "id|en-US-AndrewMultilingualNeural": "🇮🇩 Andrew (pria, hangat, multibahasa)",
    "id|en-AU-WilliamMultilingualNeural": "🇮🇩 William (pria, ramah, multibahasa)",
    "id|en-US-AvaMultilingualNeural": "🇮🇩 Ava (wanita, ekspresif, multibahasa)",
    "id|en-US-EmmaMultilingualNeural": "🇮🇩 Emma (wanita, ceria, multibahasa)",
    "id|eleven:TX3LPaxmHKxFdv7VOQHJ": "✨ Liam ElevenLabs (pria, energik, paling natural)",
    # English
    "en-US-AndrewMultilingualNeural": "🇺🇸 Andrew (male, warm)",
    "en-US-BrianMultilingualNeural": "🇺🇸 Brian (male, casual)",
    "en-US-ChristopherNeural": "🇺🇸 Christopher (male, documentary/authority)",
    "en-US-GuyNeural": "🇺🇸 Guy (male, passionate)",
    "en-US-EricNeural": "🇺🇸 Eric (male, calm)",
    "en-US-RogerNeural": "🇺🇸 Roger (male, lively)",
    "en-US-AvaMultilingualNeural": "🇺🇸 Ava (female, expressive)",
    "en-US-EmmaMultilingualNeural": "🇺🇸 Emma (female, cheerful)",
    "en-US-JennyNeural": "🇺🇸 Jenny (female, friendly)",
    "en-US-AriaNeural": "🇺🇸 Aria (female, confident)",
    "en-GB-RyanNeural": "🇬🇧 Ryan (male, British)",
    "en-GB-ThomasNeural": "🇬🇧 Thomas (male, British)",
    "en-GB-SoniaNeural": "🇬🇧 Sonia (female, British)",
    "en-GB-LibbyNeural": "🇬🇧 Libby (female, British)",
    "en-AU-WilliamMultilingualNeural": "🇦🇺 William (male, Australian)",
    "en-AU-NatashaNeural": "🇦🇺 Natasha (female, Australian)",
}


def voice_lang(voice):
    """Bahasa naskah untuk suara: "id|..." -> id, "en-US-..." -> en."""
    return (voice or "").split("|")[0].split("-")[0] if "|" in (voice or "") else (voice or "").split("-")[0]
# Kecepatan bicara suara edge-tts (terukur sekitar 2,0 kata/detik untuk Ardi), untuk menentukan panjang naskah
WORDS_PER_SEC = 2.0


def _word_range(c):
    target = (c["end"] - c["start"]) * WORDS_PER_SEC
    return int(target * 0.9), int(target * 1.05)


def _merge_labels(imgs):
    """[(label, None), (label2, path), ...] -> [(label+label2, path), ...] (label klip digabung ke frame pertamanya)."""
    out, pend = [], ""
    for lbl, p in imgs:
        if p is None:
            pend += lbl + "\n"; continue
        out.append((pend + lbl, p)); pend = ""
    return out


def _ask_json_list(prompt, n, api_key, model, log):
    if api_key and api_key.startswith("gemini:"):
        import motion
        raw = motion._ask_gemini(prompt, api_key[7:], log)
    elif api_key == "__claude_code__":
        raw = _ask_claude_code(prompt, log)
    else:
        import anthropic
        log(f"Claude API ({model}) menulis narasi...")
        msg = anthropic.Anthropic(api_key=api_key).messages.create(
            model=model, max_tokens=6000, messages=[{"role": "user", "content": prompt}])
        raw = "".join(b.text for b in msg.content if b.type == "text")
    m = re.search(r"\[.*\]", raw, re.S)
    if not m:
        raise RuntimeError("Gagal membuat narasi: jawaban AI tidak berisi JSON")
    texts = json.loads(m.group(0))
    if len(texts) != n:
        raise RuntimeError(f"Gagal membuat narasi: {len(texts)} naskah untuk {n} klip")
    return [str(t).strip() for t in texts]


HEALTH_RULES = """ATURAN TOPIK KESEHATAN/MAKANAN/OBAT (wajib bila topiknya menyangkut tubuh, penyakit, makanan, atau obat;
kebijakan informasi medis YouTube + keselamatan penonton):
- DILARANG klaim menyembuhkan/menghancurkan/membunuh/menghilangkan penyakit, "dijamin", "pasti", "100%", "tanpa obat".
  Pakai bahasa yang jujur: "bisa membantu", "berpotensi", "dikaitkan dengan", "menurut penelitian".
- DILARANG menyarankan berhenti minum obat atau mengganti pengobatan dokter dengan makanan/herbal.
- Hanya fakta yang umum terdokumentasi (kandungan gizi, temuan penelitian yang dikenal); mitos/testimoni disebut sebagai mitos.
- Sisipkan satu batasan singkat yang natural (mis. "tapi jangan berlebihan, apalagi kalau punya penyakit ginjal").
- Hook tetap boleh dramatis/mengejutkan selama faktanya benar ("Hampir semua orang salah makan buah ini saat demam")."""


def viral_pattern(bahasa):
    """Pola naskah Shorts: hook 0-3 dtk, 3 poin cepat, penutup yang menyambung balik ke hook (loop)."""
    en = bahasa.lower().startswith("english")
    p1, p2, p3 = ("First", "Second", "And the worst one, third") if en else ("Pertama", "Kedua", "Dan yang paling fatal, ketiga")
    ex = ("\"Almost everyone gets this wrong...\" or \"Never do this if you don't want to [risk]...\"" if en else
          '"Hampir semua orang salah paham soal hal ini..." atau "Jangan pernah lakukan ini kalau gak mau [risiko]..."')
    return f"""POLA WAJIB (format Shorts yang menahan penonton sampai habis dan diputar ulang):
1. HOOK (detik 0-3, maks 8 kata): satu kalimat pemancing ala {ex}. Langsung ke inti, tanpa salam/perkenalan.
2. PENJELASAN: tepat 3 poin cepat dengan penanda kata persis "{p1}...", "{p2}...", "{p3}...".
   Tiap poin 1-2 kalimat pendek dan padat. Poin ketiga = yang paling mengejutkan/penting.
3. PENUTUP (5 detik terakhir): JANGAN pakai ajakan subscribe/like. Kalimat terakhir sengaja menggantung dan
   menyambung LANGSUNG ke kata pertama hook, jadi saat video diputar ulang terdengar seperti satu kalimat utuh
   (contoh: "...dan itulah alasan kenapa" lalu video kembali ke "Hampir semua orang salah paham soal hal ini").
   Kalimat terakhir diakhiri tanpa titik.
""" + HEALTH_RULES


def write_shorts_metadata(clips, lang=None, api_key="__claude_code__", model="claude-sonnet-5-5", log=print):
    """Metadata upload YouTube Shorts (opsi judul, deskripsi, tag, hashtag) untuk klip yang sudah jadi,
    berdasarkan naskah narasi (bila ada) atau isi asli klip. Satu permintaan untuk semua klip."""
    bahasa = {"id": "Bahasa Indonesia", "en": "English", "ms": "Bahasa Melayu"}.get(lang or "", "bahasa yang sama dengan isinya")
    items = [{"i": i, "judul_sekarang": c.get("title", ""), "hook": c.get("hook_text", ""),
              "isi": (c.get("narration") or c.get("text") or "")[:1500]} for i, c in enumerate(clips)]
    prompt = f"""Kamu ahli SEO YouTube Shorts. Untuk tiap klip di bawah, tulis metadata upload dalam {bahasa}.
- "titles": 3 opsi judul (maks 70 karakter): kata kunci yang dicari orang di depan, bikin penasaran tapi jujur, boleh 1 emoji.
- "description": 2-3 kalimat merangkum isi + 1 pertanyaan untuk memancing komentar.
- "tags": 10-15 tag pencarian (tanpa #). "hashtags": 3-5, wajib ada #shorts, sisanya spesifik topik.
{HEALTH_RULES}
- Topik kesehatan: description diakhiri "Konten edukasi, bukan pengganti saran medis." (sesuaikan bahasa).
Klip: {json.dumps(items, ensure_ascii=False)}
Balas HANYA JSON array sesuai urutan klip:
[{{"titles": ["..."], "description": "...", "tags": ["..."], "hashtags": ["#shorts"]}}]"""
    if api_key and api_key.startswith("gemini:"):
        import motion
        raw = motion._ask_gemini(prompt, api_key[7:], log)
    elif api_key == "__claude_code__":
        raw = _ask_claude_code(prompt, log)
    else:
        import anthropic
        msg = anthropic.Anthropic(api_key=api_key).messages.create(model=model, max_tokens=6000,
                                                                   messages=[{"role": "user", "content": prompt}])
        raw = "".join(b.text for b in msg.content if b.type == "text")
    m = re.search(r"\[.*\]", raw, re.S)
    if not m:
        raise RuntimeError("Jawaban AI tidak berisi JSON")
    data = json.loads(m.group(0))
    if len(data) != len(clips):
        raise RuntimeError(f"Metadata {len(data)} untuk {len(clips)} klip")
    return data


def shorts_metadata_text(c):
    """Isi metadata.txt untuk satu Shorts (format sama dengan video panjang)."""
    titles = [t for t in (c.get("titles") or []) if t] or [c.get("title") or c.get("hook_text") or ""]
    desc = (c.get("description") or c.get("hook") or "").strip()
    tags = [t for t in (c.get("tags") or []) if t]
    if not tags:   # cadangan dari hashtag
        tags = [h.lstrip("#") for h in (c.get("hashtags") or []) if h.lstrip("#").lower() != "shorts"]
    tags_line = ", ".join(tags)
    if len(tags_line) > 480:   # batas tag YouTube 500 karakter
        tags_line = tags_line[:480].rsplit(",", 1)[0]
    lines = ["TITLE OPTIONS:"] + [f"{i}. {t}" for i, t in enumerate(titles, 1)] + ["", "DESCRIPTION:", desc]
    if c.get("hashtags"):
        lines += ["", " ".join(c["hashtags"])]
    lines += ["", "TAGS:", tags_line]
    if c.get("flag"):
        lines += ["", "CLAIMS TO VERIFY (not for upload):", c["flag"]]
    return "\n".join(lines) + "\n"


def write_narrations(clips, lang=None, api_key="__claude_code__", model="claude-sonnet-5-5", log=print):
    """Tulis ulang isi tiap klip jadi naskah narasi baru (kata-kata berbeda dari video asli).
    Semua klip dalam satu permintaan agar gaya bahasanya seragam. Naskah yang terlalu pendek
    untuk durasi klip diminta diperpanjang satu kali."""
    items = []
    for i, c in enumerate(clips):
        lo, hi = _word_range(c)
        items.append({"i": i, "detik": round(c["end"] - c["start"]), "min_kata": lo, "maks_kata": hi,
                      "isi_asli": c.get("text", "")[:2500]})
    bahasa = {"id": "Bahasa Indonesia", "en": "English", "ms": "Bahasa Melayu"}.get(lang or "", "bahasa yang sama dengan isi asli")
    prompt = f"""Kamu penulis naskah voice-over untuk YouTube Shorts / TikTok / Reels.
Untuk setiap klip di bawah, tulis NARASI BARU dalam {bahasa} yang menyampaikan inti isi asli,
tetapi dengan kata-kata, susunan kalimat, dan sudut pandang yang JELAS BERBEDA dari ucapan asli (jangan mengutip).
{viral_pattern(bahasa)}
Aturan:
- Gaya bicara santai, kalimat pendek, enak dibacakan.
- PANJANG WAJIB antara "min_kata" dan "maks_kata" kata, karena narasi harus mengisi seluruh durasi klip.
  Kalau isi asli sedikit (banyak jeda/musik), tambahkan konteks, penjelasan, contoh, atau fakta pendukung yang relevan
  sampai mencapai min_kata. Akhiri dengan penutup loop sesuai pola di atas.
- Tanpa emoji, tanpa hashtag, tanpa petunjuk adegan, tanpa tanda kurung.
Klip:
{json.dumps(items, ensure_ascii=False)}

Balas HANYA JSON array string sesuai urutan klip, contoh: ["narasi klip 0", "narasi klip 1"]"""
    texts = _ask_json_list(prompt, len(clips), api_key, model, log)

    short = [i for i, t in enumerate(texts) if len(t.split()) < items[i]["min_kata"] * 0.9]
    if short:
        log(f"Memperpanjang {len(short)} narasi yang terlalu pendek...")
        fix = [{"i": i, "detik": items[i]["detik"], "min_kata": items[i]["min_kata"], "maks_kata": items[i]["maks_kata"],
                "jumlah_kata_sekarang": len(texts[i].split()), "narasi": texts[i], "isi_asli": items[i]["isi_asli"]}
               for i in short]
        prompt2 = f"""Narasi voice-over berikut TERLALU PENDEK untuk durasi klipnya.
Perpanjang masing-masing sampai antara min_kata dan maks_kata kata dalam {bahasa}: pertahankan hook, 3 poin, penutup loop, dan gayanya,
tambahkan penjelasan, contoh, atau konteks yang relevan dengan isi asli. Tetap jangan mengutip ucapan asli.
Tanpa emoji, hashtag, atau tanda kurung.
{json.dumps(fix, ensure_ascii=False)}

Balas HANYA JSON array string berisi narasi yang sudah diperpanjang, urut sesuai daftar di atas."""
        try:
            for i, t in zip(short, _ask_json_list(prompt2, len(short), api_key, model, log)):
                if len(t.split()) > len(texts[i].split()):
                    texts[i] = t
        except Exception as e:
            log(f"Gagal memperpanjang narasi ({e}), pakai naskah awal")
    return texts


def extract_frames(src, start, end, out_dir, prefix, every=2.0, max_n=12):
    """Ambil frame kecil (lebar 512) tiap `every` detik dari [start, end]. Kembalikan [(detik relatif, path)]."""
    dur = end - start
    n = max(3, min(max_n, int(dur / every) + 1))
    out = []
    for k in range(n):
        t = dur * (k + 0.5) / n
        p = os.path.join(out_dir, f"{prefix}_{k:02d}.jpg")
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start + t:.2f}", "-i", os.path.abspath(src),
             "-frames:v", "1", "-vf", "scale=512:-2", "-q:v", "4", p])
        if os.path.exists(p):
            out.append((round(t, 1), p))
    return out


def write_visual_narrations(clips, frames, lang=None, api_key="__claude_code__", model="claude-sonnet-5-5", log=print):
    """Narasi dari ISI GAMBAR video (tanpa ucapan): Claude melihat frame tiap klip, mengenali apa yang terjadi
    (hewan, benda, tindakan) lalu menulis narasi berisi penjelasan + fakta relevan sesuai urutan kejadian.
    frames: list per klip berisi [(detik, path)]."""
    bahasa = {"id": "Bahasa Indonesia", "en": "English", "ms": "Bahasa Melayu"}.get(lang or "", "Bahasa Indonesia")
    items = []
    for i, (c, fr) in enumerate(zip(clips, frames)):
        lo, hi = _word_range(c)
        items.append({"i": i, "detik": round(c["end"] - c["start"]), "min_kata": lo, "maks_kata": hi,
                      "frame": [{"detik": t, "file": os.path.basename(p)} for t, p in fr]})
    rules = f"""Kamu narator video pendek ala dokumenter / fakta menarik (YouTube Shorts, Reels, TikTok).
Video ini TIDAK punya narasi; kamu harus menulis narasi berdasarkan apa yang TERLIHAT di gambar.
Untuk setiap klip:
1. Lihat semua frame-nya (urut waktu) dan kenali apa yang ada dan apa yang terjadi: hewan/benda/tempat, tindakan, perubahan.
2. Tulis narasi {bahasa} yang MENGIKUTI URUTAN KEJADIAN di video, diselingi FAKTA MENARIK yang akurat dan relevan
   (mis. jenis burung itu, kenapa ia melakukan tindakan itu, kemampuan uniknya, angka/statistik yang umum diketahui).
   Kalau tidak yakin spesiesnya, sebut secara umum (mis. "burung pemangsa ini") daripada menebak nama yang salah.
3. Ikuti pola di bawah; 3 poin boleh mengikuti urutan kejadian di video. Gaya santai, kalimat pendek.
{viral_pattern(bahasa)}
4. Panjang WAJIB antara min_kata dan maks_kata. Tanpa emoji, hashtag, petunjuk adegan, atau tanda kurung.
Balas HANYA JSON array string sesuai urutan klip: ["narasi klip 0", ...]"""
    if api_key and api_key.startswith("gemini:"):
        import motion
        imgs = []
        for it, fr in zip(items, frames):
            imgs.append((json.dumps({k: v for k, v in it.items() if k != "frame"}, ensure_ascii=False), None))
            imgs += [(f"klip {it['i']} detik {t}:", p) for t, p in fr]
        log("Gemini melihat video & menulis narasi...")
        raw = motion._ask_gemini(rules + "\n\nFrame tiap klip (label klip lalu gambarnya):", api_key[7:], log,
                                 images=_merge_labels(imgs))
    elif api_key and api_key != "__claude_code__":
        import anthropic, base64
        content = [{"type": "text", "text": rules}]
        for it, fr in zip(items, frames):
            content.append({"type": "text", "text": json.dumps({k: v for k, v in it.items() if k != "frame"}, ensure_ascii=False)})
            for t, p in fr:
                content.append({"type": "text", "text": f"klip {it['i']} detik {t}:"})
                with open(p, "rb") as f:
                    content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                                "data": base64.b64encode(f.read()).decode()}})
        log(f"Claude API ({model}) melihat video & menulis narasi...")
        msg = anthropic.Anthropic(api_key=api_key).messages.create(
            model=model, max_tokens=6000, messages=[{"role": "user", "content": content}])
        raw = "".join(b.text for b in msg.content if b.type == "text")
    else:
        # Claude Code membuka file frame sendiri dengan tool Read (folder kerja = folder frame)
        folder = os.path.dirname(frames[0][0][1])
        raw = _ask_claude_code(rules + "\n\nBuka (Read) SEMUA file gambar frame berikut dari folder kerja sebelum menulis:\n"
                               + json.dumps(items, ensure_ascii=False), log, cwd=folder, read_files=True)
    m = re.search(r"\[.*\]", raw, re.S)
    if not m:
        raise RuntimeError("Gagal membuat narasi visual: jawaban AI tidak berisi JSON")
    texts = [str(t).strip() for t in json.loads(m.group(0))]
    if len(texts) != len(clips):
        raise RuntimeError(f"Gagal membuat narasi visual: {len(texts)} naskah untuk {len(clips)} klip")
    return texts


def visual_segments(duration, n_clips, min_len, max_len, exclude=()):
    """Tanpa transkrip: bagi video jadi potongan berurutan sepanjang max_len (sisa < min_len digabung)."""
    out, t = [], 0.0
    while t < duration - 0.5 and len(out) < n_clips + len(exclude):
        end = min(t + max_len, duration)
        if duration - end < min_len:   # sisa terlalu pendek: ikutkan ke potongan ini
            end = duration
        out.append({"start": round(t, 2), "end": round(end, 2), "text": "", "score": 0, "viral": 0})
        t = end
    from_ex = [c for c in out if all(c["end"] <= p["start"] or c["start"] >= p["end"] for p in exclude)]
    return from_ex[:n_clips]


def is_eleven(voice):
    return "eleven:" in (voice or "")


_ELEVEN_DOWN = [0.0]   # setelah gagal (kuota/key), lewati ElevenLabs 30 menit agar sisa video tetap satu suara


def eleven_key():
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        try:
            key = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"),
                                 encoding="utf-8")).get("elevenlabs_key")
        except Exception:
            key = None
    return key


def eleven_preflight(voice, text, log=print):
    """Sebelum membuat suara satu video: kalau sisa kuota ElevenLabs tidak cukup untuk seluruh teks,
    langsung pakai suara lama agar satu video tidak bercampur dua suara. Cek gagal = tetap ElevenLabs."""
    if not is_eleven(voice):
        return voice
    import urllib.request
    key = eleven_key()
    if not key:
        return eleven_fallback(voice, "API key belum diset")
    import time, urllib.error
    if time.time() < _ELEVEN_DOWN[0]:
        return eleven_fallback(voice, "baru saja gagal, coba lagi nanti")
    try:
        req = urllib.request.Request("https://api.elevenlabs.io/v1/user/subscription", headers={"xi-api-key": key})
        with urllib.request.urlopen(req, timeout=20) as r:
            sub = json.loads(r.read())
        left = sub["character_limit"] - sub["character_count"]
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")
        if "missing_permissions" in body:   # key tanpa izin user_read: tidak bisa cek kuota, coba langsung
            return voice
        _ELEVEN_DOWN[0] = time.time() + 1800
        return eleven_fallback(voice, f"{e.code} {body[:120]}")
    except Exception:
        return voice
    if left < len(text):
        fb = eleven_fallback(voice, f"sisa kuota {left} karakter, butuh {len(text)}")
        log(f"Kuota ElevenLabs tidak cukup ({left} karakter tersisa), pakai suara {fb}")
        return fb
    return voice


def eleven_fallback(voice, err):
    """Kuota habis / key salah / gangguan: pakai suara edge-tts gratis yang sudah ada (Ardi untuk id)."""
    lang = voice.split("|")[0] if "|" in voice else "id"
    fb = "id-ID-ArdiNeural" if lang == "id" else "en-US-BrianMultilingualNeural"
    print(f"[ElevenLabs gagal: {str(err)[:160]}] -> beralih ke {fb}", flush=True)
    return fb


def eleven_tts(text, voice, out_path, speed=1.0):
    """ElevenLabs TTS (model multilingual, bahasa dari prefix "id|"). Key: env ELEVENLABS_API_KEY
    atau "elevenlabs_key" di config.json. Mengembalikan daftar kata {start, end, word} dari timing karakter."""
    import base64, urllib.request, urllib.error
    key = eleven_key()
    if not key:
        raise RuntimeError("API key ElevenLabs belum diset (env ELEVENLABS_API_KEY)")
    vid = voice.split("eleven:")[-1]
    lang = voice.split("|")[0] if "|" in voice else "id"
    body = {"text": text, "model_id": "eleven_multilingual_v2", "language_code": lang,
            "voice_settings": {"stability": 0.4, "similarity_boost": 0.75, "style": 0.25,
                               "use_speaker_boost": True, "speed": round(speed, 2)}}
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{vid}/with-timestamps?output_format=mp3_44100_128",
        data=json.dumps(body).encode(), headers={"xi-api-key": key, "Content-Type": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                res = json.loads(r.read())
            break
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="ignore")[:300]
            if e.code in (429, 500, 502, 503) and attempt < 2:
                import time; time.sleep(5 * (attempt + 1)); continue
            raise RuntimeError(f"ElevenLabs error {e.code}: {msg}")
    with open(out_path, "wb") as f:
        f.write(base64.b64decode(res["audio_base64"]))
    al = res.get("alignment") or {}
    words, cur, s, e = [], "", None, None
    for ch, cs, ce in zip(al.get("characters", []), al.get("character_start_times_seconds", []),
                          al.get("character_end_times_seconds", [])):
        if ch.isspace():
            if cur:
                words.append({"start": s, "end": e, "word": cur}); cur = ""
            continue
        if not cur:
            s = cs
        cur += ch; e = ce
    if cur:
        words.append({"start": s, "end": e, "word": cur})
    return words


def tts(text, voice, out_path, max_dur=None):
    """Bacakan teks dengan edge-tts. Kalau lebih panjang dari max_dur, dipercepat (maks +40%);
    kalau jauh lebih pendek, diperlambat sedikit (maks -12%) agar tidak banyak diam di akhir.
    Mengembalikan (durasi, daftar kata {start, end, word}) untuk subtitle."""
    if is_eleven(voice) and __import__("time").time() < _ELEVEN_DOWN[0]:
        voice = eleven_fallback(voice, "dilewati sementara setelah gagal")
    if is_eleven(voice):
        try:
            words = eleven_tts(text, voice, out_path)
            dur = get_duration(out_path)
            if max_dur and (dur > max_dur or dur < max_dur * 0.9):
                speed = max(0.88, min(1.2, dur / max_dur))     # batas kecepatan ElevenLabs 0.7-1.2
                if abs(speed - 1) > 0.02:
                    words = eleven_tts(text, voice, out_path, speed)
                    dur = get_duration(out_path)
            return dur, words
        except Exception as e:
            _ELEVEN_DOWN[0] = __import__("time").time() + 1800
            voice = eleven_fallback(voice, e)
    import asyncio, edge_tts

    async def speak(rate):
        words = []
        c = edge_tts.Communicate(text, voice.split("|")[-1], rate=rate, boundary="WordBoundary")   # "id|suara" -> suara
        with open(out_path, "wb") as f:
            async for ch in c.stream():
                if ch["type"] == "audio":
                    f.write(ch["data"])
                elif ch["type"] == "WordBoundary":
                    s = ch["offset"] / 1e7
                    words.append({"start": s, "end": s + ch["duration"] / 1e7, "word": ch["text"]})
        return words

    words = asyncio.run(speak("+0%"))
    dur = get_duration(out_path)
    if max_dur and dur > max_dur:
        pct = min(int((dur / max_dur - 1) * 100) + 3, 40)
        words = asyncio.run(speak(f"+{pct}%"))
        dur = get_duration(out_path)
    elif max_dur and dur < max_dur * 0.9:
        pct = min(int((1 - dur / max_dur) * 100), 12)
        words = asyncio.run(speak(f"-{pct}%"))
        dur = get_duration(out_path)
    return dur, words


FILLERS = {"eh", "ehm", "em", "emm", "eee", "ee", "uh", "um", "umm", "uhm", "hmm", "mm", "erm"}


def _plain(word):
    return re.sub(r"[^\w]", "", word.lower())


def jumpcut(src, start, end, words, out_path, max_gap=0.45, pad=0.12):
    """Buang jeda > max_gap dan kata pengisi (eh, ehm, um...). Hasil: (kata dengan waktu baru, durasi) atau None.
    Video dipotong jadi file sementara agar langkah render berikutnya tetap sama."""
    ws = [w for w in words if w["start"] >= start - 0.05 and w["end"] <= end + 0.05 and _plain(w["word"]) not in FILLERS]
    if len(ws) < 3:
        return None
    segs, a, prev = [], max(start, ws[0]["start"] - pad), ws[0]
    for w in ws[1:]:
        if w["start"] - prev["end"] > max_gap:
            segs.append((a, prev["end"] + pad)); a = w["start"] - pad
        prev = w
    segs.append((a, min(end, prev["end"] + 0.35)))
    total = sum(y - x for x, y in segs)
    if end - start - total < 0.6:   # hampir tidak ada yang dibuang
        return None
    n, lines = len(segs), []
    for i, (x, y) in enumerate(segs):
        lines.append(f"[0:v]trim={x - start:.3f}:{y - start:.3f},setpts=PTS-STARTPTS[v{i}];"
                     f"[0:a]atrim={x - start:.3f}:{y - start:.3f},asetpts=PTS-STARTPTS[a{i}];")
    lines.append("".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v][a]")
    script = out_path + ".filter.txt"
    with open(script, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    run(["ffmpeg", "-y", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", os.path.abspath(src),
         "-/filter_complex", script, "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "14", "-c:a", "pcm_s16le", out_path])
    os.remove(script)
    out, off = [], 0.0
    for x, y in segs:
        out += [dict(w, start=max(w["start"] - x, 0) + off, end=min(w["end"], y) - x + off)
                for w in ws if x <= w["start"] < y]
        off += y - x
    return out, total


def punch_expr(words, dur, hook_sec=3.0, zoom=0.12):
    """Zoom punch-in bergantian di awal kalimat (minimal 2,5 detik sekali), mulai setelah hook.
    Tanpa tanda baca: tiap ~5 detik. Hasil: ekspresi faktor skala untuk filter scale."""
    marks = [w["start"] for i, w in enumerate(words) if i and words[i - 1]["word"].rstrip()[-1:] in ".?!"]
    if len(marks) < 2:
        marks = [hook_sec + 5 * k for k in range(int(dur // 5))]
    pts = []
    for t in marks:
        if t > hook_sec and t < dur - 1 and (not pts or t - pts[-1] >= 2.5):
            pts.append(t)
    ons = [(pts[i], pts[i + 1] if i + 1 < len(pts) else dur + 1) for i in range(0, len(pts), 2)]
    if not ons:
        return None
    return f"1+{zoom}*(" + "+".join(f"between(t,{x:.2f},{y:.2f})" for x, y in ons) + ")"


SFX = {   # dibuat sekali dari noise/sinus (tanpa file dari internet, aman hak cipta)
    "whoosh": ["anoisesrc=d=0.7:c=pink:a=0.6", "highpass=f=400,lowpass=f=5000,afade=t=in:d=0.3:curve=exp,afade=t=out:st=0.3:d=0.4,volume=0.8"],
    "pop": ["sine=f=900:d=0.12", "vibrato=f=12:d=0.6,afade=t=out:st=0.02:d=0.1,volume=0.7"],
    "boom": ["sine=f=55:d=1.4", "afade=t=out:st=0.05:d=1.35:curve=exp,bass=g=6,volume=1.6"],
    # riser: desis naik 1,2 dtk yang memuncak tepat di titik grafis muncul; impact: hantaman pendek + dentum rendah
    "riser": ["anoisesrc=d=1.2:c=white:a=0.5", "highpass=f=700,lowpass=f=9000,afade=t=in:d=1.15:curve=qsin,afade=t=out:st=1.15:d=0.05,volume=0.55"],
    "impact": ["anoisesrc=d=0.9:c=brown:a=0.9", "lowpass=f=180,afade=t=out:st=0.02:d=0.85:curve=exp,bass=g=8,volume=2.2"],
}
POINT_WORDS = {"pertama": "pop", "kedua": "pop", "ketiga": "boom", "first": "pop", "second": "pop", "third": "boom",
               "firstly": "pop", "secondly": "pop", "thirdly": "boom"}


def sfx(kind):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", f"sfx_{kind}.wav")
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        src, af = SFX[kind]
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", src, "-af", af, "-ar", "44100", path])
    return path


def sfx_events(words, hook=True):
    """[(detik, jenis)]: whoosh saat hook muncul, pop/boom saat penanda poin (Pertama/Kedua/ketiga) diucapkan."""
    ev, seen = ([(0.0, "whoosh")] if hook else []), set()
    for w in words:
        k = _plain(w["word"])
        if k in POINT_WORDS and k not in seen and w["start"] > 1.5:
            seen.add(k); ev.append((max(w["start"] - 0.08, 0), POINT_WORDS[k]))
    return ev


def mix_sfx_graph(main, events, first_input):
    """Potongan filtergraph: [main] + sfx (input mulai first_input) -> [a]."""
    if not events:
        return [f"[{main}]anull[a]"]
    g = [f"[{first_input + i}:a]adelay={int(t * 1000)}|{int(t * 1000)},volume=0.55[x{i}]" for i, (t, _) in enumerate(events)]
    g.append(f"[{main}]" + "".join(f"[x{i}]" for i in range(len(events)))
             + f"amix=inputs={len(events) + 1}:duration=first:normalize=0[a]")
    return g


def render_clip(src, clip, words, out_dir, idx, opts, log=print):
    """voice_mode: "asli" = suara video, "ai" = narasi dibacakan suara AI,
    "teks" = video tanpa suara (narasi diisi sendiri, naskah ada di clip["narration"]),
    "kosong" = video saja tanpa suara & subtitle; naskah AI hanya sebagai hint.
    jumpcut = buang jeda & kata pengisi (suara asli), punch = zoom punch-in, sfx = whoosh saat hook."""
    w, h = get_size(src)
    start, end = clip["start"], clip["end"]
    base = f"clip_{idx:02d}"
    ass_name = base + ".ass"
    out_name = base + ".mp4"
    mode = opts.get("voice_mode", "asli")
    silent = mode in ("teks", "kosong")   # video tanpa suara asli
    tmp = None
    if mode == "asli" and opts.get("jumpcut", True):
        tmp = os.path.join(out_dir, base + "_cut.mkv")
        jc = jumpcut(src, start, end, words, tmp)
        if jc:
            log(f"Klip {idx}: jeda & kata pengisi dibuang ({end - start:.0f}s -> {jc[1]:.0f}s)")
            words, src, start, end = jc[0], tmp, 0.0, jc[1]
    narr = None
    if mode == "ai":
        narr = base + "_narasi.mp3"
        _, tw = tts(clip["narration"], opts.get("voice") or "id-ID-ArdiNeural",
                    os.path.join(out_dir, narr), max_dur=end - start)
        words = [dict(x, start=x["start"] + start, end=x["end"] + start) for x in tw]   # subtitle ikut narasi
    filters = []
    if opts.get("vertical", True):
        filters.append(frame_filter(src, start, end, w, h, frame_mode(opts)))
        rel = [dict(x, start=x["start"] - start) for x in words if start <= x["start"] <= end]
        z = punch_expr(rel, end - start) if opts.get("punch", True) else None
        if z:
            filters.append(f"scale=w='trunc(1080*({z})/2)*2':h=-2:eval=frame,crop=1080:1920,setsar=1")
    # tanpa suara: tanpa subtitle juga, karena waktu bicara baru diketahui setelah direkam sendiri
    show_subs = opts.get("subtitles", True) and not silent
    # label pemilik video; "{channel}" diganti nama channel asli
    ctext = opts.get("credit_text") or ""
    if "{channel}" in ctext and not opts.get("channel"):   # upload file: nama pemilik tidak diketahui
        ctext = ""
    credit = {"text": ctext.replace("{channel}", opts.get("channel") or "").strip(),
              "opacity": int(opts.get("credit_opacity") or 60), "pos": opts.get("credit_pos", "bawah")}
    # cadangan judul hanya untuk klip pilihan AI; judul bawaan platform ("Video by ...") bukan hook
    hook = (clip.get("hook_text") or (clip.get("title") if clip.get("hook") else "") or "") if opts.get("hook", True) else ""
    if show_subs or credit["text"] or hook:
        make_ass(words if show_subs else [], start, end, os.path.join(out_dir, ass_name),
                 opts.get("sub_color", "kuning"), credit, hook)
        filters.append(f"ass={ass_name}")
    cmd = ["ffmpeg", "-y", "-ss", f"{start:.2f}", "-to", f"{end:.2f}", "-i", os.path.abspath(src)]
    graph = ["[0:v]" + (",".join(filters) or "null") + "[v]"]
    if not silent:
        main = "0:a"
        if narr:
            cmd += ["-i", narr]; main = "1:a"
        graph.append(f"[{main}]apad,atrim=0:{end - start:.2f}[m]")
        rel = [dict(x, start=x["start"] - start) for x in words if start <= x["start"] <= end]
        ev = sfx_events(rel, bool(hook)) if opts.get("sfx", True) else []
        for _, kind in ev:
            cmd += ["-i", sfx(kind)]
        graph += mix_sfx_graph("m", ev, 2 if narr else 1)
    script = os.path.join(out_dir, base + ".filter.txt")
    with open(script, "w", encoding="utf-8") as f:
        f.write(";\n".join(graph))
    cmd += ["-/filter_complex", os.path.basename(script), "-map", "[v]"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
    cmd += ["-an"] if silent else ["-map", "[a]", "-c:a", "aac", "-b:a", "160k"]
    cmd += ["-movflags", "+faststart", out_name]
    if silent and clip.get("narration"):
        with open(os.path.join(out_dir, base + "_narasi.txt"), "w", encoding="utf-8") as f:
            f.write(clip.get("narration", ""))
    log(f"Render klip {idx}...")
    try:
        run(cmd, cwd=out_dir)   # cwd = folder output agar path subtitle aman di Windows
    finally:
        for f in (script, tmp):
            if f and os.path.exists(f):
                os.remove(f)
    return out_name


def _fmt(t):
    return f"{int(t // 60)}:{t % 60:04.1f}"


def find_highlights_claude(transcript, n_clips, min_len, max_len, api_key,
                           model="claude-sonnet-5-5", log=print):
    """Minta Claude memilih momen paling viral dari transkrip bertimestamp."""
    segs = transcript["segments"]
    lines = [f"[{i}] ({s['start']:.1f}-{s['end']:.1f}) {s['text']}" for i, s in enumerate(segs)]
    text = "\n".join(lines)
    if len(text) > 400_000:
        text = text[:400_000]
    prompt = f"""Kamu adalah editor video short-form (TikTok/Reels/Shorts) kelas dunia.
Berikut transkrip video, tiap baris: [index segmen] (detik mulai-detik selesai) teks.

<transkrip>
{text}
</transkrip>

Pilih {n_clips} momen PALING berpotensi viral. Aturan:
- Durasi tiap klip {min_len}-{max_len} detik.
- Klip harus berdiri sendiri: dibuka dengan hook kuat di 3 detik pertama, berakhir di akhir kalimat/poin yang tuntas.
- Tidak boleh tumpang tindih.
- Gunakan index segmen untuk batas awal & akhir.
- Judul, hook, hook_text, dan hashtag pakai bahasa yang sama dengan video.
- hook_text = teks besar yang tampil di layar 3 detik pertama: 3-7 kata, bikin penasaran, bukan spoiler, tanpa emoji/hashtag.
- Topik kesehatan/makanan/obat: judul dan hook_text DILARANG klaim menyembuhkan/menghancurkan/dijamin; pakai "bantu", "cegah", "fakta".
- Metadata YouTube Shorts per klip: "titles" = 3 opsi judul (maks 70 karakter, kata kunci yang dicari orang di depan),
  "description" = 2-3 kalimat yang merangkum isi + 1 pertanyaan untuk memancing komentar (topik kesehatan: akhiri dengan
  "Konten edukasi, bukan pengganti saran medis."), "tags" = 10-15 tag pencarian, "hashtags" = 3-5 (#shorts + spesifik).

Balas HANYA JSON valid tanpa teks lain, format:
{{"clips":[{{"start_seg":0,"end_seg":5,"viral":87,"title":"judul pendek menarik","hook":"alasan klip ini menarik","hook_text":"teks layar 3-7 kata",
"titles":["opsi 1","opsi 2","opsi 3"],"description":"deskripsi","tags":["tag"],"hashtags":["#a","#b"]}}]}}"""
    if api_key and api_key.startswith("gemini:"):
        import motion
        raw = motion._ask_gemini(prompt, api_key[7:], log)
    elif api_key == "__claude_code__":
        raw = _ask_claude_code(prompt, log)
    else:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        log(f"Claude API ({model}) sedang menganalisis transkrip...")
        msg = client.messages.create(model=model, max_tokens=4000,
                                     messages=[{"role": "user", "content": prompt}])
        raw = "".join(b.text for b in msg.content if b.type == "text")
    m = re.search(r"\{.*\}", raw, re.S)
    data = json.loads(m.group(0))
    out = []
    for c in data.get("clips", []):
        a = max(0, min(int(c["start_seg"]), len(segs) - 1))
        b = max(a, min(int(c["end_seg"]), len(segs) - 1))
        # potong jika terlalu panjang
        while b > a and segs[b]["end"] - segs[a]["start"] > max_len + 5:
            b -= 1
        clip = {"start": segs[a]["start"], "end": segs[b]["end"],
                "text": " ".join(s["text"] for s in segs[a:b + 1]),
                "viral": int(c.get("viral", 75)), "score": int(c.get("viral", 75)),
                "title": c.get("title", ""), "hook": c.get("hook", ""), "hook_text": c.get("hook_text", ""),
                "hashtags": c.get("hashtags", []), "titles": c.get("titles") or [], "description": c.get("description", ""),
                "tags": c.get("tags") or []}
        if all(clip["end"] <= p["start"] or clip["start"] >= p["end"] for p in out):
            out.append(clip)
    return sorted(out, key=lambda c: c["start"])


def _repair_json(s):
    """Perbaiki kesalahan JSON khas jawaban AI: tanda kutip di dalam teks yang tidak di-escape
    (mis. "hook": "Kata "rahasia" ini..."), baris baru mentah di dalam string, koma yang hilang antar item
    (}\\n{ atau "a"\\n"b"), dan koma berlebih sebelum } / ]."""
    out, i, n, ins = [], 0, len(s), False
    while i < n:
        c = s[i]
        if ins:
            if c == "\\":
                out.append(s[i:i + 2]); i += 2; continue
            if c == '"':
                j = i + 1
                while j < n and s[j] in " \t\r\n": j += 1
                if j >= n or s[j] in ",:}]":
                    ins = False; out.append(c)
                elif s[j] == '"' and "\n" in s[i:j]:   # string berikutnya di baris baru: koma hilang
                    ins = False; out.append('",')
                else:
                    out.append('\\"')   # kutip di tengah teks
            elif c == "\n":
                out.append("\\n")
            else:
                out.append(c)
        else:
            if c == '"':
                ins = True
            out.append(c)
        i += 1
    r = "".join(out)
    r = re.sub(r",\s*([}\]])", r"\1", r)
    r = re.sub(r"([}\]])(\s*)([{\[])", r"\1,\2\3", r)
    return r


def loose_json(text, kind="{"):
    """Ambil objek ({) atau array ([) JSON dari jawaban AI, dengan perbaikan otomatis bila formatnya rusak."""
    text = re.sub(r"```(?:json)?", "", text or "")
    m = re.search(r"\{.*\}" if kind == "{" else r"\[.*\]", text, re.S)
    if not m:
        raise RuntimeError("Jawaban AI tidak berisi JSON: " + text[:300])
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        try:
            return json.loads(_repair_json(m.group(0)))
        except json.JSONDecodeError:
            raise RuntimeError(f"Format JSON dari AI rusak ({e}). Coba klik lagi.")


USAGE_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "usage.jsonl")


def record_usage(log, src, inp, out, cache_read=0, cache_write=0, cost=None, secs=None):
    """Catat pemakaian token per panggilan Claude: tampil di log job + disimpan ke logs/usage.jsonl."""
    import time
    total = inp + out + cache_read + cache_write
    msg = f"🔢 Token: {total:,} (input {inp + cache_read + cache_write:,}, output {out:,})"
    if cost is not None:
        msg += f" · setara ${cost:.2f}" + (" (langganan: tidak ditagih)" if src == "code" else "")
    if secs: msg += f" · {secs / 60:.1f} menit"
    try:
        log(msg)
    except Exception:
        pass
    try:
        os.makedirs(os.path.dirname(USAGE_LOG), exist_ok=True)
        with open(USAGE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "src": src, "input": inp, "output": out, "cache_read": cache_read,
                                "cache_write": cache_write, "cost": cost, "secs": secs}) + "\n")
    except OSError:
        pass


def api_usage(msg, log=print):
    """Catat token dari respons Anthropic API (messages.create)."""
    u = getattr(msg, "usage", None)
    if u:
        record_usage(log, "api", u.input_tokens or 0, u.output_tokens or 0, getattr(u, "cache_read_input_tokens", 0) or 0,
                     getattr(u, "cache_creation_input_tokens", 0) or 0)
    return msg


def _hook_anthropic():
    """Semua panggilan Claude API (messages.create) di aplikasi otomatis tercatat tokennya."""
    try:
        from anthropic.resources.messages import Messages
    except Exception:
        return
    if getattr(Messages.create, "_usage_hooked", False):
        return
    orig = Messages.create
    def create(self, *a, **k):
        msg = orig(self, *a, **k)
        if not k.get("stream"):
            try: api_usage(msg)
            except Exception: pass
        return msg
    create._usage_hooked = True
    Messages.create = create


_hook_anthropic()


def usage_summary(days=30):
    import time
    rows = []
    try:
        with open(USAGE_LOG, encoding="utf-8") as f:
            rows = [json.loads(l) for l in f if l.strip()]
    except OSError:
        pass
    now = time.time(); out = {}
    for k, sec in (("hari_ini", 86400), ("7_hari", 7 * 86400), (f"{days}_hari", days * 86400)):
        r = [x for x in rows if now - x["t"] < sec]
        out[k] = {"panggilan": len(r), "token": sum(x["input"] + x["output"] + x["cache_read"] + x["cache_write"] for x in r),
                  "output": sum(x["output"] for x in r), "setara_usd": round(sum(x["cost"] or 0 for x in r), 2)}
    return out


def _ask_claude_code(prompt, log=print, cwd=None, read_files=False, timeout=900, progress=None):
    """Pakai Claude Code CLI (login langganan Pro/Max), tanpa API key.
    read_files=True: Claude boleh memakai tool Read (mis. melihat gambar frame) di folder cwd.
    progress(detik_berjalan) dipanggil tiap 5 detik selama menunggu, agar UI tidak tampak macet."""
    import shutil
    exe = shutil.which("claude") or shutil.which("claude.cmd")
    if not exe:
        raise RuntimeError("Claude Code belum terpasang. Jalankan: npm install -g @anthropic-ai/claude-code lalu 'claude' untuk login.")
    log("Claude Code (langganan Pro) sedang bekerja...")
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)   # pastikan pakai login langganan, bukan API berbayar
    cmd = [exe, "-p", "--output-format", "json"]   # json: berisi teks jawaban + pemakaian token
    if read_files:
        cmd += ["--allowedTools", "Read"]
    import time, threading
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd,
                         text=True, encoding="utf-8", errors="ignore", env=env)
    result = {}
    t = threading.Thread(target=lambda: result.update(zip(("out", "err"), p.communicate(prompt))), daemon=True)
    t.start()
    t0 = time.time()
    while t.is_alive():
        t.join(5)
        elapsed = int(time.time() - t0)
        if elapsed > timeout:
            p.kill()
            raise RuntimeError(f"Claude Code tidak selesai dalam {timeout // 60} menit (timeout)")
        if progress and t.is_alive():
            progress(elapsed)
    if p.returncode != 0:
        raise RuntimeError("Claude Code error: " + (result.get("err") or result.get("out") or "")[-800:])
    raw = result.get("out", "")
    try:
        j = json.loads(raw)
    except ValueError:
        return raw   # versi CLI lama: teks biasa
    if not isinstance(j, dict) or "result" not in j:
        return raw
    if j.get("is_error"):
        raise RuntimeError("Claude Code error: " + str(j.get("result"))[-800:])
    u = j.get("usage") or {}
    record_usage(log, "code", u.get("input_tokens", 0), u.get("output_tokens", 0), u.get("cache_read_input_tokens", 0),
                 u.get("cache_creation_input_tokens", 0), j.get("total_cost_usd"), (j.get("duration_ms") or 0) / 1000)
    return j.get("result") or ""
