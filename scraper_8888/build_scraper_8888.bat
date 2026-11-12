@echo off
REM Build Scraper8888.exe (single file, no console window). Run from the project folder.
REM First time only:  pip install pyinstaller openpyxl requests playwright
pyinstaller --noconfirm --clean --onefile --windowed --name Scraper8888 --paths scraper_8888 --collect-all playwright scraper_8888\scraper_8888_ui.py
echo.
echo Done. The exe is in: dist\Scraper8888.exe
pause
