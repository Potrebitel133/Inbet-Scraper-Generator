"""
8888 Match Scraper window (Tkinter): same look and workflow as the Inbet scraper.

Choose the tournament links file and the output file, press the button, and the
odds for football, basketball and tennis are written in the Formatted Matches
layout. On first start a sample links file is created in data/.

    python scraper_8888_ui.py
    Scraper8888.exe --selftest      (used by the Windows build)
"""

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

SRC_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC_DIR))

from run_scraper_8888 import (  # noqa: E402
    read_tournament_urls,
    scrape_all,
    validate_input_file,
    write_matches,
    write_sample_links,
)
import scraper_8888  # noqa: E402
from scraper_8888 import MAX_TOURNAMENT_WAIT_SECONDS, MIN_TOURNAMENT_WAIT_SECONDS  # noqa: E402


def app_dir():
    """Next to the .exe when frozen, else this folder."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return SRC_DIR


HERE = app_dir()
DATA_DIR = HERE / "data"
DEFAULT_INPUT = DATA_DIR / "Tournament_Links_8888.xlsx"
scraper_8888.DEBUG_DIR = str(DATA_DIR / "debug")   # site data of tournaments with skipped matches

BG = "#f4f6f8"
CARD = "#ffffff"
INK = "#1f2933"
MUTE = "#7b8794"
BLUE = "#1565c0"
CONSOLE_BG = "#0f1115"
CONSOLE_FG = "#d7dde3"


def default_output_path():
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    return str(DATA_DIR / f"Scraped_Matches_8888_{stamp}.xlsx")


class ScraperUI:
    def __init__(self, root):
        self.root = root
        root.title("8888 Match Scraper")
        root.geometry("780x560")
        root.configure(bg=BG)

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        created_sample = False
        if not DEFAULT_INPUT.exists():
            write_sample_links(DEFAULT_INPUT)
            created_sample = True

        self.input_var = tk.StringVar(value=str(DEFAULT_INPUT))
        self.output_var = tk.StringVar(value=default_output_path())
        self.tourney_wait_min_var = tk.StringVar(value=str(MIN_TOURNAMENT_WAIT_SECONDS))
        self.tourney_wait_max_var = tk.StringVar(value=str(MAX_TOURNAMENT_WAIT_SECONDS))
        self.worker = None
        self._events = queue.Queue()   # worker thread -> window, read every 100 ms

        self._build_header()
        self._build_file_row("Tournament links file:", self.input_var, self._choose_input)
        self._build_file_row("Output file:", self.output_var, self._choose_output, show_open_folder=True)
        self._build_wait_row()
        self._build_action_row()
        self._build_console()
        if created_sample:
            self._log(f"Created a sample links file: {DEFAULT_INPUT}\n"
                      "Column B holds one 8888 tournament link (or tournament number) per row.")
        self.root.after(100, self._drain)

    # --- layout ---------------------------------------------------------------------
    def _build_header(self):
        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", padx=18, pady=(16, 6))
        tk.Label(head, text="8888 Match Scraper", bg=BG, fg=INK,
                 font=("Segoe UI", 16, "bold")).pack(anchor="w")
        tk.Label(head, text="Football, basketball and tennis odds for the tournaments in your links file.",
                 bg=BG, fg=MUTE, font=("Segoe UI", 10)).pack(anchor="w", pady=(2, 0))

    def _build_file_row(self, label, var, browse_cmd, show_open_folder=False):
        row = tk.Frame(self.root, bg=BG)
        row.pack(fill="x", padx=18, pady=6)
        tk.Label(row, text=label, bg=BG, fg=INK, font=("Segoe UI", 10),
                 width=20, anchor="w").pack(side="left")
        tk.Entry(row, textvariable=var, font=("Segoe UI", 10), relief="flat",
                 highlightthickness=1, highlightbackground="#d2d9e0").pack(
            side="left", fill="x", expand=True, padx=(0, 8))
        self._btn(row, "Browse...", browse_cmd).pack(side="left")
        if show_open_folder:
            self._btn(row, "Open Folder", self._open_output_folder).pack(side="left", padx=(8, 0))

    def _build_wait_row(self):
        row = tk.Frame(self.root, bg=BG)
        row.pack(fill="x", padx=18, pady=6)
        tk.Label(row, text="Wait between (sec):", bg=BG, fg=INK, font=("Segoe UI", 10),
                 width=20, anchor="w").pack(side="left")
        tk.Label(row, text="Tournaments:", bg=BG, fg=MUTE, font=("Segoe UI", 10)).pack(side="left", padx=(0, 4))
        for i, var in enumerate((self.tourney_wait_min_var, self.tourney_wait_max_var)):
            if i:
                tk.Label(row, text="-", bg=BG, fg=MUTE, font=("Segoe UI", 10)).pack(side="left", padx=2)
            tk.Entry(row, textvariable=var, width=4, justify="center", font=("Segoe UI", 10),
                     relief="flat", highlightthickness=1, highlightbackground="#d2d9e0").pack(side="left")

    def _build_action_row(self):
        row = tk.Frame(self.root, bg=BG)
        row.pack(fill="x", padx=18, pady=(14, 6))
        self.btn_run = self._btn(row, "Get All Match Data", self._run, bg=BLUE, fg="white", big=True)
        self.btn_run.pack(side="left")
        self.status_var = tk.StringVar(value="Idle")
        tk.Label(row, textvariable=self.status_var, bg=BG, fg=MUTE,
                 font=("Segoe UI", 10)).pack(side="left", padx=(12, 0))

    def _build_console(self):
        frame = tk.Frame(self.root, bg=BG)
        frame.pack(fill="both", expand=True, padx=18, pady=(10, 16))
        tk.Label(frame, text="ACTIVITY", bg=BG, fg=MUTE,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 4))
        console_frame = tk.Frame(frame, bg=CONSOLE_BG)
        console_frame.pack(fill="both", expand=True)
        self.console = tk.Text(console_frame, bg=CONSOLE_BG, fg=CONSOLE_FG, relief="flat",
                               font=("Consolas", 9), wrap="word", state="disabled",
                               insertbackground=CONSOLE_FG)
        scrollbar = tk.Scrollbar(console_frame, command=self.console.yview)
        self.console.configure(yscrollcommand=scrollbar.set)
        self.console.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _btn(self, parent, text, cmd, bg=CARD, fg=INK, big=False):
        return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg, relief="flat",
                         activebackground=bg, activeforeground=fg, cursor="hand2",
                         font=("Segoe UI", 11 if big else 10, "bold"),
                         padx=14, pady=8 if big else 5, bd=0,
                         highlightthickness=1, highlightbackground="#d2d9e0")

    # --- actions ---------------------------------------------------------------------
    def _choose_input(self):
        path = filedialog.askopenfilename(title="Choose tournament links file",
                                          initialdir=str(DATA_DIR), filetypes=[("Excel files", "*.xlsx")])
        if path:
            self.input_var.set(path)

    def _choose_output(self):
        path = filedialog.asksaveasfilename(title="Choose output file", initialdir=str(DATA_DIR),
                                            initialfile=os.path.basename(self.output_var.get()),
                                            defaultextension=".xlsx", filetypes=[("Excel files", "*.xlsx")])
        if path:
            self.output_var.set(path)

    def _open_output_folder(self):
        folder = os.path.dirname(self.output_var.get()) or str(DATA_DIR)
        os.makedirs(folder, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(folder)
        else:
            subprocess.run(["xdg-open", folder])

    def _log(self, text):
        self._events.put(("log", text))

    def _set_running(self, running, text="Idle"):
        self._events.put(("state", (running, text)))

    def _drain(self):
        """Apply the worker's messages on the window's own thread (Tk is not thread-safe)."""
        try:
            while True:
                kind, value = self._events.get_nowait()
                if kind == "log":
                    self.console.config(state="normal")
                    self.console.insert("end", value + "\n")
                    self.console.see("end")
                    self.console.config(state="disabled")
                elif kind == "state":
                    running, text = value
                    self.btn_run.config(state="disabled" if running else "normal")
                    self.status_var.set(text)
                elif kind == "output":
                    self.output_var.set(value)
        except queue.Empty:
            pass
        self.root.after(100, self._drain)

    def _run(self):
        if self.worker and self.worker.is_alive():
            return
        input_path = self.input_var.get().strip()
        output_path = self.output_var.get().strip()
        if not input_path or not output_path:
            messagebox.showerror("Missing file", "Choose a tournament links file and an output file first.")
            return
        errors = validate_input_file(input_path)
        if errors:
            messagebox.showerror("Tournament links file has errors",
                                 "Fix the following before fetching data:\n\n" +
                                 "\n".join(f"  • {e}" for e in errors))
            return
        try:
            lo = float(self.tourney_wait_min_var.get().strip())
            hi = float(self.tourney_wait_max_var.get().strip())
            if lo < 0 or hi < lo:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid wait time", "Tournaments wait must be two numbers, 0 <= min <= max.")
            return
        self.console.config(state="normal")
        self.console.delete("1.0", "end")
        self.console.config(state="disabled")
        self._set_running(True, "Running...")
        self.worker = threading.Thread(target=self._run_worker, args=(input_path, output_path, (lo, hi)),
                                       daemon=True)
        self.worker.start()

    def _run_worker(self, input_path, output_path, tourney_wait):
        try:
            rows = scrape_all(read_tournament_urls(input_path), tourney_wait, log=self._log)
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            write_matches(rows, output_path)
        except Exception as e:  # noqa: BLE001 - shown in the activity box
            self._log(f"! {e}")
            self._set_running(False, "Error")
            return
        self._log(f"\nWrote {len(rows)} match(es) -> {output_path}")
        self._set_running(False, "Done")
        self._events.put(("output", default_output_path()))   # next run writes a new file


def _selftest():
    """Open the window, check parsing on a built-in sample, close; result in selftest_8888.txt."""
    try:
        from scraper_8888 import day_and_time, parse_tournament_url
        assert day_and_time("2026-11-08T14:00:00Z") == ("Sunday, 08.11.2026", "16:00")
        assert parse_tournament_url("2936") == ("id", 2936)
        root = tk.Tk()
        ScraperUI(root)
        root.update()
        root.destroy()
        from browser_session import BrowserSession
        browser = BrowserSession("about:blank", "-", executable=scraper_8888.BROWSER_EXECUTABLE)
        try:
            browser.launch_only()                # the bundled driver can start this PC's browser
            name = browser.browser_name
        finally:
            browser.close()
        result, code = f"selftest ok (browser: {name})", 0
    except Exception as e:  # noqa: BLE001
        result, code = f"selftest FAILED: {e!r}", 1
    with open(HERE / "selftest_8888.txt", "w", encoding="utf-8") as f:
        f.write(result + "\n")
    sys.exit(code)


def main():
    if "--selftest" in sys.argv:
        _selftest()
    root = tk.Tk()
    ScraperUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
