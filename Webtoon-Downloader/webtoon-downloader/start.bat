@echo off
echo Starting Webtoon Downloader...
cd /d "C:\Users\hp\Documents\Webtoon-Downloader\webtoon-downloader\backend"
call "C:\Users\hp\Documents\Webtoon-Downloader\.venv\Scripts\activate.bat"
start "" python app.py
timeout /t 4 /nobreak
start "" "C:\Users\hp\Documents\Webtoon-Downloader\webtoon-downloader\frontend\index.html"
pause