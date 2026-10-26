"""Agen Trend: topik yang sedang naik -> naskah orisinal (Claude) -> footage stok Pexels
(bebas lisensi) -> suara AI + subtitle -> video 9:16 siap direview."""
import json, os, re, urllib.parse, urllib.request
import clipper, research

PEXELS_URL = "https://api.pexels.com/videos/search?"


def _claude(prompt, api_key, model, log):
    if api_key and api_key != "__claude_code__":
        import anthropic
        msg = anthropic.Anthropic(api_key=api_key).messages.create(
            model=model, max_tokens=6000, messages=[{"role": "user", "content": prompt}])
        return "".join(b.text for b in msg.content if b.type == "text")
    return clipper._ask_claude_code(prompt, log)


def _json(raw, kind="{"):
    m = re.search(r"\{.*\}" if kind == "{" else r"\[.*\]", raw, re.S)
    if not m:
        raise RuntimeError("Jawaban AI tidak berisi JSON: " + raw[:200])
    return json.loads(m.group(0))


def find_topics(niche, n=3, lang="id", geo="ID", used=(), api_key=None, model="claude-sonnet-5-5", log=print):
    """Pilih n topik video dari Google Trends + pertanyaan audiens, hindari topik yang sudah dipakai."""
    log("Mengambil data trend...")
    data = {"niche": niche}
    try:
        t = research.trends(niche, geo, "today 1-m", lang)
        data["trends_rising"], data["trends_top"] = t["rising"][:15], t["top"][:10]
    except Exception as e:
        log(f"Google Trends gagal ({str(e)[:80]}), lanjut tanpa Trends...")
    try:
        q = research.questions(niche, lang)
        data["pertanyaan_audiens"] = {k: v[:12] for k, v in q.items()}
    except Exception:
        pass
    log("Claude memilih topik...")
    prompt = f"""Kamu strategis konten YouTube Shorts. Niche channel: "{niche}".
Data pencarian yang sedang naik dan pertanyaan audiens:
{json.dumps(data, ensure_ascii=False)[:8000]}

Topik yang SUDAH pernah dibuat (jangan diulang atau dibuat mirip):
{json.dumps(list(used)[-60:], ensure_ascii=False)}

Pilih {n} topik video pendek yang paling berpotensi ditonton SEKARANG, prioritaskan yang sedang naik (Rising).
Topik harus bisa dijelaskan secara informatif dalam 30-60 detik dan divisualkan dengan footage stok umum
(bukan butuh gambar orang terkenal, logo, atau kejadian spesifik).
Balas HANYA JSON array: [{{"topic": "judul topik singkat", "angle": "sudut pandang/hook", "why": "alasan, rujuk data", "potensi": 0-100}}]"""
    return _json(_claude(prompt, api_key, model, log), "[")[:n]


def write_script(topic, angle="", lang="id", seconds=45, api_key=None, model="claude-sonnet-5-5", log=print):
    """Naskah orisinal per adegan, tiap adegan punya kata kunci footage (bahasa Inggris untuk Pexels)."""
    words = int(seconds * clipper.WORDS_PER_SEC)
    bahasa = {"id": "Bahasa Indonesia", "en": "English", "ms": "Bahasa Melayu"}.get(lang, "Bahasa Indonesia")
    log("Claude menulis naskah...")
    prompt = f"""Tulis naskah voice-over YouTube Shorts ORISINAL dalam {bahasa}.
Topik: {topic}
Sudut pandang: {angle}
Aturan:
- Total {int(words * 0.9)}-{words} kata (sekitar {seconds} detik dibacakan).
- Adegan pertama = hook 1 kalimat yang bikin penasaran. Adegan terakhir = penutup/ajakan singkat.
- 5-8 adegan, tiap adegan 1-2 kalimat pendek, gaya santai dan informatif, fakta harus akurat dan umum diketahui.
- Untuk tiap adegan beri "search": 2-4 kata kunci BAHASA INGGRIS untuk mencari footage stok yang cocok
  (benda/suasana umum, mis. "stock market chart", "person counting money"; tanpa nama merek/orang terkenal).
- Tanpa emoji di naskah.
Balas HANYA JSON:
{{"title": "judul Shorts menarik (maks 70 karakter)", "description": "deskripsi 2-3 kalimat",
"hashtags": ["#shorts", "#..."], "potensi": 0-100,
"scenes": [{{"text": "kalimat narasi", "search": "english keywords"}}]}}"""
    s = _json(_claude(prompt, api_key, model, log))
    if not s.get("scenes"):
        raise RuntimeError("Naskah tidak berisi adegan")
    return s


