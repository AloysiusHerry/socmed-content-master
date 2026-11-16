"""Video dokumenter panjang (16:9, 1080p): naskah per adegan + suara (CapCut) + gambar (Google Flow)
-> alignment Whisper -> render FFmpeg bertema -> final.mp4, final.srt, metadata.txt.

Satu folder per proyek (docs/<id>/): script.json, images/, voice.mp3, music.* (opsional), output/.
CLI:  python documentary.py <folder_proyek> [--theme noir] [--music file.mp3]"""
import hashlib, json, os, random, re, shutil, subprocess, sys, threading
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher

import clipper

BASE = os.path.dirname(os.path.abspath(__file__))
DOCS_DIR = os.path.join(BASE, "docs")
FONTS_DIR = os.path.join(BASE, "fonts")      # taruh font tambahan (mis. Poppins) di sini
W, H, FPS = 1920, 1080, 30
THUMB_SEC = 1.0   # lama gambar thumbnail di awal video
WPS = 2.5                                    # perkiraan kecepatan TTS (kata/detik) untuk SRT sementara
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")
VID_EXT = (".mp4", ".mov", ".webm")


def clip_for(pdir, im):
    """Klip animasi untuk gambar `im` (mis. flow_003.jpg -> flow_003.mp4 hasil Veo image-to-video), atau None.
    Gambar yang punya klip dirender sebagai video bergerak, bukan zoom gambar diam."""
    stem = os.path.splitext(im)[0]
    for e in VID_EXT:
        f = os.path.join(pdir, "images", stem + e)
        if os.path.exists(f): return f
    return None


def themes():
    with open(os.path.join(BASE, "themes.json"), encoding="utf-8") as f:
        return {k: v for k, v in json.load(f).items() if not k.startswith("_")}


# ---------- proyek ----------

def load(pdir):
    with open(os.path.join(pdir, "script.json"), encoding="utf-8") as f:
        return json.load(f)


def save(pdir, s):
    # tulis ke file sementara lalu ganti sekaligus, agar script.json tidak rusak bila server mati saat menyimpan
    path = os.path.join(pdir, "script.json")
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)
    os.replace(path + ".tmp", path)


def _ts(t, sep=","):
    t = max(t, 0)
    return f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{int(t % 60):02d}{sep}{int(round((t % 1) * 1000)) % 1000:03d}"


