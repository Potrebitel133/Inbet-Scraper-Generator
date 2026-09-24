"""
Match Scraper UI (Tkinter, stdlib only).

Scrapes Early Payout odds for the tournaments listed in a links workbook and
writes them to an output workbook in the "Formatted Matches" layout used by
the combo-ticket bot's Excel input.

    python scraper_ui.py
"""

import os
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

SRC_DIR = Path(__file__).resolve().parent          # tournaments/ (or _MEIPASS/tournaments when frozen)
sys.path.insert(0, str(SRC_DIR))
sys.path.insert(0, str(SRC_DIR.parent))  # project root: common.py, config.py

from run_scraper import read_tournament_urls, validate_input_file, write_matches
from scraper import (
    MAX_MATCH_WAIT_SECONDS,
    MAX_TOURNAMENT_WAIT_SECONDS,
    MIN_MATCH_WAIT_SECONDS,
    MIN_TOURNAMENT_WAIT_SECONDS,
    _wait,
    scrape_tournament,
)


def app_dir():
    """Directory to read/write data from: next to the .exe when frozen, else here."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return SRC_DIR


HERE = app_dir()
DATA_DIR = HERE / "data"
DEFAULT_INPUT = DATA_DIR / "Tournament_Link.xlsx"

# palette (matches dashboard.py)
BG = "#f4f6f8"
CARD = "#ffffff"
INK = "#1f2933"
MUTE = "#7b8794"
GREEN = "#2e7d32"
BLUE = "#1565c0"
RED = "#c0392b"
CONSOLE_BG = "#0f1115"
CONSOLE_FG = "#d7dde3"


def default_output_path():
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    return str(DATA_DIR / f"Scraped_Matches_{stamp}.xlsx")


class ScraperUI:
    def __init__(self, root):
        self.root = root
        root.title("Inbet Match Scraper — Soccer")
        root.geometry("760x560")
        root.configure(bg=BG)

        DATA_DIR.mkdir(parents=True, exist_ok=True)

        self.input_var = tk.StringVar(value=str(DEFAULT_INPUT if DEFAULT_INPUT.exists() else ""))
        self.output_var = tk.StringVar(value=default_output_path())
        self.match_wait_min_var = tk.StringVar(value=str(MIN_MATCH_WAIT_SECONDS))
        self.match_wait_max_var = tk.StringVar(value=str(MAX_MATCH_WAIT_SECONDS))
        self.tourney_wait_min_var = tk.StringVar(value=str(MIN_TOURNAMENT_WAIT_SECONDS))
        self.tourney_wait_max_var = tk.StringVar(value=str(MAX_TOURNAMENT_WAIT_SECONDS))
        self.worker = None

        self._build_header()
        self._build_file_row("Tournament links file:", self.input_var, self._choose_input)
        self._build_file_row("Output file:", self.output_var, self._choose_output, show_open_folder=True)
        self._build_wait_row()
        self._build_action_row()
        self._build_console()

    # --- layout ----------------------------------------------------------------
    def _build_header(self):
        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", padx=18, pady=(16, 6))
        tk.Label(head, text="Inbet Match Scraper", bg=BG, fg=INK,
                 font=("Segoe UI", 16, "bold")).pack(anchor="w")
        tk.Label(head, text="Scrapes Early Payout odds for the tournaments you point it at.",
                 bg=BG, fg=MUTE, font=("Segoe UI", 10)).pack(anchor="w", pady=(2, 0))

    def _build_file_row(self, label, var, browse_cmd, show_open_folder=False):
        row = tk.Frame(self.root, bg=BG)
        row.pack(fill="x", padx=18, pady=6)
        tk.Label(row, text=label, bg=BG, fg=INK, font=("Segoe UI", 10),
                 width=20, anchor="w").pack(side="left")
        entry = tk.Entry(row, textvariable=var, font=("Segoe UI", 10), relief="flat",
                          highlightthickness=1, highlightbackground="#d2d9e0")
        entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._btn(row, "Browse...", browse_cmd).pack(side="left")
        if show_open_folder:
            self._btn(row, "Open Folder", self._open_output_folder).pack(side="left", padx=(8, 0))

    def _build_wait_row(self):
        row = tk.Frame(self.root, bg=BG)
        row.pack(fill="x", padx=18, pady=6)
        tk.Label(row, text="Wait between (sec):", bg=BG, fg=INK, font=("Segoe UI", 10),
                 width=20, anchor="w").pack(side="left")
        self._wait_field(row, "Matches", self.match_wait_min_var, self.match_wait_max_var)
        self._wait_field(row, "Tournaments", self.tourney_wait_min_var, self.tourney_wait_max_var)

    def _wait_field(self, parent, label, min_var, max_var):
        tk.Label(parent, text=f"{label}:", bg=BG, fg=MUTE, font=("Segoe UI", 10)).pack(side="left", padx=(0, 4))
        tk.Entry(parent, textvariable=min_var, width=4, justify="center", font=("Segoe UI", 10),
                 relief="flat", highlightthickness=1, highlightbackground="#d2d9e0").pack(side="left")
        tk.Label(parent, text="-", bg=BG, fg=MUTE, font=("Segoe UI", 10)).pack(side="left", padx=2)
        tk.Entry(parent, textvariable=max_var, width=4, justify="center", font=("Segoe UI", 10),
                 relief="flat", highlightthickness=1, highlightbackground="#d2d9e0").pack(side="left", padx=(0, 16))

    def _build_action_row(self):
        row = tk.Frame(self.root, bg=BG)
        row.pack(fill="x", padx=18, pady=(14, 6))
        self.btn_run = self._btn(row, "Get All Match Data", self._run,
                                  bg=BLUE, fg="white", big=True)
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
        return tk.Button(
            parent, text=text, command=cmd, bg=bg, fg=fg, relief="flat",
            activebackground=bg, activeforeground=fg, cursor="hand2",
            font=("Segoe UI", 11 if big else 10, "bold"),
            padx=14, pady=8 if big else 5, bd=0,
            highlightthickness=1, highlightbackground="#d2d9e0")

    # --- actions -----------------------------------------------------------------
    def _choose_input(self):
        path = filedialog.askopenfilename(
            title="Choose tournament links file",
            initialdir=str(DATA_DIR), filetypes=[("Excel files", "*.xlsx")])
        if path:
            self.input_var.set(path)

    def _choose_output(self):
        path = filedialog.asksaveasfilename(
            title="Choose output file", initialdir=str(DATA_DIR),
            initialfile=os.path.basename(self.output_var.get()),
            defaultextension=".xlsx", filetypes=[("Excel files", "*.xlsx")])
        if path:
            self.output_var.set(path)

    def _open_output_folder(self):
        folder = os.path.dirname(self.output_var.get()) or str(DATA_DIR)
        if not os.path.isdir(folder):
            os.makedirs(folder, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(folder)
        else:
            subprocess.run(["xdg-open", folder])

    def _log(self, text):
        def write():
            self.console.config(state="normal")
            self.console.insert("end", text + "\n")
            self.console.see("end")
            self.console.config(state="disabled")
        self.root.after(0, write)

    def _set_running(self, running, text="Idle"):
        def update():
            self.btn_run.config(state="disabled" if running else "normal")
            self.status_var.set(text)
        self.root.after(0, update)

    def _wait_range(self, min_var, max_var, label):
        """Parse+validate a min/max wait field pair, or raise ValueError."""
        lo, hi = float(min_var.get().strip()), float(max_var.get().strip())
        if lo < 0 or hi < lo:
            raise ValueError(f"{label} wait range must be 0 <= min <= max")
        return lo, hi

    def _run(self):
        if self.worker and self.worker.is_alive():
            return
        input_path = self.input_var.get().strip()
        output_path = self.output_var.get().strip()
        if not input_path:
            messagebox.showerror("No links file", "Choose a tournament links file first.")
            return
        if not output_path:
            messagebox.showerror("No output file", "Choose an output file first.")
            return
        # Validate the workbook + every URL in it BEFORE any scraping starts,
        # so problems show up as one clear popup instead of failing mid-run.
        validation_errors = validate_input_file(input_path)
        if validation_errors:
            lines = ["Fix the following before fetching data:\n"]
            lines.extend(f"  • {error}" for error in validation_errors)
            messagebox.showerror("Tournament links file has errors", "\n".join(lines))
            return
        try:
            match_wait = self._wait_range(self.match_wait_min_var, self.match_wait_max_var, "Matches")
            tourney_wait = self._wait_range(self.tourney_wait_min_var, self.tourney_wait_max_var, "Tournaments")
        except ValueError as e:
            messagebox.showerror("Invalid wait time", str(e))
            return
        self.console.config(state="normal")
        self.console.delete("1.0", "end")
        self.console.config(state="disabled")
        self._set_running(True, "Running...")
        self.worker = threading.Thread(
            target=self._run_worker, args=(input_path, output_path, match_wait, tourney_wait), daemon=True)
        self.worker.start()

    def _run_worker(self, input_path, output_path, match_wait, tourney_wait):
        try:
            urls = read_tournament_urls(input_path)
        except Exception as e:
            self._log(f"! Could not read links file: {e}")
            self._set_running(False, "Error")
            return
        if not urls:
            self._log(f"! No tournament URLs found in {input_path}")
            self._set_running(False, "Error")
            return

        all_rows = []
        seen_match_ids = set()
        for i, url in enumerate(urls):
            if i > 0:
                _wait(*tourney_wait, lambda s: self._log(f"  (waiting {s:.1f}s before next tournament...)"))
            self._log(f"Scraping: {url}")
            rows, errors = scrape_tournament(
                url, *match_wait, on_wait=lambda s: self._log(f"  (waiting {s:.1f}s before next match...)"))
            for err in errors:
                self._log(f"  ! {err}")
            added = 0
            for row in rows:
                key = row["Match ID"] or row["Match"]
                if key in seen_match_ids:
                    continue
                seen_match_ids.add(key)
                all_rows.append(row)
                added += 1
            self._log(f"  -> {added} match(es) added ({len(rows) - added} duplicate(s) skipped)")

        try:
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            write_matches(all_rows, output_path)
        except Exception as e:
            self._log(f"! Could not write output file: {e}")
            self._set_running(False, "Error")
            return

        self._log(f"\nWrote {len(all_rows)} match(es) -> {output_path}")
        self._set_running(False, "Done")


def main():
    root = tk.Tk()
    ScraperUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
