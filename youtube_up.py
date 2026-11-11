"""Agen upload YouTube: antrean video jadi -> upload otomatis sebagai Private + jadwal tayang (publishAt).
YouTube sendiri yang menayangkan di jam yang dijadwalkan, jadi komputer tidak perlu menyala saat jam tayang.
Jam tayang dihitung dalam zona America/New_York (penonton AS), otomatis ikut pergantian jam musim panas."""
import json, os, re, threading, time, uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

BASE = os.path.dirname(os.path.abspath(__file__))
SECRET = os.path.join(BASE, "client_secret.json")      # OAuth client (Desktop app) dari Google Cloud Console
TOKEN = os.path.join(BASE, "youtube_token.json")       # RAHASIA: jangan dibagikan
QUEUE = os.path.join(BASE, "uploads.json")
SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]
ET, WIB = ZoneInfo("America/New_York"), ZoneInfo("Asia/Jakarta")
LONG_HOUR, SHORT_HOURS = 14, (12, 15, 19)    # jam tayang ET: video panjang 14.00, Shorts 12.00 / 15.00 / 19.00
LONG_GAP_DAYS, MAX_SHORTS_DAY = 2, 3
LOCK = threading.Lock()
STATE = {"auth_msg": "", "running": False}


# ---------- antrean ----------
def load():
    try:
        with open(QUEUE, encoding="utf-8") as f: return json.load(f)
    except (OSError, ValueError):
        return []


def save(q):
    tmp = QUEUE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: json.dump(q, f, ensure_ascii=False, indent=1)
    os.replace(tmp, QUEUE)


def parse_metadata(path):
    """metadata.txt dokumenter -> (judul opsi 1, deskripsi, tags)."""
    try:
        t = open(path, encoding="utf-8").read()
    except OSError:
        return "", "", []
    m = re.search(r"TITLE OPTIONS:\s*\n\s*1\.\s*(.+)", t)
    d = re.search(r"DESCRIPTION:\s*\n(.*?)(?=\n\s*TAGS:|\n\s*CLAIMS TO VERIFY|\Z)", t, re.S)
    g = re.search(r"TAGS:\s*\n(.+)", t)
    tags = [x.strip() for x in (g.group(1) if g else "").split(",") if x.strip()]
    return (m.group(1).strip() if m else ""), (d.group(1).strip() if d else ""), tags


def _taken(q, kind):
    return [datetime.fromisoformat(i["publish_at"]) for i in q if i["kind"] == kind and i["status"] != "batal"]


def next_slot(q, kind, now=None):
    """Slot tayang berikutnya (datetime UTC) yang belum dipakai antrean."""
    now = (now or datetime.now(timezone.utc)).astimezone(ET)
    taken = _taken(q, kind)
    min_start = now + timedelta(hours=2)    # beri waktu upload + proses HD
    for day in range(0, 120):
        d = (now + timedelta(days=day)).date()
        hours = (LONG_HOUR,) if kind == "long" else SHORT_HOURS
        for h in hours:
            t = datetime(d.year, d.month, d.day, h, tzinfo=ET)
            if t < min_start:
                continue
            if kind == "long":
                if any(abs((t - x).total_seconds()) < LONG_GAP_DAYS * 86400 - 3600 for x in taken): continue
            else:
                same = [x for x in taken if x.astimezone(ET).date() == d]
                if len(same) >= MAX_SHORTS_DAY or any(abs((t - x).total_seconds()) < 3600 for x in same): continue
            return t.astimezone(timezone.utc)
    raise RuntimeError("Tidak ada slot kosong 120 hari ke depan")


def add(item):
    """item: pid, kind (long|short), file, title, description, tags, thumb, publish_at (opsional ISO)."""
    with LOCK:
        q = load()
        if any(i["file"] == item["file"] and i["status"] in ("antre", "upload", "terjadwal", "kuota") for i in q):
            raise ValueError("Video ini sudah ada di antrean")
        pa = item.get("publish_at")
        item["publish_at"] = (datetime.fromisoformat(pa).astimezone(timezone.utc) if pa else next_slot(q, item["kind"])).isoformat()
        item.update(id=uuid.uuid4().hex[:8], status="antre", video_id="", error="", added=time.time())
        item["title"] = (item.get("title") or "")[:100]
        q.append(item); save(q)
        return item


def update(iid, **kw):
    with LOCK:
        q = load()
        for i in q:
            if i["id"] == iid:
                i.update(kw); save(q); return i
    return None


def remove(iid):
    with LOCK:
        q = load()
        it = next((i for i in q if i["id"] == iid), None)
        if it and it["status"] == "upload": raise ValueError("Sedang di-upload, tunggu selesai")
        save([i for i in q if i["id"] != iid])
        return it


REALISTIC = {"cinematic", "archive"}   # gaya gambar fotorealistis -> wajib label "konten sintetis"