def pexels_search(query, key, used_ids=()):
    """Cari video portrait di Pexels; kembalikan (id, url file, nama pembuat) yang belum dipakai."""
    for orientation in ("portrait", None):   # kalau portrait tidak ada, ambil landscape lalu di-crop
        p = {"query": query, "per_page": 10, "size": "medium"}
        if orientation:
            p["orientation"] = orientation
        req = urllib.request.Request(PEXELS_URL + urllib.parse.urlencode(p),
                                     headers={"Authorization": key, "User-Agent": "ShortsClips"})
        try:
            data = json.loads(urllib.request.urlopen(req, timeout=20).read())
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise RuntimeError("Pexels API key ditolak. Cek lagi key di pengaturan Agen Trend.")
            raise
        for v in data.get("videos", []):
            if v["id"] in used_ids:
                continue
            files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("width")]
            # pilih file terkecil yang sisi pendeknya >= 720 (cukup tajam untuk 1080x1920, unduhan ringan)
            good = sorted([f for f in files if min(f["width"], f["height"]) >= 720], key=lambda f: f["width"] * f["height"])
            f = (good or sorted(files, key=lambda f: -f["width"] * f["height"]))[:1]
            if f:
                return v["id"], f[0]["link"], (v.get("user") or {}).get("name", "Pexels")
    return None


def _download(url, path):
    req = urllib.request.Request(url, headers={"User-Agent": "ShortsClips"})
    with urllib.request.urlopen(req, timeout=60) as r, open(path, "wb") as f:
        while chunk := r.read(1 << 16):
            f.write(chunk)
    return path


def make_video(script, out_dir, idx, opts, pexels_key, log=print):
    """Render naskah jadi video 9:16: tiap adegan = footage stok + narasi, lalu digabung."""
    voice = opts.get("voice") or "id-ID-ArdiNeural"
    base = f"auto_{idx:02d}"
    tmp = os.path.join(out_dir, base + "_tmp")
    os.makedirs(tmp, exist_ok=True)
    scenes, used, credits, words, t0 = script["scenes"], set(), [], [], 0.0
    n = len(scenes)
    for k, sc in enumerate(scenes):
        log(f"Adegan {k + 1}/{n}: suara & footage...", 0.1 + 0.75 * k / n)
        a = os.path.join(tmp, f"a{k}.mp3")
        dur, w = clipper.tts(sc["text"], voice, a)
        dur += 0.25   # jeda kecil antar adegan
        words += [dict(x, start=x["start"] + t0, end=x["end"] + t0) for x in w]
        hit = None
        for q in (sc.get("search"), " ".join((sc.get("search") or "").split()[:2]), opts.get("niche_en") or "abstract background"):
            if q and (hit := pexels_search(q, pexels_key, used)):
                break
        if not hit:
            raise RuntimeError(f"Tidak menemukan footage untuk adegan {k + 1} ({sc.get('search')})")
        vid, link, author = hit
        used.add(vid); credits.append(author)
        raw = _download(link, os.path.join(tmp, f"src{k}.mp4"))
        # potong/ulang footage sepanjang narasi adegan, crop ke 1080x1920
        clipper.run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", raw, "-i", a, "-t", f"{dur:.2f}",
                     "-map", "0:v:0", "-map", "1:a:0", "-af", "apad",
                     "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,setsar=1",
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
                     os.path.join(tmp, f"s{k}.mp4")])
        t0 += dur
    log("Menggabungkan adegan & subtitle...", 0.9)
    with open(os.path.join(tmp, "list.txt"), "w", encoding="utf-8") as f:
        f.write("".join(f"file 's{k}.mp4'\n" for k in range(n)))
    joined = os.path.join(tmp, "joined.mp4")
    clipper.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list.txt", "-c", "copy", "joined.mp4"], cwd=tmp)
    credit = {"text": (opts.get("credit_text") or "").strip(), "opacity": int(opts.get("credit_opacity") or 60),
              "pos": opts.get("credit_pos", "bawah")}
    clipper.make_ass(words if opts.get("subtitles", True) else [], 0, t0, os.path.join(out_dir, base + ".ass"),
                     opts.get("sub_color", "kuning"), credit)
    out = base + ".mp4"
    clipper.run(["ffmpeg", "-y", "-i", joined, "-vf", f"ass={base}.ass", "-c:v", "libx264", "-preset", "veryfast",
                 "-crf", "20", "-c:a", "copy", "-movflags", "+faststart", out], cwd=out_dir)
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    return out, round(t0, 2), sorted(set(credits))
