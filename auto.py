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
    return clipper.loose_json(raw, kind)


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
{clipper.viral_pattern(bahasa)}
Aturan:
- Total {int(words * 0.9)}-{words} kata (sekitar {seconds} detik dibacakan).
- Adegan = 5 bagian berurutan: hook, poin 1, poin 2, poin 3, penutup loop (poin boleh dipecah jadi 2 adegan).
  Gaya santai dan informatif, fakta harus akurat dan umum diketahui.
- Untuk tiap adegan beri "search": 2-4 kata kunci BAHASA INGGRIS untuk mencari footage stok yang cocok
  (benda/suasana umum, mis. "stock market chart", "person counting money"; tanpa nama merek/orang terkenal).
  Adegan hook: footage bergerak cepat yang memikat mata, tambahkan "timelapse" atau "macro" (mis. "city night timelapse").
- "hook_text": teks besar di layar saat hook, 3-6 kata, boleh sama dengan inti kalimat hook.
- Tanpa emoji di naskah.
- "cek": daftar klaim di naskah yang sebaiknya kamu verifikasi sebelum upload (kosong [] bila semua fakta umum).
  Topik kesehatan: description diakhiri "Konten edukasi, bukan pengganti saran medis."
Balas HANYA JSON:
{{"title": "judul Shorts menarik (maks 70 karakter)", "hook_text": "teks layar 3-6 kata", "description": "deskripsi 2-3 kalimat",
"cek": ["klaim yang perlu dicek"],
"hashtags": ["#shorts", "#..."], "potensi": 0-100,
"scenes": [{{"text": "kalimat narasi", "search": "english keywords"}}]}}"""
    s = _json(_claude(prompt, api_key, model, log))
    if not s.get("scenes"):
        raise RuntimeError("Naskah tidak berisi adegan")
    return s


def pexels_search(query, key, used_ids=(), n=None):
    """Cari video portrait di Pexels; kembalikan (id, url file, nama pembuat) yang belum dipakai
    (n > 1: daftar sampai n hasil dari satu pencarian, untuk ganti-ganti B-roll)."""
    hits = []
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
            if v["id"] in used_ids or any(h[0] == v["id"] for h in hits):
                continue
            files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("width")]
            # pilih file terkecil yang sisi pendeknya >= 720 (cukup tajam untuk 1080x1920, unduhan ringan)
            good = sorted([f for f in files if min(f["width"], f["height"]) >= 720], key=lambda f: f["width"] * f["height"])
            f = (good or sorted(files, key=lambda f: -f["width"] * f["height"]))[:1]
            if f:
                hits.append((v["id"], f[0]["link"], (v.get("user") or {}).get("name", "Pexels")))
                if len(hits) >= (n or 1):
                    break
        if len(hits) >= (n or 1):
            break
    if n:
        return hits
    return hits[0] if hits else None


def _download(url, path):
    req = urllib.request.Request(url, headers={"User-Agent": "ShortsClips"})
    with urllib.request.urlopen(req, timeout=60) as r, open(path, "wb") as f:
        while chunk := r.read(1 << 16):
            f.write(chunk)
    return path


CUT_SEC = 1.75   # B-roll diganti tiap ~1,5-2 detik agar mata penonton tidak bosan


def make_video(script, out_dir, idx, opts, pexels_key, log=print):
    """Render naskah jadi video 9:16: tiap adegan = beberapa footage stok yang berganti cepat + narasi,
    lalu digabung dengan teks hook, subtitle, dan SFX di pergantian poin."""
    voice = opts.get("voice") or "id-ID-ArdiNeural"
    base = f"auto_{idx:02d}"
    tmp = os.path.join(out_dir, base + "_tmp")
    os.makedirs(tmp, exist_ok=True)
    scenes, used, credits, words, t0 = script["scenes"], set(), [], [], 0.0
    n = len(scenes)
    voice = clipper.eleven_preflight(voice, " ".join(sc["text"] for sc in scenes), lambda m: log(m, 0.1))
    for k, sc in enumerate(scenes):
        log(f"Adegan {k + 1}/{n}: suara & footage...", 0.1 + 0.75 * k / n)
        a = os.path.join(tmp, f"a{k}.mp3")
        dur, w = clipper.tts(sc["text"], voice, a)
        if k < n - 1:
            dur += 0.25   # jeda kecil antar adegan; adegan terakhir tanpa jeda agar loop ke hook mulus
        words += [dict(x, start=x["start"] + t0, end=x["end"] + t0) for x in w]
        cuts = max(1, round(dur / CUT_SEC))
        hits = []
        for q in (sc.get("search"), " ".join((sc.get("search") or "").split()[:2]), opts.get("niche_en") or "abstract background"):
            if q:
                hits += [h for h in pexels_search(q, pexels_key, used | {h[0] for h in hits}, n=min(cuts, 3) - len(hits))]
            if len(hits) >= min(cuts, 3):
                break
        if not hits:
            raise RuntimeError(f"Tidak menemukan footage untuk adegan {k + 1} ({sc.get('search')})")
        srcs = []
        for j, (vid, link, author) in enumerate(hits):
            used.add(vid); credits.append(author)
            srcs.append(_download(link, os.path.join(tmp, f"src{k}_{j}.mp4")))
        # potong jadi `cuts` bagian bergantian dari tiap footage; hook dipercepat agar gerakannya terasa cepat
        cd, speed = dur / cuts, (0.6 if k == 0 else 1.0)
        cmd, graph = ["ffmpeg", "-y"], []
        for src in srcs:
            cmd += ["-stream_loop", "-1", "-i", src]
        cmd += ["-i", a]
        for j in range(cuts):
            off = (j // len(srcs)) * cd / speed + 0.3
            graph.append(f"[{j % len(srcs)}:v]trim=start={off:.2f}:duration={cd / speed:.3f},setpts=PTS-STARTPTS"
                         f"{f',setpts={speed}*PTS' if speed != 1 else ''},scale=1080:1920:force_original_aspect_ratio=increase,"
                         f"crop=1080:1920,fps=30,setsar=1[c{j}]")
        graph.append("".join(f"[c{j}]" for j in range(cuts)) + f"concat=n={cuts}:v=1:a=0[v]")
        graph.append(f"[{len(srcs)}:a]apad,atrim=0:{dur:.3f}[a]")
        cmd += ["-filter_complex", ";".join(graph), "-map", "[v]", "-map", "[a]", "-t", f"{dur:.3f}",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
                os.path.join(tmp, f"s{k}.mp4")]
        clipper.run(cmd)
        t0 += dur
    log("Menggabungkan adegan, teks hook, subtitle & SFX...", 0.9)
    with open(os.path.join(tmp, "list.txt"), "w", encoding="utf-8") as f:
        f.write("".join(f"file 's{k}.mp4'\n" for k in range(n)))
    joined = os.path.join(tmp, "joined.mp4")
    clipper.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list.txt", "-c", "copy", "joined.mp4"], cwd=tmp)
    credit = {"text": (opts.get("credit_text") or "").strip(), "opacity": int(opts.get("credit_opacity") or 60),
              "pos": opts.get("credit_pos", "bawah")}
    hook = script.get("hook_text") or script.get("title") or ""
    clipper.make_ass(words if opts.get("subtitles", True) else [], 0, t0, os.path.join(out_dir, base + ".ass"),
                     opts.get("sub_color", "kuning"), credit, hook)
    ev = clipper.sfx_events(words, bool(hook)) if opts.get("sfx", True) else []
    cmd = ["ffmpeg", "-y", "-i", joined]
    for _, kind in ev:
        cmd += ["-i", clipper.sfx(kind)]
    graph = [f"[0:v]ass={base}.ass[v]"] + clipper.mix_sfx_graph("0:a", ev, 1)
    out = base + ".mp4"
    clipper.run(cmd + ["-filter_complex", ";".join(graph), "-map", "[v]", "-map", "[a]", "-c:v", "libx264",
                       "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out],
                cwd=out_dir)
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    return out, round(t0, 2), sorted(set(credits))
