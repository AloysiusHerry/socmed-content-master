@echo off
cd /d %~dp0

rem --- Drive C penuh: simpan semua file sementara, cache pip & model AI di drive D ---
if not exist "%~dp0cache\tmp" mkdir "%~dp0cache\tmp"
set "TEMP=%~dp0cache\tmp"
set "TMP=%~dp0cache\tmp"
set "PIP_CACHE_DIR=%~dp0cache\pip"
set "HF_HOME=%~dp0cache\huggingface"
set "XDG_CACHE_HOME=%~dp0cache"

rem --- FFmpeg: pakai yang terpasang, kalau tidak ada unduh otomatis ke folder ini ---
if exist "%~dp0ffmpeg\bin\ffmpeg.exe" set "PATH=%~dp0ffmpeg\bin;%PATH%"
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo Mengunduh FFmpeg, mohon tunggu...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip -OutFile ffmpeg.zip; Expand-Archive ffmpeg.zip -DestinationPath ffmpeg_tmp -Force; $d=Get-ChildItem ffmpeg_tmp -Directory | Select-Object -First 1; Move-Item $d.FullName ffmpeg; Remove-Item ffmpeg_tmp,ffmpeg.zip -Recurse -Force"
  set "PATH=%~dp0ffmpeg\bin;%PATH%"
)
where ffmpeg >nul 2>nul || (echo GAGAL menyiapkan FFmpeg. & pause & exit /b)

set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" python -m venv venv
"%PY%" -m pip --version >nul 2>nul || "%PY%" -m ensurepip --upgrade
"%PY%" -m pip --version >nul 2>nul || (
  echo venv rusak, membuat ulang...
  rmdir /s /q venv
  python -m venv venv
  "%PY%" -m ensurepip --upgrade
)
"%PY%" -m pip install -q --upgrade pip
"%PY%" -m pip install -r requirements.txt || (echo GAGAL install paket. Kirim screenshot error di atas. & pause & exit /b)
start http://127.0.0.1:5000
"%PY%" app.py
pause