def needs_ai_label(visual):
    """YouTube mewajibkan label hanya untuk konten AI yang tampak NYATA; kartun/ilustrasi/animasi tidak wajib."""
    return (visual or "") in REALISTIC


def wib(iso):
    return datetime.fromisoformat(iso).astimezone(WIB).strftime("%a %d %b %H:%M WIB")


# ---------- OAuth + YouTube API ----------
def creds(interactive=False):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    c = Credentials.from_authorized_user_file(TOKEN, SCOPES) if os.path.exists(TOKEN) else None
    if c and c.expired and c.refresh_token:
        try:
            c.refresh(Request())
            open(TOKEN, "w").write(c.to_json())
        except Exception:
            c = None
    if (not c or not c.valid) and interactive:
        if not os.path.exists(SECRET):
            raise RuntimeError("client_secret.json belum ada di folder ShortsClips (lihat panduan di halaman Upload)")
        from google_auth_oauthlib.flow import InstalledAppFlow
        c = InstalledAppFlow.from_client_secrets_file(SECRET, SCOPES).run_local_server(
            port=0, open_browser=True, authorization_prompt_message="", success_message="Berhasil. Tab ini boleh ditutup.")
        open(TOKEN, "w").write(c.to_json())
    return c if c and c.valid else None


def service():
    from googleapiclient.discovery import build
    c = creds()
    if not c: raise RuntimeError("Belum terhubung ke YouTube")
    return build("youtube", "v3", credentials=c, cache_discovery=False)


def channel():
    r = service().channels().list(part="snippet,statistics", mine=True).execute().get("items") or []
    return {"title": r[0]["snippet"]["title"], "subs": r[0]["statistics"].get("subscriberCount")} if r else None


def upload_one(it, log=print):
    from googleapiclient.http import MediaFileUpload
    from googleapiclient.errors import HttpError
    yt = service()
    desc = it.get("description", "")
    if it["kind"] == "short" and "#shorts" not in desc.lower(): desc = (desc + "\n\n#Shorts").strip()
    body = {"snippet": {"title": it["title"], "description": desc[:4900], "tags": it.get("tags", [])[:30],
                        "categoryId": it.get("category") or ("24" if it["kind"] == "short" else "27"), "defaultLanguage": it.get("lang", "id")},
            "status": {"privacyStatus": "private", "publishAt": it["publish_at"].replace("+00:00", "Z"),
                       "selfDeclaredMadeForKids": False, "containsSyntheticMedia": bool(it.get("ai", True))}}   # wajib True untuk AI yang tampak nyata
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(it["file"], chunksize=8 * 1024 * 1024, resumable=True))
    resp = None
    while resp is None:
        st, resp = req.next_chunk()
        if st: update(it["id"], progress=int(st.progress() * 100))
    vid = resp["id"]
    if it.get("thumb") and os.path.exists(it["thumb"]) and it["kind"] == "long":
        try:
            yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(it["thumb"])).execute()
        except HttpError as e:   # akun belum terverifikasi tidak boleh thumbnail kustom
            log(f"Thumbnail gagal: {str(e)[:150]}")
    return vid


def run_pending(log=print):
    """Upload semua item 'antre' (dan 'kuota' yang sudah lewat 1 hari). Kuota API default ~6 upload/hari."""
    if STATE["running"]: return
    STATE["running"] = True
    try:
        from googleapiclient.errors import HttpError
        for it in sorted(load(), key=lambda i: i["publish_at"]):
            if it["status"] == "kuota" and time.time() - it.get("tried", 0) < 6 * 3600: continue
            if it["status"] not in ("antre", "kuota"): continue
            if not creds(): return
            if datetime.fromisoformat(it["publish_at"]) < datetime.now(timezone.utc) + timedelta(minutes=20):
                q = load(); new = next_slot([i for i in q if i["id"] != it["id"]], it["kind"]).isoformat()
                update(it["id"], publish_at=new); it["publish_at"] = new   # slot terlewat -> geser ke slot berikutnya
            update(it["id"], status="upload", progress=0, error="")
            try:
                vid = upload_one(it, log)
                update(it["id"], status="terjadwal", video_id=vid, progress=100)
                log(f"YouTube: '{it['title']}' terjadwal {wib(it['publish_at'])}")
            except HttpError as e:
                quota = "quota" in str(e).lower() or "uploadLimitExceeded" in str(e)
                update(it["id"], status="kuota" if quota else "error", tried=time.time(),
                       error=("Kuota/batas upload harian habis, dicoba lagi nanti" if quota else str(e)[:300]))
                if quota: return
            except Exception as e:
                update(it["id"], status="error", error=str(e)[:300])
    finally:
        STATE["running"] = False


def loop(log=print):
    while True:
        try:
            if any(i["status"] in ("antre", "kuota") for i in load()): run_pending(log)
        except Exception as e:
            log(f"Agen upload: {e}")
        time.sleep(600)
