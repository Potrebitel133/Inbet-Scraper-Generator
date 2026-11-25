"""
efbet Ticket Generator window: the 8888 robot's window (dashboard.py) with the
efbet backend (bot_efbet). Same workflow: choose the workbook, pick the ticket
type (the robot takes that type's sheets, 5-1, 5-2, ...), the stake rule,
"Check only" first if you like, then "Generate". Barcodes, PDF export, logs,
Stop/Resume and never-twice work as on 8888.

    python ticket_efbet/dashboard_efbet.py
    TicketEfbet.exe --selftest        (used by the Windows build)
"""

import sys
import tkinter as tk
from pathlib import Path

HERE = Path(__file__).resolve().parent
for folder in (HERE, HERE.parent, HERE.parent / "scraper_8888", HERE.parent / "ticket_8888"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

import bot_efbet  # noqa: E402

bot_efbet.use_efbet_files()     # before the window reads its file names
import dashboard  # noqa: E402

dashboard.backend = bot_efbet
dashboard.APP_TITLE = "efbet Ticket Generator"
dashboard.WINDOW_WIDTH = 1450
dashboard.SHOW_REFRESH = False
dashboard.CONFIRM_LIVE = "This will create real efbet tickets (barcodes) from:"
dashboard.SELFTEST_FILE = "selftest_ticketefbet.txt"
dashboard.CODE_NAME = "Barcode"
dashboard.WIDE_CODE = True


def main():
    if "--selftest" in sys.argv:
        dashboard._selftest()
    root = tk.Tk()
    dashboard.Dashboard(root)
    root.mainloop()


if __name__ == "__main__":
    main()
