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

## Dokumenter panjang (menu 📽️ Dokumenter)
Video 16:9 1080p dengan suara dari CapCut dan gambar dari Google Flow. Satu folder per proyek di `docs/<id>/`
(`script.json`, `images/`, `voice.mp3`, `music.*`, `output/`).

1. **Naskah**: isi topik, lalu satu panggilan AI menulis adegan, prompt gambar, chapter, klaim yang perlu dicek, dan metadata.
   Atau **import folder bahan** yang sudah ada (Excel/SRT naskah + gambar + mp3/mp4 suara).
2. **Suara**: unduh `script.srt`, import ke CapCut, buat TTS, export MP3, lalu upload.
3. **Gambar**: salin prompt per batch ke Google Flow, upload hasilnya, lalu atur urutannya (seret, atau pilih adegan per gambar).
4. **Render**: jeda suara > 0,6 dtk dirapatkan, Whisper menyelaraskan kata ke naskah, lalu FFmpeg merakit video
   (Ken Burns, transisi, subtitle, musik dengan ducking). Hasilnya `final.mp4`, `final.srt`, dan `metadata.txt` (dengan chapter).
5. **✂️ Buat Shorts**: memotong video jadi Shorts 9:16 memakai timing kata yang sudah ada (tanpa Whisper ulang).

Tema ada di `themes.json` (Documentary, Noir, Archive, Fun). Tema baru cukup ditambahkan di file itu.
Font tambahan (mis. Poppins) bisa ditaruh di folder `fonts/`. CLI: `python documentary.py docs/<id> --theme noir --music musik.mp3`

## Tips
- Model `small` cukup untuk bahasa Indonesia; `medium`/`large-v3` lebih akurat tapi lambat di CPU.
- Kata hook bisa ditambah di `HOOK_WORDS` dalam `clipper.py`.
- Konten terbatas (Instagram 18+/wilayah, akun privat): ekspor cookies browser saat login (ekstensi "Get cookies.txt LOCALLY"), simpan sebagai `cookies.txt` di folder ini. Dipakai otomatis untuk semua situs kecuali YouTube. File ini rahasia (setara login) dan sudah di-.gitignore.

## Ekstensi Chrome: ShortsClips Flow Importer
Mengirim gambar hasil Google Flow langsung ke proyek Dokumenter (dipasang otomatis ke adegannya).
1. Buka `chrome://extensions`, nyalakan **Developer mode**, klik **Load unpacked**, pilih folder `extension/flow-importer`.
2. Jalankan ShortsClips (`run.bat`), buka project di flow.google.com: panel **ShortsClips Flow Importer** muncul di kanan bawah.
3. Pilih proyek → **📋 Salin batch berikutnya** → tempel di kotak Flow → klik buat (tetap kamu yang menekan).
4. Setelah gambar jadi: **🔍 Pindai gambar** → cek pasangan nomor prompt → **⬆ Kirim ke ShortsClips**.
