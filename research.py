"""Riset ide konten: autocomplete (ala AnswerThePublic), video outlier (ala 1of10/vidIQ),
Google Trends (pytrends, tidak resmi) dan generator ide AI."""
import json, re, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

# Awalan/akhiran untuk memancing autocomplete, dikelompokkan seperti AnswerThePublic
GROUPS = {
    "id": {
        "Pertanyaan": ["apa itu", "kenapa", "bagaimana", "cara", "apakah", "berapa", "kapan", "siapa", "dimana"],
        "Perbandingan": ["{q} vs", "{q} atau", "{q} dan", "{q} lebih baik"],
        "Kata tambahan": ["{q} untuk pemula", "{q} terbaik", "{q} murah", "{q} 2026", "{q} tips", "{q} tanpa", "{q} yang", "{q} di"],
    },
    "en": {
        "Questions": ["what is", "why", "how to", "how", "can", "is", "which", "when", "where"],
        "Comparisons": ["{q} vs", "{q} or", "{q} and", "{q} better than"],
        "Modifiers": ["{q} for beginners", "best {q}", "{q} cheap", "{q} 2026", "{q} tips", "{q} without", "{q} near"],
    },
}


def _get_json(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore"))


def _suggest(q, lang, youtube=True):
    p = {"client": "firefox", "hl": lang, "q": q}
    if youtube:
        p["ds"] = "yt"
    try:
        return _get_json("https://suggestqueries.google.com/complete/search?" + urllib.parse.urlencode(p))[1]
    except Exception:
        return []


def questions(topic, lang="id", source="youtube"):
    """Kelompok -> daftar saran autocomplete yang mengandung topik."""
    topic = topic.strip()
    jobs = []
    for group, pats in GROUPS.get(lang, GROUPS["id"]).items():
        for p in pats:
            jobs.append((group, p.format(q=topic) if "{q}" in p else f"{p} {topic}"))
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(lambda j: _suggest(j[1], lang, source == "youtube"), jobs))
    out, seen = {}, set()
    key = topic.lower().split()[0] if topic else ""
    for (group, _), sug in zip(jobs, res):
        for s in sug:
            if s.lower() in seen or (key and key not in s.lower()):
                continue
            seen.add(s.lower())
            out.setdefault(group, []).append(s)
    return out


def _full_info(url):
    import yt_dlp
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True, "no_warnings": True}) as y:
            return y.extract_info(url, download=False)
    except Exception:
        return None


