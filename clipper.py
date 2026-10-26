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


def _mb(b):
    return f"{b / 1048576:.1f} MB"


def _ytdlp(args, label, log=print):
    """Jalankan yt-dlp dengan progress. log(msg, frac) dipanggil dengan frac 0..1.
    Mengembalikan path file hasil."""
    cmd = YTDLP + args[:-1] + ["--newline", "--progress", "--progress-template",
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
        raise RuntimeError(f"Gagal: {label.lower()}\n{err[-1500:]}")
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
    try:
        out = run(YTDLP + ["--skip-download", "--print", "%(title)s|||%(channel,uploader,creator)s", url])
        title, _, owner = out.strip().splitlines()[0].partition("|||")
        return title[:120], ("" if owner == "NA" else owner)
    except Exception:
        return url, ""


def video_title(url):
    return video_info(url)[0]


def download_section(url, start, end, out_dir, idx):
    """Unduh hanya potongan [start, end] detik. Dipotong tepat di keyframe agar waktu akurat."""
    out = os.path.join(out_dir, f"part_{idx:02d}.%(ext)s")
    run(YTDLP + ["-f", VIDEO_FMT] + VIDEO_SORT + ["--merge-output-format", "mp4",
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


def face_center_x(path, start, end, width):
    """Rata-rata posisi X wajah di rentang klip (OpenCV). Fallback: tengah."""
    try:
        import cv2
        cap = cv2.VideoCapture(path)
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        xs = []
        for k in range(12):
            t = start + (end - start) * (k + 0.5) / 12
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok:
                continue
            small = cv2.resize(frame, None, fx=0.5, fy=0.5)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(gray, 1.1, 5, minSize=(40, 40))
            if len(faces):
                x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
                xs.append((x + w / 2) * 2)
        cap.release()
        if xs:
            xs.sort()
            return xs[len(xs) // 2]
    except Exception:
        pass
    return width / 2


def _ass_time(t):
    t = max(t, 0)
    h, m = int(t // 3600), int(t % 3600 // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def credit_alpha(opacity):
    """Opacity 0-100 (%) -> alpha ASS (00 = pekat, FF = transparan)."""
    return f"{round((1 - max(0, min(100, opacity)) / 100) * 255):02X}"


def make_ass(words, start, end, out_path, style, credit=None):
    """credit = {"text", "opacity" (0-100), "pos" ("atas"/"bawah")}: label sumber video,
    tampil sepanjang klip dengan transparansi."""
    cpos = (credit or {}).get("pos")
    color = {"kuning": "&H0000FFFF", "putih": "&H00FFFFFF", "hijau": "&H0000FF7F"}.get(style, "&H0000FFFF")
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial Black,84,&H00FFFFFF,{color},&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,6,2,2,60,60,520,1
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


def _ask_json_list(prompt, n, api_key, model, log):
    if api_key == "__claude_code__":
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
Aturan:
- Kalimat pertama = hook yang bikin penasaran.
- Gaya bicara santai, kalimat pendek, enak dibacakan.
- PANJANG WAJIB antara "min_kata" dan "maks_kata" kata, karena narasi harus mengisi seluruh durasi klip.
  Kalau isi asli sedikit (banyak jeda/musik), tambahkan konteks, penjelasan, contoh, atau fakta pendukung yang relevan
  sampai mencapai min_kata. Jangan berhenti di tengah: akhiri dengan kalimat penutup atau ajakan.
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
Perpanjang masing-masing sampai antara min_kata dan maks_kata kata dalam {bahasa}: pertahankan hook dan gayanya,
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
3. Kalimat pertama = hook yang bikin penasaran. Gaya santai, kalimat pendek, enak dibacakan.
4. Panjang WAJIB antara min_kata dan maks_kata. Tanpa emoji, hashtag, petunjuk adegan, atau tanda kurung.
Balas HANYA JSON array string sesuai urutan klip: ["narasi klip 0", ...]"""
    if api_key and api_key != "__claude_code__":
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


def tts(text, voice, out_path, max_dur=None):
    """Bacakan teks dengan edge-tts. Kalau lebih panjang dari max_dur, dipercepat (maks +40%);
    kalau jauh lebih pendek, diperlambat sedikit (maks -12%) agar tidak banyak diam di akhir.
    Mengembalikan (durasi, daftar kata {start, end, word}) untuk subtitle."""
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


def render_clip(src, clip, words, out_dir, idx, opts, log=print):
    """voice_mode: "asli" = suara video, "ai" = narasi dibacakan suara AI,
    "teks" = video tanpa suara (narasi diisi sendiri, naskah ada di clip["narration"]),
    "kosong" = video saja tanpa suara & subtitle; naskah AI hanya sebagai hint."""
    w, h = get_size(src)
    start, end = clip["start"], clip["end"]
    base = f"clip_{idx:02d}"
    ass_name = base + ".ass"
    out_name = base + ".mp4"
    mode = opts.get("voice_mode", "asli")
    silent = mode in ("teks", "kosong")   # video tanpa suara asli
    narr = None
    if mode == "ai":
        narr = base + "_narasi.mp3"
        _, tw = tts(clip["narration"], opts.get("voice") or "id-ID-ArdiNeural",
                    os.path.join(out_dir, narr), max_dur=end - start)
        words = [dict(x, start=x["start"] + start, end=x["end"] + start) for x in tw]   # subtitle ikut narasi
    filters = []
    if opts.get("vertical", True):
        crop_w = min(w, int(h * 9 / 16)) // 2 * 2
        cx = face_center_x(src, start, end, w) if opts.get("face", True) else w / 2
        x = int(min(max(cx - crop_w / 2, 0), w - crop_w))
        filters.append(f"crop={crop_w}:{h}:{x}:0,scale=1080:1920")
    # tanpa suara: tanpa subtitle juga, karena waktu bicara baru diketahui setelah direkam sendiri
    show_subs = opts.get("subtitles", True) and not silent
    # label pemilik video; "{channel}" diganti nama channel asli
    credit = {"text": (opts.get("credit_text") or "").replace("{channel}", opts.get("channel") or "").strip(),
              "opacity": int(opts.get("credit_opacity") or 60), "pos": opts.get("credit_pos", "bawah")}
    if show_subs or credit["text"]:
        make_ass(words if show_subs else [], start, end, os.path.join(out_dir, ass_name),
                 opts.get("sub_color", "kuning"), credit)
        filters.append(f"ass={ass_name}")
    cmd = ["ffmpeg", "-y", "-ss", f"{start:.2f}", "-to", f"{end:.2f}", "-i", os.path.abspath(src)]
    if narr:
        cmd += ["-i", narr, "-map", "0:v:0", "-map", "1:a:0", "-af", "apad", "-t", f"{end - start:.2f}"]
    if filters:
        cmd += ["-vf", ",".join(filters)]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
    cmd += ["-an"] if silent else ["-c:a", "aac", "-b:a", "160k"]
    cmd += ["-movflags", "+faststart", out_name]
    if silent and clip.get("narration"):
        with open(os.path.join(out_dir, base + "_narasi.txt"), "w", encoding="utf-8") as f:
            f.write(clip.get("narration", ""))
    log(f"Render klip {idx}...")
    run(cmd, cwd=out_dir)   # cwd = folder output agar path subtitle aman di Windows
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
- Judul, hook, dan hashtag pakai bahasa yang sama dengan video.

Balas HANYA JSON valid tanpa teks lain, format:
{{"clips":[{{"start_seg":0,"end_seg":5,"viral":87,"title":"judul pendek menarik","hook":"alasan klip ini menarik","hashtags":["#a","#b"]}}]}}"""
    if api_key == "__claude_code__":
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
                "title": c.get("title", ""), "hook": c.get("hook", ""),
                "hashtags": c.get("hashtags", [])}
        if all(clip["end"] <= p["start"] or clip["start"] >= p["end"] for p in out):
            out.append(clip)
    return sorted(out, key=lambda c: c["start"])


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
    cmd = [exe, "-p", "--output-format", "text"]
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
    return result.get("out", "")
