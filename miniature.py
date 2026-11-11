"""Video "giant hand miniature": AI menulis rencana fase timelapse + prompt Veo (9:16), klip dibuat di Flow/Veo/Runway/Luma,
lalu klip digabung, dipercepat jadi timelapse, dan diberi lapisan suara ASMR (ketukan, kaca, kawat) yang disinkronkan otomatis."""
import json, os, re, subprocess, wave
import numpy as np
import clipper, motion

W, H, FPS, SR = 1080, 1920, 30, 44100
SOUNDS = {"tap": "ketukan / tapping", "glass": "denting kaca", "wire": "gesekan kawat", "click": "klik kecil",
          "brush": "sapuan kuas / lem", "sand": "pasir / tanah"}


def write_plan(subject, phases=3, lang="id", notes="", api_key=None, model="claude-sonnet-5-5", log=print):
    bahasa = {"id": "Bahasa Indonesia", "en": "English"}.get(lang, "Bahasa Indonesia")
    log("Claude menyusun fase timelapse & prompt Veo...")
    raw = motion._ask(f"""You plan a viral vertical AI video in the "giant hand miniature" style: two giant, realistic human hands
build a detailed miniature on a craft workbench, shown as a time-lapse. The miniature is shaped like the subject.
Subject: {subject}
{f'Extra notes from the creator: {notes}' if notes else ''}
Split the build into exactly {phases} phases, in logical construction order (e.g. 1. foundation/base shape of the subject,
2. wire frame of the upper parts + glass panels installed, 3. interior floors, tiny shops, dome, warm golden lights, landscaping).
For EVERY phase write one Veo 3 video prompt (English, 8 seconds) that:
- starts with: "Vertical 9:16 full-screen, hyper-realistic macro time-lapse,"
- repeats the SAME detailed description of the miniature, materials, workbench and lighting in every prompt (continuity),
- says exactly what the hands add in this phase and how the build looks at the start and end of the clip,
- has one cinematic camera move (slow tracking, orbit, push-in or crane up), shallow depth of field,
- ends with the audio: "Audio: crisp ASMR sounds only (…specific sounds…), no music, no voice." and "No text, no captions."
Also write:
- "start_image": an image prompt (English, Nano Banana / Flow) for the very first frame: giant hands and the empty base on the
  workbench, same style, 9:16.
- "title": short scroll-stopping title in {bahasa}; "caption": caption in {bahasa} that names the concept and lists the phase
  prompts briefly like "Prompt fase 1: ..." (people love seeing the prompt); "hashtags": 6-10 hashtags.
- for each phase "sounds": 2-3 of {list(SOUNDS)} that match what happens on screen.
Return ONLY JSON: {{"title": "...", "caption": "...", "hashtags": ["#..."], "start_image": "...",
"phases": [{{"name": "short phase name in {bahasa}", "prompt": "...", "sounds": ["tap", "glass"]}}]}}""", api_key, model, log)
    m = re.search(r"\{.*\}", raw, re.S)
    data = json.loads(m.group(0)) if m else {}
    ph = [{"name": (p.get("name") or f"Fase {i + 1}").strip(), "prompt": (p.get("prompt") or "").strip(),
           "sounds": [s for s in p.get("sounds") or [] if s in SOUNDS] or ["tap"]} for i, p in enumerate(data.get("phases") or [])]
    if not ph or not all(p["prompt"] for p in ph):
        raise RuntimeError("Rencana fase tidak valid dari AI")
    return {"title": data.get("title") or subject, "caption": data.get("caption", ""), "hashtags": data.get("hashtags") or [],
            "start_image": data.get("start_image", ""), "phases": ph}


# ---------- suara ASMR sintetis (numpy, deterministik per seed) ----------
def _env(n, decay):
    return np.exp(-np.arange(n) / (SR * decay))


def _lowpass(x, k):
    return np.convolve(x, np.ones(k) / k, mode="same") if k > 1 else x


def _sound(kind, rng):
    if kind == "tap":   # ketukan jari/pinset di kayu-plastik: burst noise + dentum kecil
        n = int(SR * 0.09); t = np.arange(n) / SR
        x = _lowpass(rng.standard_normal(n), rng.integers(2, 6)) * _env(n, 0.012)
        x += 0.6 * np.sin(2 * np.pi * rng.uniform(180, 420) * t) * _env(n, 0.02)
    elif kind == "glass":   # denting kaca: harmonik tak-selaras yang meluruh
        n = int(SR * 0.7); t = np.arange(n) / SR; f = rng.uniform(1900, 3600)
        x = sum(a * np.sin(2 * np.pi * f * r * t) * _env(n, d) for a, r, d in ((1, 1, .22), (.5, 2.76, .12), (.25, 5.4, .06)))
        x[:int(SR * 0.004)] += rng.standard_normal(int(SR * 0.004)) * 0.6
    elif kind == "wire":   # gesekan kawat logam
        n = int(SR * rng.uniform(0.12, 0.25)); t = np.arange(n) / SR
        x = (rng.standard_normal(n) - _lowpass(rng.standard_normal(n), 8)) * (0.5 + 0.5 * np.sin(2 * np.pi * 35 * t))
        x *= np.hanning(n)
    elif kind == "click":
        n = int(SR * 0.025); x = rng.standard_normal(n) * _env(n, 0.003)
    elif kind == "brush":
        n = int(SR * rng.uniform(0.25, 0.45)); x = _lowpass(rng.standard_normal(n), 3) * np.hanning(n) * 0.5
    else:   # sand: butiran halus
        n = int(SR * 0.35); x = np.zeros(n)
        for i in rng.integers(0, n - 200, 40):
            x[i:i + 200] += rng.standard_normal(200) * _env(200, 0.0015) * rng.uniform(.2, .6)
    return x / (np.abs(x).max() + 1e-9)