def write_temp_srt(pdir, s):
    """script.srt untuk CapCut: 1 caption = 1 adegan, durasi dari jumlah kata (sementara)."""
    # suara Indonesia lebih lambat dari Inggris; slot yang terlalu pendek membuat suara CapCut tumpang tindih.
    # Slot dibuat lega (jeda kelebihannya nanti dipotong otomatis oleh "Rapatkan jeda").
    wps = WPS if s.get("lang", "en") == "en" else 1.8
    t, out = 0.0, []
    for i, sc in enumerate(s["scenes"], 1):
        d = max(2.5, len(sc["text"].split()) / wps * 1.1)
        out.append(f"{i}\n{_ts(t)} --> {_ts(t + d)}\n{sc['text'].strip()}\n")
        t += d + 0.5
    path = os.path.join(pdir, "script.srt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    return path


def natural_key(name):
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", name)]


def list_images(pdir, order="name"):
    d = os.path.join(pdir, "images")
    if not os.path.isdir(d):
        return []
    files = [f for f in os.listdir(d) if f.lower().endswith(IMG_EXT) and not f.lower().startswith("thumb.")]   # thumbnail bukan gambar adegan
    if order == "time":
        return sorted(files, key=lambda f: os.path.getmtime(os.path.join(d, f)))
    return sorted(files, key=natural_key)


def auto_map(s, files):
    """Bagi rata gambar ke adegan bila jumlahnya kelipatan jumlah adegan. Kembalikan pesan error atau None."""
    n = len(s["scenes"])
    if not files:
        return "Belum ada gambar"
    if len(files) % n:
        return f"{len(files)} gambar untuk {n} adegan bukan kelipatan; atur gambar per adegan secara manual"
    k = len(files) // n
    for i, sc in enumerate(s["scenes"]):
        sc["images"] = files[i * k:(i + 1) * k]
    return None


# ---------- generator naskah (1 panggilan AI) ----------

VISUAL_STYLES = {
    "medical3d": "clean 3D educational medical illustration, soft studio lighting, semi-transparent stylized organs, "
                 "fresh glossy fruits and food, plain bright pastel background, friendly and scientific, no gore, 16:9",
    "cartoon": "cartoon comic book illustration style, bold outlines, vibrant colors, 16:9",
    "caveman": "funny hand-drawn doodle of prehistoric cavemen, thick black marker lines, simple flat colors, "
               "beige paper background, expressive cartoon faces, 16:9",
    "detektif": "noir detective cartoon illustration, a detective in a fedora hat and beige trench coat investigating "
                "the scene as a case (magnifying glass, case file, evidence board with polaroids, yellow sticky notes and "
                "red string, interrogation lamp), dark warm brown and black palette, rainy night, single warm yellow light, "
                "vignette, bold clean cartoon outlines, 16:9",
    "stickman": "minimalist black stickman drawing on a plain white background, clean thin lines, small red and blue "
                "accent colors only, explainer video style, 16:9",
    "flathistory": "flat vector cartoon illustration of historical scenes, simple shapes, muted earthy palette, "
                   "clean outlines, educational history channel style, 16:9",
    "sketch": "colored pencil hand sketch with loose scribbled coloring on white paper, visible pencil strokes, "
              "notebook explainer style, 16:9",
    "clay3d": "cute 3D claymation style, soft plasticine characters and props, rounded shapes, bright pastel colors, "
              "soft studio lighting, 16:9",
    "anime": "soft painted anime illustration, gentle watercolor lighting, delicate colors, Studio-style background "
             "art, emotional atmosphere, 16:9",
    "papercut": "layered paper cutout collage, textured craft paper, visible paper edges and soft drop shadows, "
                "handmade diorama look, 16:9",
    "nusantara": "Indonesian folklore storybook illustration, batik-inspired ornaments, warm traditional colors, "
                 "wayang-inspired characters, painterly, 16:9",
    "toonstory": "2D cartoon character with a big round white head, small dot eyes, thin expressive eyebrows and a tiny "
                 "mouth, simple thin body in a period costume, flat cel shading with soft outline, standing in a richly "
                 "detailed colorful 3D-rendered environment, warm saturated lighting, storybook animated explainer look, 16:9",
    "lofi": "lofi animation style, cozy storybook illustration, soft grainy texture, warm nostalgic lighting, muted pastel "
            "palette, simple expressive characters in everyday Indonesian settings (warung, office, kos room, street), "
            "calm and emotional mood, 16:9",
    "finance": "dark premium financial data visualization, deep navy and charcoal background, glowing finance-green and "
               "alert-red accents, sleek 3D bar charts, line graphs, stacks of cash, skyscrapers, trading screens and "
               "symbolic objects, cinematic rim lighting, minimal and elegant, no readable text or numbers, 16:9",
    "cinematic": "cinematic photorealistic still, dramatic lighting, shallow depth of field, film look, 16:9",
    "painting": "detailed digital oil painting, historical illustration, warm muted palette, 16:9",
    "noir": "dark film noir illustration, high contrast, moody shadows, desaturated, 16:9",
    "archive": "vintage black and white archival photograph style, film grain, 1940s, 16:9",
}


LANGS = {   # bahasa narasi: (instruksi naskah, model Whisper untuk alignment, kode bahasa Whisper)
    "en": ("You are a YouTube documentary scriptwriter for a US audience. Write an ENGLISH narration script.", "base.en", "en"),
    "id": ("You are a YouTube documentary scriptwriter for an Indonesian audience. Write the narration script in natural, "
           "engaging BAHASA INDONESIA (casual-informative storytelling tone, not stiff). Write every number and year in "
           "words as it should be spoken (e.g. \"tahun seribu sembilan ratus dua puluh lima\"). Titles, description, tags, "
           "hashtags and chapter titles must also be in Bahasa Indonesia. Image prompts stay in ENGLISH.", "small", "id"),
}


FORMATS = {   # format naskah: instruksi struktur
    "documentary": "classic documentary storytelling: setup, rising discoveries, climax, reflective ending",
    "mystery": "unsolved mystery: open with the strangest detail, present clues one by one, competing theories, open ending",
    "top": "countdown list (e.g. top 7): each item is its own chapter, build up to the most surprising item last",
    "chronology": "chronological timeline: clear dates and turning points, cause and effect between events",
    "folklore": "folk tale / legend retold as a story: characters, conflict, moral; clearly framed as legend, not fact",
    "biography": "biography of a person: origin, struggle, turning point, legacy",
    "howitworks": "how it works explainer: simple analogy first, then step by step mechanism, then surprising implication",
    "facts": "fast weird-facts compilation: each scene one surprising, verifiable fact with a mini hook",
    "health": "health explainer: common misconception, what research says, practical safe tips, who should be careful",
    "history": "history of an invention/discovery: the problem, failed attempts, breakthrough, how it changed the world",
    "whatif": "what-if scenario: realistic science-based consequences step by step, clearly framed as hypothetical",
    "storytale": "micro story that explains a real invention, trick or historical fact: first line is the strange action "
                 "itself (\"He cut gaps into a railroad every 39 feet.\"), then present tense storytelling with one clever "
                 "protagonist, a doubter who mocks him with 1-2 short quoted lines, and a rival who does it the 'normal' way "
                 "and seems to win; then the twist where the hidden science/history proves him right, and a final line with "
                 "the real-world fact. No narrator intro, no 'did you know', every sentence pushes the story forward",
    "macroessay": "macroeconomic video essay ('connecting the dots'): (1) COLD HOOK, no greeting/intro, open on a recent big "
                  "event plus a contradiction (\"Last week the Fed voted unanimously. Seven weeks earlier, the same room "
                  "couldn't agree on anything.\"); (2) IN PLAIN ENGLISH: translate the jargon and say what it means for an "
                  "ordinary person's wallet; (3) THE SCALE: make huge numbers tangible by comparing them with history or "
                  "everyday things (e.g. AI data-center spending vs building America's railroads in the 1800s); (4) THE REAL "
                  "PROBLEM / plot twist, the longest part: shift from the obvious issue to the hidden time bomb most people "
                  "miss (debt, liquidity, who is really funding it); (5) OUTRO: connect all the dots in one conclusion. "
                  "Dense, no filler, every sentence a new idea, cite real institutions, reports and numbers; analysis only, "
                  "never personal investment advice or buy/sell calls",
    "povstory": "second-person POV personal-finance fable (\"you\"): a fictional modern short story, NOT a lecture. "
                "(1) HOOK: a very specific everyday scene with contrast (your shoes resoled again and again while your "
                "coworker shows off a new motorbike), then the financial twist (same salary, his balance 2.4 million, "
                "yours 1.1 billion); (2) ORIGIN: the small epiphany moment from an ordinary person (e.g. an old cobbler: "
                "'taking care of what you have makes it last') as a metaphor for investing; (3) PROGRESSION & MISTAKE: "
                "time jumps stated with age + balance, then a mistake (you reveal your balance at the office and friends "
                "start seeing you as a wallet; lesson: money grows best in silence); (4) REALITY CHECK: the market crashes, "
                "the balance shrinks, the loneliness of not celebrating with anyone; (5) RESOLUTION: the goal is reached "
                "quietly in a cheap street-food stall, and the money is used to give a parent relief (e.g. a holiday for "
                "mom), not to show off. Use concrete numbers (ages, balances, monthly auto-debit) the viewer can picture; "
                "invent a named friend; numbers are illustrative. Never recommend specific products or tickers",
    "versus": "versus comparison: two sides, criteria round by round, honest verdict",
    "tokoh": "facts told as ONE continuous second-person POV story (\"you\") where the VIEWER is the main character, stuck in "
             "one tense, specific real-life situation that fits the topic (e.g. a stormy night flight in seat 12A, the last "
             "train home, a night alone in an old hospital ward). Structure: (1) COLD OPEN, no greeting: day/time + exact "
             "place + the moment it goes wrong, sensory details, and a fearful thought (\"That wing is going to snap.\"); "
             "(2) FACT LOOPS, each in its own scene and each one escalating: the character notices something frightening "
             "or strange -> states the scary assumption a normal person would make -> the reveal (\"But here is the first "
             "secret engineers hide from you:\", \"The reality?\", \"No. That was...\") giving the real name of the thing, "
             "one concrete number, and how it actually protects or helps you; then move the plot forward to the next moment; "
             "(3) RESOLUTION: the situation ends calmly with a small human detail, then ONE big theme sentence that ties all "
             "the facts together (\"The aviation industry isn't built on bravery. It is built on highly intelligent "
             "paranoia.\") and a final line about what the viewer never noticed; (4) OUTRO: a like/subscribe line tied to "
             "one surprising fact from the video, plus one question for the comments. Present tense, short punchy "
             "sentences, every fact real and documented. Image prompts: show the viewer-character as ONE fixed person "
             "(age, hair, clothes, one signature detail) described identically in every prompt where they appear, mixing "
             "POV shots, close-ups of the strange object, and explanatory diagram/cutaway shots for each reveal"
}
TONES = {
    "santai": "casual and friendly, like talking to a friend",
    "misteri": "suspenseful and mysterious, slow reveals",
    "dramatis": "dramatic and cinematic, strong emotions",
    "lucu": "light and humorous, playful jokes (facts stay accurate)",
    "dokumenter": "calm, authoritative documentary narrator",
    "inspiratif": "inspiring and motivational",
    "edukatif": "clear teacher-like explanation for beginners",
    "analis": "calm, dry, slightly cynical and very authoritative, like a senior Wall Street analyst quietly telling "
              "you what really happens behind the curtain",
    "reflektif": "calm, reflective, slightly melancholic and intimate, like reading a novel aloud or telling a close "
                 "friend a secret late at night; no vlogger hype",
    "dongeng":"warm bedtime-story tone for children, simple words",
}


def lang_of(s):
    return s.get("lang") if s.get("lang") in LANGS else "en"


def storyboard_minutes(sb):
    """Durasi storyboard dari timestamp terakhir, mis. "[08:30 - 09:30]" -> 9.5 menit (None bila tidak ada)."""
    ts = [int(m) * 60 + int(s) for m, s in re.findall(r"\b(\d{1,2}):(\d{2})\b", sb or "")]
    return round(max(ts) / 60, 1) if ts else None


def storyboard_plan(sb, lang, wpm=None):
    """Target per adegan storyboard dari timestamp "Adegan 4 (01:00 - 01:40)": (nama, detik, kata, jumlah gambar)."""
    wpm = wpm or (130 if lang == "id" else 150)
    rows = []   # juga "SCENE 3 — Judul (2:00–3:00)" (judul di antara nomor & timestamp, tanda pisah en/em)
    for m in re.finditer(r"(Adegan|Scene)\s*(\d+)[^\n]*?\((\d{1,2}):(\d{2})\s*[-–—]\s*(\d{1,2}):(\d{2})\)", sb, re.I):
        secs = (int(m.group(5)) * 60 + int(m.group(6))) - (int(m.group(3)) * 60 + int(m.group(4)))
        if secs > 0:
            rows.append((f"{m.group(1)} {m.group(2)}", secs, round(secs * wpm / 60), max(1, round(secs / 10))))
    return rows


def storyboard_prompt(sb, lang, style, words, tone, wpm=None):
    plan = storyboard_plan(sb, lang, wpm)
    table = ("\nREQUIRED LENGTH PER STORYBOARD SCENE (hard requirement, the voiceover must really fill this time):\n" +
             "\n".join(f"- {n}: {s} s -> about {w} words of narration, split into {k} script scenes (one image each)"
                       for n, s, w, k in plan) +
             f"\nTotal: {sum(r[3] for r in plan)} script scenes, about {sum(r[2] for r in plan)} words. To fill long moments,"
             " add more accurate, funny historical detail and commentary about the same visual idea (extra facts, comparisons"
             " with today, the narrator's reactions) — never filler or repetition. Each extra script scene shows a new"
             " camera angle or small step of the same storyboard action.\n") if plan else ""
    return _storyboard_body(sb, lang, style, words, tone) + table


def expand_storyboard(scenes, sb, lang, words, ask, parse, log, rounds=2, wpm=None):
    """Narasi terlalu pendek untuk durasi storyboard -> minta AI memperpanjang (maks `rounds` putaran) sampai >= 85% target.
    AI menerima naskah lengkap + kekurangan kata per adegan storyboard, lalu mengembalikan daftar adegan baru yang utuh."""
    wpm = wpm or (130 if lang == "id" else 150)
    plan = {int(re.sub(r"\D", "", n)): (w, k) for n, s, w, k in storyboard_plan(sb, lang, wpm)}
    count = lambda xs: sum(len(x["text"].split()) for x in xs)
    for r in range(rounds):
        got = count(scenes)
        if got >= words * 0.85:
            break
        log(f"Narasi {got} kata (~{got / wpm:.1f} mnt) masih kurang dari target {words} kata; AI memperpanjang "
            f"(putaran {r + 1}/{rounds})...")
        per = {}
        for sc in scenes:
            try:
                per[int(sc.get("sb"))] = per.get(int(sc.get("sb")), 0) + len(sc["text"].split())
            except (TypeError, ValueError):
                pass
        short_rows = [f"- storyboard scene {n}: has {per.get(n, 0)} words in {sum(1 for x in scenes if x.get('sb') == n)} "
                      f"script scenes, needs about {w} words in {k} script scenes" for n, (w, k) in sorted(plan.items())
                      if per.get(n, 0) < w * 0.85]
        prompt = f"""The narration script below is TOO SHORT for its storyboard: {got} words, but the video needs about
{words} words ({words / wpm:.1f} minutes of voiceover). Rewrite and EXPAND it to about {words} words.
{chr(10).join(short_rows) or "- every storyboard scene needs proportionally more narration"}
Rules: keep the same order, storyboard scenes, chapters, language, tone and the existing good lines; add new script scenes
(one new image each, 20-40 words) with accurate extra historical detail, comparisons with today and the narrator's funny
reactions about the SAME visual idea — no filler, no repetition. New scenes need their own image prompt in the same style
prefix, a "motion" sentence, "sfx" (usually empty) and the storyboard scene number in "sb". Facts worth checking go in "flag".
STORYBOARD (for reference):
\"\"\"{sb.strip()}\"\"\"
CURRENT SCRIPT (JSON):
{json.dumps(scenes, ensure_ascii=False)}
Return ONLY valid JSON: {{"scenes": [{{"text": "...", "prompts": ["..."], "motion": "...", "chapter": "... or null", "num": "", "flag": "", "sfx": "", "sb": 1}}]}}"""
        try:
            m = re.search(r"\{.*\}", ask(prompt, "memperpanjang naskah"), re.S)
            longer = parse(json.loads(m.group(0)).get("scenes", [])) if m else []
        except (ValueError, RuntimeError) as e:
            log(f"Perpanjangan gagal ({str(e)[:80]}), naskah lama dipakai")
            break
        if count(longer) <= got:   # AI tidak memperpanjang: jangan ganti naskah yang sudah ada
            break
        scenes = longer
    got = count(scenes)
    if got < words * 0.6:
        log(f"Peringatan: narasi {got} kata (~{got / wpm:.1f} mnt), lebih pendek dari target {words} kata. "
            "Tambah di editor atau buat ulang dengan Claude.")
    else:
        log(f"Narasi {got} kata (~{got / wpm:.1f} mnt), target {words / wpm:.1f} mnt")
    return scenes


def _storyboard_body(sb, lang, style, words, tone):
    voice = {"id": "BAHASA INDONESIA lisan yang santai dan lucu, seperti ngobrol dengan teman (pakai \"kamu\"; ikuti gaya "
                   "bahasa storyboard bila memakai \"lu/gua\"). Tulis angka dan tahun dalam kata seperti diucapkan.",
             "en": "casual, funny spoken ENGLISH, like talking to a friend."}.get(lang, "casual spoken English.")
    return f"""You are the narrator-writer for a YouTube video. The creator already made the STORYBOARD below (visuals,
character movements and SFX per scene, with timestamps) and may or may not include draft narration. Write the narration in {voice}
IF a storyboard scene already has narration (NARASI / NARRATION / VO): keep its lines, wording and key facts (light polish only)
as the core of that scene, then add accurate extra detail around it to reach the length below; keep promises it makes
(e.g. "number 12 will save you") and any quiz -> answer order. On-screen text lines (TEKS LAYAR) are not narration.
Narration tone: {TONES.get(tone, "light and humorous, playful jokes (facts stay accurate)")}.

RULES
- Follow the storyboard EXACTLY: same order, same characters, same segments. Do not add or drop storyboard scenes.
- Each storyboard scene ("Adegan") becomes 1 OR MORE consecutive script scenes of 20-40 words each, so that one image is
  shown every ~8-12 seconds. Budget words by the storyboard timestamps (about {round(words / max(storyboard_minutes(sb) or 1, 0.1))} words per minute);
  total about {words} words.
- The narration must describe/comment on what is happening on screen in that moment (the joke or fact behind the visual),
  so the voice matches the picture. Historical facts must be accurate and well documented; frame legends as legends and put
  a short note in "flag" for any claim worth double-checking.
- The intro/outro calls to action in the storyboard (subscribe, like) may be spoken naturally, short.
- Every storyboard SEGMENT title becomes a "chapter" (3-6 words) on the first script scene of that segment; null otherwise.
- "prompts": exactly 1 image prompt per script scene that shows that moment's visual. Start every prompt with this shared
  style: "{style}". Keep each recurring character described the SAME way in every prompt (e.g. the narrator stickman always
  wears black sunglasses). No text, captions or logos in images unless the storyboard asks for a label on an object.
- "motion": one short English sentence for image-to-video, taken from the storyboard "Gerakan" (what moves + one camera move).
- "sfx": the storyboard SFX for that moment (in the storyboard's language), empty if none for this part.

STORYBOARD:
\"\"\"{sb.strip()}\"\"\"
"""


def generate_script(topic, minutes=10, visual="cartoon", images_per_scene=1, theme_hint="", api_key=None,
                    model="claude-sonnet-5-5", log=print, lang="en", fmt="", tone="", aspect="16:9", storyboard="", wpm=None):
    """wpm: kecepatan suara yang akan dipakai (CapCut ±200 kata/menit; default suara AI 150 EN / 130 ID).
    Naskah + prompt gambar + metadata YouTube + penanda klaim, dalam SATU permintaan JSON (hemat token).
    storyboard: storyboard buatan sendiri (visual/gerakan/SFX tanpa narasi) -> AI menulis narasinya mengikuti urutan itu."""
    lang = lang if lang in LANGS else "en"
    if storyboard:
        minutes = storyboard_minutes(storyboard) or minutes
        images_per_scene = 1
    wpm = int(wpm or (150 if lang == "en" else 130))
    words = int(minutes * wpm)   # kata/menit narasi TTS (kata Indonesia lebih panjang)
    short = minutes < 3
    if short:   # Shorts: ±15 gambar per menit (ganti tiap ±4 dtk, tiap gambar dipecah 2 sudut kamera); pola hook - 3 poin - loop
        n_lo, n_hi, per = max(6, int(words / 14)), max(8, int(words / 10)), "10-14"
    else:
        n_lo, n_hi, per = max(8, words // 45), max(10, words // 32), "25-45"
    style = VISUAL_STYLES.get(visual, visual).replace("16:9", aspect)
    extra = ""
    if fmt in FORMATS:
        extra += f"\nFormat: {FORMATS[fmt]}."
    if tone in TONES:
        extra += f"\nNarration tone: {TONES[tone]}."
    if short and fmt == "storytale":
        extra += ("\nThis is a ~1 minute vertical SHORT told as one continuous story (no list of points). Scene 1 is the strange "
                  "action in max 10 words. Keep sentences short and visual, the twist lands in the last 15 seconds, the final "
                  "sentence states the real fact plainly. No like/subscribe call to action. Use the SAME protagonist description "
                  "(face, hat, clothes, colors) in every image prompt so the character stays consistent.")
    elif short:
        extra += ("\nThis is a ~1 minute vertical SHORT: scene 1 is a scroll-stopping hook (max 8 words), then exactly 3 fast "
                  "points, last scene ends mid-sentence so it loops back into the hook. No like/subscribe call to action."
                  "\nThe hook must be a contradiction or a mystery that the video then answers, e.g. \"History books lied to "
                  "you about...\", \"This animal can live for months without...\", \"Nobody can explain why...\". It must "
                  "still be TRUE (no fake claims). No greeting, no intro, no 'in this video'. Keep every sentence short and "
                  "punchy; no filler words.")
    ending = "" if short else "\nLast scene: ask viewers a question to answer in the comments, then ask them to like and subscribe."
    if fmt == "povstory":
        ending = ("\nLast scene: a short disclaimer that the story is fictional and the numbers are only illustrations, not "
                  "financial advice; then ONE deep psychological question for the comments (e.g. \"If you had one billion "
                  "one day, who would you tell, or would you keep it secret?\"). No hype, no 'smash the like button'.")
    if fmt == "macroessay" and not short:
        ending = ("\nLast scene: a dry outro saying this is the kind of detail traditional media skips because it doesn't make "
                  "a catchy headline; if you want to see behind the curtain of the economy, subscribe. No 'like and comment' list.")
    prompt = f"""{LANGS[lang][0]}
Topic: {topic}
Target length: about {words} words total (~{minutes} minutes of voiceover), split into {n_lo}-{n_hi} scenes of {per} words each.
Style: storytelling, curiosity hook in the first scene, open loops, concrete facts, numbers and names; vary sentence length.{extra}{ending}
HEALTH/FOOD/MEDICINE topics (YouTube medical misinformation policy): never claim something cures, destroys, kills or
eliminates a disease, never say "guaranteed", "100%" or "without medicine", never suggest stopping or replacing doctor-prescribed
treatment. Use honest wording ("may help", "is linked to", "research suggests"), mention limits/who should be careful, and add a
short "educational content, not medical advice" line at the end of the description (in the narration language). Titles must follow
the same rule and still be click-worthy (curiosity, not false promises). Flag every specific health claim in "flag".
ACCURACY: only state facts that are well documented. If a claim is folklore, anecdotal, or unproven, frame it as such
in the narration ("legend says", "some people claim") AND put a short note in that scene's "flag" field.
For every scene give {images_per_scene} image prompt(s) that visualise exactly what is being said. Each prompt must start with
this shared style so all images look consistent: "{style}". No text, captions, logos or real celebrities in images.
When the narration lists several points/reasons/steps, START each point in its own scene and put its number in "num" ("1", "2", ...; empty otherwise).
Mark the scene where each new chapter starts with a short chapter title (3-6 words) in "chapter" (null otherwise);
the first scene must have a chapter. Aim for {'3-4' if short else '5-8'} chapters.
{f'Visual mood hint: {theme_hint}' if theme_hint else ''}
Return ONLY valid JSON:
{{"metadata": {{"titles": ["3 different click-worthy but honest titles, max 70 chars"], "description": "2-3 paragraphs, SEO friendly",
"tags": ["15-25 tags"], "hashtags": ["#3-5"]}}, "thumbnail_prompt": "ONE eye-catching YouTube thumbnail image prompt (same shared style prefix) built around ONE absurd, physically impossible visual twist that makes people stop scrolling and ask \"wait, what?\" — e.g. a giant object in a tiny place, something melting/cracking/floating that never does, wrong scale, an everyday item doing something impossible — directly tied to the topic, not random; bold single subject, close-up, high contrast, strong facial emotion (shock/fear/curiosity), empty space for title text, no text in image",
"theme": "one of documentary|noir|archive|fun that fits the topic",
"scenes": [{{"text": "narration", "prompts": ["..."], "motion": "one short English sentence: how this image should animate for image-to-video (what the character does + one camera move)", "chapter": "Chapter title or null", "num": "point number or empty", "flag": "claim to verify or empty"}}]}}"""
    if storyboard:   # ganti bagian penulisan bebas dengan aturan storyboard; format JSON & metadata tetap sama
        tail = prompt[prompt.index("HEALTH/FOOD"):]
        tail = tail.replace('"flag": "claim to verify or empty"}', '"flag": "claim to verify or empty", "sfx": "SFX or empty", "sb": storyboard scene number (integer)}')
        prompt = f"Topic: {topic}\n" + storyboard_prompt(storyboard, lang, style, words, tone, wpm) + tail
    def ask(prompt, what="menulis naskah"):
        if api_key and api_key.startswith("gemini:"):
            import motion
            return motion._ask_gemini(prompt, api_key[7:], log, label=f"Gemini {what}", est=240)
        if api_key and api_key != "__claude_code__":
            import anthropic
            log(f"Claude API ({model}) {what}...")
            msg = anthropic.Anthropic(api_key=api_key).messages.create(
                model=model, max_tokens=16000, messages=[{"role": "user", "content": prompt}])
            return "".join(b.text for b in msg.content if b.type == "text")
        tick = lambda s: log(f"Claude {what}... {s // 60}:{s % 60:02d} berjalan (biasanya 2-6 menit)", min(s / 300, 0.95))
        return clipper._ask_claude_code(prompt, log, timeout=1500, progress=tick)

    def parse(sc_list):
        return [{"text": sc["text"].strip(), "prompts": [p for p in sc.get("prompts", []) if p][:images_per_scene] or [],
                 "chapter": sc.get("chapter") or None, "flag": (sc.get("flag") or "").strip(),
                 "motion": (sc.get("motion") or "").strip(),
                 **({"sfx": sc["sfx"].strip()} if str(sc.get("sfx") or "").strip() else {}),
                 **({"num": str(sc["num"]).strip().lstrip("#")} if str(sc.get("num") or "").strip() else {}),
                 **({"sb": sc["sb"]} if storyboard and sc.get("sb") is not None else {})}
                for sc in sc_list if str(sc.get("text") or "").strip()]

    raw = ask(prompt)
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise RuntimeError("Jawaban AI tidak berisi JSON")
    data = json.loads(m.group(0))
    scenes = parse(data.get("scenes", []))
    if not scenes:
        raise RuntimeError("Naskah kosong dari AI")
    if storyboard:
        scenes = expand_storyboard(scenes, storyboard, lang, words, ask, parse, log, wpm=wpm)
        for sc in scenes:
            sc.pop("sb", None)
    if short:   # AI kadang menulis terlalu banyak gambar: maks ±18 gambar per menit
        cap_prompts(scenes, max(8, int(18 * minutes)))
    out = {"topic": topic, "lang": lang, "visual": visual, "aspect": aspect, "format": fmt, "tone": tone,
            "dynamics": "rapi", "theme": "toon" if fmt == "storytale" else "finance" if fmt == "macroessay" else "lofi" if fmt == "povstory" else data.get("theme") if data.get("theme") in themes() else "auto",
            "metadata": data.get("metadata") or {}, "scenes": scenes,
            "thumbnail_prompt": (data.get("thumbnail_prompt") or "").strip(), "minutes": minutes, "wpm": wpm}
    if not storyboard and not short:   # AI sering menulis terlalu pendek: perpanjang otomatis (maks 2 putaran)
        for r in range(2):
            got = sum(len(x["text"].split()) for x in out["scenes"])
            if got >= words * 0.85: break
            log(f"Naskah {got} kata, target {words}: memperpanjang otomatis ({r + 1}/2)...")
            try:
                out, _ = expand_script(out, words, api_key, log=log)
            except Exception as e:
                log(f"Perpanjangan gagal ({e}); pakai naskah yang ada."); break
    return out


def cap_prompts(scenes, cap):
    """Kurangi total prompt gambar jadi maks `cap`: prompt ke-2 dibuang dulu dari adegan berteks paling pendek
    (setiap adegan tetap punya minimal 1 gambar). Mengembalikan jumlah prompt yang dibuang."""
    total = sum(len(sc.get("prompts") or []) for sc in scenes)
    cut = 0
    for sc in sorted((sc for sc in scenes if len(sc.get("prompts") or []) > 1), key=lambda sc: len(sc["text"].split())):
        if total - cut <= cap:
            break
        sc["prompts"] = sc["prompts"][:1]; cut += 1
    return cut


def parse_scenes(spec, n):
    """'3-7, 12' -> [2,3,4,5,6,11] (indeks 0-based). Kosong = semua adegan."""
    if not (spec or "").strip():
        return list(range(n))
    out = set()
    for part in re.split(r"[,\s]+", spec.strip()):
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2) or m.group(1))
            out.update(i - 1 for i in range(min(a, b), max(a, b) + 1) if 1 <= i <= n)
    return sorted(out)


