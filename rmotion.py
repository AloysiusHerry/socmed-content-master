"""Motion mesin Remotion: AI hanya menulis naskah + data per adegan (template hook/number/compare/list),
lalu proyek Remotion di folder remotion/ merender MP4 9:16 dengan subtitle karaoke dari timing kata TTS."""
import json, os, re, subprocess
import clipper, motion

BASE = os.path.dirname(os.path.abspath(__file__))
RDIR = os.path.join(BASE, "remotion")
FPS = 30
TEMPLATES = ("hook", "number", "compare", "list")

SPEC = """Setiap adegan WAJIB memakai salah satu template berikut (isi field-nya, singkat & padat):
- "hook": {"template":"hook","kicker":"2-4 kata label kecil","title":"judul 3-7 kata, 1 kata kunci diapit *bintang* jadi miring berwarna"}
- "number": {"template":"number","kicker":"...","title":"... *kata*","value":angka (tanpa titik/teks),"prefix":"Rp/$/kosong","suffix":"%/x/ km/kosong","decimals":0,"label":"apa angka ini","source":"sumber/hitungan singkat"}
- "compare": {"template":"compare","kicker":"...","title":"A vs *B*","label":"satuan/judul kartu","unit":"satuan pendek atau kosong","items":[{"name":"nama 1-3 kata","value":angka},{"name":"...","value":angka}],"source":"..."}  (2-4 item)
- "list": {"template":"list","kicker":"...","title":"... *kata*","points":["poin 2-6 kata","...","..."]}  (2-4 poin)
Aturan: adegan 1 = "hook". Pakai "number"/"compare" hanya untuk angka yang AKURAT (tulis sumbernya); kalau tidak yakin
angkanya, pakai "list" atau "hook". Angka di kartu harus sama dengan yang diucapkan narasi. Variasikan template."""


def write_script(topic, lang="id", seconds=30, api_key=None, model="claude-sonnet-5-5", log=print, own_script=""):
    words = int((seconds or 45) * clipper.WORDS_PER_SEC)
    bahasa = {"id": "Bahasa Indonesia", "en": "English", "ms": "Bahasa Melayu"}.get(lang, "Bahasa Indonesia")
    log("AI menulis naskah & data adegan (Remotion)...")
    if own_script:
        rule = f"""Naskah sudah ditulis pemilik channel. Bagi menjadi adegan TANPA mengubah, menambah, atau menghapus kata
(gabungan semua "text" harus sama persis):\n\"\"\"{own_script}\"\"\""""
    elif seconds and seconds > 0:
        rule = f"- Total {int(words * 0.9)}-{words} kata narasi (sekitar {seconds} detik)."
    else:
        mx = int(60 * clipper.WORDS_PER_SEC * 0.92)
        rule = f"- Panjang narasi kamu tentukan sendiri (biasanya 25-55 detik), MAKSIMAL {mx} kata."
    raw = motion._ask(f"""Tulis naskah video Shorts pendek dalam {bahasa}, gaya penjelas data yang santai.
Topik / brief: {topic or '(lihat naskah)'}
{rule}
- 4-7 adegan, tiap adegan 1-2 kalimat lisan yang pendek (pakai "kamu", bahasa ngobrol, bukan bahasa berita).
- Tulis angka di narasi seperti diucapkan bila perlu supaya suara AI membacanya benar.
- Tanpa sapaan, tanpa ajakan subscribe. Adegan terakhir = penutup yang membekas.
{SPEC}
Balas HANYA JSON: {{"title":"...","description":"...","hashtags":["#..."],
"scenes":[{{"text":"narasi adegan", ...field template...}}]}}""", api_key, model, log)
    m = re.search(r"\{.*\}", raw, re.S)
    s = json.loads(m.group(0)) if m else None
    if not s or not s.get("scenes"):
        raise RuntimeError("Naskah Remotion tidak valid dari AI")
    s["scenes"] = [clean_scene(x, i) for i, x in enumerate(s["scenes"]) if str(x.get("text", "")).strip()]
    for sc in s["scenes"]:   # ringkasan untuk editor naskah
        sc["visual"] = describe(sc)
    return s


