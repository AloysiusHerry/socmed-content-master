import os, re, sys, uuid, json, threading, shutil, traceback

# Kalau dijalankan dengan Python sistem (mis. "python app.py"), pindah ke Python venv
# karena semua paket (faster-whisper, flask, dll) terpasang di sana.
_VENV_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "venv", "Scripts", "python.exe")
if __name__ == "__main__" and os.path.exists(_VENV_PY) and \
        os.path.normcase(os.path.abspath(sys.executable)) != os.path.normcase(_VENV_PY):
    import subprocess
    sys.exit(subprocess.call([_VENV_PY] + sys.argv))

# Sama seperti run.bat: cache model AI & file sementara di drive D (drive C penuh),
# juga saat app dijalankan langsung tanpa run.bat
_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
os.environ.setdefault("HF_HOME", os.path.join(_CACHE, "huggingface"))
os.environ.setdefault("XDG_CACHE_HOME", _CACHE)
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from flask import Flask, render_template, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
import clipper, research, auto, motion

BASE = os.path.dirname(os.path.abspath(__file__))
JOBS_DIR = os.path.join(BASE, "jobs")
os.makedirs(JOBS_DIR, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 ** 3
JOBS = {}
CFG = os.path.join(BASE, "config.json")


def load_cfg():
    try:
        with open(CFG, encoding="utf-8") as f: return json.load(f)
    except Exception: return {}


def save_job(jid):
    with open(os.path.join(JOBS_DIR, jid, "job.json"), "w", encoding="utf-8") as f:
        json.dump(JOBS[jid], f, ensure_ascii=False, indent=2)


def load_jobs():
    for jid in os.listdir(JOBS_DIR):
        p = os.path.join(JOBS_DIR, jid, "job.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                JOBS[jid] = json.load(f)
                if JOBS[jid]["status"] not in ("selesai", "error"):
                    JOBS[jid]["status"] = "error"; JOBS[jid]["log"] = "Terhenti (server restart)"


def _logger(jid):
    job = JOBS[jid]
    def log(msg, pct=None):
        job["log"] = msg
        if pct is not None: job["progress"] = round(pct)
        print(f"[{jid}] {msg}")
    def stage(lo, hi):
        # log untuk satu tahap: frac 0..1 dipetakan ke rentang progress total lo..hi %
        return lambda msg, frac=None: log(msg, None if frac is None else lo + (hi - lo) * frac)
    return log, stage


def _overlaps(c, clips):
    return any(not (c["end"] <= p["start"] or c["start"] >= p["end"]) for p in clips)


def _visual(opts):
    return opts.get("narr_source") == "visual"


def _highlights(tr, opts, log, exclude=(), src=None):
    """Pilih highlight sesuai mode AI; bagian yang sudah jadi klip (exclude) dilewati.
    Narasi dari visual: tanpa transkrip, video dibagi jadi potongan berurutan."""
    n = opts["n_clips"]
    if _visual(opts):
        return clipper.visual_segments(clipper.get_duration(src), n, opts["min_len"], opts["max_len"], exclude)
    want = n + len(exclude)   # minta lebih banyak, lalu buang yang bertabrakan dengan klip lama
    mode = opts.get("ai_mode", "code")
    key = {"code": "__claude_code__",
           "api": opts.get("api_key") or load_cfg().get("api_key") or os.environ.get("ANTHROPIC_API_KEY")}.get(mode)
    hl = None
    if key:
        try:
            hl = clipper.find_highlights_claude(tr, want, opts["min_len"], opts["max_len"],
                                                key, opts.get("ai_model") or "claude-sonnet-5-5", log)
        except Exception as e:
            log(f"Claude gagal ({e}), pakai metode lokal..."); hl = None
    if not hl:
        hl = clipper.find_highlights(tr, want, opts["min_len"], opts["max_len"])
    return [c for c in (hl or []) if not _overlaps(c, exclude)][:n]


def _narr_lang(opts):
    """Bahasa naskah narasi: pilihan "Bahasa narasi" > bahasa suara AI (en-/id-/ms-) > bahasa video."""
    if opts.get("narr_lang"):
        return opts["narr_lang"]
    if opts.get("voice_mode") == "ai" and opts.get("voice"):
        return clipper.voice_lang(opts["voice"])
    return opts.get("language")


def _narrate(clips, opts, log, force=False, src=None, work_dir=None):
    """Isi c["narration"] untuk klip yang belum punya (mode suara AI / narasi saja)."""
    if opts.get("voice_mode", "asli") not in ("ai", "teks", "kosong"):
        return
    todo = [c for c in clips if force or not c.get("narration")]
    if not todo:
        return
    key = (opts.get("api_key") or load_cfg().get("api_key") or os.environ.get("ANTHROPIC_API_KEY")) \
        if opts.get("ai_mode") == "api" else "__claude_code__"
    if _visual(opts):
        # narasi dari isi gambar: ambil frame tiap klip, Claude melihat lalu menulis narasi + fakta
        fdir = os.path.join(work_dir, "frames"); shutil.rmtree(fdir, ignore_errors=True); os.makedirs(fdir)
        log(f"Mengambil frame dari {len(todo)} klip...")
        frames = [clipper.extract_frames(src, c["start"], c["end"], fdir, f"k{i}") for i, c in enumerate(todo)]
        log("Claude melihat video & menulis narasi...")
        texts = clipper.write_visual_narrations(todo, frames, _narr_lang(opts), key,
                                                opts.get("ai_model") or "claude-sonnet-5-5", log)
        for c, t in zip(todo, texts):
            c["narration"] = t
            c["title"] = c.get("title") or " ".join(t.split()[:8]) + "..."
            c["text"] = c.get("text") or t
        shutil.rmtree(fdir, ignore_errors=True)
        return
    log(f"Menulis narasi baru untuk {len(todo)} klip...")
    key = (opts.get("api_key") or load_cfg().get("api_key") or os.environ.get("ANTHROPIC_API_KEY"))         if opts.get("ai_mode") == "api" else "__claude_code__"
    for c, t in zip(todo, clipper.write_narrations(todo, _narr_lang(opts), key,
                                                   opts.get("ai_model") or "claude-sonnet-5-5", log)):
        c["narration"] = t


def _render(jid, c, idx, tr, opts, log, lo, hi):
    """Render satu klip. YouTube: unduh potongan dulu; lainnya: potong dari file lokal."""
    job, d = JOBS[jid], os.path.join(JOBS_DIR, jid)
    quiet = lambda *a: None   # pesan render_clip sendiri tidak perlu, sudah ada "Klip i/n"
    url, src = job.get("section_url"), job.get("src")
    if url:
        log(f"Klip {idx}: mengunduh potongan video...", lo)
        s0 = max(c["start"] - 1, 0)   # jeda 1 detik di awal
        part = clipper.download_section(url, s0, c["end"] + 1, d, idx)
        # geser waktu klip & kata ke timeline potongan
        local = dict(c, start=c["start"] - s0, end=c["end"] - s0)
        words = [dict(w, start=w["start"] - s0, end=w["end"] - s0) for w in tr["words"]
                 if c["start"] - 0.1 <= w["start"] <= c["end"] + 0.1]
        log(f"Klip {idx}: render...", (lo + hi) / 2)
        f = clipper.render_clip(part, local, words, d, idx, opts, quiet)
        os.remove(part)
    else:
        log(f"Klip {idx}: render...", lo)
        f = clipper.render_clip(src, c, tr["words"], d, idx, opts, quiet)
    return f


def _load_tr(jid):
    with open(os.path.join(JOBS_DIR, jid, "transcript.json"), encoding="utf-8") as f:
        return json.load(f)


def _run(jid, body):
    job = JOBS[jid]
    try:
        body()
    except Exception as e:
        traceback.print_exc()
        job["status"] = "error"; job["log"] = str(e)
    save_job(jid)


def process(jid, src, opts):
    job, d = JOBS[jid], os.path.join(JOBS_DIR, jid)
    log, stage = _logger(jid)
    def body():
        url = opts.get("url")
        job["src"] = src
        if url:
            job["status"] = "download"; log("Mengambil info video...", 0)
            job["name"], job["channel"] = clipper.video_info(url)
            opts["channel"] = job["channel"]; save_job(jid)
            if clipper.platform_of(url) == "youtube" and not _visual(opts) and not clipper.is_short_url(url):
                # Hanya audio dulu; video diunduh per potongan setelah highlight dipilih
                job["src"] = clipper.download_audio(url, d, stage(0, 10))
                job["section_url"] = url
            else:
                # TikTok/Instagram/dll (atau narasi dari visual): unduh video penuh lalu potong lokal
                job["src"] = clipper.download_video(url, d, stage(0, 10))
        save_job(jid)
        if _visual(opts):
            tr = {"language": opts.get("language"), "segments": [], "words": []}   # narasi dari gambar, tanpa transkripsi
        else:
            job["status"] = "transkripsi"
            tr = clipper.transcribe(job["src"], opts["model"], opts["language"], stage(10 if url else 0, 60))
        with open(os.path.join(d, "transcript.json"), "w", encoding="utf-8") as f:
            json.dump(tr, f, ensure_ascii=False)
        dur = clipper.get_duration(job["src"])
        if clipper.is_short_url(url) or dur <= clipper.SHORT_MAX_SEC:
            # Instagram/TikTok/YouTube Shorts atau video pendek: 1 klip penuh, tidak dibagi
            job["full"] = True
            hl = [{"start": 0.0, "end": round(dur, 2), "score": 0, "viral": 0, "title": job["name"] if url else "",
                   "text": " ".join(s["text"] for s in tr["segments"])}]
        else:
            job["status"] = "analisis"; log("Mencari momen terbaik...", 60)
            hl = _highlights(tr, opts, log, src=job["src"])
        if not hl:
            raise RuntimeError("Tidak menemukan klip. Coba perkecil durasi minimum.")
        job["status"] = "analisis"
        _narrate(hl, opts, log, src=job["src"], work_dir=d)
        job["status"] = "render"
        n = len(hl)
        for i, c in enumerate(hl, 1):
            c["file"] = _render(jid, c, i, tr, opts, lambda m, p=None: log(m.replace(f"Klip {i}", f"Klip {i}/{n}"), p),
                                65 + 35 * (i - 1) / n, 65 + 35 * i / n)
            c["title"] = c.get("title") or " ".join(c["text"].split()[:8]) + "..."
            c["opts"] = _style(opts)
            job["clips"].append(c); save_job(jid)
        job["status"] = "selesai"; log(f"Selesai! {n} klip dibuat.", 100)
    _run(jid, body)


STYLE_KEYS = ("vertical", "face", "subtitles", "sub_color", "voice_mode", "voice",
              "credit_text", "credit_opacity", "credit_pos")
def _clip_no(c):
    m = re.search(r"clip_(\d+)", c.get("file", ""))
    return int(m.group(1)) if m else 0

def _style(opts):
    return {k: opts.get(k) for k in STYLE_KEYS}


def process_more(jid, opts):
    """Buat klip tambahan dari transkrip yang sudah ada (tanpa unduh & transkripsi ulang)."""
    job = JOBS[jid]
    log, _ = _logger(jid)
    def body():
        tr = _load_tr(jid)
        job["status"] = "analisis"; log("Mencari momen baru (transkrip lama dipakai ulang)...", 5)
        hl = _highlights(tr, opts, log, exclude=job["clips"], src=job.get("src"))
        if not hl:
            raise RuntimeError("Tidak ada momen baru yang tidak bertabrakan dengan klip lama. Coba ubah durasi.")
        _narrate(hl, opts, log, src=job.get("src"), work_dir=os.path.join(JOBS_DIR, jid))
        job["status"] = "render"
        n, base = len(hl), max([_clip_no(c) for c in job["clips"]] + [0])
        for k, c in enumerate(hl, 1):
            idx = base + k
            c["file"] = _render(jid, c, idx, tr, opts, lambda m, p=None: log(m.replace(f"Klip {idx}", f"Klip baru {k}/{n}"), p),
                                10 + 90 * (k - 1) / n, 10 + 90 * k / n)
            c["title"] = c.get("title") or " ".join(c["text"].split()[:8]) + "..."
            c["opts"] = _style(opts)
            job["clips"].append(c); save_job(jid)
        job["status"] = "selesai"; log(f"Selesai! {n} klip baru ditambahkan.", 100)
    _run(jid, body)


def process_rerender(jid, opts, files):
    """Render ulang klip yang ada dengan gaya baru (subtitle, warna, crop wajah)."""
    job = JOBS[jid]
    log, _ = _logger(jid)
    def body():
        tr = _load_tr(jid)
        targets = [c for c in job["clips"] if not files or c["file"] in files]
        job["status"] = "analisis"
        if _visual(opts) and job.get("section_url"):
            raise RuntimeError("Narasi dari visual butuh video penuh; project YouTube ini hanya menyimpan audio. Buat project baru dengan sumber narasi 'visual'.")
        _narrate(targets, opts, log, force=opts.get("renarrate"), src=job.get("src"), work_dir=os.path.join(JOBS_DIR, jid))
        job["status"] = "render"; n = len(targets)
        for k, c in enumerate(targets, 1):
            idx = _clip_no(c) or k
            c["file"] = _render(jid, c, idx, tr, opts, lambda m, p=None: log(m.replace(f"Klip {idx}", f"Render ulang {k}/{n}"), p),
                                90 * (k - 1) / n, 90 * k / n)
            c["opts"] = _style(opts); c["v"] = c.get("v", 0) + 1   # v = pembeda cache browser
            save_job(jid)
        job["status"] = "selesai"; log(f"Selesai! {n} klip dirender ulang.", 100)
    _run(jid, body)


def process_download(jid, url, fmt):
    """Mode Downloader: hanya unduh video/audio tanpa membuat klip."""
    job, d = JOBS[jid], os.path.join(JOBS_DIR, jid)
    def log(msg, frac=None):
        job["log"] = msg
        if frac is not None: job["progress"] = round(frac * 100)
    try:
        job["status"] = "download"; log("Mengambil info video...", 0)
        title = clipper.video_title(url); job["name"] = title; save_job(jid)
        name = secure_filename(title)[:80] or "video"
        if fmt == "mp3":
            path = clipper.download_audio(url, d, log, name=name, mp3=True)
        else:
            path = clipper.download_video(url, d, log, name=name)
        job["files"] = [os.path.basename(path)]
        job["size"] = os.path.getsize(path)
        job["status"] = "selesai"; log("Selesai diunduh.", 1)
    except Exception as e:
        traceback.print_exc()
        job["status"] = "error"; log(str(e))
    save_job(jid)


@app.route("/api/download", methods=["POST"])
def download():
    import time
    url = request.form.get("url", "").strip()
    if not url:
        return jsonify(error="Isi link video"), 400
    jid = uuid.uuid4().hex[:10]; os.makedirs(os.path.join(JOBS_DIR, jid))
    JOBS[jid] = {"id": jid, "kind": "download", "name": url, "url": url,
                 "platform": clipper.platform_of(url), "format": request.form.get("format", "mp4"),
                 "status": "antri", "log": "Menunggu...", "clips": [], "files": [], "created": time.time()}
    save_job(jid)
    threading.Thread(target=process_download, args=(jid, url, JOBS[jid]["format"]), daemon=True).start()
    return jsonify(id=jid)


def _api(fn):
    """Bungkus endpoint riset: error dikirim sebagai JSON agar tampil di halaman."""
    try:
        return jsonify(fn())
    except Exception as e:
        traceback.print_exc()
        return jsonify(error=str(e)), 500


@app.route("/api/research/questions")
def r_questions():
    a = request.args
    return _api(lambda: research.questions(a.get("q", ""), a.get("lang", "id"), a.get("source", "youtube")))


@app.route("/api/research/outliers")
def r_outliers():
    a = request.args
    return _api(lambda: research.outliers(a.get("q", ""), int(a.get("n", 20)), a.get("kind", "all"), int(a.get("days", 0))))


@app.route("/api/research/trends")
def r_trends():
    a = request.args
    return _api(lambda: research.trends(a.get("q", ""), a.get("geo", "ID"), a.get("tf", "today 3-m"), a.get("lang", "id")))


@app.route("/api/research/ideas", methods=["POST"])
def r_ideas():
    b = request.get_json(force=True)
    key = load_cfg().get("api_key") or os.environ.get("ANTHROPIC_API_KEY")
    return _api(lambda: research.ideas(b.get("q", ""), b.get("context", {}), int(b.get("n", 10)), b.get("lang", "id"),
                                       key, b.get("ai_model", "claude-sonnet-5-5"), b.get("mode", "code")))


@app.route("/api/voices")
def voices():
    return jsonify(clipper.VOICES)


# ===== Agen Trend: topik trend -> naskah -> footage Pexels -> video, masuk antrian review =====
AUTO_DEFAULT = {"niche": "", "lang": "id", "geo": "ID", "voice": "id-ID-ArdiNeural", "per_run": 2, "seconds": 45,
                "interval_hours": 24, "enabled": False, "pexels_key": "", "sub_color": "kuning", "subtitles": True,
                "credit_text": "", "credit_opacity": 60, "credit_pos": "bawah", "ai_mode": "code",
                "used_topics": [], "last_run": 0, "last_msg": ""}
AUTO_LOCK = threading.Lock()   # satu proses agen sekaligus agar CPU tidak penuh


def save_cfg(cfg):
    with open(CFG, "w", encoding="utf-8") as fh: json.dump(cfg, fh, ensure_ascii=False, indent=2)


def auto_cfg():
    return dict(AUTO_DEFAULT, **(load_cfg().get("auto") or {}))


def auto_save(**kw):
    cfg = load_cfg(); a = dict(AUTO_DEFAULT, **(cfg.get("auto") or {})); a.update(kw)
    cfg["auto"] = a; save_cfg(cfg); return a


def _auto_key(a):
    return (load_cfg().get("api_key") or os.environ.get("ANTHROPIC_API_KEY")) if a.get("ai_mode") == "api" else "__claude_code__"


def _new_auto_job(title):
    import time
    jid = uuid.uuid4().hex[:10]; os.makedirs(os.path.join(JOBS_DIR, jid))
    JOBS[jid] = {"id": jid, "kind": "auto", "name": title, "url": "", "platform": "agen", "status": "antri",
                 "log": "Menunggu giliran...", "clips": [], "created": time.time()}
    save_job(jid); return jid


def process_auto(jid, topic, a):
    """Satu video dari satu topik."""
    job, d = JOBS[jid], os.path.join(JOBS_DIR, jid)
    log, _ = _logger(jid)
    def body():
        with AUTO_LOCK:
            key = _auto_key(a)
            job["status"] = "analisis"; log("Menulis naskah...", 5)
            s = auto.write_script(topic["topic"], topic.get("angle", ""), a["lang"], int(a["seconds"]), key, log=log)
            job["name"] = s.get("title") or topic["topic"]; save_job(jid)
            job["status"] = "render"
            f, dur, credits = auto.make_video(s, d, 1, a, a["pexels_key"], lambda m, p=None: log(m, None if p is None else 10 + 90 * p))
            desc = (s.get("description") or "").strip()
            desc += "\n\nFootage: Pexels (" + ", ".join(credits) + ")" if credits else ""
            job["clips"] = [{"file": f, "start": 0, "end": dur, "title": job["name"], "viral": int(s.get("potensi") or topic.get("potensi") or 0),
                             "text": topic.get("why", ""), "hook": topic.get("angle", ""), "hashtags": s.get("hashtags", []),
                             "narration": " ".join(x["text"] for x in s["scenes"]), "description": desc,
                             "opts": {"voice_mode": "ai"}, "review": "menunggu"}]
            job["topic"] = topic["topic"]
            job["status"] = "selesai"; log("Siap direview.", 100)
    _run(jid, body)


def run_agent(topics=None):
    """Satu putaran agen: pilih topik (atau pakai topik manual) lalu buat video untuk tiap topik."""
    import time
    a = auto_cfg()
    if not a["pexels_key"]:
        auto_save(last_msg="Isi Pexels API key dulu."); return
    if not topics:
        if not a["niche"]:
            auto_save(last_msg="Isi niche/topik channel dulu."); return
        try:
            topics = auto.find_topics(a["niche"], int(a["per_run"]), a["lang"], a["geo"], a["used_topics"],
                                      _auto_key(a), log=lambda m, *x: auto_save(last_msg=m))
        except Exception as e:
            traceback.print_exc(); auto_save(last_msg=f"Gagal memilih topik: {e}", last_run=time.time()); return
    auto_save(used_topics=(a["used_topics"] + [t["topic"] for t in topics])[-200:], last_run=time.time(),
              last_msg=f"{len(topics)} topik: " + "; ".join(t["topic"] for t in topics))
    for t in topics:
        jid = _new_auto_job(t["topic"])
        threading.Thread(target=process_auto, args=(jid, t, auto_cfg()), daemon=True).start()


def agent_loop():
    """Penjadwal: cek tiap menit apakah sudah waktunya putaran berikutnya."""
    import time
    while True:
        try:
            a = auto_cfg()
            if a["enabled"] and time.time() - a["last_run"] >= float(a["interval_hours"]) * 3600:
                run_agent()
        except Exception:
            traceback.print_exc()
        time.sleep(60)


@app.route("/api/auto", methods=["GET", "POST"])
def auto_settings():
    if request.method == "POST":
        f = request.form
        kw = {k: f[k].strip() for k in ("niche", "lang", "geo", "voice", "sub_color", "credit_text", "credit_pos", "ai_mode") if k in f}
        for k in ("per_run", "seconds", "credit_opacity"):
            if f.get(k): kw[k] = int(f[k])
        if f.get("interval_hours"): kw["interval_hours"] = float(f["interval_hours"])
        if f.get("pexels_key", "").strip(): kw["pexels_key"] = f["pexels_key"].strip()   # kosong = tetap pakai key lama
        for k in ("enabled", "subtitles"):
            kw[k] = f.get(k) == "on"
        auto_save(**kw)
    a = auto_cfg()
    return jsonify({k: v for k, v in a.items() if k != "pexels_key"} | {"has_pexels": bool(a["pexels_key"])})


@app.route("/api/auto/run", methods=["POST"])
def auto_run():
    topic = (request.form.get("topic") or "").strip()
    topics = [{"topic": topic, "angle": request.form.get("angle", ""), "why": "Topik manual"}] if topic else None
    threading.Thread(target=run_agent, args=(topics,), daemon=True).start()
    return jsonify(ok=True)


@app.route("/api/job/<jid>/review", methods=["POST"])
def review(jid):
    job = JOBS.get(jid)
    if not job or not job["clips"]: return jsonify(error="Video tidak ditemukan"), 404
    c, f = job["clips"][0], request.form
    for k in ("title", "description"):
        if k in f: c[k] = f[k].strip()
    if "hashtags" in f: c["hashtags"] = [h if h.startswith("#") else "#" + h for h in f["hashtags"].split()]
    if f.get("review") in ("menunggu", "disetujui", "ditolak"): c["review"] = f["review"]
    job["name"] = c["title"]; save_job(jid)
    return jsonify(ok=True)


# ===== Motion Graphic: animasi dari kode (canvas) yang dirender jadi MP4 =====
MOTION_LOCK = threading.Lock()   # render frame berat: satu per satu


def _motion_key(p):
    return (load_cfg().get("api_key") or os.environ.get("ANTHROPIC_API_KEY")) if p.get("ai_mode") == "api" else "__claude_code__"


def _motion_html(jid, html, key, log):
    """Simpan versi animasi baru setelah lolos uji render (diperbaiki AI maks 2x bila error)."""
    job, d = JOBS[jid], os.path.join(JOBS_DIR, jid)
    for attempt in range(3):
        err = motion.check(html)
        if not err:
            break
        if attempt == 2:
            raise RuntimeError(f"Animasi masih error setelah diperbaiki: {err}")
        log(f"Animasi error ({err[:80]}), AI memperbaiki...")
        html = motion.revise_animation(html, "", key, log=lambda m, f=None: log(m), error=err)
    v = job.get("version", 0) + 1
    with open(os.path.join(d, f"anim_v{v}.html"), "w", encoding="utf-8") as f: f.write(html)
    job["version"] = v; save_job(jid)
    return html


def _motion_render(jid, html, log, lo):
    job, d = JOBS[jid], os.path.join(JOBS_DIR, jid)
    job["status"] = "render"
    out = f"motion_v{job['version']}.mp4"
    with MOTION_LOCK:
        motion.render(html, os.path.join(d, out), job.get("audio") and os.path.join(d, job["audio"]),
                      log=lambda m, p=None: log(m, None if p is None else lo + (100 - lo) * p))
    m = job.get("meta") or {}
    job["clips"] = [{"file": out, "start": 0, "end": job["duration"], "title": m.get("title") or job["name"], "viral": 0,
                     "text": "", "hashtags": m.get("hashtags", []), "description": m.get("description", ""),
                     "narration": " ".join(s["text"] for s in job["timeline"]), "opts": {"voice_mode": job["voice_mode"]}}]
    job["status"] = "selesai"; log("Selesai! Video siap.", 100)


def process_motion(jid, p):
    job, d = JOBS[jid], os.path.join(JOBS_DIR, jid)
    log, stage = _logger(jid)
    def body():
        key = _motion_key(p)
        job["status"] = "analisis"; log("Menyiapkan naskah...", 2)
        if p["voice_mode"] == "upload":
            log("Transkripsi narasi kamu...", 5)
            tl, dur = motion.timeline_from_audio(os.path.join(d, p["audio"]), p["lang"], log)
            meta = {"title": p["topic"] or (tl[0]["text"][:60] if tl else "Motion graphic")}
            job["audio"] = p["audio"]
        else:
            meta = motion.write_script(p["topic"], p["lang"], p["seconds"], key, log=log, own_script=p["script"])
            if p["voice_mode"] == "ai":
                tl, audio, dur = motion.timeline_from_tts(meta["scenes"], p["voice"], d, log)
                job["audio"] = os.path.basename(audio)
            else:
                tl, dur = motion.timeline_estimate(meta["scenes"])
        job.update(timeline=tl, duration=dur, meta={k: meta.get(k) for k in ("title", "description", "hashtags")})
        job["name"] = meta.get("title") or job["name"]; save_job(jid)
        log("Claude menulis kode animasi (biasanya 5-15 menit)...", 20)
        html = motion.write_animation(job["name"], tl, dur, p["aspect"], p["style"], p["notes"], key, log=stage(20, 45))
        log("Menguji animasi...", 45)
        html = _motion_html(jid, html, key, log)
        _motion_render(jid, html, log, 50)
    _run(jid, body)


def process_motion_revise(jid, instruction, p):
    job, d = JOBS[jid], os.path.join(JOBS_DIR, jid)
    log, stage = _logger(jid)
    def body():
        key = _motion_key(p)
        with open(os.path.join(d, f"anim_v{job['version']}.html"), encoding="utf-8") as f: html = f.read()
        job["status"] = "analisis"; log("Claude merevisi animasi...", 5)
        html = motion.revise_animation(html, instruction, key, log=stage(5, 38))
        html = _motion_html(jid, html, key, log)
        job.setdefault("revisions", []).append(instruction); save_job(jid)
        _motion_render(jid, html, log, 40)
    _run(jid, body)


@app.route("/api/motion/create", methods=["POST"])
def motion_create():
    import time
    f = request.form
    p = {"topic": f.get("topic", "").strip(), "script": f.get("script", "").strip(), "aspect": f.get("aspect", "9:16"),
         "style": f.get("style", "modern"), "seconds": int(f.get("seconds") or 30), "voice_mode": f.get("voice_mode", "ai"),
         "voice": f.get("voice", "id-ID-ArdiNeural"), "lang": f.get("lang", "id"), "ai_mode": f.get("ai_mode", "code"),
         "notes": f.get("notes", "").strip()}
    up = request.files.get("audio")
    if p["voice_mode"] == "upload" and not (up and up.filename):
        return jsonify(error="Pilih file audio narasi"), 400
    if not (p["topic"] or p["script"] or p["voice_mode"] == "upload"):
        return jsonify(error="Isi topik atau naskah"), 400
    jid = uuid.uuid4().hex[:10]; d = os.path.join(JOBS_DIR, jid); os.makedirs(d)
    if p["voice_mode"] == "upload":
        p["audio"] = "narasi_" + (secure_filename(up.filename) or "audio.mp3"); up.save(os.path.join(d, p["audio"]))
    JOBS[jid] = {"id": jid, "kind": "motion", "name": p["topic"] or "Motion graphic", "url": "", "platform": "motion",
                 "status": "antri", "log": "Menunggu...", "clips": [], "created": time.time(), "motion": p,
                 "voice_mode": p["voice_mode"], "aspect": p["aspect"]}
    save_job(jid)
    threading.Thread(target=process_motion, args=(jid, p), daemon=True).start()
    return jsonify(id=jid)


@app.route("/api/motion/<jid>/revise", methods=["POST"])
def motion_revise(jid):
    job = JOBS.get(jid)
    if not job or job.get("kind") != "motion" or not job.get("version"):
        return jsonify(error="Animasi belum ada"), 400
    if job["status"] not in ("selesai", "error"):
        return jsonify(error="Masih diproses"), 400
    ins = request.form.get("instruction", "").strip()
    if not ins: return jsonify(error="Tulis apa yang mau diubah"), 400
    job.update(status="antri", log="Menunggu...", progress=0); save_job(jid)
    threading.Thread(target=process_motion_revise, args=(jid, ins, job["motion"]), daemon=True).start()
    return jsonify(ok=True)


def process_motion_rerender(jid):
    """Render ulang versi animasi terakhir (mis. setelah render gagal), tanpa menulis ulang kode."""
    job, d = JOBS[jid], os.path.join(JOBS_DIR, jid)
    log, _ = _logger(jid)
    def body():
        key = _motion_key(job["motion"])
        with open(os.path.join(d, f"anim_v{job['version']}.html"), encoding="utf-8") as f: html = f.read()
        job["status"] = "analisis"; log("Menguji animasi...", 2)
        err = motion.check(html)
        if err:   # masih error walau sudah diberi pengaman: minta AI memperbaiki (jadi versi baru)
            log(f"Animasi error ({err[:80]}), AI memperbaiki...", 5)
            html = _motion_html(jid, motion.revise_animation(html, "", key, log=lambda m, f=None: log(m), error=err), key, log)
        _motion_render(jid, html, log, 10)
    _run(jid, body)


@app.route("/api/motion/<jid>/rerender", methods=["POST"])
def motion_rerender(jid):
    job = JOBS.get(jid)
    if not job or job.get("kind") != "motion" or not job.get("version"):
        return jsonify(error="Animasi belum ada"), 400
    if job["status"] not in ("selesai", "error"):
        return jsonify(error="Masih diproses"), 400
    job.update(status="antri", log="Menunggu...", progress=0); save_job(jid)
    threading.Thread(target=process_motion_rerender, args=(jid,), daemon=True).start()
    return jsonify(ok=True)


@app.route("/motion/<jid>/preview")
def motion_preview(jid):
    """Pratinjau langsung di browser: animasi diputar ulang terus, sinkron dengan audio narasi bila ada."""
    job = JOBS.get(jid)
    if not job or not job.get("version"): return "Belum ada animasi", 404
    with open(os.path.join(JOBS_DIR, jid, f"anim_v{job['version']}.html"), encoding="utf-8") as f: html = f.read()
    audio = f'<audio id="snd" src="/media/{jid}/{job["audio"]}"></audio>' if job.get("audio") else ""
    player = audio + """<style>html,body{margin:0;height:100%;background:#000;overflow:hidden}
canvas{width:100%!important;height:100%!important;object-fit:contain;display:block}
#pb{position:fixed;left:8px;bottom:8px;font:600 14px Segoe UI;color:#fff;background:#0009;border:0;border-radius:8px;padding:6px 10px;cursor:pointer}</style>
<button id="pb">▶ Putar</button><script>(function(){const s=document.getElementById('snd'),b=document.getElementById('pb');let t0=null,play=false;
function loop(now){if(play){let t=s?s.currentTime:(now-t0)/1000;if(!s&&t>=DURATION){t0=now;t=0}if(s&&s.ended){play=false;b.textContent='▶ Putar'}
try{renderFrame(Math.min(t,DURATION-0.001))}catch(e){}}requestAnimationFrame(loop)}
b.onclick=()=>{play=!play;b.textContent=play?'⏸ Jeda':'▶ Putar';if(s){play?s.play():s.pause()}else if(play)t0=performance.now()};
try{renderFrame(0.5)}catch(e){}requestAnimationFrame(loop)})()</script>"""
    html = motion.guard(html)
    page = html.replace("</body>", player + "</body>") if "</body>" in html else html + player
    # kode animasi ditulis AI: tanpa akses jaringan/API (hanya script & style inline + audio narasi)
    return page, 200, {"Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; "
                       "style-src 'unsafe-inline'; media-src 'self'; img-src data: blob:"}


@app.route("/api/jobs")
def jobs():
    return jsonify(sorted(JOBS.values(), key=lambda j: j["created"], reverse=True))


@app.route("/")
def index():
    jobs = sorted(JOBS.values(), key=lambda j: j["created"], reverse=True)
    return render_template("index.html", jobs=jobs, voices=clipper.VOICES, styles=motion.STYLES, has_key=bool(load_cfg().get("api_key") or os.environ.get("ANTHROPIC_API_KEY")))


@app.route("/api/create", methods=["POST"])
def create():
    import time
    jid = uuid.uuid4().hex[:10]
    d = os.path.join(JOBS_DIR, jid); os.makedirs(d)
    f = request.form
    opts = {
        "url": f.get("url", "").strip(),
        "model": f.get("model", "small"),
        "language": f.get("language") or None,
        "n_clips": int(f.get("n_clips", 5)),
        "min_len": int(f.get("min_len", 20)),
        "max_len": int(f.get("max_len", 60)),
        "vertical": f.get("vertical") == "on",
        "face": f.get("face") == "on",
        "subtitles": f.get("subtitles") == "on",
        "sub_color": f.get("sub_color", "kuning"),
        "ai_mode": f.get("ai_mode", "code"),
        "ai_model": f.get("ai_model", "claude-sonnet-5-5"),
        "voice_mode": f.get("voice_mode", "asli"),
        "voice": f.get("voice", "id-ID-ArdiNeural"),
        "credit_text": f.get("credit_text", "").strip(),
        "credit_opacity": int(f.get("credit_opacity") or 60),
        "credit_pos": f.get("credit_pos", "bawah"),
        "narr_source": f.get("narr_source", "transkrip"),
        "narr_lang": f.get("narr_lang", ""),
    }
    if f.get("api_key", "").strip():   # simpan key agar tidak perlu diisi ulang
        cfg = load_cfg(); cfg["api_key"] = f["api_key"].strip()
        with open(CFG, "w", encoding="utf-8") as fh: json.dump(cfg, fh)
    src, name = None, opts["url"]
    up = request.files.get("video")
    if up and up.filename:
        name = secure_filename(up.filename) or "video.mp4"
        src = os.path.join(d, "source_" + name); up.save(src)
    elif not opts["url"]:
        shutil.rmtree(d); return jsonify(error="Upload video atau isi link video"), 400
    JOBS[jid] = {"id": jid, "kind": "shorts", "name": name, "url": opts["url"] if not src else "",
                 "platform": clipper.platform_of(opts["url"] if not src else ""),
                 "status": "antri", "log": "Menunggu...",
                 "clips": [], "created": time.time(), "opts": opts}
    save_job(jid)
    threading.Thread(target=process, args=(jid, src, opts), daemon=True).start()
    return jsonify(id=jid)


@app.route("/api/job/<jid>")
def job(jid):
    return jsonify(JOBS.get(jid) or {}), (200 if jid in JOBS else 404)


DEFAULT_OPTS = {"model": "small", "language": None, "n_clips": 5, "min_len": 20, "max_len": 60,
                "vertical": True, "face": True, "subtitles": True, "sub_color": "kuning",
                "ai_mode": "code", "ai_model": "claude-sonnet-5-5", "voice_mode": "asli", "voice": "id-ID-ArdiNeural",
                "credit_text": "", "credit_opacity": 60, "credit_pos": "bawah", "narr_source": "transkrip", "narr_lang": ""}


def _prepare(jid):
    """Siapkan job lama/selesai untuk diproses lagi. Job lama belum menyimpan opts/src."""
    job, d = JOBS.get(jid), os.path.join(JOBS_DIR, jid)
    if not job or job.get("kind") == "download":
        return None, "Project tidak ditemukan"
    if job["status"] not in ("selesai", "error"):
        return None, "Project masih diproses"
    if not os.path.exists(os.path.join(d, "transcript.json")):
        return None, "Transkrip belum ada. Jalankan project ini dari awal."
    if not job.get("src") or not os.path.exists(job["src"]):
        url, files = job.get("url") or "", os.listdir(d)
        if clipper.platform_of(url) == "youtube":
            job["section_url"] = url
            job["src"] = next((os.path.join(d, f) for f in files if f.startswith("audio.")), None)
        else:
            job["src"] = next((os.path.join(d, f) for f in files if f.startswith("source")), None)
        if not job.get("section_url") and not job["src"]:
            return None, "File video sumber tidak ditemukan"
    if job.get("url") and "channel" not in job:   # project lama: ambil nama channel untuk {channel}
        job["channel"] = clipper.video_info(job["url"])[1]
    return job, None


def _form_opts(job, f):
    """Pengaturan lama project + yang diubah di form."""
    o = dict(DEFAULT_OPTS, **(job.get("opts") or {}))
    for k in ("n_clips", "min_len", "max_len"):
        if f.get(k): o[k] = int(f[k])
    for k in ("ai_mode", "sub_color", "voice_mode", "voice", "narr_source"):
        if f.get(k): o[k] = f[k]
    if "narr_lang" in f: o["narr_lang"] = f["narr_lang"]   # "" = otomatis (ikut suara)
    for k in ("vertical", "face", "subtitles"):
        if k in f: o[k] = f[k] in ("on", "true", "1")
    o["renarrate"] = f.get("renarrate") in ("on", "true", "1")
    if "credit_text" in f: o["credit_text"] = f["credit_text"].strip()   # boleh dikosongkan
    if f.get("credit_opacity"): o["credit_opacity"] = int(f["credit_opacity"])
    if f.get("credit_pos"): o["credit_pos"] = f["credit_pos"]
    o["channel"] = job.get("channel") or o.get("channel") or ""
    return o


@app.route("/api/job/<jid>/more", methods=["POST"])
def more(jid):
    job, err = _prepare(jid)
    if err: return jsonify(error=err), 400
    opts = _form_opts(job, request.form)
    job.update(status="antri", log="Menunggu...", progress=0); save_job(jid)
    threading.Thread(target=process_more, args=(jid, opts), daemon=True).start()
    return jsonify(ok=True)


@app.route("/api/job/<jid>/rerender", methods=["POST"])
def rerender(jid):
    job, err = _prepare(jid)
    if err: return jsonify(error=err), 400
    if not job["clips"]: return jsonify(error="Belum ada klip untuk dirender ulang"), 400
    opts = _form_opts(job, request.form)
    files = request.form.getlist("files")
    job.update(status="antri", log="Menunggu...", progress=0); save_job(jid)
    threading.Thread(target=process_rerender, args=(jid, opts, files), daemon=True).start()
    return jsonify(ok=True)


@app.route("/api/job/<jid>", methods=["DELETE"])
def delete(jid):
    JOBS.pop(jid, None); shutil.rmtree(os.path.join(JOBS_DIR, jid), ignore_errors=True)
    return jsonify(ok=True)


@app.route("/media/<jid>/<path:fn>")
def media(jid, fn):
    return send_from_directory(os.path.join(JOBS_DIR, jid), fn,
                               as_attachment=request.args.get("dl") == "1")


if __name__ == "__main__":
    load_jobs()
    threading.Thread(target=agent_loop, daemon=True).start()
    # 0.0.0.0 = bisa dibuka dari HP/laptop lain di jaringan Wi-Fi yang sama.
    # Set SHORTSCLIPS_HOST=127.0.0.1 untuk membatasi ke komputer ini saja.
    host = os.environ.get("SHORTSCLIPS_HOST", "0.0.0.0")
    print("Buka http://127.0.0.1:5000")
    if host == "0.0.0.0":
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("8.8.8.8", 80))
            print(f"Dari HP / device lain (Wi-Fi sama): http://{s.getsockname()[0]}:5000")
            s.close()
        except OSError:
            pass
    app.run(host=host, port=5000, debug=False, threaded=True)
