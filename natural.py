"""Rapikan bahasa narasi agar natural diucapkan.
Utama: Claude Code CLI (login langganan, tanpa API key). Cadangan: Gemini."""
import json, os, re, tempfile

import clipper

RULES = {
    "id": """Tulis ulang setiap narasi berikut dalam Bahasa Indonesia lisan yang natural, seperti YouTuber Indonesia bercerita ke temannya.
- Hindari kata baku/kaku: "merupakan", "tersebut", "adalah sebuah", "di mana", "oleh karena itu", "hal ini". Ganti dengan bahasa tutur ("itu", "jadi", "nah", "soalnya", "bayangin").
- Kalimat pendek (maks ±15 kata), enak dibaca keras, tidak terdengar seperti terjemahan.
- Boleh sapaan "kamu" ke penonton, tapi jangan berlebihan dan jangan tiap kalimat.
- Angka tulis seperti diucapkan bila membantu (mis. "abad ke-17", "sekitar lima ratus tahun").""",
    "en": """Rewrite each narration below as natural spoken English, like a YouTuber talking to a friend.
- Short sentences (max ~15 words), contractions, no stiff or academic phrasing.
- Address the viewer as "you" occasionally, not every sentence.""",
}

COMMON = """
ATURAN WAJIB / MUST:
- Isi, fakta, nama, dan urutan cerita TIDAK berubah. Jangan tambah fakta baru.
- Panjang tiap item tetap mirip (±10% jumlah kata) supaya durasi video tidak berubah.
- Jumlah item keluaran HARUS sama persis: {n} item, urutan sama.
- Keluarkan HANYA JSON array berisi {n} string, tanpa penjelasan, tanpa ```.

INPUT (JSON array):
{data}"""

BATCH = 15   # adegan per panggilan, supaya jawaban tidak terpotong


def _parse(raw, n):
    m = re.search(r"\[.*\]", raw or "", re.S)
    if not m:
        raise ValueError("jawaban AI bukan JSON array")
    out = json.loads(m.group(0))
    if not isinstance(out, list) or len(out) != n or not all(isinstance(x, str) and x.strip() for x in out):
        raise ValueError(f"jumlah adegan tidak cocok ({len(out) if isinstance(out, list) else '?'} ≠ {n})")
    return [x.strip() for x in out]


def _claude(prompt, log):
    return clipper._ask_claude_code(prompt, log, cwd=tempfile.gettempdir(), timeout=600)


def _gemini(prompt, key, log):
    import motion
    return motion._ask_gemini(prompt, key, log)


def naturalize(texts, lang="id", gemini_key=None, log=print):
    """texts: list narasi → (list hasil, mesin yang dipakai). Teks kosong dibiarkan."""
    idx = [i for i, t in enumerate(texts) if (t or "").strip()]
    out, engine = list(texts), None
    rules = RULES.get(lang, RULES["en"])
    for b in range(0, len(idx), BATCH):
        part = idx[b:b + BATCH]
        src = [texts[i] for i in part]
        prompt = rules + COMMON.format(n=len(src), data=json.dumps(src, ensure_ascii=False, indent=1))
        res, err = None, ""
        if engine != "gemini":
            for _ in range(2):
                try:
                    res = _parse(_claude(prompt, log), len(src)); engine = "claude"; break
                except Exception as e:
                    err = str(e)[:200]; log(f"Claude: {err}")
        if res is None:
            if not gemini_key:
                raise RuntimeError(f"Claude gagal ({err}) dan Gemini key belum diisi")
            log("Beralih ke Gemini...")
            res = _parse(_gemini(prompt, gemini_key, log), len(src)); engine = "gemini"
        for i, t in zip(part, res):
            out[i] = t
        log(f"Selesai {min(b + BATCH, len(idx))}/{len(idx)} adegan")
    return out, engine or "claude"
