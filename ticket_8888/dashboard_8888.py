"""
8888 Ticket Generator window: the Inbet robot's window (dashboard.py) with the
8888 backend (bot_8888). Same workflow: choose the workbook, pick the matches per
ticket and the stake rule, "Check only" first if you like, then "Generate".
QR codes, PDF export, logs, Stop/Resume and never-twice work as on Inbet.

    python ticket_8888/dashboard_8888.py
    Ticket8888.exe --selftest        (used by the Windows build)
"""

import sys
import tkinter as tk
from pathlib import Path

HERE = Path(__file__).resolve().parent
for folder in (HERE, HERE.parent, HERE.parent / "scraper_8888"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

import bot_8888  # noqa: E402

bot_8888.use_8888_files()       # before the window reads its file names
import dashboard  # noqa: E402

dashboard.backend = bot_8888
dashboard.APP_TITLE = "8888 Ticket Generator"
dashboard.WINDOW_WIDTH = 1640
dashboard.CONFIRM_LIVE = "This will create real 8888 tickets (QR codes) from:"
dashboard.SELFTEST_FILE = "selftest_ticket8888.txt"


def main():
    if "--selftest" in sys.argv:
        dashboard._selftest()
    root = tk.Tk()
    dashboard.Dashboard(root)
    root.mainloop()


if __name__ == "__main__":
    main()