def regen_prompts(s, idxs, visual=None, images_per_scene=None, api_key=None, model="claude-sonnet-5-5", log=print, batch=25):
    """Buat ulang HANYA prompt gambar untuk adegan `idxs`; naskah, chapter, dan urutan gambar tidak diubah."""
    visual = visual or s.get("visual") or "cartoon"
    style = VISUAL_STYLES.get(visual, visual).replace("16:9", s.get("aspect", "16:9"))
    ips = int(images_per_scene or max([len(sc.get("prompts") or []) for sc in s["scenes"]] + [1]))
    for b0 in range(0, len(idxs), batch):
        part = idxs[b0:b0 + batch]
        log(f"Menulis prompt gambar adegan {part[0] + 1}-{part[-1] + 1} ({b0 + len(part)}/{len(idxs)})...", b0 / len(idxs))
        ctx = [{"scene": i + 1, "text": s["scenes"][i]["text"],
                "prev": s["scenes"][i - 1]["text"][-200:] if i else "", "old_prompts": s["scenes"][i].get("prompts") or []}
               for i in part]
        prompt = f"""You write image prompts for a documentary video titled "{s.get('topic', '')}".
For EACH scene below write {ips} NEW image prompt(s) in ENGLISH that visualise exactly what that scene's narration says
(use "prev" only for context). Every prompt must START with this shared style so all images look consistent:
"{style}". Be concrete (subject, action, setting, composition, camera angle). Vary composition between scenes.
No text, captions, logos or real celebrities. Health topics: no gore, no frightening medical imagery.
Do not copy old_prompts; write fresh ones that match the style above.
Scenes: {json.dumps(ctx, ensure_ascii=False)}
Return ONLY JSON: {{"scenes": [{{"scene": 1, "prompts": ["..."]}}]}}"""
        if api_key and api_key.startswith("gemini:"):
            import motion
            raw = motion._ask_gemini(prompt, api_key[7:], log)
        elif api_key and api_key != "__claude_code__":
            import anthropic
            msg = anthropic.Anthropic(api_key=api_key).messages.create(
                model=model, max_tokens=8000, messages=[{"role": "user", "content": prompt}])
            raw = "".join(b.text for b in msg.content if b.type == "text")
        else:
            raw = clipper._ask_claude_code(prompt, log, timeout=900)
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            raise RuntimeError("Jawaban AI tidak berisi JSON")
        got = {int(x.get("scene", 0)): [p for p in x.get("prompts", []) if p][:ips] for x in json.loads(m.group(0)).get("scenes", [])}
        for i in part:
            if got.get(i + 1):
                s["scenes"][i]["prompts"] = got[i + 1]
    s["visual"] = visual
    return s


def _ask_ai(prompt, api_key, log, timeout=1500, model="claude-sonnet-5-5"):
    if api_key and api_key.startswith("gemini:"):
        import motion
        return motion._ask_gemini(prompt, api_key[7:], log)
    if api_key and api_key != "__claude_code__":
        import anthropic
        msg = anthropic.Anthropic(api_key=api_key).messages.create(
            model=model, max_tokens=32000, messages=[{"role": "user", "content": prompt}])
        return "".join(b.text for b in msg.content if b.type == "text")
    return clipper._ask_claude_code(prompt, log, timeout=timeout)


def expand_script(s, target_words, api_key=None, log=print):
    """Panjangkan naskah yang sudah ada sampai ±target_words. Adegan lama (beserta gambar & prompt-nya) dipertahankan
    dan urutannya tetap; AI hanya boleh menambah kata di adegan lama dan MENYISIPKAN adegan baru (dengan prompt gambar)."""
    scenes = s["scenes"]
    got = sum(len(x["text"].split()) for x in scenes)
    if got >= target_words * 0.95:
        return s, 0
    style = VISUAL_STYLES.get(s.get("visual") or "cartoon", s.get("visual") or "").replace("16:9", s.get("aspect") or "16:9")
    fmt = FORMATS.get(s.get("format") or "", "")
    cur = [{"id": i, "text": x["text"], "chapter": x.get("chapter")} for i, x in enumerate(scenes)]
    prompt = f"""{LANGS.get(s.get('lang') or 'en', LANGS['en'])[0]}
This YouTube narration script is TOO SHORT: {got} words, but it needs about {target_words} words.
Expand it to about {target_words} words (add ~{target_words - got} words).
{f'Story format to keep: {fmt}.' if fmt else ''}
Rules:
- Keep EVERY existing scene, in the same order, with its "id". You may lengthen an existing scene's text a little
  (smoothly, same meaning), but most new words must come from NEW scenes inserted between existing ones ("id": null).
- New scenes: 20-40 words each, real and documented extra facts, deeper explanations, concrete numbers, vivid sensory
  moments and tension that fit the story at that point. No filler, no repeating what was already said.
- If the story has numbered secrets/points, you may add NEW secrets as new loops (each starts its own scene with "num").
- Keep the last scene(s) (resolution / outro / call to action) at the end.
- Each NEW scene needs one image prompt starting with this exact style prefix: "{style}", keeping recurring characters
  described exactly as in the existing story; plus a short English "motion" sentence.
Return ONLY a JSON array, in final order:
[{{"id": 0, "text": "..."}}, {{"id": null, "text": "...", "prompts": ["..."], "motion": "...", "chapter": null, "num": "", "flag": ""}}, ...]
CURRENT SCRIPT:
{json.dumps(cur, ensure_ascii=False)}"""
    log(f"AI memperpanjang naskah {got} → ±{target_words} kata...", 0.1)
    raw = _ask_ai(prompt, api_key, log)
    m = re.search(r"\[.*\]", raw or "", re.S)
    if not m:
        raise RuntimeError("Jawaban AI bukan JSON")
    items = json.loads(m.group(0))
    ids = [x.get("id") for x in items if isinstance(x.get("id"), int)]
    if ids != list(range(len(scenes))):
        raise RuntimeError("AI mengubah/menghapus urutan adegan lama; coba lagi")
    out = []
    for x in items:
        t = str(x.get("text") or "").strip()
        if not t: continue
        if isinstance(x.get("id"), int):
            sc = dict(scenes[x["id"]]); sc["text"] = t
            for k in ("start", "end"): sc.pop(k, None)
        else:
            sc = {"text": t, "prompts": [p for p in x.get("prompts") or [] if p][:1], "chapter": x.get("chapter") or None,
                  "flag": (x.get("flag") or "").strip(), "motion": (x.get("motion") or "").strip()}
            if str(x.get("num") or "").strip(): sc["num"] = str(x["num"]).strip().lstrip("#")
        out.append(sc)
    s = dict(s); s["scenes"] = out
    s.pop("alignment", None)
    return s, len(out) - len(scenes)


def prompt_items(s):
    """Semua prompt bernomor urut seperti di batch Flow: [(no, indeks_adegan, indeks_prompt, prompt)]."""
    out, k = [], 0
    for i, sc in enumerate(s["scenes"]):
        for j, p in enumerate(sc.get("prompts") or []):
            k += 1
            out.append((k, i, j, p))
    return out


