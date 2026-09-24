@echo off
REM Build InbetComboTicketBot.exe from dashboard.py (single-file, no console window).
REM Run once from the project folder. Output: dist\InbetComboTicketBot.exe
REM
REM First time only:  pip install pyinstaller pillow qrcode openpyxl requests

pyinstaller --noconfirm --clean --onefile --windowed ^
  --name InbetComboTicketBot ^
  --collect-submodules qrcode ^
  --hidden-import PIL._tkinter_finder ^
  dashboard.py

echo.
echo Done. The exe is in: dist\InbetComboTicketBot.exe
echo Put it in a folder of its own — it writes logs\, qr_codes\, tickets_log.json next to itself.
pause