def _num(v):
    if isinstance(v, (int, float)):
        return v
    t = re.sub(r"[^\d,.\-]", "", str(v or ""))
    t = t.replace(".", "").replace(",", ".") if re.search(r"\d\.\d{3}", t) else t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def clean_scene(x, i):
    """Pastikan data template valid; kalau rusak, turunkan ke template yang aman (hook/list)."""
    t = x.get("template") if x.get("template") in TEMPLATES else ("hook" if i == 0 else "list")
    sc = {"text": str(x["text"]).strip(), "template": t, "kicker": str(x.get("kicker") or "")[:40],
          "title": str(x.get("title") or "")[:70]}
    if t == "number":
        v = _num(x.get("value"))
        if v is None:
            t = sc["template"] = "hook"
        else:
            sc.update(value=v, prefix=str(x.get("prefix") or "")[:4], suffix=str(x.get("suffix") or "")[:6],
                      decimals=int(x.get("decimals") or (0 if float(v).is_integer() else 1)),
                      label=str(x.get("label") or "")[:50], source=str(x.get("source") or "")[:80])
    if t == "compare":
        items = [{"name": str(it.get("name") or "")[:24], "value": _num(it.get("value"))} for it in (x.get("items") or [])[:4]]
        items = [it for it in items if it["name"] and it["value"] is not None]
        if len(items) < 2:
            t = sc["template"] = "list"
            x = dict(x, points=[f"{it['name']}" for it in items] or [sc["title"] or sc["text"][:40]])
        else:
            sc.update(items=items, label=str(x.get("label") or "")[:40], unit=str(x.get("unit") or "")[:10],
                      source=str(x.get("source") or "")[:80])
    if t == "list":
        pts = [str(p)[:48] for p in (x.get("points") or []) if str(p).strip()][:4]
        sc["points"] = pts or [sc["title"] or sc["text"][:40]]
    if t == "hook" and not sc["title"]:
        sc["title"] = " ".join(sc["text"].split()[:6])
    return sc


def describe(sc):
    t = sc["template"]
    if t == "number":
        return f"🔢 Angka: {sc.get('prefix','')}{sc['value']:,.{sc.get('decimals',0)}f}{sc.get('suffix','')} · {sc.get('label','')}".replace(",", ".")
    if t == "compare":
        return "📊 Perbandingan: " + " vs ".join(f"{it['name']} {it['value']:,.0f}".replace(",", ".") for it in sc["items"])
    if t == "list":
        return "📋 Daftar: " + " · ".join(sc["points"])
    return f"🎣 Judul: {sc.get('title','')}"


def render(job_dir, scenes, timeline, duration, audio, brand, out_name, log=print):
    """Tulis props.json di folder job lalu render dengan Remotion (folder job = public dir, untuk audio)."""
    if not os.path.isdir(os.path.join(RDIR, "node_modules")):
        raise RuntimeError("Remotion belum terpasang: jalankan 'npm install' di folder remotion")
    end = duration + 0.6
    props = {"audio": audio, "duration": round(end, 2), "brand": brand,
             "scenes": [dict({k: v for k, v in sc.items() if k not in ("text", "visual")}, start=tl["start"],
                             end=(tl["end"] if i < len(timeline) - 1 else end))
                        for i, (sc, tl) in enumerate(zip(scenes, timeline))],
             "words": [w for tl in timeline for w in tl.get("words", [])]}
    pf = os.path.join(job_dir, "remotion_props.json")
    with open(pf, "w", encoding="utf-8") as f:
        json.dump(props, f, ensure_ascii=False)
    out = os.path.join(job_dir, out_name)
    cmd = ["npx.cmd" if os.name == "nt" else "npx", "remotion", "render", "src/index.ts", "Short", out,
           f"--props={pf}", f"--public-dir={job_dir}", "--log=info"]
    p = subprocess.Popen(cmd, cwd=RDIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="ignore")
    tail = []
    for line in p.stdout:
        tail = (tail + [line.rstrip()])[-30:]
        m = re.search(r"(Rendered|Encoded) (\d+)/(\d+)", line)
        if m:
            k, n = int(m.group(2)), int(m.group(3)) or 1
            frac = (0.85 * k / n) if m.group(1) == "Rendered" else 0.85 + 0.15 * k / n
            log(f"Render Remotion {m.group(1).lower()} {k}/{n} frame", frac)
    if p.wait() != 0 or not os.path.exists(out):
        raise RuntimeError("Render Remotion gagal:\n" + "\n".join(tail[-12:]))
    return out
