# ShortsClips — OpusClip versi lokal (Flask + Python)

## Syarat
1. Python 3.10+  — https://python.org (centang "Add to PATH")
2. FFmpeg — `winget install ffmpeg` (pastikan `ffmpeg -version` jalan di CMD)

## Menjalankan
Klik dua kali `run.bat`. Saat pertama dijalankan, script membuat venv dan menginstal paket.
Browser akan terbuka di http://127.0.0.1:5000

## Fitur
- Input: upload file atau link YouTube (yt-dlp)
- Transkripsi per kata dengan faster-whisper (memakai GPU NVIDIA jika ada, kalau tidak CPU)
- Pemilihan highlight otomatis + skor viral (kata hook, pertanyaan, kepadatan bicara)
- Crop 9:16 yang mengikuti posisi wajah (OpenCV)
- Subtitle gaya karaoke (kata yang diucapkan disorot), ditanam ke video
- Riwayat project tersimpan di folder `jobs/`

## AI Claude — mode Claude Code (pakai langganan Pro, tanpa biaya API)
1. Pasang Node.js (https://nodejs.org), lalu di CMD: `npm install -g @anthropic-ai/claude-code`
2. Jalankan `claude` sekali, pilih login dengan akun Claude Pro Anda.
3. Di aplikasi pilih "Claude Code (langganan Pro)". Pemakaian mengurangi kuota Pro Anda.

## AI Claude — mode API (berbayar terpisah)
Masukkan API key dari https://console.anthropic.com di kolom "API key Claude" (disimpan di `config.json`),
atau set environment variable `ANTHROPIC_API_KEY`. Claude memilih momen viral + membuat judul, hook, dan hashtag.
Biaya kira-kira beberapa sen per video. Jika key kosong/gagal, otomatis pakai metode lokal.

## Tips
- Model `small` cukup untuk bahasa Indonesia; `medium`/`large-v3` lebih akurat tapi lambat di CPU.
- Kata hook bisa ditambah di `HOOK_WORDS` dalam `clipper.py`.