VOL = {"tap": .55, "glass": .32, "wire": .25, "click": .4, "brush": .3, "sand": .3}
RATE = {"tap": 2.2, "glass": .5, "wire": .7, "click": 1.2, "brush": .6, "sand": .5}   # rata-rata kejadian per detik


def asmr_track(windows, dur, path, seed=7):
    """windows = [(mulai, selesai, [jenis suara])]. Tulis WAV stereo 44.1 kHz."""
    rng = np.random.default_rng(seed)
    L = np.zeros(int(SR * (dur + 1))); R = np.zeros_like(L)
    room = _lowpass(rng.standard_normal(len(L)), 40) * 0.01   # nada ruangan sangat pelan
    L += room; R += room
    for a, b, kinds in windows:
        for k in kinds:
            t = a + rng.exponential(1 / RATE[k])
            while t < b - 0.1:
                s = _sound(k, rng) * VOL[k] * rng.uniform(.6, 1)
                i = int(t * SR); s = s[:len(L) - i]; pan = rng.uniform(.25, .75)
                L[i:i + len(s)] += s * (1 - pan) * 1.4; R[i:i + len(s)] += s * pan * 1.4
                t += rng.exponential(1 / RATE[k]) + 0.05
    st = np.stack([L, R], 1)[:int(SR * dur)]
    st = st * (0.9 / (np.abs(st).max() + 1e-9))   # puncak -1 dB: ASMR harus jelas terdengar
    with wave.open(path, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((st * 32767).astype("<i2").tobytes())
    return path


def _has_audio(p):
    return bool(clipper.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                             "-of", "csv=p=0", p]).strip())


def _atempo(f):
    out = []
    while f > 2.0:
        out.append("atempo=2.0"); f /= 2.0
    out.append(f"atempo={f:.4f}")
    return ",".join(out)


def render(clips, phases, out, sec_per_clip=0, audio="mix", log=print):
    """clips: path video per fase (urut). sec_per_clip > 0: percepat tiap klip jadi sekian detik (timelapse).
    audio: mix (suara asli pelan + ASMR) | asmr (ASMR saja) | original (suara asli saja)."""
    work = os.path.join(os.path.dirname(out), "_mini_tmp"); os.makedirs(work, exist_ok=True)
    parts, windows, t = [], [], 0.0
    for i, c in enumerate(clips):
        d = clipper.get_duration(c)
        f = max(1.0, d / sec_per_clip) if sec_per_clip else 1.0
        nd = d / f
        log(f"Menyiapkan klip {i + 1}/{len(clips)}" + (f" (timelapse {f:.1f}x)" if f > 1 else ""), i / len(clips) * 0.6)
        p = os.path.join(work, f"p{i:02d}.mp4")
        vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={FPS},setpts=PTS/{f:.4f}"
        if _has_audio(c):
            cmd = ["-i", c, "-filter_complex", f"[0:v]{vf}[v];[0:a]aresample={SR},{_atempo(f) if f > 1 else 'anull'}[a]"]
        else:
            cmd = ["-i", c, "-f", "lavfi", "-i", f"anullsrc=r={SR}:cl=stereo", "-filter_complex", f"[0:v]{vf}[v];[1:a]anull[a]"]
        clipper.run(["ffmpeg", "-y", "-v", "error"] + cmd + ["-map", "[v]", "-map", "[a]", "-t", f"{nd:.3f}",
                     "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", str(SR),
                     "-ac", "2", p])
        nd = clipper.get_duration(p)
        windows.append((t, t + nd, (phases[i] if i < len(phases) else {}).get("sounds") or ["tap"]))
        parts.append(p); t += nd
    lst = os.path.join(work, "list.txt")
    with open(lst, "w", encoding="utf-8") as fh:
        fh.writelines(f"file '{p.replace(os.sep, '/')}'\n" for p in parts)
    joined = os.path.join(work, "joined.mp4")
    clipper.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", joined])
    log("Membuat suara ASMR tapping & kaca...", 0.75)
    if audio == "original":
        os.replace(joined, out)
    else:
        a = asmr_track(windows, t, os.path.join(work, "asmr.wav"))
        mix = ("[0:a]volume=0.45[o];[o][1:a]amix=inputs=2:duration=first:normalize=0[a]" if audio == "mix"
               else "[1:a]anull[a]")
        clipper.run(["ffmpeg", "-y", "-v", "error", "-i", joined, "-i", a, "-filter_complex", mix, "-map", "0:v", "-map", "[a]",
                     "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", f"{t:.3f}", out])
    import shutil
    shutil.rmtree(work, ignore_errors=True)
    log("Selesai", 1.0)
    return out