def outliers(keyword, n=20, kind="all", days=0, log=None):
    """Cari video lalu urutkan berdasarkan views/hari dan rasio views:subscriber."""
    import yt_dlp
    # Pencarian biasa YouTube jarang memuat video pendek, jadi untuk Shorts pakai filter
    # "di bawah 4 menit" (sp=EgIYAQ==), dengan dan tanpa #shorts, lalu gabungkan.
    if kind == "shorts":
        sources = [f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(q)}&sp=EgIYAQ%253D%253D"
                   for q in (keyword, keyword + " #shorts")]
    else:
        sources = [f"ytsearch{max(n * 2, 20)}:{keyword}"]
    flat, seen = [], set()
    with yt_dlp.YoutubeDL({"quiet": True, "extract_flat": True, "skip_download": True, "playlistend": n * 2}) as y:
        for src in sources:
            for e in y.extract_info(src, download=False).get("entries") or []:
                if e and e.get("id") and e["id"] not in seen:
                    seen.add(e["id"]); flat.append(e)
    if kind == "long":
        flat = [e for e in flat if (e.get("duration") or 0) > 240]
    elif kind == "shorts":
        flat = [e for e in flat if not e.get("duration") or e["duration"] <= 240]
    flat = flat[:n]
    urls = [f"https://www.youtube.com/watch?v={e['id']}" for e in flat]
    infos, done = [], 0
    with ThreadPoolExecutor(8) as ex:
        for info in ex.map(_full_info, urls):
            done += 1
            if log:
                log(done, len(urls))
            infos.append(info)
    now, rows = datetime.now(), []
    for e, info in zip(flat, infos):
        info = info or {}
        dur = info.get("duration") or e.get("duration") or 0
        if (kind == "shorts" and dur > 240) or (kind == "long" and dur <= 240):
            continue
        up = info.get("upload_date")
        age = max((now - datetime.strptime(up, "%Y%m%d")).days, 1) if up else None
        if days and age and age > days:
            continue
        views = info.get("view_count") or e.get("view_count") or 0
        subs = info.get("channel_follower_count")
        rows.append({
            "id": e["id"], "url": f"https://www.youtube.com/watch?v={e['id']}",
            "title": e.get("title"), "channel": e.get("channel") or e.get("uploader"),
            "duration": dur, "views": views, "subs": subs,
            "likes": info.get("like_count"), "comments": info.get("comment_count"),
            "upload_date": up, "age_days": age,
            "views_per_day": round(views / age) if age else None,
            "ratio": round(views / subs, 2) if subs else None,
            "thumb": f"https://i.ytimg.com/vi/{e['id']}/hqdefault.jpg",
            "tags": (info.get("tags") or [])[:8],
        })
    # Outlier = rasio views:subscriber tinggi; video <1000 views tidak dihitung outlier
    rows.sort(key=lambda r: ((r["ratio"] or 0) if r["views"] >= 1000 else 0, r["views_per_day"] or 0), reverse=True)
    return rows


def trends(keyword, geo="ID", timeframe="today 3-m", lang="id"):
    """Google Trends lewat pytrends (tidak resmi: bisa gagal / kena limit 429)."""
    from pytrends.request import TrendReq
    hl = "id-ID" if lang == "id" else "en-US"
    last = None
    for attempt in range(3):
        try:
            p = TrendReq(hl=hl, tz=420, timeout=(10, 25))
            p.build_payload([keyword], geo=geo, timeframe=timeframe)
            iot = p.interest_over_time()
            series = [] if iot.empty else [{"date": str(i.date()), "value": int(v)} for i, v in iot[keyword].items()]
            rq = p.related_queries().get(keyword) or {}
            def rows(df):
                return [] if df is None else [{"query": r["query"], "value": int(r["value"])} for r in df.head(15).to_dict("records")]
            return {"series": series, "rising": rows(rq.get("rising")), "top": rows(rq.get("top"))}
        except Exception as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Google Trends gagal (pytrends tidak resmi, mungkin kena limit): {last}")


def ideas(topic, context, n=10, lang="id", api_key=None, model="claude-sonnet-5-5", mode="code"):
    """Minta Claude membuat ide konten Shorts dari hasil riset."""
    import clipper
    bahasa = "Bahasa Indonesia" if lang == "id" else "English"
    prompt = f"""Kamu adalah strategis konten YouTube Shorts / TikTok / Reels.
Topik: {topic}
Data riset (pertanyaan audiens, video outlier, Google Trends):
{json.dumps(context, ensure_ascii=False)[:12000]}

Buat {n} ide konten video pendek (30-60 detik) yang memanfaatkan pola dari data di atas.
Tulis dalam {bahasa}. Balas HANYA JSON array, tiap item:
{{"title": "...", "hook": "kalimat 3 detik pertama", "outline": ["poin 1", "poin 2", "poin 3"],
"why": "kenapa ide ini berpotensi viral, rujuk data", "hashtags": ["#..."]}}"""
    if mode == "api" and api_key:
        import anthropic
        msg = anthropic.Anthropic(api_key=api_key).messages.create(
            model=model, max_tokens=4000, messages=[{"role": "user", "content": prompt}])
        text = msg.content[0].text
    else:
        text = clipper._ask_claude_code(prompt, log=lambda *a: None)
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        raise RuntimeError("Jawaban AI tidak berisi JSON: " + text[:300])
    return json.loads(m.group(0))
