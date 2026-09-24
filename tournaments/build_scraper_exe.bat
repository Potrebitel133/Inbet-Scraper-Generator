@echo off
REM Build InbetScraperBot.exe from scraper_ui.py (single-file, no console window).
REM Run once from this folder. Output: dist\InbetScraperBot.exe
REM
REM First time only:  pip install pyinstaller openpyxl requests

pyinstaller --noconfirm --clean --onefile --windowed ^
  --name InbetScraperBot ^
  --paths .. ^
  --hidden-import config ^
  --hidden-import common ^
  scraper_ui.py

echo.
echo Done. The exe is in: dist\InbetScraperBot.exe
echo Put it in a folder of its own with a data\ subfolder containing Tournament_Link.xlsx.
pause
