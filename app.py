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

from flask import Flask, render_template, request, jsonify, send_from_directory, Response
from urllib.parse import quote
from werkzeug.utils import secure_filename
import clipper, natural, research, auto, motion, rmotion, miniature, documentary as doc, youtube_up as yt

BASE = os.path.dirname(os.path.abspath(__file__))
JOBS_DIR = os.path.join(BASE, "jobs")
os.makedirs(JOBS_DIR, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 ** 3
LOG_DIR = os.path.join(BASE, "logs")


@app.errorhandler(Exception)
def on_error(e):
    """Error tak terduga: catat traceback ke logs/error.log, dan untuk /api/ kirim pesan aslinya
    (bukan 'INTERNAL SERVER ERROR') agar terlihat di popup."""
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    import time
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(os.path.join(LOG_DIR, "error.log"), "a", encoding="utf-8") as f:
        f.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {request.method} {request.path}\n{traceback.format_exc()}")
    traceback.print_exc()
    msg = f"{type(e).__name__}: {e}"
    if request.path.startswith("/api/"):
        return jsonify(error=msg), 500
    return f"Terjadi error: {msg} (detail di logs/error.log)", 500
JOBS = {}
CFG = os.path.join(BASE, "config.json")


def load_cfg():
    try:
        with open(CFG, encoding="utf-8") as f: return json.load(f)
    except Exception: return {}


def write_json_atomic(path, data):
    """Tulis ke file sementara lalu ganti sekaligus: kalau server mati di tengah penulisan,
    file lama tetap utuh (tidak jadi 0 byte / setengah tertulis)."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def save_job(jid):
    write_json_atomic(os.path.join(JOBS_DIR, jid, "job.json"), JOBS[jid])


def load_jobs():
    for jid in os.listdir(JOBS_DIR):
        p = os.path.join(JOBS_DIR, jid, "job.json")
        if not os.path.exists(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                JOBS[jid] = json.load(f)
        except (ValueError, OSError) as e:
            # satu file rusak tidak boleh membuat server gagal start; simpan cadangannya untuk dicek
            os.replace(p, p + ".rusak")
            print(f"[peringatan] {p} rusak ({e}); dilewati, disimpan sebagai job.json.rusak")
            continue
        if JOBS[jid].get("status") not in ("selesai", "error", "naskah"):   # naskah tetap menunggu revisi
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
    key = _shorts_key(opts) if opts.get("ai_mode", "code") in ("code", "api", "gemini") else None
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


def _shorts_key(opts):
    """Mesin AI fitur Shorts: Claude Code (default), Claude API, atau Gemini API (key Gemini Dokumenter)."""
    mode = opts.get("ai_mode", "code")
    if mode == "gemini":
        k = load_cfg().get("gemini_key") or os.environ.get("GEMINI_API_KEY")
        if not k: raise RuntimeError("API key Gemini belum diisi (isi di editor Dokumenter → Gemini)")
        return "gemini:" + k
    if mode == "api":
        return opts.get("api_key") or load_cfg().get("api_key") or os.environ.get("ANTHROPIC_API_KEY")
    return "__claude_code__"


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
    key = _shorts_key(opts)
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
    key = _shorts_key(opts)
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
        elif opts.get("transcript_ready") and os.path.exists(os.path.join(d, "transcript.json")):
            # dari menu Dokumenter: timing kata sudah ada dari alignment, tanpa Whisper ulang
            with open(os.path.join(d, "transcript.json"), encoding="utf-8") as f: tr = json.load(f)
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


STYLE_KEYS = ("vertical", "face", "frame", "hook", "jumpcut", "punch", "sfx", "subtitles", "sub_color", "voice_mode", "voice",
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
    write_json_atomic(CFG, cfg)


def auto_cfg():
    return dict(AUTO_DEFAULT, **(load_cfg().get("auto") or {}))


def auto_save(**kw):
    cfg = load_cfg(); a = dict(AUTO_DEFAULT, **(cfg.get("auto") or {})); a.update(kw)
    cfg["auto"] = a; save_cfg(cfg); return a


def _auto_key(a):
    return _shorts_key({"ai_mode": a.get("ai_mode", "code")})


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
                             "opts": {"voice_mode": "ai"}, "review": "menunggu",
                             "flag": "; ".join(x for x in (s.get("cek") or []) if isinstance(x, str) and x.strip())}]
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
    if p.get("ai_mode") == "gemini":
        c = load_cfg()   # key khusus Motion (akun lain/Pro); kosong = pakai key Gemini Dokumenter
        k = c.get("motion_gemini_key") or c.get("gemini_key") or os.environ.get("GEMINI_API_KEY")
        if not k: raise RuntimeError("API key Gemini belum diisi (isi di form Motion)")
        return "gemini:" + k
    return (load_cfg().get("api_key") or os.environ.get("ANTHROPIC_API_KEY")) if p.get("ai_mode") == "api" else "__claude_code__"


def _photo_paths(jid):
    """Path foto upload pengguna untuk job motion (urut), atau None."""
    ph = (JOBS.get(jid) or {}).get("photos") or []
    return [os.path.join(JOBS_DIR, jid, "photos", x["file"]) for x in ph] or None


def _motion_html(jid, html, key, log):
    """Simpan versi animasi baru setelah lolos uji render (diperbaiki AI maks 2x bila error)."""
    job, d = JOBS[jid], os.path.join(JOBS_DIR, jid)
    for attempt in range(3):
        err = motion.check(html, photos=_photo_paths(jid))
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


MOTION_SFX = {"tipis": 0.14, "sedang": 0.28}   # volume efek suara terhadap narasi


def _motion_sfx_audio(jid):
    """Narasi + efek suara tipis (whoosh tiap ganti adegan, pop saat angka diucapkan). None = pakai audio apa adanya."""
    job, d = JOBS[jid], os.path.join(JOBS_DIR, jid)
    vol = MOTION_SFX.get((job.get("motion") or {}).get("sfx", "tipis"))
    narr = job.get("audio") and os.path.join(d, job["audio"])
    tl, dur = job.get("timeline") or [], float(job.get("duration") or 0)
    if not vol or not tl or dur <= 0:
        return narr
    ev = [(max(sc["start"] - 0.3, 0), "whoosh", vol) for sc in tl[1:]]
    for sc in tl:   # pop halus saat angka/nomor diucapkan
        for w in sc.get("words") or []:
            if re.search(r"\d", w["w"]): ev.append((w["t"], "pop", vol * 0.6))
    trk = doc.sfx_track(ev, dur, os.path.join(d, "_sfx.wav"))
    out = os.path.join(d, "_mix.m4a")
    if narr:
        clipper.run(["ffmpeg", "-y", "-v", "error", "-i", narr, "-i", trk, "-filter_complex",
                     "[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]", "-map", "[a]",
                     "-c:a", "aac", "-b:a", "160k", out])
    else:
        clipper.run(["ffmpeg", "-y", "-v", "error", "-i", trk, "-c:a", "aac", "-b:a", "160k", out])
    return out


def _motion_render(jid, html, log, lo):
    job, d = JOBS[jid], os.path.join(JOBS_DIR, jid)
    job["status"] = "render"
    out = f"motion_v{job['version']}.mp4"
    try:
        audio = _motion_sfx_audio(jid)
    except Exception as e:   # efek suara gagal: tetap render dengan narasi saja
        log(f"Efek suara dilewati: {str(e)[:80]}"); audio = job.get("audio") and os.path.join(d, job["audio"])
    with MOTION_LOCK:
        motion.render(html, os.path.join(d, out), audio, label=(job.get("motion") or {}).get("label", ""),
                      log=lambda m, p=None: log(m, None if p is None else lo + (100 - lo) * p), photos=_photo_paths(jid))
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
            brief = p["topic"]
            if job.get("photos"):   # naskah dirancang di sekitar foto upload
                brief += "\nFoto yang akan tampil di video (rancang adegan yang memakainya, sesuai urutan): " + ", ".join(x["name"] for x in job["photos"])
            if p.get("engine") == "remotion":
                meta = rmotion.write_script(brief, p["lang"], p["seconds"], key, log=log, own_script=p["script"])
            else:
                meta = motion.write_script(brief, p["lang"], p["seconds"], key, log=log, own_script=p["script"], style=p["style"])
            if p.get("review"):   # berhenti: pengguna cek & revisi naskah dulu, lalu /script melanjutkan
                job.update(draft=meta, status="naskah", progress=15)
                job["name"] = meta.get("title") or job["name"]
                log("Naskah siap. Cek & perbaiki kata-katanya, lalu klik Lanjut.")
                return
            tl, dur = _motion_voice(jid, p, meta, log)
        _motion_build(jid, p, key, meta, tl, dur, log, stage)
    _run(jid, body)


def _motion_voice(jid, p, meta, log):
    job, d = JOBS[jid], os.path.join(JOBS_DIR, jid)
    if p["voice_mode"] == "eleven_web" and job.get("audio"):   # audio dari ekstensi ElevenLabs
        log("Mencocokkan suara ElevenLabs dengan naskah...", 18)
        tl, dur = motion.timeline_from_audio_scenes(os.path.join(d, job["audio"]), meta["scenes"], p["lang"], log)
    elif p["voice_mode"] == "ai":
        tl, audio, dur = motion.timeline_from_tts(meta["scenes"], p["voice"], d, log)
        job["audio"] = os.path.basename(audio)
    else:
        tl, dur = motion.timeline_estimate(meta["scenes"])
    return tl, dur


def process_motion_continue(jid, p):
    """Lanjutan setelah naskah disetujui: suara -> kode animasi -> uji -> render."""
    job = JOBS[jid]
    log, stage = _logger(jid)
    def body():
        key = _motion_key(p)
        meta = job.pop("draft")
        job["status"] = "analisis"; log("Membuat suara dari naskah...", 16)
        tl, dur = _motion_voice(jid, p, meta, log)
        _motion_build(jid, p, key, meta, tl, dur, log, stage)
    _run(jid, body)


def _motion_build(jid, p, key, meta, tl, dur, log, stage):
    job = JOBS[jid]
    job.update(timeline=tl, duration=dur, meta={k: meta.get(k) for k in ("title", "description", "hashtags")})
    job["name"] = meta.get("title") or job["name"]; save_job(jid)
    if p.get("engine") == "remotion":   # template Remotion: tanpa kode animasi AI
        job["status"] = "render"; job["scenes"] = meta["scenes"]
        out = f"remotion_v{job.get('rversion', 0) + 1}.mp4"
        brand = {"name": p.get("brand") or "ShortsClips", "accent": "#FFD84D"}
        with MOTION_LOCK:
            rmotion.render(os.path.join(JOBS_DIR, jid), meta["scenes"], tl, dur, job.get("audio"), brand, out,
                           log=stage(25, 100))
        job["rversion"] = job.get("rversion", 0) + 1
        job["clips"] = [{"file": out, "start": 0, "end": dur, "title": meta.get("title") or job["name"], "viral": 0,
                         "text": "", "hashtags": meta.get("hashtags", []), "description": meta.get("description", ""),
                         "narration": " ".join(s["text"] for s in tl), "opts": {"voice_mode": job["voice_mode"]}}]
        job["status"] = "selesai"; log("Selesai! Video siap.", 100)
        return
    log("AI menulis kode animasi...", 20)
    html = motion.write_animation(job["name"], tl, dur, p["aspect"], p["style"], p["notes"], key, log=stage(20, 45),
                                  photos=job.get("photos"))
    log("Menguji animasi...", 45)
    html = _motion_html(jid, html, key, log)
    _motion_render(jid, html, log, 50)


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
         "notes": f.get("notes", "").strip(), "review": f.get("review") == "1",
         "engine": "remotion" if f.get("engine") == "remotion" else "code", "brand": f.get("brand", "").strip()[:30],
         "label": f.get("label", "").strip()[:60],
         "sfx": f.get("sfx", "tipis") if f.get("sfx", "tipis") in ("tipis", "sedang", "none") else "tipis"}
    if p["engine"] == "remotion":
        if p["voice_mode"] == "upload":
            return jsonify(error="Mesin Remotion belum mendukung upload narasi; pilih Suara AI atau Tanpa suara"), 400
        p["aspect"] = "9:16"   # template Remotion saat ini khusus vertikal
        cfg = load_cfg()
        if p["brand"] and cfg.get("remotion_brand") != p["brand"]:
            cfg["remotion_brand"] = p["brand"]; save_cfg(cfg)
        p["brand"] = p["brand"] or cfg.get("remotion_brand") or "ShortsClips"
    if f.get("motion_gemini_key", "").strip():   # simpan key Gemini khusus Motion (tidak ikut ke job.json)
        cfg = load_cfg(); cfg["motion_gemini_key"] = f["motion_gemini_key"].strip(); save_cfg(cfg)
    up = request.files.get("audio")
    if p["voice_mode"] == "upload" and not (up and up.filename):
        return jsonify(error="Pilih file audio narasi"), 400
    pics = [x for x in request.files.getlist("photos") if x and x.filename][:20]   # foto untuk digabung ke animasi
    if not (p["topic"] or p["script"] or p["voice_mode"] == "upload" or pics):
        return jsonify(error="Isi topik atau naskah"), 400
    if pics and len([t for t in p["topic"].splitlines() if t.strip()]) > 1:
        return jsonify(error="Foto hanya untuk 1 video; untuk batch kosongkan foto"), 400
    if pics and not p["topic"] and not p["script"]:
        p["topic"] = "Video dari foto-foto ini"
    topics = [t.strip() for t in p["topic"].splitlines() if t.strip()] or [""]
    if p["voice_mode"] == "upload" or p["script"]:
        topics = [" ".join(topics)]          # naskah/audio sendiri = 1 video
    if len(topics) > 10:
        return jsonify(error="Maksimal 10 topik per batch"), 400
    ids = []
    for t in topics:
        q = dict(p, topic=t)
        jid = uuid.uuid4().hex[:10]; d = os.path.join(JOBS_DIR, jid); os.makedirs(d)
        if q["voice_mode"] == "upload":
            q["audio"] = "narasi_" + (secure_filename(up.filename) or "audio.mp3"); up.save(os.path.join(d, q["audio"]))
        photos = []
        if pics:
            pd = os.path.join(d, "photos"); os.makedirs(pd)
            for i, x in enumerate(pics):
                raw = os.path.join(pd, f"_raw{i}" + os.path.splitext(x.filename)[1].lower()); x.save(raw)
                fn = f"foto_{i + 1:02d}.jpg"
                try:
                    w, h = motion.prepare_photo(raw, os.path.join(pd, fn))
                except Exception:
                    shutil.rmtree(d, ignore_errors=True)
                    return jsonify(error=f"Foto tidak bisa dibaca: {x.filename}"), 400
                finally:
                    os.path.exists(raw) and os.remove(raw)
                photos.append({"i": i, "file": fn, "name": os.path.splitext(x.filename)[0][:60], "w": w, "h": h})
        JOBS[jid] = {"photos": photos,"id": jid, "kind": "motion", "name": t or "Motion graphic", "url": "", "platform": "motion",
                     "status": "antri", "log": "Menunggu giliran..." if len(topics) > 1 else "Menunggu...", "clips": [],
                     "created": time.time() + len(ids) * 0.001, "motion": q, "voice_mode": q["voice_mode"], "aspect": q["aspect"]}
        save_job(jid)
        threading.Thread(target=_motion_slot, args=(jid, q), daemon=True).start()
        ids.append(jid)
    return jsonify(id=ids[0], ids=ids, parallel=MOTION_PARALLEL)


MOTION_PARALLEL = 2                      # berapa video batch dikerjakan Claude bersamaan
MOTION_SEM = threading.Semaphore(MOTION_PARALLEL)


def _motion_slot(jid, p, fn=None):
    with MOTION_SEM:
        (fn or process_motion)(jid, p)


def motion_srt_text(tl, max_words=7):
    """SRT dari timeline Motion: pakai waktu per kata bila ada (suara AI/upload), selain itu bagi rata per adegan (tanpa suara)."""
    def ts(x):
        ms = int(round(max(x, 0) * 1000)); return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    cues = []
    for sc in tl:
        ws = sc.get("words")
        if ws:
            for i in range(0, len(ws), max_words):
                ch = ws[i:i + max_words]
                end = ws[i + max_words]["t"] if i + max_words < len(ws) else sc["end"]
                cues.append((ch[0]["t"], min(end, (ch[-1].get("e") or ch[-1]["t"] + 0.4) + 0.5), " ".join(w["w"] for w in ch)))
        else:
            words = sc["text"].split()
            n = max(1, -(-len(words) // max_words)); step = (sc["end"] - sc["start"]) / n
            for k in range(n):
                cues.append((sc["start"] + k * step, sc["start"] + (k + 1) * step, " ".join(words[k * max_words:(k + 1) * max_words])))
    nl = chr(10)
    return nl.join(f"{i}{nl}{ts(a)} --> {ts(b)}{nl}{t}{nl}" for i, (a, b, t) in enumerate(cues, 1) if t)


@app.route("/motion/<jid>/srt")
def motion_srt(jid):
    job = JOBS.get(jid)
    if not job or not job.get("timeline"): return "Belum ada naskah bertimeline", 404
    name = secure_filename(job.get("name") or jid) or jid
    return Response(motion_srt_text(job["timeline"]), mimetype="application/x-subrip",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}.srt"})


@app.route("/api/naturalize", methods=["POST"])
def api_naturalize():
    """Rapikan bahasa narasi (Claude langganan, cadangan Gemini). Body: {texts:[...], lang}."""
    data = request.get_json(silent=True) or {}
    texts = [str(t) for t in data.get("texts") or []]
    if not texts or len(texts) > 200:
        return jsonify(error="Tidak ada teks"), 400
    c = load_cfg()
    key = c.get("gemini_key") or c.get("motion_gemini_key") or os.environ.get("GEMINI_API_KEY")
    try:
        out, engine = natural.naturalize(texts, data.get("lang") or "id", key, log=lambda *a, **k: None)
    except Exception as e:
        return jsonify(error=str(e)[:300]), 500
    return jsonify(texts=out, engine=engine)


@app.route("/api/motion/<jid>/script", methods=["POST"])
def motion_script(jid):
    """Simpan naskah hasil revisi pengguna lalu lanjutkan ke suara & animasi."""
    job = JOBS.get(jid)
    if not job or job.get("kind") != "motion" or job.get("status") != "naskah" or not job.get("draft"):
        return jsonify(error="Naskah tidak sedang menunggu revisi"), 400
    data = request.get_json(silent=True) or {}
    texts = [str(t).strip() for t in data.get("scenes") or []]
    scenes = job["draft"].get("scenes") or []
    if len(texts) != len(scenes) or not all(texts):
        return jsonify(error="Teks adegan tidak boleh kosong"), 400
    for sc, t in zip(scenes, texts):
        sc["text"] = t
    if str(data.get("title", "")).strip():
        job["draft"]["title"] = job["name"] = data["title"].strip()
    voice = str(data.get("voice") or "").strip()
    if voice == "eleven_web":   # naskah disimpan; suara datang dari ekstensi ElevenLabs (/eleven_audio melanjutkan)
        job["eleven_wait"] = True
        job.update(log="Menunggu suara dari ekstensi ElevenLabs..."); save_job(jid)
        return jsonify(ok=True, wait="eleven")
    job.pop("eleven_wait", None)
    if voice == "none":
        job["motion"]["voice_mode"] = job["voice_mode"] = "none"
    elif voice:
        job["motion"].update(voice=voice, voice_mode="ai"); job["voice_mode"] = "ai"
    job.update(status="antri", log="Menunggu giliran...", progress=15); save_job(jid)
    threading.Thread(target=_motion_slot, args=(jid, job["motion"], process_motion_continue), daemon=True).start()
    return jsonify(ok=True)


@app.route("/api/eleven/queue")
def eleven_queue():
    """Untuk ekstensi ElevenLabs: Shorts Motion yang naskahnya menunggu (status NASKAH)."""
    out = [{"id": j["id"], "name": j.get("name", ""), "lang": (j.get("motion") or {}).get("lang", "en"),
            "text": " ".join(s.get("text", "") for s in j["draft"].get("scenes") or []), "wait": bool(j.get("eleven_wait"))}
           for j in sorted(JOBS.values(), key=lambda x: (not x.get("eleven_wait"), x.get("created", 0)))
           if j.get("kind") == "motion" and j.get("status") == "naskah" and j.get("draft")]
    return jsonify(jobs=out)


@app.route("/api/motion/<jid>/eleven_audio", methods=["POST"])
def motion_eleven_audio(jid):
    """Terima MP3 dari ekstensi ElevenLabs (base64), lalu lanjutkan job: waktu kata dari audio -> animasi -> render."""
    import base64
    job = JOBS.get(jid)
    if not job or job.get("kind") != "motion" or job.get("status") != "naskah" or not job.get("draft"):
        return jsonify(error="Job tidak sedang di tahap NASKAH"), 400
    data = request.get_json(silent=True) or {}
    try:
        raw = base64.b64decode(data.get("audio") or "")
    except Exception:
        return jsonify(error="Audio rusak"), 400
    if len(raw) < 2000: return jsonify(error="Audio kosong/terlalu pendek"), 400
    job["audio"] = "narasi_eleven.mp3"
    with open(os.path.join(JOBS_DIR, jid, job["audio"]), "wb") as f: f.write(raw)
    job["motion"]["voice_mode"] = job["voice_mode"] = "eleven_web"; job.pop("eleven_wait", None)
    job.update(status="antri", log="Suara ElevenLabs diterima, menunggu giliran...", progress=15); save_job(jid)
    threading.Thread(target=_motion_slot, args=(jid, job["motion"], process_motion_continue), daemon=True).start()
    return jsonify(ok=True)


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
        err = motion.check(html, photos=_photo_paths(jid))
        if err:   # masih error walau sudah diberi pengaman: minta AI memperbaiki (jadi versi baru)
            log(f"Animasi error ({err[:80]}), AI memperbaiki...", 5)
            html = _motion_html(jid, motion.revise_animation(html, "", key, log=lambda m, f=None: log(m), error=err), key, log)
        _motion_render(jid, html, log, 10)
    _run(jid, body)


def process_motion_revoice(jid, mode, voice):
    """Render ulang dengan suara/narasi lain. 'none' = animasi sama tanpa audio; 'ai'/'upload' = waktu berubah,
    jadi kode animasi ditulis ulang mengikuti timeline baru."""
    job, d = JOBS[jid], os.path.join(JOBS_DIR, jid)
    log, stage = _logger(jid)
    def body():
        p = job["motion"]
        if mode == "none":
            job.update(audio=None, voice_mode="none"); p["voice_mode"] = "none"
            with open(os.path.join(d, f"anim_v{job['version']}.html"), encoding="utf-8") as f: html = f.read()
            _motion_render(jid, html, log, 5)
            return
        key = _motion_key(p)
        scenes = [{"text": s["text"], "visual": s.get("visual", "")} for s in job["timeline"]]
        job["status"] = "analisis"
        if mode == "ai":
            log("Membuat suara baru...", 3)
            tl, audio, dur = motion.timeline_from_tts(scenes, voice, d, log)
            job["audio"] = os.path.basename(audio); p["voice"] = voice
        else:
            log("Transkripsi narasi kamu...", 3)
            tl, dur = motion.timeline_from_audio(os.path.join(d, job["audio"]), p["lang"], log)
        p["voice_mode"] = job["voice_mode"] = mode
        meta = dict(job.get("meta") or {}, scenes=scenes)
        _motion_build(jid, p, key, meta, tl, dur, log, stage)
    _run(jid, body)


@app.route("/api/motion/<jid>/rerender", methods=["POST"])
def motion_rerender(jid):
    job = JOBS.get(jid)
    if not job or job.get("kind") != "motion" or not job.get("version"):
        return jsonify(error="Animasi belum ada"), 400
    if job["status"] not in ("selesai", "error"):
        return jsonify(error="Masih diproses"), 400
    mode = request.form.get("voice_mode", "keep")
    if mode not in ("keep", "ai", "none", "upload"):
        return jsonify(error="Pilihan suara tidak dikenal"), 400
    if mode != "keep" and not job.get("timeline"):
        return jsonify(error="Timeline naskah tidak ada"), 400
    if request.form.get("sfx") in ("tipis", "sedang", "none"): job["motion"]["sfx"] = request.form["sfx"]
    if "label" in request.form: job["motion"]["label"] = request.form["label"].strip()[:60]
    voice = request.form.get("voice", "").strip() or job["motion"].get("voice") or "en-US-GuyNeural"
    if mode == "upload":
        up = request.files.get("audio")
        if not (up and up.filename): return jsonify(error="Pilih file audio narasi"), 400
        job["audio"] = "narasi_" + (secure_filename(up.filename) or "audio.mp3")
        up.save(os.path.join(JOBS_DIR, jid, job["audio"]))
    job.update(status="antri", log="Menunggu...", progress=0); save_job(jid)
    if mode == "keep":
        threading.Thread(target=process_motion_rerender, args=(jid,), daemon=True).start()
    else:
        threading.Thread(target=_motion_slot, args=(jid, mode, lambda j, m: process_motion_revoice(j, m, voice)),
                         daemon=True).start()
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
    html = motion.with_photos(html, _photo_paths(jid))
    page = html.replace("</body>", player + "</body>") if "</body>" in html else html + player
    # kode animasi ditulis AI: tanpa akses jaringan/API (hanya script & style inline + audio narasi)
    return page, 200, {"Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; "
                       "style-src 'unsafe-inline'; media-src 'self'; img-src data: blob:"}


# ===== Giant hand miniature: rencana fase + prompt Veo -> upload klip -> timelapse + ASMR =====
def process_mini_plan(jid, p):
    job = JOBS[jid]
    log, stage = _logger(jid)
    def body():
        job["status"] = "analisis"; log("Menyusun rencana...", 5)
        plan = miniature.write_plan(p["subject"], p["phases"], p["lang"], p["notes"], _motion_key(p), log=stage(5, 95))
        job.update(plan=plan, name=plan["title"]); job["status"] = "selesai"
        log("Rencana siap. Buat klip tiap fase di Flow (Veo), lalu upload di sini.", 100)
    _run(jid, body)


def process_mini_render(jid, files, sec, audio):
    job, d = JOBS[jid], os.path.join(JOBS_DIR, jid)
    log, stage = _logger(jid)
    def body():
        job["status"] = "render"
        v = job.get("version", 0) + 1
        out = f"miniatur_v{v}.mp4"
        with MOTION_LOCK:
            miniature.render([os.path.join(d, f) for f in files], job["plan"]["phases"], os.path.join(d, out), sec, audio,
                             log=stage(2, 100))
        pl = job["plan"]
        job["version"] = v
        job["clips"] = [{"file": out, "start": 0, "end": clipper.get_duration(os.path.join(d, out)), "title": pl["title"],
                         "viral": 0, "text": "", "hashtags": pl.get("hashtags", []), "description": pl.get("caption", ""),
                         "narration": "", "opts": {}}]
        job["status"] = "selesai"; log("Selesai! Video siap.", 100)
    _run(jid, body)


@app.route("/api/mini/create", methods=["POST"])
def mini_create():
    import time
    f = request.form
    p = {"subject": f.get("subject", "").strip(), "phases": max(2, min(6, int(f.get("phases") or 3))),
         "lang": f.get("lang", "id"), "notes": f.get("notes", "").strip(), "ai_mode": f.get("ai_mode", "code")}
    if not p["subject"]:
        return jsonify(error="Isi subjek miniatur"), 400
    jid = uuid.uuid4().hex[:10]; os.makedirs(os.path.join(JOBS_DIR, jid))
    JOBS[jid] = {"id": jid, "kind": "mini", "name": p["subject"], "url": "", "platform": "miniatur", "status": "antri",
                 "log": "Menunggu...", "clips": [], "created": time.time(), "mini": p}
    save_job(jid)
    threading.Thread(target=process_mini_plan, args=(jid, p), daemon=True).start()
    return jsonify(id=jid)


@app.route("/api/mini/<jid>/render", methods=["POST"])
def mini_render(jid):
    job = JOBS.get(jid)
    if not job or job.get("kind") != "mini" or not job.get("plan"):
        return jsonify(error="Rencana belum ada"), 400
    if job["status"] not in ("selesai", "error"):
        return jsonify(error="Masih diproses"), 400
    d = os.path.join(JOBS_DIR, jid)
    ups = [u for u in request.files.getlist("clips") if u and u.filename]
    if ups:   # klip baru menggantikan yang lama; urutan = urutan nama file (fase 1, 2, 3 ...)
        ups.sort(key=lambda u: doc.natural_key(u.filename))
        names = []
        for i, u in enumerate(ups):
            n = f"fase_{i + 1:02d}{os.path.splitext(secure_filename(u.filename))[1] or '.mp4'}"
            u.save(os.path.join(d, n)); names.append(n)
        job["mini_files"] = names
    if not job.get("mini_files"):
        return jsonify(error="Upload klip video tiap fase"), 400
    sec = float(request.form.get("sec") or 0)
    audio = request.form.get("audio", "mix")
    job.update(status="antri", log="Menunggu...", progress=0, mini_opts={"sec": sec, "audio": audio}); save_job(jid)
    threading.Thread(target=process_mini_render, args=(jid, job["mini_files"], sec, audio), daemon=True).start()
    return jsonify(ok=True)


# ===== Dokumenter panjang: naskah -> (suara CapCut + gambar Flow) -> alignment -> render bertema =====
DOC_STATE = {}                    # pid -> {"status", "log", "progress"} (proses yang sedang berjalan)
DOC_LOCK = threading.Lock()       # render berat: satu per satu


def _pdir(pid):
    p = os.path.join(doc.DOCS_DIR, secure_filename(pid))
    if not os.path.isfile(os.path.join(p, "script.json")):
        raise FileNotFoundError("Proyek tidak ditemukan")
    return p


def _doc_run(pid, fn, label):
    st = DOC_STATE[pid] = {"status": "jalan", "log": label, "progress": 0}
    def log(m, f=None):
        st["log"] = m
        if f is not None: st["progress"] = round(f * 100)
        print(f"[doc {pid}] {m}")
    def body():
        try:
            fn(log)
            st.update(status="selesai", progress=100)
        except Exception as e:
            traceback.print_exc()
            st.update(status="error", log=str(e))
    threading.Thread(target=body, daemon=True).start()


def _doc_summary(pid):
    p = os.path.join(doc.DOCS_DIR, pid)
    s = doc.load(p)
    out = os.path.join(p, "output")
    words = sum(len(sc["text"].split()) for sc in s["scenes"])
    return {"id": pid, "topic": s.get("topic", pid), "aspect": s.get("aspect", "16:9"), "pair": s.get("pair"),
            "scenes": len(s["scenes"]), "words": words,
            "images": len(doc.list_images(p)), "voice": os.path.exists(os.path.join(p, "voice.mp3")),
            "final": os.path.exists(os.path.join(out, "final.mp4")), "theme_used": s.get("theme_used"),
            "modified": os.path.getmtime(os.path.join(p, "script.json")), "state": DOC_STATE.get(pid)}


@app.route("/api/docs")
def docs_list():
    os.makedirs(doc.DOCS_DIR, exist_ok=True)
    ids = [d for d in os.listdir(doc.DOCS_DIR) if os.path.isfile(os.path.join(doc.DOCS_DIR, d, "script.json"))]
    out = []
    for i in ids:   # satu proyek dengan script.json rusak tidak boleh membuat seluruh daftar gagal
        try:
            out.append(_doc_summary(i))
        except Exception as e:
            out.append({"id": i, "topic": f"⚠️ {i} (script.json rusak: {str(e)[:60]})", "scenes": 0, "words": 0, "images": 0,
                        "voice": False, "final": False, "theme_used": None, "state": None,
                        "modified": os.path.getmtime(os.path.join(doc.DOCS_DIR, i, "script.json"))})
    return jsonify(sorted(out, key=lambda x: -x["modified"]))


def _doc_pid(topic, short):
    import time
    return (secure_filename(topic.lower().replace(" ", "_"))[:40] or "dokumenter") + time.strftime("_%m%d_%H%M") + ("_shorts" if short else "")


def _doc_key(mode):
    """Mesin AI untuk naskah & prompt Dokumenter: Claude Code (default), Claude API, atau Gemini (key Gemini Dokumenter)."""
    if mode == "gemini":
        k = load_cfg().get("gemini_key") or os.environ.get("GEMINI_API_KEY")
        if not k: raise RuntimeError("API key Gemini belum diisi (isi di langkah gambar Gemini di editor Dokumenter)")
        return "gemini:" + k
    return (load_cfg().get("api_key") or os.environ.get("ANTHROPIC_API_KEY")) if mode == "api" else "__claude_code__"


def _new_doc(topic, o, short, minutes, pair=None, pid=None):
    """Buat proyek dokumenter baru (naskah ditulis AI di latar belakang). o = pengaturan form.
    pair = id proyek pasangannya (versi panjang <-> Shorts dari ide yang sama, masing-masing dengan gambar sendiri)."""
    pid = pid or _doc_pid(topic, short)
    p = os.path.join(doc.DOCS_DIR, pid)
    if os.path.exists(p): raise RuntimeError("Proyek dengan nama ini baru saja dibuat, tunggu 1 menit")
    os.makedirs(os.path.join(p, "images"))
    doc.save(p, {"id": pid, "topic": topic, "scenes": [], "metadata": {}, "theme": o.get("theme", "auto"), "pair": pair,
                 "aspect": "9:16" if short else "16:9"})
    key = _doc_key(o.get("ai_mode"))
    def work(log):
        s = doc.generate_script(topic, minutes, o.get("visual", "cartoon"),
                                int(o.get("images_per_scene") or 1), o.get("hint", ""), key, log=log,
                                lang=o.get("lang", "en"), fmt=o.get("format", ""), tone=o.get("tone", ""),
                                aspect="9:16" if short else "16:9", storyboard=o.get("storyboard", ""),
                                wpm=int(o.get("wpm") or 0) or None)
        if o.get("storyboard"): s["storyboard"] = o["storyboard"]   # disimpan untuk referensi & kolom SFX di editor
        s.update(id=pid, pair=pair, ai_mode=o.get("ai_mode", "code"),
                 theme=o.get("theme") if o.get("theme") not in (None, "", "auto") else s.get("theme", "auto"))
        doc.save(p, s); doc.write_temp_srt(p, s)
        log(f"Naskah siap: {len(s['scenes'])} adegan", 1)
    _doc_run(pid, work, "Menulis naskah Shorts..." if short else "Menulis naskah...")
    return pid


def _link(a, b):
    pa = os.path.join(doc.DOCS_DIR, a); s = doc.load(pa); s["pair"] = b; doc.save(pa, s)


@app.route("/api/docs/new", methods=["POST"])
def docs_new():
    f = request.form.to_dict()
    topic = f.get("topic", "").strip()
    sb = f.get("storyboard", "").strip()
    if sb:   # storyboard sendiri: 1 proyek, durasi dari timestamp, judul dari baris "..." bila topik kosong
        if len(sb) > 40000: return jsonify(error="Storyboard terlalu panjang (maks 40.000 huruf)"), 400
        m = re.search(r'["“]([^"”]{4,80})["”]', sb)
        topic = topic or (m.group(1) if m else sb.splitlines()[0][:80].strip(" #🎬:"))
        f["storyboard"] = sb
        short = f.get("aspect") == "9:16"
        mins = doc.storyboard_minutes(sb) or float(f.get("minutes") or (1 if short else 10))
        return jsonify(id=_new_doc(topic, f, short, mins))
    if not topic: return jsonify(error="Isi topik"), 400
    aspect = f.get("aspect", "16:9")
    if aspect == "both":   # 1 ide -> 2 proyek: video panjang 16:9 + Shorts 9:16, naskah & gambar masing-masing
        long_id, short_id = _doc_pid(topic, False), _doc_pid(topic, True)
        _new_doc(topic, f, False, float(f.get("minutes") or 10), pair=short_id, pid=long_id)
        _new_doc(topic, f, True, 1, pair=long_id, pid=short_id)
        return jsonify(id=long_id, pair=short_id)
    short = aspect == "9:16"
    return jsonify(id=_new_doc(topic, f, short, float(f.get("minutes") or (1 if short else 10))))


@app.route("/api/docs/<pid>/pair", methods=["POST"])
def docs_pair(pid):
    """Dari proyek yang sudah ada: buat versi pasangannya (panjang -> Shorts 9:16, atau sebaliknya)
    dengan topik & pengaturan yang sama, tapi naskah dan gambar baru sesuai formatnya (tidak memotong video lama)."""
    p = _pdir(pid); s = doc.load(p)
    if (DOC_STATE.get(pid) or {}).get("status") == "jalan": return jsonify(error="Masih diproses"), 400
    if s.get("pair") and os.path.isdir(os.path.join(doc.DOCS_DIR, s["pair"])):
        return jsonify(id=s["pair"], existed=True)
    short = s.get("aspect") != "9:16"
    o = {k: s.get(k) or "" for k in ("visual", "lang", "format", "tone", "theme")}
    o.update(ai_mode=request.form.get("ai_mode", "code"), images_per_scene=1)
    try:
        nid = _new_doc(s.get("topic", pid), o, short, 1 if short else float(request.form.get("minutes") or 10), pair=pid)
    except RuntimeError as e:
        return jsonify(error=str(e)), 400
    _link(pid, nid)
    return jsonify(id=nid)


@app.route("/api/docs/import", methods=["POST"])
def docs_import():
    src = request.form.get("folder", "").strip().strip('"')
    if not os.path.isdir(src): return jsonify(error="Folder tidak ditemukan"), 400
    voice = request.form.get("voice", "").strip().strip('"') or None
    if voice and not os.path.isfile(voice): return jsonify(error="File suara tidak ditemukan"), 400
    pid = secure_filename(os.path.basename(src.rstrip("\\/")).lower().replace(" ", "_")) or "import"
    if os.path.exists(os.path.join(doc.DOCS_DIR, pid)): return jsonify(error=f"Proyek '{pid}' sudah ada"), 400
    try:
        _, err = doc.import_folder(src, pid, request.form.get("topic") or None, voice, lang=request.form.get("lang", "en"))
    except Exception as e:
        return jsonify(error=str(e)), 400
    return jsonify(id=pid, warning=err)


@app.route("/api/docs/<pid>", methods=["GET", "DELETE"])
def docs_get(pid):
    try:
        p = _pdir(pid)
    except FileNotFoundError as e:
        return jsonify(error=str(e)), 404
    if request.method == "DELETE":
        if (DOC_STATE.get(pid) or {}).get("status") == "jalan": return jsonify(error="Masih diproses"), 400
        shutil.rmtree(p, ignore_errors=True); DOC_STATE.pop(pid, None)
        return jsonify(ok=True)
    s = doc.load(p)
    out = os.path.join(p, "output")
    files = {f: os.path.exists(os.path.join(out, f)) for f in ("final.mp4", "final.srt", "metadata.txt")}
    vdur = clipper.get_duration(os.path.join(p, "voice.mp3")) if os.path.exists(os.path.join(p, "voice.mp3")) else None
    music = next((f for f in os.listdir(p) if f.startswith("music.")), None)
    return jsonify(script=s, images=doc.list_images(p), voice_duration=vdur, music=music, outputs=files,
                   problems=doc.validate(p, s) if s["scenes"] else ["Naskah belum ada"], state=DOC_STATE.get(pid),
                   themes={k: v["label"] for k, v in doc.themes().items()}, visuals=list(doc.VISUAL_STYLES))


@app.route("/api/docs/<pid>/script", methods=["POST"])
def docs_save(pid):
    p = _pdir(pid); s = doc.load(p); b = request.get_json(force=True)
    old = [sc["text"] for sc in s["scenes"]]
    for k in ("scenes", "metadata", "theme", "tighten", "topic", "lang", "subtitles", "transition_sfx", "dynamics", "chapter_cards", "num_cards", "aspect", "visual", "thumbnail_prompt", "thumb_intro"):
        if k in b: s[k] = b[k]
    if "text_backup" in b:   # naskah sebelum "Rapikan bahasa" (null = hapus)
        if b["text_backup"]: s["text_backup"] = b["text_backup"]
        else: s.pop("text_backup", None)
    changed = [sc["text"] for sc in s["scenes"]] != old
    if changed:   # naskah berubah -> alignment lama tidak berlaku
        s.pop("alignment", None)
    doc.save(p, s)
    if changed or not os.path.exists(os.path.join(p, "script.srt")):   # SRT dari CapCut tidak ditimpa
        doc.write_temp_srt(p, s)
    return jsonify(ok=True)


@app.route("/api/docs/<pid>/tts", methods=["POST"])
def docs_tts(pid):
    """Suara AI langsung dari naskah (edge-tts gratis): tanpa CapCut, tanpa SRT."""
    p = _pdir(pid); s = doc.load(p); voice = request.form.get("voice") or ""
    if not s["scenes"]: return jsonify(error="Naskah masih kosong"), 400
    if (DOC_STATE.get(pid) or {}).get("status") == "jalan": return jsonify(error="Masih diproses"), 400
    rate = request.form.get("rate") or "+0%"
    key = None
    if voice.startswith("gemini:"):
        if request.form.get("gemini_key", "").strip():
            cfg = load_cfg(); cfg["gemini_key"] = request.form["gemini_key"].strip(); save_cfg(cfg)
        key = load_cfg().get("gemini_key") or os.environ.get("GEMINI_API_KEY")
        if not key: return jsonify(error="Suara Gemini butuh Gemini API key (isi di bagian 4 · Gambar, atau aistudio.google.com/apikey)"), 400
    def work(log):
        log("Membacakan naskah dengan suara AI...", 0.1)
        doc.ai_voice(p, s, voice, rate, log, key=key, tone=s.get("tone", ""))
        log(f"Suara AI siap ({clipper.get_duration(os.path.join(p, 'voice.mp3')):.0f} dtk)", 1)
    _doc_run(pid, work, "Menyiapkan suara AI...")
    return jsonify(ok=True)


@app.route("/api/docs/<pid>/gemini", methods=["POST"])
def docs_gemini(pid):
    """Gambar otomatis lewat Gemini API untuk prompt yang belum punya gambar (bisa dilanjutkan)."""
    p = _pdir(pid); s = doc.load(p); f = request.form
    if f.get("gemini_key", "").strip():
        cfg = load_cfg(); cfg["gemini_key"] = f["gemini_key"].strip(); save_cfg(cfg)
    key = load_cfg().get("gemini_key") or os.environ.get("GEMINI_API_KEY")
    if not key: return jsonify(error="Isi Gemini API key dulu (aistudio.google.com/apikey)"), 400
    if not any(sc.get("prompts") for sc in s["scenes"]): return jsonify(error="Belum ada prompt gambar"), 400
    if (DOC_STATE.get(pid) or {}).get("status") == "jalan": return jsonify(error="Masih diproses"), 400
    nos = {int(x) for x in re.findall(r"\d+", f.get("nos", ""))} or None
    model = f.get("model", "").strip() or load_cfg().get("gemini_model")
    def work(log):
        n, fails = doc.gemini_images(p, key, model, nos, workers=int(f.get("workers") or 3), log=log)
        if fails:
            raise RuntimeError(f"{n} gambar jadi, {len(fails)} gagal (klik lagi untuk melanjutkan). " + " | ".join(fails[:3]))
        log(f"Selesai: {n} gambar Gemini terpasang", 1)
    _doc_run(pid, work, "Menyiapkan Gemini...")
    return jsonify(ok=True)


@app.route("/api/docs/<pid>/srt", methods=["POST"])
def docs_srt(pid):
    p = _pdir(pid); up = request.files.get("srt")
    if not up or not up.filename.lower().endswith(".srt"): return jsonify(error="Pilih file .srt"), 400
    tmp = os.path.join(p, "_upload.srt"); up.save(tmp)
    try:
        s = doc.load(p); msg = doc.replace_from_srt(p, s, tmp); doc.save(p, s)
    except Exception as e:
        return jsonify(error=str(e)), 400
    finally:
        if os.path.exists(tmp): os.remove(tmp)
    return jsonify(ok=True, message=msg)


# ===== Ekstensi Chrome "Flow Importer": ambil batch prompt & terima gambar hasil Google Flow =====
@app.route("/api/flow/projects")
def flow_projects():
    out = []
    for d in sorted(os.listdir(doc.DOCS_DIR), key=lambda x: -os.path.getmtime(os.path.join(doc.DOCS_DIR, x))) if os.path.isdir(doc.DOCS_DIR) else []:
        try:
            s = doc.load(os.path.join(doc.DOCS_DIR, d))
        except Exception:
            continue
        items = doc.prompt_items(s)
        if items:
            done = sum(1 for _, i, j, _ in items if len(s["scenes"][i].get("images") or []) > j)
            ti = doc.thumb_item(s)
            kind = "📱 Shorts" if s.get("aspect") == "9:16" else "🖥️ Panjang"   # judul Shorts & video panjang bisa sama
            out.append({"id": d, "topic": f"{kind} · {s.get('topic', d)}", "total": len(items) + bool(ti), "done": done + bool(ti and ti[2])})
    return jsonify(out)


@app.route("/api/flow/<pid>/manifest")
def flow_manifest(pid):
    s = doc.load(_pdir(pid))
    ti = doc.thumb_item(s)   # thumbnail = nomor 0, dibuat paling dulu
    pair = s.get("pair") if s.get("pair") and os.path.isfile(os.path.join(doc.DOCS_DIR, s.get("pair") or "", "script.json")) else None
    return jsonify(aspect=s.get("aspect", "16:9"), pair=pair, items=([{"no": 0, "scene": 0, "prompt": ti[1], "has_image": ti[2]}] if ti else []) + [
        {"no": no, "scene": i + 1, "prompt": p, "has_image": len(s["scenes"][i].get("images") or []) > j}
        for no, i, j, p in doc.prompt_items(s)])


@app.route("/api/flow/<pid>/import", methods=["POST"])
def flow_import(pid):
    import base64
    p = _pdir(pid); b = request.get_json(force=True)
    try:
        no = int(b["no"]); data = base64.b64decode(b["data"].split(",")[-1])
        ext = "png" if "png" in (b.get("type") or "") else "webp" if "webp" in (b.get("type") or "") else "jpg"
        scene, name = doc.set_prompt_image(p, no, data, ext)
    except Exception as e:
        return jsonify(error=str(e)), 400
    return jsonify(ok=True, scene=scene, file=name)


@app.route("/api/docs/<pid>/reprompt", methods=["POST"])
def docs_reprompt(pid):
    p = _pdir(pid); s = doc.load(p); f = request.form
    if (DOC_STATE.get(pid) or {}).get("status") == "jalan": return jsonify(error="Masih diproses"), 400
    idxs = doc.parse_scenes(f.get("scenes", ""), len(s["scenes"]))
    if not idxs: return jsonify(error="Nomor adegan tidak valid"), 400
    key = _doc_key(f.get("ai_mode") or s.get("ai_mode"))
    def work(log):
        s2 = doc.regen_prompts(doc.load(p), idxs, f.get("visual") or None, f.get("images_per_scene") or None, key, log=log)
        if f.get("clear") == "1":   # gaya berubah: gambar lama adegan itu dihapus agar dibuat ulang
            for i in idxs:
                for im in s2["scenes"][i].get("images") or []:
                    src = os.path.join(p, "images", im)
                    if os.path.isfile(src): os.remove(src)
                s2["scenes"][i]["images"] = []
        doc.save(p, s2)
        log(f"Prompt gambar baru siap untuk {len(idxs)} adegan", 1)
    _doc_run(pid, work, "Menulis ulang prompt gambar...")
    return jsonify(ok=True, n=len(idxs))


@app.route("/api/docs/<pid>/expand", methods=["POST"])
def docs_expand(pid):
    """Panjangkan naskah ke durasi target. wpm = kecepatan suara (CapCut ±200, suara AI ±150 kata/menit)."""
    p = _pdir(pid); s = doc.load(p); f = request.form
    if (DOC_STATE.get(pid) or {}).get("status") == "jalan": return jsonify(error="Masih diproses"), 400
    mins, wpm = float(f.get("minutes") or 10), int(f.get("wpm") or 150)
    target = int(mins * wpm)
    got = sum(len(x["text"].split()) for x in s["scenes"])
    if got >= target * 0.95: return jsonify(error=f"Naskah sudah {got} kata (±{got / wpm:.1f} menit), tidak perlu diperpanjang"), 400
    key = _doc_key(s.get("ai_mode"))
    def work(log):
        s0 = doc.load(p)
        s0["text_backup_scenes"] = [dict(x) for x in s0["scenes"]]
        s2, added = doc.expand_script(s0, target, key, log=log)
        doc.save(p, s2); doc.write_temp_srt(p, s2)
        n = sum(len(x["text"].split()) for x in s2["scenes"])
        log(f"Naskah jadi {n} kata (±{n / wpm:.1f} menit), {added} adegan baru. Buat gambar untuk adegan baru & suara ulang.", 1)
    _doc_run(pid, work, "AI memperpanjang naskah...")
    return jsonify(ok=True, target=target)


@app.route("/api/docs/<pid>/expand/undo", methods=["POST"])
def docs_expand_undo(pid):
    p = _pdir(pid); s = doc.load(p)
    if not s.get("text_backup_scenes"): return jsonify(error="Tidak ada cadangan"), 400
    s["scenes"] = s.pop("text_backup_scenes"); s.pop("alignment", None)
    doc.save(p, s); doc.write_temp_srt(p, s)
    return jsonify(ok=True)


@app.route("/api/docs/<pid>/prompts")
def docs_prompts(pid):
    return jsonify(doc.prompt_batches(doc.load(_pdir(pid)), int(request.args.get("size", 8))))


@app.route("/api/docs/<pid>/voice", methods=["POST"])
def docs_voice(pid):
    p = _pdir(pid); up = request.files.get("voice")
    if not up or not up.filename: return jsonify(error="Pilih file suara"), 400
    tmp = os.path.join(p, "_upload_" + secure_filename(up.filename)); up.save(tmp)
    try:   # mp3/wav/m4a/mp4 apa pun -> voice.mp3
        clipper.run(["ffmpeg", "-y", "-v", "error", "-i", tmp, "-vn", "-c:a", "libmp3lame", "-b:a", "192k", os.path.join(p, "voice.mp3")])
    finally:
        os.remove(tmp)
    s = doc.load(p); s.pop("alignment", None); doc.save(p, s)
    return jsonify(ok=True, duration=clipper.get_duration(os.path.join(p, "voice.mp3")))


@app.route("/api/docs/<pid>/music", methods=["POST", "DELETE"])
def docs_music(pid):
    p = _pdir(pid)
    for f in os.listdir(p):
        if f.startswith("music."): os.remove(os.path.join(p, f))
    if request.method == "POST":
        up = request.files.get("music")
        if not up or not up.filename: return jsonify(error="Pilih file musik"), 400
        up.save(os.path.join(p, "music" + os.path.splitext(up.filename)[1].lower()))
    return jsonify(ok=True)


@app.route("/api/docs/<pid>/images", methods=["POST"])
def docs_images(pid):
    p = _pdir(pid); d = os.path.join(p, "images"); os.makedirs(d, exist_ok=True)
    n = 0
    for up in request.files.getlist("images"):
        fn = secure_filename(up.filename or "")
        if fn.lower().endswith(doc.VID_EXT):   # klip animasi: nama sama dgn gambar, atau nomor prompt di nama (mis. "003.mp4")
            stem = os.path.splitext(fn)[0]
            if not any(os.path.splitext(x)[0] == stem for x in os.listdir(d)):
                m = re.search(r"\d+", stem)
                if not m: continue
                stem = f"flow_{int(m.group()):03d}"
            up.save(os.path.join(d, stem + os.path.splitext(fn)[1].lower())); n += 1
        elif fn.lower().endswith(doc.IMG_EXT):
            up.save(os.path.join(d, fn)); n += 1
    s = doc.load(p)
    warn = None
    if not any(sc.get("images") for sc in s["scenes"]):   # belum dipetakan: coba bagi rata otomatis
        warn = doc.auto_map(s, doc.list_images(p)); doc.save(p, s)
    return jsonify(ok=True, added=n, warning=warn)


@app.route("/api/docs/<pid>/anim")
def docs_anim(pid):
    p = _pdir(pid)
    items = doc.anim_prompts(doc.load(p), p)
    return jsonify(items=[{"no": n, "image": im, "prompt": pr, "clip": c} for n, im, pr, c in items],
                   clips=sum(1 for *_, c in items if c))


@app.route("/api/docs/<pid>/images/<name>", methods=["DELETE"])
def docs_image_del(pid, name):
    p = _pdir(pid); name = secure_filename(name)
    path = os.path.join(p, "images", name)
    if os.path.exists(path): os.remove(path)
    s = doc.load(p)
    for sc in s["scenes"]:
        sc["images"] = [x for x in sc.get("images") or [] if x != name]
    doc.save(p, s)
    return jsonify(ok=True)


@app.route("/api/docs/<pid>/map", methods=["POST"])
def docs_map(pid):
    """{"order": [file...], "auto": true} = urutan baru lalu bagi rata; {"scenes": [[file...], ...]} = manual."""
    p = _pdir(pid); s = doc.load(p); b = request.get_json(force=True)
    if b.get("scenes") is not None:
        for sc, imgs in zip(s["scenes"], b["scenes"]): sc["images"] = imgs
        err = None
    else:
        order = b.get("order") or doc.list_images(p, b.get("sort", "name"))
        err = doc.auto_map(s, order)
    doc.save(p, s)
    return jsonify(ok=not err, error=err)


@app.route("/api/docs/<pid>/render", methods=["POST"])
def docs_render(pid):
    p = _pdir(pid); s = doc.load(p)
    if (DOC_STATE.get(pid) or {}).get("status") == "jalan": return jsonify(error="Masih diproses"), 400
    probs = doc.validate(p, s)
    if probs: return jsonify(error="Belum bisa render:\n- " + "\n- ".join(probs[:12])), 400
    theme = request.form.get("theme") or s.get("theme") or "auto"
    music = next((os.path.join(p, f) for f in os.listdir(p) if f.startswith("music.")), None)
    def work(log):
        with DOC_LOCK:
            doc.render(p, theme, music, log=log)
    _doc_run(pid, work, "Menunggu giliran render...")
    return jsonify(ok=True)


@app.route("/api/docs/<pid>/shorts", methods=["POST"])
def docs_shorts(pid):
    """Potong video dokumenter jadi Shorts 9:16 memakai timing kata hasil alignment (tanpa Whisper ulang)."""
    import time
    p = _pdir(pid); final = os.path.join(p, "output", "final.mp4")
    if not os.path.exists(final): return jsonify(error="Render video dulu"), 400
    with open(os.path.join(p, "words.json"), encoding="utf-8") as f: words = json.load(f)
    jid = uuid.uuid4().hex[:10]; d = os.path.join(JOBS_DIR, jid); os.makedirs(d)
    src = os.path.join(d, "source_final.mp4"); shutil.copy2(final, src)
    chunks = doc._chunks(words, 14)
    s = doc.load(p); lang = doc.lang_of(s)
    tr = {"language": lang, "words": [{"start": w["start"], "end": w["end"], "word": w["word"]} for w in words],
          "segments": [{"start": c[0]["start"], "end": c[-1]["end"], "text": " ".join(w["word"] for w in c)} for c in chunks]}
    with open(os.path.join(d, "transcript.json"), "w", encoding="utf-8") as f: json.dump(tr, f, ensure_ascii=False)
    f_ = request.form
    opts = dict(DEFAULT_OPTS, n_clips=int(f_.get("n_clips") or 4), min_len=30, max_len=58, language=lang,
                ai_mode=f_.get("ai_mode", "code"), transcript_ready=True, frame=f_.get("frame", "auto"))
    JOBS[jid] = {"id": jid, "kind": "shorts", "name": s.get("topic", pid) + " (Shorts)", "url": "", "platform": "upload",
                 "status": "antri", "log": "Menunggu...", "clips": [], "created": time.time(), "opts": opts}
    save_job(jid)
    threading.Thread(target=process, args=(jid, src, opts), daemon=True).start()
    return jsonify(id=jid)


def _nice_name(title, fn, suffix=""):
    """Nama file unduhan dari judul (bukan clip_01.mp4): karakter terlarang Windows dibuang, maks 80 huruf."""
    t = re.sub(r'[<>:"/\\|?*\x00-\x1f#]+', " ", title or "")
    t = re.sub(r"\s+([)\]])", r"\1", re.sub(r"\s+", " ", t)).strip(" .")[:80].strip(" .")
    return (t + suffix + os.path.splitext(fn)[1]) if t else os.path.basename(fn)


@app.route("/docs/<pid>/file/<path:fn>")
def docs_file(pid, fn):
    dl = request.args.get("dl") == "1"
    name = None
    if dl:
        s = doc.load(_pdir(pid))
        titles = (s.get("metadata") or {}).get("titles") or []
        title = titles[0] if titles else s.get("topic", "").replace("_", " ")
        base = os.path.basename(fn)
        suffix = {"final.mp4": "", "final.srt": "", "metadata.txt": " - metadata"}.get(base)
        if suffix is not None: name = _nice_name(title, base, suffix)
    return send_from_directory(_pdir(pid), fn, as_attachment=dl, download_name=name)


@app.route("/api/jobs")
def jobs():
    return jsonify(sorted(JOBS.values(), key=lambda j: j["created"], reverse=True))


@app.route("/")
def index():
    jobs = sorted(JOBS.values(), key=lambda j: j["created"], reverse=True)
    return render_template("index.html", jobs=jobs, voices=clipper.VOICES, styles=motion.STYLES, has_key=bool(load_cfg().get("api_key") or os.environ.get("ANTHROPIC_API_KEY")), has_gemini=bool(load_cfg().get("gemini_key") or os.environ.get("GEMINI_API_KEY")), has_mgemini=bool(load_cfg().get("motion_gemini_key")), gemini_voices=doc.GEMINI_VOICES, rbrand=load_cfg().get("remotion_brand", ""))


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
        "frame": f.get("frame", "auto"),
        "hook": f.get("hook") == "on",
        "jumpcut": f.get("jumpcut") == "on",
        "punch": f.get("punch") == "on",
        "sfx": f.get("sfx") == "on",
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
        save_cfg(cfg)
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
                "vertical": True, "face": True, "frame": "auto", "hook": True, "jumpcut": True, "punch": True, "sfx": True, "subtitles": True, "sub_color": "kuning",
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
    for k in ("ai_mode", "sub_color", "voice_mode", "voice", "narr_source", "frame"):
        if f.get(k): o[k] = f[k]
    if "narr_lang" in f: o["narr_lang"] = f["narr_lang"]   # "" = otomatis (ikut suara)
    for k in ("vertical", "face", "hook", "jumpcut", "punch", "sfx", "subtitles"):
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
    if len(files) == 1 and "hook_text" in request.form:   # teks hook bisa diedit per klip
        for c in job["clips"]:
            if c["file"] != files[0]: continue
            c["hook_text"] = request.form["hook_text"].strip()
            # geser awal/akhir klip (detik, boleh negatif); batas asli disimpan agar bisa dikembalikan
            c.setdefault("orig", [c["start"], c["end"]])
            ts, te = float(request.form.get("trim_start") or 0), float(request.form.get("trim_end") or 0)
            c["start"] = max(0.0, round(c["orig"][0] + ts, 2))
            c["end"] = round(max(c["orig"][1] + te, c["start"] + 3), 2)
            if job.get("duration"): c["end"] = min(c["end"], job["duration"])
    job.update(status="antri", log="Menunggu...", progress=0); save_job(jid)
    threading.Thread(target=process_rerender, args=(jid, opts, files), daemon=True).start()
    return jsonify(ok=True)


@app.route("/api/job/<jid>", methods=["DELETE"])
def delete(jid):
    JOBS.pop(jid, None); shutil.rmtree(os.path.join(JOBS_DIR, jid), ignore_errors=True)
    return jsonify(ok=True)


@app.route("/api/job/<jid>/meta/<fn>")
def clip_meta(jid, fn):
    job = JOBS.get(jid) or {}
    c = next((c for c in job.get("clips") or [] if c.get("file") == fn), None)
    if not c: return jsonify(error="Klip tidak ditemukan"), 404
    text = clipper.shorts_metadata_text(c)
    if request.args.get("dl") != "1":
        return jsonify(text=text)
    no = f" ({_clip_no(c)})" if len(job["clips"]) > 1 and _clip_no(c) else ""
    name = _nice_name(c.get("title") or job.get("name"), "metadata.txt", no + " - metadata")
    return Response(text, mimetype="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


@app.route("/api/job/<jid>/meta", methods=["POST"])
def clip_meta_ai(jid):
    """Tulis (ulang) metadata AI untuk semua klip job ini."""
    job = JOBS.get(jid)
    if not job or not job.get("clips"): return jsonify(error="Belum ada klip"), 400
    opts = job.get("opts") or {}
    key = _shorts_key(opts)
    try:
        data = clipper.write_shorts_metadata(job["clips"], _narr_lang(opts) or opts.get("language"), key)
    except Exception as e:
        return jsonify(error=f"Gagal membuat metadata: {e}"), 500
    for c, m in zip(job["clips"], data):
        c.update(titles=m.get("titles") or [], description=m.get("description", ""), tags=m.get("tags") or [],
                 hashtags=m.get("hashtags") or c.get("hashtags", []))
    save_job(jid)
    return jsonify(ok=True)


@app.route("/media/<jid>/<path:fn>")
def media(jid, fn):
    dl = request.args.get("dl") == "1"
    name = None
    if dl:
        job = JOBS.get(jid) or {}
        clips = job.get("clips") or []
        c = next((c for c in clips if c.get("file") == fn), None)
        if c:   # klip: judul klip, tambah nomor bila ada beberapa klip
            no = f" ({_clip_no(c)})" if len(clips) > 1 and _clip_no(c) else ""
            name = _nice_name(c.get("title") or c.get("hook_text") or job.get("name"), fn, no)
        elif job.get("name") and os.path.splitext(fn)[1].lower() in (".mp4", ".mp3", ".m4a", ".webm", ".mkv"):
            name = _nice_name(job["name"], fn)
    return send_from_directory(os.path.join(JOBS_DIR, jid), fn, as_attachment=dl, download_name=name)



# ===== Agen upload YouTube: antrean -> upload Private + jadwal tayang (jam penonton AS) =====
def _yt_candidates():
    q = yt.load(); used = {i["file"] for i in q if i["status"] != "batal"}
    out = []
    if os.path.isdir(doc.DOCS_DIR):
        for pid in os.listdir(doc.DOCS_DIR):
            p = os.path.join(doc.DOCS_DIR, pid); f = os.path.join(p, "output", "final.mp4")
            if not os.path.exists(f): continue
            try: s = doc.load(p)
            except Exception: continue
            t, d, tags = yt.parse_metadata(os.path.join(p, "output", "metadata.txt"))
            th = os.path.join(p, s["thumbnail"]) if s.get("thumbnail") else ""
            out.append({"pid": pid, "kind": "short" if s.get("aspect") == "9:16" else "long", "file": f,
                        "title": t or s.get("topic", pid), "description": d, "tags": tags, "lang": s.get("lang", "id"),
                        "ai": yt.needs_ai_label(s.get("visual")), "visual": s.get("visual", ""),
                        "thumb": th if th and os.path.exists(th) else "", "queued": f in used, "mtime": os.path.getmtime(f)})
    for hd in [os.path.join(BASE, "hasil")] + list(load_cfg().get("yt_folders") or []):   # + folder video tambahan
        if not os.path.isdir(hd): continue
        for n in os.listdir(hd):
            f = os.path.join(hd, n)
            if n.lower().endswith(".mp4"):
                out.append({"pid": "", "kind": "short", "file": f, "title": os.path.splitext(n)[0], "description": "", "ai": False,
                            "tags": [], "thumb": "",
                            "lang": "en" if re.fullmatch(r"[\x00-\x7F]+", n) else "id", "queued": f in used, "mtime": os.path.getmtime(f)})
    return sorted(out, key=lambda x: -x["mtime"])


@app.route("/api/yt/status")
def yt_status():
    ch, err = None, ""
    try:
        if yt.creds(): ch = yt.channel()
    except Exception as e:
        err = str(e)[:200]
    q = sorted(yt.load(), key=lambda i: i["publish_at"])
    for i in q: i["wib"] = yt.wib(i["publish_at"])
    return jsonify(folders=load_cfg().get("yt_folders") or [], secret=os.path.exists(yt.SECRET), connected=bool(ch), channel=ch, error=err or yt.STATE["auth_msg"],
                   queue=q, candidates=_yt_candidates(), running=yt.STATE["running"])


@app.route("/api/yt/folders", methods=["POST"])
def yt_folders():
    """{"add": "D:\\folder"} / {"remove": "..."}: folder berisi video jadi yang ikut tampil di daftar siap upload."""
    a = request.get_json(force=True); cfg = load_cfg(); fl = list(cfg.get("yt_folders") or [])
    if a.get("add"):
        f = os.path.normpath(a["add"].strip().strip('"'))
        if not os.path.isdir(f): return jsonify(error="Folder tidak ditemukan"), 400
        if f not in fl: fl.append(f)
    if a.get("remove") in fl: fl.remove(a["remove"])
    cfg["yt_folders"] = fl; save_cfg(cfg)
    return jsonify(folders=fl)


@app.route("/api/yt/connect", methods=["POST"])
def yt_connect():
    def body():
        yt.STATE["auth_msg"] = "Menunggu login Google di tab browser yang terbuka..."
        try:
            yt.creds(interactive=True); yt.STATE["auth_msg"] = ""
        except Exception as e:
            yt.STATE["auth_msg"] = f"Gagal menghubungkan: {str(e)[:200]}"
    threading.Thread(target=body, daemon=True).start()
    return jsonify(ok=True)


@app.route("/api/yt/disconnect", methods=["POST"])
def yt_disconnect():
    if os.path.exists(yt.TOKEN): os.remove(yt.TOKEN)
    return jsonify(ok=True)


@app.route("/api/yt/queue", methods=["POST"])
def yt_queue():
    a = request.get_json(force=True)
    c = next((x for x in _yt_candidates() if x["file"] == a.get("file")), None)
    if not c: return jsonify(error="Video tidak ditemukan"), 404
    item = {k: c[k] for k in ("pid", "kind", "file", "title", "description", "tags", "lang", "thumb", "ai")}
    if "ai" in a: item["ai"] = bool(a["ai"])
    for k in ("title", "description", "kind", "publish_at"):
        if a.get(k): item[k] = a[k]
    try:
        it = yt.add(item)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    return jsonify(dict(it, wib=yt.wib(it["publish_at"])))


@app.route("/api/yt/<iid>", methods=["DELETE"])
def yt_remove(iid):
    try:
        it = yt.remove(iid)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    return jsonify(ok=True, video_id=(it or {}).get("video_id", ""))


@app.route("/api/yt/<iid>/retry", methods=["POST"])
def yt_retry(iid):
    yt.update(iid, status="antre", error="")
    return jsonify(ok=True)


@app.route("/api/yt/run", methods=["POST"])
def yt_run():
    threading.Thread(target=yt.run_pending, daemon=True).start()
    return jsonify(ok=True)


@app.route("/api/usage")
def usage():
    return jsonify(clipper.usage_summary())

if __name__ == "__main__":
    load_jobs()
    threading.Thread(target=agent_loop, daemon=True).start()
    threading.Thread(target=yt.loop, daemon=True).start()
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
