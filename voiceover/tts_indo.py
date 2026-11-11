"""
ElevenLabs voice-over otomatis untuk naskah bahasa Indonesia.

Cara pakai:
  1. Set API key sekali (PowerShell):  setx ELEVENLABS_API_KEY "kunci_anda"
     lalu buka terminal baru.
  2. Taruh naskah .txt di folder  naskah/
  3. Jalankan:  python tts_indo.py
     -> hasil .mp3 ada di folder  audio/  (file yang sudah ada dilewati)

Opsi:
  python tts_indo.py --voices          daftar suara di akun Anda (cari voice_id)
  python tts_indo.py --voice ID        pakai suara tertentu
  python tts_indo.py --watch           pantau folder naskah/, proses otomatis tiap ada file baru
  python tts_indo.py --dry-run         hanya tampilkan teks setelah dirapikan, tanpa memanggil API
  python tts_indo.py --force           buat ulang semua audio
"""
import argparse, json, os, re, sys, time, urllib.request, urllib.error

BASE = os.path.dirname(os.path.abspath(__file__))
IN_DIR = os.path.join(BASE, "naskah")
OUT_DIR = os.path.join(BASE, "audio")
CONFIG = os.path.join(BASE, "config.json")

DEFAULTS = {
    "voice_id": "",                       # isi setelah lihat --voices
    "model_id": "eleven_multilingual_v2",
    "stability": 0.40,                    # rendah = lebih ekspresif
    "similarity_boost": 0.75,
    "style": 0.25,
    "use_speaker_boost": True,
    "speed": 1.0,
    "max_chars": 2500,                    # potong naskah panjang per bagian
}

# ---------- perapian teks agar diucapkan natural ----------
SATUAN = ["", "satu", "dua", "tiga", "empat", "lima", "enam", "tujuh", "delapan", "sembilan",
          "sepuluh", "sebelas"]