def anim_prompts(s, pdir):
    """Prompt image-to-video (Veo di Flow) per gambar: [(no, nama_gambar, prompt, sudah_ada_klip)]."""
    out = []
    for no, i, j, _ in prompt_items(s):
        sc = s["scenes"][i]; imgs = sc.get("images") or []
        im = imgs[j] if j < len(imgs) else f"flow_{no:03d}.jpg"
        act = sc.get("motion") or f"the character acts out: {sc['text'][:160]}"
        out.append((no, im, f"Animate this exact image, keep the same art style and character design. {act} "
                            "Smooth expressive cartoon motion, subtle camera movement, no new characters, no text, no speech, "
                            "no lip sync. Ambient sound only.", bool(clip_for(pdir, im))))
    return out


def thumb_item(s):
    """Prompt thumbnail sebagai item nomor 0: (0, prompt, sudah_ada_gambar) atau None."""
    p = (s.get("thumbnail_prompt") or "").strip()
    return (0, p, bool(s.get("thumbnail"))) if p else None


def set_prompt_image(pdir, no, data, ext="jpg"):
    """Simpan gambar untuk prompt nomor `no` lalu pasang ke adegannya (posisi sesuai urutan prompt di adegan)."""
    s = load(pdir)
    if no == 0:   # nomor 0 = thumbnail (tampil 1 detik di awal video)
        name = f"thumb.{ext}"
        os.makedirs(os.path.join(pdir, "images"), exist_ok=True)
        with open(os.path.join(pdir, "images", name), "wb") as f:
            f.write(data)
        s["thumbnail"] = name; save(pdir, s)
        return 0, name
    item = next((x for x in prompt_items(s) if x[0] == no), None)
    if not item:
        raise RuntimeError(f"Prompt nomor {no} tidak ada")
    _, i, j, _ = item
    name = f"flow_{no:03d}.{ext}"
    # nomor prompt bisa bergeser setelah adegan disisipkan: jangan timpa gambar yang dipakai adegan/slot lain
    mine = (s["scenes"][i].get("images") or [None] * (j + 1))[j:j + 1]
    used = {im for k, sc in enumerate(s["scenes"]) for jj, im in enumerate(sc.get("images") or []) if (k, jj) != (i, j)}
    n = 1
    while name in used and name not in mine:
        n += 1; name = f"flow_{no:03d}_{n}.{ext}"
    os.makedirs(os.path.join(pdir, "images"), exist_ok=True)
    with open(os.path.join(pdir, "images", name), "wb") as f:
        f.write(data)
    imgs = list(s["scenes"][i].get("images") or [])
    while len(imgs) <= j:
        imgs.append(None)
    imgs[j] = name
    s["scenes"][i]["images"] = [x for x in imgs if x]
    save(pdir, s)
    return i + 1, name


GEMINI_MODELS = ["gemini-3-pro-image-preview", "gemini-2.5-flash-image"]   # dicoba berurutan


def _gemini_image(key, model, prompt, aspect):
    import base64, urllib.request, urllib.error
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": aspect}}}
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json", "x-goog-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"HTTP {e.code}: {msg}") from None
    for c in data.get("candidates") or []:
        for part in (c.get("content") or {}).get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                mime = inline.get("mimeType") or inline.get("mime_type") or "image/png"
                return base64.b64decode(inline["data"]), ("jpg" if "jpeg" in mime else "png")
    reason = (data.get("promptFeedback") or {}).get("blockReason") or \
        ((data.get("candidates") or [{}])[0].get("finishReason")) or "tanpa gambar"
    raise RuntimeError(f"Gemini tidak mengembalikan gambar ({reason})")


def gemini_images(pdir, key, model=None, nos=None, workers=3, log=print):
    """Buat gambar untuk prompt yang belum punya gambar (atau nomor `nos`) lewat Gemini API, lalu pasang ke adegannya.
    Bisa dilanjutkan: prompt yang sudah ada gambarnya dilewati. Error 429/5xx dicoba ulang dengan jeda."""
    import time
    from concurrent.futures import ThreadPoolExecutor
    s = load(pdir)
    aspect = s.get("aspect", "16:9")
    todo = [(no, p) for no, i, j, p in prompt_items(s)
            if (nos and no in nos) or (not nos and len(s["scenes"][i].get("images") or []) <= j)]
    ti = thumb_item(s)
    if ti and ((nos and 0 in nos) or (not nos and not ti[2])):
        todo.insert(0, (0, ti[1]))
    if not todo:
        log("Semua prompt sudah punya gambar", 1)
        return 0, []
    models = [m for m in [model] + GEMINI_MODELS if m]
    models = list(dict.fromkeys(models))
    lock = threading.Lock()
    done, fails = [0], []

    def one(item):
        no, prompt = item
        last = None
        for m in models:
            for attempt in range(4):
                try:
                    data, ext = _gemini_image(key, m, prompt, aspect)
                    with lock:   # set_prompt_image baca-tulis script.json: satu per satu
                        set_prompt_image(pdir, no, data, ext)
                        done[0] += 1
                        log(f"Gambar {done[0]}/{len(todo)} jadi (prompt {no:02d}, {m})", done[0] / len(todo))
                    return
                except RuntimeError as e:
                    last = str(e)
                    if last.startswith(("HTTP 429", "HTTP 500", "HTTP 503")):
                        time.sleep(min(60, 5 * 2 ** attempt))
                        continue
                    if last.startswith(("HTTP 404", "HTTP 400")) and "model" in last.lower():
                        break   # model tidak tersedia untuk key ini: coba model berikutnya
                    if last.startswith(("HTTP 401", "HTTP 403")):
                        raise
                    break
        with lock:
            fails.append(f"{no:02d}: {last}")

    log(f"Membuat {len(todo)} gambar dengan Gemini ({aspect})...", 0.01)
    with ThreadPoolExecutor(max(1, workers)) as ex:
        list(ex.map(one, todo))
    return done[0], fails


def prompt_batches(s, size=8):
    """Teks siap tempel ke agent chat Google Flow, per `size` prompt, bernomor sesuai nama file yang diharapkan."""
    items, k = [], 0
    for i, sc in enumerate(s["scenes"], 1):
        for j, p in enumerate(sc.get("prompts") or []):
            k += 1
            items.append(f"{k:02d}. [scene {i}{'' if len(sc['prompts']) == 1 else chr(97 + j)}] {p}")
    head = f"Generate one {s.get('aspect', '16:9')} image for each prompt below, in order. Name/number them exactly as listed.\n\n"
    return [head + "\n".join(items[i:i + size]) for i in range(0, len(items), size)]


# ---------- import bahan yang sudah ada ----------

def read_xlsx(path):
    """Baca sheet pertama xlsx tanpa library tambahan -> list baris {kolom: nilai}."""
    import zipfile, html
    z = zipfile.ZipFile(path)
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        shared = [html.unescape(re.sub("<[^>]+>", "", m)) for m in
                  re.findall(r"<si>(.*?)</si>", z.read("xl/sharedStrings.xml").decode("utf-8"), re.S)]
    x = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
    rows = []
    for r in re.findall(r"<row[^>]*>(.*?)</row>", x, re.S):
        row = {}
        for ref, attrs, body in re.findall(r'<c r="([A-Z]+)\d+"([^>]*)>(.*?)</c>', r, re.S):
            v, t = re.search(r"<v>(.*?)</v>", body, re.S), re.search(r"<t[^>]*>(.*?)</t>", body, re.S)
            if 't="s"' in attrs and v:
                row[ref] = shared[int(v.group(1))]
            else:
                row[ref] = html.unescape((t or v).group(1)) if (t or v) else ""
        rows.append(row)
    return rows


def scenes_from_xlsx(path):
    """Kolom dikenali dari judul: naskah ('script'/'voiceover'/'narration'), prompt ('prompt'), nama file ('filename')."""
    rows = read_xlsx(path)
    head = {k: v.lower() for k, v in rows[0].items()}
    col = lambda *keys: next((k for k, v in head.items() if any(x in v for x in keys)), None)
    c_txt, c_pr, c_fn = col("script", "voiceover", "narration", "naskah"), col("prompt"), col("filename", "file")
    if not c_txt:
        raise RuntimeError("Kolom naskah tidak ditemukan di Excel")
    out = []
    for r in rows[1:]:
        if r.get(c_txt, "").strip():
            out.append({"text": r[c_txt].strip(), "prompts": [r[c_pr].strip()] if c_pr and r.get(c_pr) else [],
                        "label": r.get(c_fn, "") if c_fn else "", "chapter": None, "flag": ""})
    return out


def scenes_from_srt(path):
    with open(path, encoding="utf-8-sig") as f:
        blocks = re.split(r"\n\s*\n", f.read().strip())
    return [{"text": " ".join(b.splitlines()[2:]).strip(), "prompts": [], "chapter": None, "flag": ""}
            for b in blocks if len(b.splitlines()) >= 3]


GEMINI_VOICES = {   # suara bawaan Gemini TTS (semua bisa bahasa Indonesia & Inggris)
    "Charon": "Charon · pria, informatif (dokumenter)", "Orus": "Orus · pria, tegas", "Algenib": "Algenib · pria, berat/serak",
    "Iapetus": "Iapetus · pria, jernih", "Fenrir": "Fenrir · pria, bersemangat", "Kore": "Kore · wanita, tegas",
    "Sulafat": "Sulafat · wanita, hangat", "Aoede": "Aoede · wanita, santai", "Leda": "Leda · wanita, muda",
}
GEMINI_TTS_MODELS = ["gemini-2.5-pro-preview-tts", "gemini-2.5-flash-preview-tts"]
TTS_STYLE = {"-10%": "slightly slow, calm pace", "+0%": "natural pace", "+10%": "slightly brisk pace"}


def _gemini_tts(key, model, text, voice, style):
    import base64, time, urllib.request, urllib.error
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {"contents": [{"parts": [{"text": f"{style}:\n{text}"}]}],
            "generationConfig": {"responseModalities": ["AUDIO"],
                                 "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}}
    for attempt in range(5):
        req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json", "x-goog-api-key": key})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            msg = f"HTTP {e.code}: " + e.read().decode("utf-8", "replace")[:300]
            if e.code in (429, 500, 503) and attempt < 4:
                time.sleep(min(60, 5 * 2 ** attempt)); continue
            raise RuntimeError(msg) from None
        for c in data.get("candidates") or []:
            for part in (c.get("content") or {}).get("parts") or []:
                inline = part.get("inlineData") or part.get("inline_data")
                if inline and inline.get("data"):
                    return base64.b64decode(inline["data"])   # PCM 16-bit mono 24 kHz
        raise RuntimeError("Gemini TTS tidak mengembalikan audio")


def _voice_parts(s, size):
    parts, cur = [], ""
    for sc in s["scenes"]:
        t = sc["text"].strip()
        if cur and len(cur) + len(t) > size:
            parts.append(cur); cur = ""
        cur = (cur + "\n" + t).strip()
    if cur:
        parts.append(cur)
    return parts


def _concat_voice(pdir, tmp, files, voice):
    with open(os.path.join(tmp, "list.txt"), "w", encoding="utf-8") as f:
        f.write("".join(f"file '{os.path.basename(x)}'\n" for x in files))
    clipper.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", "list.txt", "-c:a", "libmp3lame",
                 "-b:a", "192k", os.path.join(pdir, "voice.mp3")], cwd=tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    s2 = load(pdir); s2.pop("alignment", None); s2["voice_ai"] = voice; save(pdir, s2)


def _gemini_voice(pdir, s, voice, rate, log, key, tone):
    if not key:
        raise RuntimeError("Gemini TTS butuh Gemini API key")
    name = voice.split(":", 1)[1]
    lang = "Indonesian" if lang_of(s) == "id" else "English"
    style = (f"Read this {lang} documentary narration aloud as a warm, engaging storyteller, {TTS_STYLE.get(rate, 'natural pace')}"
             + (f", {tone} tone" if tone else "") + ". Read every word exactly, do not add anything")
    parts = _voice_parts(s, 1500)   # bagian pendek: lebih stabil & tidak terpotong
    tmp = os.path.join(pdir, "_tts"); os.makedirs(tmp, exist_ok=True)
    done = [0]; lock = threading.Lock()

    def one(i):
        last = None
        for m in GEMINI_TTS_MODELS:
            try:
                pcm = _gemini_tts(key, m, parts[i], name, style)
                break
            except RuntimeError as e:
                last = e
                if str(e).startswith(("HTTP 401", "HTTP 403")):
                    raise
        else:
            raise RuntimeError(f"Bagian {i + 1}: {last}")
        raw = os.path.join(tmp, f"p{i:03d}.pcm"); out = os.path.join(tmp, f"p{i:03d}.wav")
        with open(raw, "wb") as f:
            f.write(pcm)
        clipper.run(["ffmpeg", "-y", "-v", "error", "-f", "s16le", "-ar", "24000", "-ac", "1", "-i", raw, out])
        with lock:
            done[0] += 1
            log(f"Suara Gemini bagian {done[0]}/{len(parts)}...", 0.05 + 0.85 * done[0] / len(parts))
        return out

    log(f"Gemini TTS: {len(parts)} bagian, suara {name}...", 0.05)
    with ThreadPoolExecutor(3) as ex:
        files = list(ex.map(one, range(len(parts))))
    _concat_voice(pdir, tmp, files, voice)


def ai_voice(pdir, s, voice, rate="+0%", log=print, key=None, tone=""):
    """voice.mp3 dari naskah, dibacakan per bagian lalu digabung supaya naskah panjang tidak gagal dalam satu permintaan.
    voice "gemini:<Nama>" = Gemini TTS (perlu key), selain itu edge-tts (gratis)."""
    if (voice or "").startswith("gemini:"):
        return _gemini_voice(pdir, s, voice, rate, log, key, tone)
    import asyncio
    import edge_tts
    voice = voice or ("id-ID-ArdiNeural" if lang_of(s) == "id" else "en-US-ChristopherNeural")
    parts = _voice_parts(s, 2500)
    voice = clipper.eleven_preflight(voice, " ".join(parts), lambda m: log(m, 0.1))
    tmp = os.path.join(pdir, "_tts"); os.makedirs(tmp, exist_ok=True)
    files = []
    for i, txt in enumerate(parts):
        log(f"Suara AI bagian {i + 1}/{len(parts)}...", 0.1 + 0.8 * i / len(parts))
        out = os.path.join(tmp, f"p{i:03d}.mp3")
        if clipper.is_eleven(voice):
            try:
                clipper.eleven_tts(txt, voice, out)
                files.append(out); continue
            except Exception as e:
                # satu suara untuk seluruh video: buang bagian ElevenLabs, ulang semua dengan suara lama
                voice = clipper.eleven_fallback(voice, e)
                log(f"Kuota/akses ElevenLabs gagal, pakai suara {voice}", 0.1)
                return ai_voice(pdir, s, voice, rate, log, key, tone)
        async def go():
            await edge_tts.Communicate(txt, voice.split("|")[-1], rate=rate).save(out)
        asyncio.run(go())
        files.append(out)
    _concat_voice(pdir, tmp, files, voice)


def replace_from_srt(pdir, s, srt_path):
    """Pakai SRT baru (mis. hasil edit di CapCut) sebagai naskah. Jumlah blok sama -> teks per adegan diganti,
    prompt/gambar/chapter tetap. Jumlah beda -> adegan disusun ulang (prompt & urutan gambar perlu diatur lagi)."""
    new = scenes_from_srt(srt_path)
    if not new:
        raise RuntimeError("SRT kosong atau formatnya tidak dikenali")
    if len(new) == len(s["scenes"]):
        for sc, n in zip(s["scenes"], new):
            sc["text"] = n["text"]
        msg = f"Teks {len(new)} adegan diperbarui; prompt, gambar, dan chapter tetap"
    else:
        old = s["scenes"]
        s["scenes"] = [dict(n, prompts=(old[i].get("prompts") if i < len(old) else []) or [],
                            chapter=old[i].get("chapter") if i < len(old) else None) for i, n in enumerate(new)]
        msg = (f"Jumlah blok berbeda ({len(old)} -> {len(new)} adegan): prompt disalin menurut nomor, "
               "cek ulang prompt & urutan gambar")
    for sc in s["scenes"]:
        sc.pop("start", None); sc.pop("end", None)
    s.pop("alignment", None)
    shutil.copy2(srt_path, os.path.join(pdir, "script.srt"))
    return msg


def import_folder(src, pid, topic=None, voice_from=None, log=print, lang="en"):
    """Buat proyek dari folder bahan: Excel/SRT (naskah), gambar, dan suara (mp3, atau audio dari mp4)."""
    pdir = os.path.join(DOCS_DIR, pid)
    os.makedirs(os.path.join(pdir, "images"), exist_ok=True)
    files = os.listdir(src)
    xl = next((f for f in files if f.lower().endswith(".xlsx")), None)
    srt = next((f for f in files if f.lower().endswith(".srt")), None)
    if xl:
        scenes = scenes_from_xlsx(os.path.join(src, xl))
    elif srt:
        scenes = scenes_from_srt(os.path.join(src, srt))
    else:
        raise RuntimeError("Tidak ada Excel atau SRT naskah di folder")
    for f in files:
        if f.lower().endswith(IMG_EXT):
            shutil.copy2(os.path.join(src, f), os.path.join(pdir, "images", f))
    voice = voice_from or next((os.path.join(src, f) for f in files if f.lower().endswith((".mp3", ".wav", ".m4a"))), None)
    if voice:
        log("Menyalin suara...")
        clipper.run(["ffmpeg", "-y", "-v", "error", "-i", voice, "-vn", "-c:a", "libmp3lame", "-b:a", "192k",
                     os.path.join(pdir, "voice.mp3")])
    s = {"id": pid, "topic": topic or os.path.basename(src.rstrip("\\/")), "scenes": scenes, "metadata": {},
         "lang": lang if lang in LANGS else "en"}
    err = auto_map(s, list_images(pdir))
    save(pdir, s)
    write_temp_srt(pdir, s)
    return pdir, err


# ---------- alignment ----------

def _key(w):
    return re.sub(r"[^a-z0-9]", "", w.lower())


def align(pdir, s, voice, model=None, log=print):
    """Whisper (timestamp per kata) lalu fuzzy match ke kata naskah. Mengisi start/end tiap adegan
    (tanpa celah: adegan berikut mulai tepat saat adegan sebelumnya selesai) dan words.json."""
    log("Whisper membaca suara...", 0.02)
    _, wmodel, wlang = LANGS[lang_of(s)]
    tr = clipper.transcribe(voice, model or wmodel, wlang, lambda m, f=None: log(m, None if f is None else 0.05 + 0.75 * f))
    dur = clipper.get_duration(voice)
    heard = [w for w in tr["words"] if _key(w["word"])]
    toks = []   # kata naskah: (indeks adegan, kata tampil)
    for i, sc in enumerate(s["scenes"]):
        toks += [(i, w) for w in sc["text"].split() if _key(w)]
    a, b = [_key(w) for _, w in toks], [_key(w["word"]) for w in heard]
    times, hidx = [None] * len(toks), [None] * len(toks)
    for blk in SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
        for k in range(blk.size):
            w = heard[blk.b + k]
            times[blk.a + k] = (w["start"], w["end"]); hidx[blk.a + k] = blk.b + k
    # kata yang "diregangkan" Whisper (> 1,5 dtk, biasanya karena kalimat sesudahnya terlewat) bukan patokan
    # waktu yang andal: dianggap tidak cocok supaya kata-kata di sekitarnya dibagi rata ke rentang itu
    for k, t in enumerate(times):
        if t and (t[1] - t[0] > 1.5 or t[1] - t[0] < 0.02):   # juga kata berdurasi nol
            times[k] = hidx[k] = None
    matched = sum(t is not None for t in times)
    ratio = matched / max(1, len(toks))
    if matched == 0:
        raise RuntimeError("Suara tidak cocok dengan naskah sama sekali. Pastikan voice.mp3 dibuat dari script.srt ini.")
    # kata yang tidak terdengar: interpolasi linear di antara kata yang cocok
    known = [k for k, t in enumerate(times) if t]
    for k in range(len(times)):
        if times[k]:
            continue
        prev = max((j for j in known if j < k), default=None)
        nxt = min((j for j in known if j > k), default=None)
        t0 = times[prev][1] if prev is not None else 0.0
        t1 = times[nxt][0] if nxt is not None else dur
        # kata naskah yang ditulis Whisper berbeda ("fifty thousand" -> "$50,000"): pakai rentang kata Whisper
        # yang tidak cocok di antara kedua tetangga, agar teks tampil tepat saat kata itu diucapkan
        hb0 = hidx[prev] + 1 if prev is not None else 0
        hb1 = hidx[nxt] if nxt is not None else len(heard)
        if hb1 > hb0:
            t0, t1 = heard[hb0]["start"], heard[hb1 - 1]["end"]
        j0 = prev if prev is not None else -1
        j1 = nxt if nxt is not None else len(times)
        f0, f1 = (k - j0) / (j1 - j0), (k - j0 + 1) / (j1 - j0)
        times[k] = (t0 + (t1 - t0) * f0, t0 + (t1 - t0) * f1)
    heard_idx = set(known)   # kata yang benar-benar terdengar (bukan hasil interpolasi)
    words = [{"scene": i, "word": w, "start": round(t[0], 3), "end": round(t[1], 3), "heard": k in heard_idx}
             for k, ((i, w), t) in enumerate(zip(toks, times))]
    s["alignment"] = {"duration": round(dur, 3), "match_ratio": round(ratio, 3)}
    # semua ucapan yang terdengar Whisper (cocok atau tidak): dipakai saat merapatkan jeda agar tidak terpotong
    with open(os.path.join(pdir, "speech.json"), "w", encoding="utf-8") as f:
        json.dump([[round(w["start"], 3), round(w["end"], 3)] for w in heard], f)
    scene_times(s, words, dur)
    log(f"Alignment selesai: {ratio * 100:.0f}% kata cocok", 0.85)
    return words


def scene_times(s, words, dur):
    """Batas adegan = kata pertama adegan; adegan pertama mulai 0, terakhir sampai akhir audio (tanpa celah)."""
    starts = []
    for i in range(len(s["scenes"])):
        ws = [w for w in words if w["scene"] == i]
        starts.append(ws[0]["start"] if ws else (starts[-1] if starts else 0.0))
    starts[0] = 0.0
    for i in range(1, len(starts)):   # tetap urut & minimal 0,5 dtk per adegan
        starts[i] = max(starts[i], starts[i - 1] + 0.5)
    for i, sc in enumerate(s["scenes"]):
        sc["start"] = round(starts[i], 3)
        sc["end"] = round(starts[i + 1] if i + 1 < len(starts) else dur, 3)
    s["alignment"]["duration"] = round(dur, 3)
    s["alignment"]["long_pauses"] = sum(1 for a, b in zip(words, words[1:]) if b["start"] - a["end"] > 0.6)


def tighten_by_words(src, words, out, max_gap=0.6, keep=0.4, speech=None):
    """Rapatkan jeda berdasarkan timing kata (andal walau ada musik/noise di jeda): setiap jeda antar kata
    > max_gap dipotong jadi `keep` detik. Kembalikan (kata dengan waktu baru, durasi lama, durasi baru, jumlah jeda)."""
    dur = clipper.get_duration(src)
    # yang dianggap ucapan: semua kata naskah (termasuk yang tak terdengar Whisper: waktunya hasil interpolasi,
    # jadi celah tempat kata itu berada tidak ikut dipotong) + semua kata yang didengar Whisper (speech)
    heard = sorted([{"start": w["start"], "end": w["end"]} for w in words]
                   + [{"start": a, "end": b} for a, b in (speech or [])], key=lambda w: w["start"])
    cuts, prev_end, first = [], 0.0, True
    for w in heard + [{"start": dur, "end": dur}]:   # termasuk hening di awal dan di akhir audio
        if w["start"] - prev_end > max_gap:
            cuts.append((0.0 if first else prev_end + keep / 2, w["start"] - keep / 2))
        first = False
        prev_end = max(prev_end, w["end"])
    cuts = [(a, b) for a, b in cuts if b - a > 0.05]
    if not cuts:
        shutil.copy(src, out)
        return words, dur, dur, 0
    # bagian yang DISIMPAN (kebalikan cuts) -> atrim tiap bagian lalu concat. Filter ditulis ke file:
    # dengan ratusan jeda, ekspresi aselect satu baris membuat ffmpeg gagal ("Cannot allocate memory").
    keepr, t = [], 0.0
    for a, b in cuts:
        if a > t + 0.01:
            keepr.append((t, a))
        t = b
    if dur > t + 0.01:
        keepr.append((t, dur))
    n = len(keepr)
    lines = [f"[0:a]aresample=44100,asplit={n}" + "".join(f"[s{i}]" for i in range(n))]
    lines += [f"[s{i}]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[k{i}]" for i, (a, b) in enumerate(keepr)]
    lines.append("".join(f"[k{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[out]")
    script = out + ".filter.txt"
    with open(script, "w", encoding="utf-8") as f:
        f.write(";\n".join(lines))
    try:
        clipper.run(["ffmpeg", "-y", "-v", "error", "-i", src, "-/filter_complex", script, "-map", "[out]",
                     "-c:a", "pcm_s16le", out])
    finally:
        os.remove(script)
    def shift(t):
        removed = 0.0
        for a, b in cuts:
            if t >= b: removed += b - a
            elif t > a: removed += t - a
        return t - removed
    new = [dict(w, start=round(shift(w["start"]), 3), end=round(shift(w["end"]), 3)) for w in words]
    return new, dur, clipper.get_duration(out), len(cuts)


# ---------- subtitle ----------

def _ass_color(hex_, alpha=0):
    h = hex_.lstrip("#")
    return f"&H{alpha:02X}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def _chunks(words, max_words):
    out, cur = [], []
    for w in words:
        cur.append(w)
        end = w["word"].rstrip('"\')').endswith((".", "?", "!"))
        soft = w["word"].endswith((",", ";", ":", "—")) and len(cur) >= max(3, max_words // 2)
        if len(cur) >= max_words or end or soft:
            out.append(cur); cur = []
    if cur:
        out.append(cur)
    return out


HOOK_COLOR = "#FFE14D"


NUM_WORDS = {"pertama": 1, "kedua": 2, "ketiga": 3, "keempat": 4, "kelima": 5, "keenam": 6, "ketujuh": 7,
             "kedelapan": 8, "kesembilan": 9, "kesepuluh": 10, "first": 1, "second": 2, "third": 3, "fourth": 4,
             "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10}
_NUM_RE = re.compile(r"^(?:#|nomor |number |no\.? ?)(\d{1,2})\b")
_ORD_RE = re.compile(r"^(?:(?:dan|and|yang|the|lalu|nah|oke|terus)\W+)*(?:(?:alasan|poin|fakta|cara|tips?|langkah|hal|"
                     r"rahasia|kebiasaan|reason|point|fact|step|way|tip|thing|secret|habit)\W+)?(?:yang\W+)?("
                     + "|".join(NUM_WORDS) + r")\b(?!\W+kali)")


def scene_num(sc):
    """Nomor poin adegan: field "num" (diisi AI/pengguna; "-" = matikan), atau tebakan dari awal narasi
    ("Pertama, ...", "Nomor 3", "Alasan kedua", "#2", "Number 4")."""
    v = str(sc.get("num") or "").strip()
    if v == "-": return ""
    if v: return v.lstrip("#")
    head = " ".join(sc.get("text", "").lower().split()[:4])
    m = _NUM_RE.match(head)
    if m: return m.group(1)
    m = _ORD_RE.match(head)
    return str(NUM_WORDS[m.group(1)]) if m else ""


def write_ass(words, st, path, cards=(), hook=0, nums=()):
    """cards: [(detik, judul chapter)] -> judul besar di tengah layar ±3 dtk saat chapter baru dimulai.
    hook: jumlah kata pembuka (kalimat hook) yang tampil BESAR di tengah layar, kontras, muncul tepat di kata pertama
    (subtitle biasa untuk kata-kata itu tidak ditampilkan)."""
    align = 5 if st.get("align") == "center" else 2   # center: teks per kata di tengah layar (gaya cerita kartun)
    font = st.get("font", "Segoe UI")
    hook_words, words = (words[:hook], words[hook:]) if hook else ([], words)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{st.get('size', 54)},{_ass_color(st.get('color', '#FFFFFF'))},&H000000FF,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,1,{st.get('outline', 3)},{st.get('shadow', 2)},{align},120,120,{st.get('margin_v', 70)},1
Style: Num,Segoe UI Black,{int(H * 0.34)},{_ass_color(HOOK_COLOR)},&H000000FF,&H00000000,&H78000000,-1,0,0,0,100,100,0,0,1,{max(8, W // 130)},6,5,0,0,0,1
Style: Card,{font},86,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,2,0,1,4,3,5,160,160,0,1
Style: Hook,Segoe UI Black,{int(W * 0.09)},{_ass_color(HOOK_COLOR)},&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,1,0,1,{max(6, W // 150)},4,5,{W // 13},{W // 13},0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    up = (lambda x: x.upper()) if st.get("uppercase") else (lambda x: x)
    esc = lambda x: x.replace("{", "(").replace("}", ")")
    lines, gi = [], 0
    chunks = _chunks(words, st.get("max_words", 8))
    for ci, ch in enumerate(chunks):
        nxt = chunks[ci + 1][0]["start"] if ci + 1 < len(chunks) else ch[-1]["end"] + 0.4
        c_end = nxt if nxt - ch[-1]["end"] < 0.6 else ch[-1]["end"] + 0.3
        if st.get("mode") == "word":
            hl = st.get("highlight") or ["#FFD54A"]
            for k, w in enumerate(ch):
                w_end = ch[k + 1]["start"] if k + 1 < len(ch) else c_end
                col = _ass_color(hl[(gi + k) % len(hl)])
                pop = r"\fscx118\fscy118\t(0,120,\fscx108\fscy108)" if st.get("pop") else ""
                txt = " ".join((f"{{\\c{col}{pop}}}{esc(up(x['word']))}{{\\r}}" if j == k else esc(up(x["word"])))
                               for j, x in enumerate(ch))
                lines.append(f"Dialogue: 0,{_ts(w['start'], '.')[:-1]},{_ts(w_end, '.')[:-1]},Default,,0,0,0,,{txt}")
            gi += len(ch)
        else:
            txt = esc(up(" ".join(x["word"] for x in ch)))
            lines.append(f"Dialogue: 0,{_ts(ch[0]['start'], '.')[:-1]},{_ts(c_end, '.')[:-1]},Default,,0,0,0,,{txt}")
    if hook_words:   # hook: langsung penuh di kata pertama, efek "pop" singkat, hilang saat kalimat berikutnya mulai
        a = hook_words[0]["start"]
        b = (words[0]["start"] if words else hook_words[-1]["end"] + 0.4)
        b = min(b, hook_words[-1]["end"] + 0.6)
        txt = esc(" ".join(x["word"] for x in hook_words).upper())
        lines.append(f"Dialogue: 2,{_ts(a, '.')[:-1]},{_ts(b, '.')[:-1]},Hook,,0,0,0,,"
                     f"{{\\fscx125\\fscy125\\t(0,140,\\fscx100\\fscy100)\\fad(0,120)}}{txt}")
    for t, title in cards:
        txt = esc(title.upper() if st.get("uppercase") else title)
        lines.append(f"Dialogue: 1,{_ts(t, '.')[:-1]},{_ts(t + 3.2, '.')[:-1]},Card,,0,0,0,,"
                     # pop-up overshoot: 0% -> 115% -> 100% (membal), lalu memudar keluar
                     f"{{\\fad(0,450)\\blur1\\fscx0\\fscy0\\t(0,200,\\fscx115\\fscy115)\\t(200,360,\\fscx100\\fscy100)}}{txt}")
    for t, n in nums:   # kartu nomor: "#1" besar di tengah, pop masuk, tampil ±1,6 dtk di awal penjelasannya
        lines.append(f"Dialogue: 3,{_ts(t, '.')[:-1]},{_ts(t + 1.6, '.')[:-1]},Num,,0,0,0,,"
                     f"{{\\fad(0,300)\\fscx0\\fscy0\\t(0,180,\\fscx118\\fscy118)\\t(180,320,\\fscx100\\fscy100)}}#{esc(n)}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(lines))


def write_srt(words, path, max_words=10):
    out = []
    chunks = _chunks(words, max_words)
    for i, ch in enumerate(chunks, 1):
        nxt = chunks[i][0]["start"] if i < len(chunks) else ch[-1]["end"] + 0.4
        end = min(nxt, ch[-1]["end"] + 0.6)
        out.append(f"{i}\n{_ts(ch[0]['start'])} --> {_ts(end)}\n{' '.join(x['word'] for x in ch)}\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))


# ---------- metadata ----------

def chapters(s, min_len=10):
    """Chapter YouTube dari field "chapter" adegan (mulai di 0:00, tiap chapter >= 10 dtk)."""
    out = []
    for sc in s["scenes"]:
        if sc.get("chapter") or not out:
            out.append([sc["start"], sc.get("chapter") or "Intro"])
    merged = []
    for t, name in out:
        if merged and t - merged[-1][0] < min_len:
            continue
        merged.append([t, name])
    merged[0][0] = 0
    fmt = lambda t: f"{int(t // 60)}:{int(t % 60):02d}" if t < 3600 else f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{int(t % 60):02d}"
    return [f"{fmt(t)} {n}" for t, n in merged] if len(merged) >= 3 else []


def write_metadata(s, path):
    m = s.get("metadata") or {}
    titles = m.get("titles") or [s.get("topic", "")]
    lines = ["TITLE OPTIONS:"] + [f"{i}. {t}" for i, t in enumerate(titles, 1)] + ["", "DESCRIPTION:", m.get("description", "").strip()]
    ch = chapters(s)
    if ch:
        lines += ["", "Chapters:"] + ch
    if m.get("hashtags"):
        lines += ["", " ".join(m["hashtags"])]
    lines += ["", "TAGS:", ", ".join(m.get("tags", []))]
    flags = [f"- Scene {i}: {sc['flag']}" for i, sc in enumerate(s["scenes"], 1) if sc.get("flag")]
    if flags:
        lines += ["", "CLAIMS TO VERIFY (not for upload):"] + flags
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ---------- tema ----------

def pick_theme(topic, used=()):
    """'auto': cocokkan kata kunci topik; kalau tidak ada yang cocok, pakai tema yang paling jarang dipakai."""
    th, t = themes(), (topic or "").lower()
    score = {k: sum(kw in t for kw in v.get("keywords", [])) for k, v in th.items()}
    best = max(score, key=score.get)
    if score[best] > 0:
        return best
    return min(th, key=lambda k: list(used).count(k))


def recent_themes(limit=10):
    out = []
    if os.path.isdir(DOCS_DIR):
        for d in sorted(os.listdir(DOCS_DIR), key=lambda x: os.path.getmtime(os.path.join(DOCS_DIR, x)))[-limit:]:
            try:
                out.append(load(os.path.join(DOCS_DIR, d)).get("theme_used"))
            except Exception:
                pass
    return [t for t in out if t]


# ---------- render ----------

DYNAMICS = {   # dinamika kamera: panjang tiap "shot" (satu gambar dipecah jadi beberapa sudut kamera) & lama potongan
    # rapi: 1 gerakan halus per gambar (pan/push ber-ease, tanpa goyang), antar gambar hard cut / zoom match-cut /
    # slide; whoosh hanya di potongan yang berarti (chapter baru, match-cut, slide)
    "rapi": {"shot": None, "cut": 0.0, "extra_tr": [], "clean": True},
    "tenang": {"shot": None, "cut": 0.0, "extra_tr": []},
    "sedang": {"shot": 5.5, "cut": 0.25, "extra_tr": [["zoomin", 0.12], ["smoothleft", 0.08], ["smoothright", 0.08]]},
    # shorts: tanpa geser kiri-kanan (layar sempit 9:16 terasa goyang); hanya zoom masuk/keluar pelan
    "shorts": {"shot": 2.4, "cut": 0.1, "extra_tr": [["zoomin", 0.25], ["fade", 0.2]], "steady": True},
    "dinamis": {"shot": 3.2, "cut": 0.12, "extra_tr": [["zoomin", 0.2], ["smoothleft", 0.15], ["smoothright", 0.15], ["slideup", 0.08]]},
}
_FOCUS = {}


def focus_points(path, k=3):
    """Titik menarik di gambar (0..1) dari saliency spectral residual: dipakai untuk close-up & pan antar detail."""
    if path in _FOCUS:
        return _FOCUS[path]
    pts = []
    try:
        import cv2
        import numpy as np
        g = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_GRAYSCALE)
        g = cv2.resize(g, (160, 90)).astype(np.float32)
        sp = np.fft.fft2(g)
        la = np.log(np.abs(sp) + 1e-6)
        sal = np.abs(np.fft.ifft2(np.exp(la - cv2.blur(la, (3, 3)) + 1j * np.angle(sp)))) ** 2
        sal = cv2.GaussianBlur(sal, (11, 11), 4)
        sal[:, :12] *= 0.4; sal[:, -12:] *= 0.4; sal[:8] *= 0.4; sal[-8:] *= 0.4
        for _ in range(k):
            y, x = np.unravel_index(np.argmax(sal), sal.shape)
            if sal[y, x] <= 0:
                break
            pts.append((min(max(x / 160, 0.22), 0.78), min(max(y / 90, 0.25), 0.75)))
            sal[max(0, y - 22):y + 23, max(0, x - 35):x + 36] = 0   # titik berikutnya harus di tempat lain
    except Exception:
        pass
    _FOCUS[path] = pts or [(0.5, 0.45), (0.33, 0.4), (0.67, 0.5)]
    return _FOCUS[path]


def _shot_motion(shot, p, rng):
    """Gerakan satu shot. Zoom tinggi aman karena gambar diskalakan 2x (close-up tetap tajam)."""
    ease = f"(3*{p}*{p}-2*{p}*{p}*{p})"
    if shot["kind"] == "clean":   # mode rapi: satu gerakan ber-ease + zoom match-cut di ujung/awal bila diminta
        n, T = shot["n"], 9   # 9 frame = 0,3 dtk untuk ledakan zoom
        if shot["move"] == "pan":
            z = "1.14"
            a, b = (0.44, 0.56) if shot["dir"] > 0 else (0.56, 0.44)
            if shot["vertical"]:
                x, y = "(0.5*iw-iw/zoom/2)", f"(({a}+({b - a:.2f})*{ease})*ih-ih/zoom/2)"
            else:
                x, y = f"(({a}+({b - a:.2f})*{ease})*iw-iw/zoom/2)", "(0.5*ih-ih/zoom/2)"
        else:   # push: dorong masuk sangat pelan
            z = f"(1.03+0.07*{ease})"
            x, y = "(0.5*iw-iw/zoom/2)", "(0.5*ih-ih/zoom/2)"
        if shot.get("zin"):    # akhir gambar: zoom melesat ke tengah (match-cut keluar)
            z = f"({z})*(1+1.2*pow(max(0,(on-{n - T})/{T}),2))"
            x, y = f"(0.5*iw-iw/zoom/2)*gte(on,{n - T})+({x})*lt(on,{n - T})", f"(0.5*ih-ih/zoom/2)*gte(on,{n - T})+({y})*lt(on,{n - T})"
        if shot.get("zout"):   # awal gambar: mulai sangat dekat lalu mundur ke normal (match-cut masuk)
            z = f"({z})*(1+1.2*pow(max(0,1-on/{T}),2))"
        return z, x, y
    kind, (fx, fy), (gx, gy) = shot["kind"], shot["a"], shot["b"]
    at = lambda u, v: (f"({u:.3f}*iw-iw/zoom/2)", f"({v:.3f}*ih-ih/zoom/2)")
    if kind == "wide":         # pembuka: gambar utuh, masuk pelan ke titik menarik
        z = f"1.0+0.12*{p}"
        x, y = f"((1-{p})*(iw/2-iw/zoom/2)+{p}*({fx:.3f}*iw-iw/zoom/2))", f"((1-{p})*(ih/2-ih/zoom/2)+{p}*({fy:.3f}*ih-ih/zoom/2))"
    elif kind == "detail":     # close-up detail, dorong pelan
        z = f"1.55+0.15*{p}"; x, y = at(fx, fy)
    elif kind == "travel":     # geser dari satu detail ke detail lain
        z = "1.45"
        x = f"(({fx:.3f}+({gx - fx:.3f})*{ease})*iw-iw/zoom/2)"
        y = f"(({fy:.3f}+({gy - fy:.3f})*{ease})*ih-ih/zoom/2)"
    elif kind == "reveal":     # mulai dari detail lalu mundur memperlihatkan seluruh gambar
        z = f"1.6-0.55*{ease}"
        x, y = f"({fx:.3f}*iw-iw/zoom/2)", f"({fy:.3f}*ih-ih/zoom/2)"
    else:                      # diag: geser diagonal sambil sedikit masuk
        z = f"1.18+0.08*{p}"
        sx, sy = rng.choice([(0.38, 0.4), (0.62, 0.4), (0.38, 0.6), (0.62, 0.6)])
        x = f"(({sx:.2f}+({1 - sx - sx:.2f})*{p})*iw-iw/zoom/2)"
        y = f"(({sy:.2f}+({1 - sy - sy:.2f})*{p})*ih-ih/zoom/2)"
    return z, x, y


def _motion(th, rng, n, shot=None):
    """Ekspresi zoompan (z, x, y) untuk satu gambar; n = jumlah frame."""
    m = th["motion"]
    z0, z1 = m["zoom"]
    move = rng.choice(m["moves"]) if not shot else shot["kind"]
    p = f"(on/{max(n - 1, 1)})"
    cx, cy = "(iw/2-iw/zoom/2)", "(ih/2-ih/zoom/2)"
    zm = max(1.06, (z0 + z1) / 2)
    if shot:
        z, x, y = _shot_motion(shot, p, rng)
    elif move == "in":
        z, x, y = f"{z0}+{z1 - z0}*{p}", cx, cy
    elif move == "out":
        z, x, y = f"{z1}-{z1 - z0}*{p}", cx, cy
    elif move == "push":     # dorong masuk lebih cepat di awal
        z, x, y = f"{z0}+{z1 - z0}*sqrt({p})", cx, cy
    elif move == "pan_left":
        z, x, y = f"{zm}", f"(iw-iw/zoom)*(1-{p})", cy
    elif move == "pan_right":
        z, x, y = f"{zm}", f"(iw-iw/zoom)*{p}", cy
    else:                    # pop: masuk membesar lalu memantul ke ukuran normal
        z, x, y = f"1.05+0.04*{p}+0.13*exp(-on/7)*cos(on/4)", cx, cy
    amp = 0.0
    if shot and shot["kind"] == "clean":   # mode rapi: tanpa goyang
        pass
    elif m.get("shake") and rng.random() < m.get("shake_prob", 0):
        amp = m["shake"]
    if not (shot and shot["kind"] == "clean"):
        amp = max(amp, m.get("jitter", 0))
    if amp:
        # goyangan dalam piksel output -> piksel input (input 2x lebih besar)
        k = f"{amp}*iw/zoom/{W}"
        x = f"({x})+{k}*(sin(on*1.9)+0.6*sin(on*5.3))"
        y = f"({y})+{k}*(cos(on*2.3)+0.6*sin(on*4.1))"
    # jaga agar tetap di dalam gambar
    x = f"max(0,min(iw-iw/zoom,{x}))"
    y = f"max(0,min(ih-ih/zoom,{y}))"
    return z, x, y, move


def _clip_cmd(clip, out, n, th):
    """Segmen dari klip video: isi penuh layar, diulang bila lebih pendek dari durasi, tanpa suara."""
    vf = [f"scale={W}:{H}:force_original_aspect_ratio=increase", f"crop={W}:{H}", "setsar=1", f"fps={FPS}"]
    if th.get("grade"): vf.append(th["grade"])
    if th.get("fx"): vf.append(th["fx"])
    vf.append("format=yuv420p")
    return ["ffmpeg", "-y", "-v", "error", "-stream_loop", "-1", "-i", clip, "-vf", ",".join(vf), "-frames:v", str(n),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-an", out]


def _segment_cmd(img, out, n, th, rng, shot=None):
    z, x, y, move = _motion(th, rng, n, shot)
    tilt = th["motion"].get("tilt", 0)
    use_tilt = tilt and rng.random() < 0.4 and not (shot and shot["kind"] == "clean")
    zw, zh = (int(W * 1.05) // 2 * 2, int(H * 1.05) // 2 * 2) if use_tilt else (W, H)
    # warna statis diterapkan SEKALI ke gambar (sebelum zoompan); hanya efek yang berubah per frame (fx) dihitung tiap frame
    vf = [f"scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase", f"crop={W * 2}:{H * 2}", "setsar=1"]
    if th.get("grade"):
        vf.append(th["grade"])
    vf.append(f"zoompan=z='{z}':x='{x}':y='{y}':d={n}:s={zw}x{zh}:fps={FPS}")
    if use_tilt:
        a = tilt * 3.14159 / 180
        vf += [f"rotate=a='{a:.4f}*sin(t*1.3)':c=black", f"crop={W}:{H}"]
    if th.get("fx"):
        vf.append(th["fx"])
    vf.append("format=yuv420p")
    return ["ffmpeg", "-y", "-v", "error", "-i", img, "-vf", ",".join(vf), "-frames:v", str(n),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-an", out], move


SHOT_ORDER = [["wide", "detail", "travel", "reveal", "detail", "diag"],
              ["reveal", "travel", "detail", "wide", "diag", "detail"],
              ["diag", "detail", "reveal", "travel", "detail", "wide"]]
STEADY_ORDER = [["wide", "detail", "reveal", "detail", "wide", "reveal"],   # tanpa travel/diag (tidak ada geser samping)
                ["reveal", "wide", "detail", "reveal", "detail", "wide"],
                ["detail", "reveal", "wide", "detail", "reveal", "wide"]]


def plan(s, th, seed, pdir=None):
    """Daftar shot dengan batas frame & transisi. Kembalikan [(path_rel, frames, transisi, durasi_transisi, shot, ganti_gambar)].
    Dinamika "sedang"/"dinamis": gambar yang tampil lama dipecah jadi beberapa shot (lebar, close-up detail,
    geser antar detail, mundur) dengan potongan cepat, supaya tidak terasa seperti slideshow."""
    dyn = DYNAMICS.get(s.get("dynamics", "sedang"), DYNAMICS["sedang"])
    if dyn.get("clean"):
        return plan_clean(s, seed, pdir)
    th = dict(th, transitions=[list(t) for t in th["transitions"]] + dyn["extra_tr"])
    steady = dyn.get("steady")
    if steady:   # buang transisi yang menyapu ke samping
        th["transitions"] = [t for t in th["transitions"] if not re.search(r"left|right|slide|wipe|smooth|hl|hr", t[0])] or [["fade", 1]]
    rng = random.Random(f"{seed}-trans")
    items = []
    for sc in s["scenes"]:
        imgs = sc.get("images") or []
        d = sc["end"] - sc["start"]
        for k, im in enumerate(imgs):
            items.append([im, sc["start"] + d * k / len(imgs)])
    total = s["alignment"]["duration"]
    names, weights = zip(*th["transitions"])
    out = []
    for i, (im, t0) in enumerate(items):
        t1 = items[i + 1][1] if i + 1 < len(items) else total
        tr = rng.choices(names, weights)[0] if i + 1 < len(items) else None
        td = th.get("flash_sec", th["transition_sec"]) if tr == "fadewhite" and th.get("flash_sec") else th["transition_sec"]
        out.append([im, t0, t1, tr, td])
    # gambar berganti sedikit SEBELUM kata pertama adegan (transisi berpusat di batas)
    for i in range(1, len(out)):
        shift = out[i - 1][4] / 2
        b = max(out[i - 1][1] + 0.4, out[i][1] - shift)
        out[i - 1][2] = b; out[i][1] = b
    res = []
    for ii, (im, t0, t1, tr, td) in enumerate(out):
        frames = max(int(round(t1 * FPS)) - int(round(t0 * FPS)), 6)
        k = 1
        if dyn["shot"] and pdir and frames / FPS > dyn["shot"] * 1.5:
            k = min(6, max(1, round(frames / FPS / dyn["shot"])))
        if pdir and clip_for(pdir, im):   # klip animasi: satu shot utuh, tanpa pecah sudut kamera
            res.append((im, frames, tr, td, None, True)); continue
        if k == 1 and not (steady and pdir):
            res.append((im, frames, tr, td, None, True)); continue
        pts = focus_points(os.path.join(pdir, "images", im))
        order = (STEADY_ORDER if steady else SHOT_ORDER)[ii % len(SHOT_ORDER)]
        if steady:   # titik fokus berdekatan dengan tengah supaya zoom tidak terasa menggeser
            pts = [(0.5 + (x - 0.5) * 0.35, 0.5 + (y - 0.5) * 0.5) for x, y in pts]
        base = frames // k
        for j in range(k):
            f_j = base if j < k - 1 else frames - base * (k - 1)
            a, b = pts[j % len(pts)], pts[(j + 1) % len(pts)]
            shot = {"kind": order[j % len(order)], "a": a, "b": b}
            last = j == k - 1
            res.append((im, f_j, tr if last else "fade", td if last else dyn["cut"], shot, last))
    return res


CUT = 0.1   # "hard cut": xfade 3 frame, tetap terlihat instan (1 frame membuat timestamp ffmpeg mundur & video terpotong)


def plan_clean(s, seed, pdir=None):
    """Mode rapi: 1 shot per gambar. Antar gambar:
    - chapter baru -> hard cut + whoosh (kontras),
    - sisanya: hard cut tanpa suara (55%), zoom match-cut + whoosh (30%), slide halus ber-ease + whoosh (15%, hanya 16:9).
    Format tuple sama dengan plan(): (gambar, frame, transisi, durasi, shot, whoosh)."""
    rng = random.Random(f"{seed}-clean")
    vertical = s.get("aspect") == "9:16"
    items = []
    for sc in s["scenes"]:
        imgs = sc.get("images") or []
        d = sc["end"] - sc["start"]
        for k, im in enumerate(imgs):
            items.append([im, sc["start"] + d * k / len(imgs), bool(sc.get("chapter")) and k == 0])
    total = s["alignment"]["duration"]
    kinds = []
    for i in range(len(items) - 1):
        if items[i + 1][2]:
            kinds.append("chapter")
        else:
            r = rng.random()
            kinds.append("cut" if r < 0.55 else "zoom" if r < 0.85 or vertical else "slide")
        if i and kinds[-1] == "zoom" and kinds[-2] == "zoom":   # jangan dua match-cut berturut-turut
            kinds[-1] = "cut"
    res = []
    for i, (im, t0, _) in enumerate(items):
        t1 = items[i + 1][1] if i + 1 < len(items) else total
        frames = max(int(round(t1 * FPS)) - int(round(t0 * FPS)), 6)
        k = kinds[i] if i < len(kinds) else None
        tr, td = (("smoothleft", 0.6) if k == "slide" else ("fade", CUT)) if k else (None, 0)
        n = frames + (int(round(td * FPS)) if tr else 0)
        clip = pdir and clip_for(pdir, im)
        shot = None if clip else {"kind": "clean", "move": rng.choice(["pan", "push", "push"]), "dir": rng.choice([-1, 1]),
                                  "vertical": vertical, "n": n, "zin": k == "zoom",
                                  "zout": i > 0 and kinds[i - 1] == "zoom"}
        res.append((im, frames, tr, td, shot, k in ("chapter", "zoom", "slide")))
    return res


def validate(pdir, s):
    probs = []
    if not os.path.exists(os.path.join(pdir, "voice.mp3")):
        probs.append("voice.mp3 belum diupload")
    for i, sc in enumerate(s["scenes"], 1):
        imgs = sc.get("images") or []
        if not imgs:
            probs.append(f"Adegan {i} belum punya gambar")
        for im in imgs:
            if not os.path.exists(os.path.join(pdir, "images", im)):
                probs.append(f"Adegan {i}: file {im} tidak ada")
    return probs


def _encoder(grain=False):
    """Intel Quick Sync bila ada (lebih cepat), kalau tidak libx264. Tema ber-grain dibatasi ~8 Mbps
    (rekomendasi YouTube 1080p), karena grain membuat kualitas-konstan membengkak (>13 Mbps)."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x240", "-t", "0.2",
                        "-c:v", "h264_qsv", "-f", "null", "-"], capture_output=True)
    if r.returncode == 0:
        return ["-c:v", "h264_qsv", "-b:v", "8M", "-maxrate", "10M", "-bufsize", "16M"] if grain \
            else ["-c:v", "h264_qsv", "-global_quality", "21", "-look_ahead", "0"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "19"] + (["-maxrate", "10M", "-bufsize", "16M"] if grain else [])


TRANSITION_SFX = {"halus": ("whoosh", 0.22), "jelas": ("whoosh", 0.45)}   # level: (jenis, volume)


def sfx_track(events, dur, path):
    """Satu file WAV berisi efek suara: events = [(detik, jenis, volume)], sisanya hening."""
    import wave
    import numpy as np
    sr, cache = 44100, {}
    out = np.zeros(int((dur + 2) * sr), dtype=np.float32)
    for t, kind, vol in events:
        if kind not in cache:
            with wave.open(clipper.sfx(kind), "rb") as w:
                cache[kind] = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
        fx = cache[kind] * vol
        a = int(max(t, 0) * sr)
        out[a:a + len(fx)] += fx[:len(out) - a]
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(np.clip(out[:int(dur * sr)], -32768, 32767).astype(np.int16).tobytes())
    return path


def render(pdir, theme="auto", music=None, log=print, workers=4, whisper_model=None):
    """Render final.mp4. Proyek 9:16 (Shorts) dirender 1080x1920."""
    global W, H
    W, H = (1080, 1920) if load(pdir).get("aspect") == "9:16" else (1920, 1080)
    s = load(pdir)
    probs = validate(pdir, s)
    if probs:
        raise RuntimeError("Belum bisa render:\n- " + "\n- ".join(probs[:15]))
    raw_voice = os.path.join(pdir, "voice.mp3")
    out_dir = os.path.join(pdir, "output"); seg_dir = os.path.join(out_dir, "segments")
    os.makedirs(seg_dir, exist_ok=True)
    # 1) rapatkan jeda + alignment (diulang hanya bila suara/pengaturan berubah)
    tight = s.get("tighten", True)
    short = s.get("aspect") == "9:16" or s.get("dynamics") == "shorts"
    # Shorts: voiceover super rapat (semua jeda napas > 0,28 dtk dipotong jadi 0,1 dtk); video panjang: lebih longgar
    gap, keep = (0.55, 0.35) if short else (0.6, 0.4)   # Shorts: jeda antar kalimat tetap ada supaya sempat dipahami
    vsig = f"{os.path.getsize(raw_voice)}-{int(os.path.getmtime(raw_voice))}-{tight}-v2-{lang_of(s)}-{gap}"   # v2: jeda pakai speech.json
    voice = os.path.join(out_dir, "voice_tight.wav") if tight else raw_voice
    wpath = os.path.join(pdir, "words.json")
    if s.get("alignment", {}).get("voice_sig") == vsig and os.path.exists(wpath) and os.path.exists(voice) \
            and all("start" in sc for sc in s["scenes"]):
        with open(wpath, encoding="utf-8") as f: words = json.load(f)
        log("Alignment lama dipakai ulang", 0.25)
    else:
        words = align(pdir, s, raw_voice, whisper_model, lambda m, f=None: log(m, None if f is None else f * 0.22))
        if tight:   # jeda > 0,6 dtk dipotong berdasarkan timing kata (andal walau ada musik di jeda)
            with open(os.path.join(pdir, "speech.json"), encoding="utf-8") as f: speech = json.load(f)
            words, d0, d1, n = tighten_by_words(raw_voice, words, voice, max_gap=gap, keep=keep, speech=speech)
            scene_times(s, words, d1)
            log(f"Jeda dirapatkan: {n} jeda, {d0:.0f}s -> {d1:.0f}s", 0.25)
        with open(wpath, "w", encoding="utf-8") as f:
            json.dump(words, f, ensure_ascii=False)
        s["alignment"]["voice_sig"] = vsig
        save(pdir, s)
    # 2) tema
    th_all = themes()
    if theme in ("auto", "rotate", "", None):
        theme = pick_theme(s.get("topic", "") if theme == "auto" else "", recent_themes())
    th = th_all[theme]
    s["theme_used"] = theme; save(pdir, s)
    seed = s.get("id") or os.path.basename(pdir)
    items = plan(s, th, seed, pdir)
    # 3) segmen per gambar (paralel, di-cache: hanya yang berubah yang dirender ulang)
    tasks, segs = [], []
    th_sig = hashlib.md5(json.dumps(th, sort_keys=True).encode()).hexdigest()[:8]
    for i, (im, frames, tr, td, shot, change) in enumerate(items):
        clip = clip_for(pdir, im)
        p = clip or os.path.join(pdir, "images", im)
        n = frames + (int(round(td * FPS)) if tr else 0)   # tambahan untuk overlap transisi
        key = f"{im}|{os.path.getsize(p)}|{int(os.path.getmtime(p))}|{n}|{theme}|{th_sig}|{i}|{json.dumps(shot)}|{W}x{H}"
        seg = os.path.join(seg_dir, f"s{i:03d}_{hashlib.md5(key.encode()).hexdigest()[:10]}.mp4")
        segs.append((seg, n, tr, td, change))
        if not os.path.exists(seg):
            cmd = _clip_cmd(p, seg, n, th) if clip else _segment_cmd(p, seg, n, th, random.Random(f"{seed}-{i}"), shot)[0]
            tasks.append(cmd)
    for f in os.listdir(seg_dir):          # buang segmen lama yang tidak terpakai
        if os.path.join(seg_dir, f) not in {x[0] for x in segs}:
            os.remove(os.path.join(seg_dir, f))
    done = [0]
    def run1(cmd):
        clipper.run(cmd)
        done[0] += 1
        log(f"Animasi gambar {done[0]}/{len(tasks)}", 0.25 + 0.45 * done[0] / max(1, len(tasks)))
    if tasks:
        log(f"Render {len(tasks)} gambar ({len(items) - len(tasks)} dari cache)...", 0.25)
        with ThreadPoolExecutor(workers) as ex:
            list(ex.map(run1, tasks))
    # 4) subtitle & srt
    cards = []
    if s.get("chapter_cards", True):
        for k, sc in enumerate(s["scenes"]):
            if sc.get("chapter") and k > 0:
                cards.append((sc["start"] + 0.2, sc["chapter"]))
    # Shorts: kalimat hook (adegan 1, maks 12 kata) tampil besar & kontras di tengah layar sejak kata pertama
    hook = 0
    if short and s.get("hook_caption", True) and s.get("subtitles", True) and words:
        hook = min(len(s["scenes"][0]["text"].split()), 12, len(words))
    nums = [(sc["start"] + 0.05, n) for sc in s["scenes"] if sc.get("start") is not None and (n := scene_num(sc))] \
        if s.get("num_cards", True) else []
    write_ass(words if s.get("subtitles", True) else [], th["subtitle"], os.path.join(out_dir, "subs.ass"), cards, hook, nums)
    thumb = os.path.join(pdir, "images", s["thumbnail"]) if s.get("thumbnail") and s.get("thumb_intro", True) else None
    if thumb and not os.path.isfile(thumb):
        thumb = None
    lead = THUMB_SEC if thumb else 0.0   # thumbnail tampil di depan: semua audio & subtitle mundur sebanyak ini
    write_srt([dict(w, start=w["start"] + lead, end=w["end"] + lead) for w in words], os.path.join(out_dir, "final.srt"))
    write_metadata(s, os.path.join(out_dir, "metadata.txt"))
    # 5) gabung: transisi + subtitle + voice + musik (ducking) -> final.mp4
    log("Menggabungkan transisi, subtitle, suara & musik...", 0.72)
    dur = s["alignment"]["duration"]
    cmd = ["ffmpeg", "-y", "-v", "error", "-stats_period", "2", "-progress", "pipe:1"]
    for seg, *_ in segs:
        cmd += ["-i", os.path.basename(seg)]
    log(f"{len(segs)} shot dari {len({x[0] for x in items})} gambar", 0.72)
    nv = len(segs)
    cmd += ["-i", voice]
    if music:
        cmd += ["-stream_loop", "-1", "-i", music]
    if thumb:
        cmd += ["-loop", "1", "-t", f"{THUMB_SEC + 0.5}", "-i", thumb]
    f, cur, off, cuts = [], "[0:v]", 0.0, []
    for i in range(1, nv):
        off += segs[i - 1][1] / FPS - segs[i - 1][3]
        if segs[i - 1][4]:   # whoosh hanya saat gambar berganti, bukan antar-shot gambar yang sama
            cuts.append(off + segs[i - 1][3] / 2 - 0.35)   # puncak whoosh di tengah transisi
        lab = f"[x{i}]"
        f.append(f"{cur}[{i}:v]xfade=transition={segs[i - 1][2]}:duration={segs[i - 1][3]}:offset={off:.4f}{lab}")
        cur = lab
    fontsdir = ""
    if os.path.isdir(FONTS_DIR):
        fontsdir = ":fontsdir=" + os.path.relpath(FONTS_DIR, seg_dir).replace("\\", "/")
    vout = "[vm]" if thumb else "[v]"
    if s.get("subtitles", True) or cards or nums:
        f.append(f"{cur}ass=../subs.ass{fontsdir},format=yuv420p,setsar=1{vout}")
    else:   # audio saja: tanpa teks di video (final.srt tetap dibuat untuk CC YouTube)
        f.append(f"{cur}format=yuv420p,setsar=1{vout}")
    ti = None
    if thumb:   # gambar thumbnail 1 detik di paling depan (zoom sangat pelan), lalu video utama
        ti = nv + 1 + (1 if music else 0)
        f.append(f"[{ti}:v]scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{H * 2},"
                 f"zoompan=z='1+0.03*on/{int(THUMB_SEC * FPS)}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s={W}x{H}:fps={FPS},"
                 f"trim=duration={THUMB_SEC},format=yuv420p,setsar=1[tv]")
        f.append("[tv][vm]concat=n=2:v=1:a=0[v]")
    level = s.get("transition_sfx", "halus")
    events = []
    if level in TRANSITION_SFX:   # whoosh di tiap perpindahan gambar
        kind, vol = TRANSITION_SFX[level]
        events = [(t, kind, vol) for t in cuts]
        if short:   # Shorts: impact saat hook muncul, riser yang memuncak tepat saat judul chapter muncul + impact
            k = 1.6 if level == "jelas" else 1.0
            if hook:
                events.append((words[0]["start"] - 0.02, "impact", 0.5 * k))
            for t, _ in cards:
                events += [(t - 1.2, "riser", 0.35 * k), (t - 0.02, "impact", 0.45 * k)]
    if events:
        cmd += ["-i", os.path.relpath(sfx_track(events, dur, os.path.join(out_dir, "sfx_track.wav")), seg_dir)]
        sfx_in = nv + (2 if music else 1) + (1 if thumb else 0)
    else:
        sfx_in = None
    if music:   # musik ~ -20 dB di bawah suara, otomatis turun saat ada suara
        f.append(f"[{nv}:a]asplit=2[va][vb]")
        f.append(f"[{nv + 1}:a]volume=0.3,afade=t=out:st={max(0, dur - 4):.2f}:d=4[m0]")
        f.append("[m0][va]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=400[md]")
        f.append("[vb][md]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a0]")
        amap = "[a0]"
    else:
        amap = f"[{nv}:a]"
    if sfx_in is not None:
        f.append(f"{amap}[{sfx_in}:a]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]")
    else:
        f.append(f"{amap}anull[a]")
    amap = "[a]"
    if thumb:
        f.append(f"[a]adelay={int(lead * 1000)}:all=1[ad]"); amap = "[ad]"
        dur += lead
    cmd += ["-filter_complex", ";".join(f), "-map", "[v]", "-map", amap, "-t", f"{dur:.3f}"]
    cmd += _encoder("noise" in (th.get("fx") or "")) + ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", os.path.join("..", "final.mp4")]
    p = subprocess.Popen(cmd, cwd=seg_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         encoding="utf-8", errors="ignore")
    for line in p.stdout:   # progres dari ffmpeg (out_time_ms)
        if line.startswith("out_time_ms="):
            try:
                t = int(line.split("=")[1]) / 1e6
                log(f"Menggabungkan... {int(t // 60)}:{int(t % 60):02d} / {int(dur // 60)}:{int(dur % 60):02d}", 0.72 + 0.27 * min(t / dur, 1))
            except ValueError:
                pass
    err = p.stderr.read()
    if p.wait() != 0:
        raise RuntimeError("Render akhir gagal: " + err[-1200:])
    log("Selesai!", 1.0)
    return os.path.join(out_dir, "final.mp4")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Render video dokumenter dari folder proyek")
    ap.add_argument("project")
    ap.add_argument("--theme", default="auto")
    ap.add_argument("--music")
    ap.add_argument("--srt-only", action="store_true", help="hanya buat script.srt untuk CapCut")
    a = ap.parse_args()
    s = load(a.project)
    if a.srt_only:
        print(write_temp_srt(a.project, s)); sys.exit()
    if not all(sc.get("images") for sc in s["scenes"]):
        err = auto_map(s, list_images(a.project))
        if err:
            sys.exit(err)
        save(a.project, s)
    print(render(a.project, a.theme, a.music, log=lambda m, f=None: print(f"[{(f or 0) * 100:5.1f}%] {m}")))