def terbilang(n: int) -> str:
    if n < 12:
        return SATUAN[n] if n else "nol"
    if n < 20:
        return SATUAN[n - 10] + " belas"
    if n < 100:
        return (SATUAN[n // 10] + " puluh " + (SATUAN[n % 10])).strip()
    if n < 200:
        return ("seratus " + (terbilang(n - 100) if n > 100 else "")).strip()
    if n < 1000:
        return (SATUAN[n // 100] + " ratus " + (terbilang(n % 100) if n % 100 else "")).strip()
    if n < 2000:
        return ("seribu " + (terbilang(n - 1000) if n > 1000 else "")).strip()
    for nilai, nama in ((10**12, "triliun"), (10**9, "miliar"), (10**6, "juta"), (1000, "ribu")):
        if n >= nilai:
            depan, sisa = divmod(n, nilai)
            return (terbilang(depan) + " " + nama + " " + (terbilang(sisa) if sisa else "")).strip()
    return str(n)

SINGKATAN = {
    r"\bAI\b": "A-I", r"\bYT\b": "YouTube", r"\bdll\b\.?": "dan lain-lain",
    r"\bdsb\b\.?": "dan sebagainya", r"\byg\b": "yang", r"\bdgn\b": "dengan",
    r"\btdk\b": "tidak", r"\bkrn\b": "karena", r"\bdlm\b": "dalam",
    r"\bRp\.?\s?(\d[\d.]*(?:,\d+)?)": r"\1 rupiah",
}

def rapikan(teks: str) -> str:
    t = teks.replace("\r", "")
    t = re.sub(r"\[[^\]]*\]", "", t)                       # buang catatan [ON SCREEN: ...]
    for pola, ganti in SINGKATAN.items():
        t = re.sub(pola, ganti, t)
    t = re.sub(r"(\d+)\s?%", lambda m: m.group(1) + " persen", t)
    t = re.sub(r"(\d+),(\d+)", lambda m: terbilang(int(m.group(1))) + " koma " +
               " ".join(terbilang(int(d)) for d in m.group(2)), t)      # 2,5 -> dua koma lima
    t = re.sub(r"\d{1,3}(?:\.\d{3})+|\d+",
               lambda m: terbilang(int(m.group(0).replace(".", ""))), t)  # 20.000 -> dua puluh ribu
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()

def potong(teks: str, maks: int):
    """Bagi per paragraf/kalimat supaya intonasi tidak terpotong di tengah kalimat."""
    bagian, buf = [], ""
    for kal in re.split(r"(?<=[.!?…])\s+|\n\n", teks):
        if buf and len(buf) + len(kal) + 1 > maks:
            bagian.append(buf.strip()); buf = ""
        buf += kal + " "
    if buf.strip():
        bagian.append(buf.strip())
    return bagian

# ---------- API ElevenLabs ----------
def api(path, key, body=None):
    req = urllib.request.Request("https://api.elevenlabs.io" + path,
                                 data=json.dumps(body).encode() if body else None,
                                 headers={"xi-api-key": key, "Content-Type": "application/json"},
                                 method="POST" if body else "GET")
    for coba in range(4):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            pesan = e.read().decode(errors="ignore")[:300]
            if e.code in (429, 500, 502, 503) and coba < 3:
                time.sleep(5 * (coba + 1)); continue
            sys.exit(f"Error API {e.code}: {pesan}")

def daftar_suara(key):
    data = json.loads(api("/v1/voices", key))
    for v in data.get("voices", []):
        lab = v.get("labels") or {}
        print(f"{v['voice_id']}  {v['name']:<25} {lab.get('accent','')} {lab.get('language','')} {lab.get('gender','')}")

def buat_audio(teks, cfg, key, keluar, prev_text=None):
    body = {
        "text": teks, "model_id": cfg["model_id"], "language_code": "id",
        "voice_settings": {k: cfg[k] for k in ("stability", "similarity_boost", "style",
                                               "use_speaker_boost", "speed")},
    }
    if prev_text:
        body["previous_text"] = prev_text[-500:]          # jaga intonasi antar bagian
    audio = api(f"/v1/text-to-speech/{cfg['voice_id']}?output_format=mp3_44100_128", key, body)
    with open(keluar, "ab") as f:
        f.write(audio)

def proses(path, cfg, key, force, dry):
    nama = os.path.splitext(os.path.basename(path))[0]
    keluar = os.path.join(OUT_DIR, nama + ".mp3")
    if os.path.exists(keluar) and not force and not dry:
        return
    with open(path, encoding="utf-8-sig") as f:
        teks = rapikan(f.read())
    bagian = potong(teks, cfg["max_chars"])
    if dry:
        print(f"===== {nama} ({len(teks)} karakter, {len(bagian)} bagian) =====\n{teks}\n"); return
    if os.path.exists(keluar):
        os.remove(keluar)
    tmp = keluar + ".part"
    if os.path.exists(tmp):
        os.remove(tmp)
    for i, b in enumerate(bagian):
        print(f"  {nama}: bagian {i+1}/{len(bagian)}")
        buat_audio(b, cfg, key, tmp, bagian[i - 1] if i else None)
    os.replace(tmp, keluar)
    print(f"OK  -> audio/{nama}.mp3")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--voices", action="store_true")
    ap.add_argument("--voice")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    os.makedirs(IN_DIR, exist_ok=True); os.makedirs(OUT_DIR, exist_ok=True)
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG):
        cfg.update(json.load(open(CONFIG, encoding="utf-8")))
    else:
        json.dump(DEFAULTS, open(CONFIG, "w", encoding="utf-8"), indent=2)
    if a.voice:
        cfg["voice_id"] = a.voice

    key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not a.dry_run and not key:
        sys.exit("ELEVENLABS_API_KEY belum diset. Jalankan:  setx ELEVENLABS_API_KEY \"kunci_anda\"  lalu buka terminal baru.")
    if a.voices:
        return daftar_suara(key)
    if not a.dry_run and not cfg["voice_id"]:
        sys.exit("voice_id kosong. Jalankan --voices, lalu isi voice_id di config.json.")

    while True:
        for fn in sorted(os.listdir(IN_DIR)):
            if fn.lower().endswith(".txt"):
                proses(os.path.join(IN_DIR, fn), cfg, key, a.force, a.dry_run)
        if not a.watch:
            break
        a.force = False
        time.sleep(5)

if __name__ == "__main__":
    main()
