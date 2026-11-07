"""
Client dashboard (Tkinter, stdlib only) — a VIEWER over what the bot writes.

It never re-implements bot logic; it reads the artifacts the bot produces and can
launch a run:

    tickets_log.json   -> ticket list + per-ticket detail (voucher/stake/odds/QR)
    qr_codes/**/*.png   -> QR preview on row-select
    logs/bot.jsonl      -> validation verdicts + activity console

Run it on the same machine as the bot:

    python dashboard.py

Before a run the client picks the ticket type (5..10 matches per ticket) and,
per type, either a fixed stake or a stake formula (e.g. 20000/(I12-1)). Both are
saved in settings.json next to the app and come back on the next start.

"Generate" launches `bot.py` (after a confirm dialog that lists every ticket's
stake) against the chosen workbook. Live output streams into the Activity console, and everything
auto-refreshes from the files as the run writes them.
"""

import json
import os
import sys
import threading
import tkinter as tk
from collections import Counter
from tkinter import filedialog, messagebox, ttk

import bot
import config
import excel_reader
import logsetup
import stake_rules
import ticket_ledger


def app_dir():
    """Directory to read/write data from: next to the .exe when frozen, else here."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _file_size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


HERE = app_dir()
TICKETS = os.path.join(HERE, config.TICKETS_LOG)
LOG_FILE = os.path.join(HERE, "logs", "bot.jsonl")
SETTINGS = os.path.join(HERE, config.SETTINGS_FILE)
PREVIEW_LINES = 25   # max per-ticket stake lines in the confirm dialog

# palette
BG = "#f4f6f8"
CARD = "#ffffff"
INK = "#1f2933"
MUTE = "#7b8794"
GREEN = "#2e7d32"
BLUE = "#1565c0"
AMBER = "#b8860b"
RED = "#c0392b"
LEVEL_COLOR = {"WARNING": AMBER, "ERROR": RED, "CRITICAL": RED}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def fmt_ts(iso):
    """'2026-07-21T16:16:59Z' -> '21 Jul 2026  16:16:59'."""
    try:
        date, tm = iso.replace("Z", "").split("T")
        y, m, d = date.split("-")
        return f"{int(d):02d} {MONTHS[int(m) - 1]} {y}  {tm}"
    except (ValueError, IndexError, TypeError):
        return iso or "-"


def _ticket_name(t):
    """'5-22' for tickets made since ticket types were added, the bare sheet before."""
    return t.get("label") or str(t.get("sheet"))


def _read_tickets():
    try:
        with open(TICKETS, encoding="utf-8") as f:
            return list(reversed(json.load(f)))  # newest first
    except (OSError, ValueError):
        return []


def _read_log_events():
    events = []
    try:
        with open(LOG_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        pass
    except OSError:
        pass
    return events


def _summarize_last_run(events):
    """From the log, the most recent run's outcome: created/failed sheets + why.

    Reads the final 'summary' event (done/skipped/errored sheet lists) and
    aggregates the per-pick SKIP reasons from that run's slice, along with the
    specific Match ID + Tournament for each unresolved match (not just a count)
    so the failure popup can name exactly what wasn't found.
    """
    idx = next((i for i in range(len(events) - 1, -1, -1)
                if events[i].get("msg", "").startswith("summary") and "done" in events[i]), None)
    if idx is None:
        return None
    ev = events[idx]
    start = next((i for i in range(idx, -1, -1)
                  if events[i].get("msg", "").startswith("using workbook")), 0)
    reasons = Counter()
    unresolved = []
    for e in events[start:idx + 1]:
        if e.get("reason"):
            reasons[e["reason"]] += 1
            unresolved.append({
                "sheet": e.get("sheet"),
                "match_id": e.get("match_id"),
                "match": e.get("match"),
                "tournament": e.get("tournament"),
                "reason": e.get("reason"),
            })
    done = ev.get("done") or []
    failed = (ev.get("skipped") or []) + (ev.get("errored") or [])
    return {"created": done, "failed": failed, "reasons": reasons, "unresolved": unresolved,
            "already": ev.get("already_done") or []}


class Dashboard:
    def __init__(self, root):
        self.root = root
        root.title("Combo-Ticket Bot — Dashboard")
        # The toolbar packs 7 buttons left + a "Run:" label and dropdown right,
        # all in one row with no wrapping — the buttons alone need ~1090px, and
        # the Run selector another ~230px beyond that. Below ~1340px there's no
        # room left for the Run selector at all and it's clipped to 0 width
        # (invisible), not shrunk. 1450 leaves real margin; never let the window
        # go narrower than that either.
        root.geometry("1450x760")
        root.minsize(1450, 640)
        root.configure(bg=BG)

        os.chdir(HERE)              # bot writes logs/qr/tickets next to the app
        logsetup.setup_logging()    # in-process bot logs go to logs/bot.jsonl

        self.worker = None
        self._worker_error = None
        self._run_done_handled = True
        self._stop_event = threading.Event()
        self._log_pos = _file_size(LOG_FILE)
        self._mtimes = {}
        self._qr_img = None
        self._last_latest = None
        self._run_labels = {}
        self._ticket_rows = []
        self._current_run_view = False
        self._current_run_id = None
        self._previous_latest_run_id = None
        self._session_has_run = False       # True once a run has actually started this session
        self._user_selected_run = False     # True once the user manually picks from the Run dropdown
        self.excel_path = os.path.join(HERE, config.EXCEL_FILE)
        # Resume state — populated when a run is stopped before all sheets finish
        self._pending_sheets = []      # sheets not yet completed in the last run
        self._resume_excel = None      # workbook used in that run
        self._current_run_done_sheets = []  # filled by on_ticket_done callback

        self.settings = stake_rules.load_settings(SETTINGS)
        self._init_style()
        self._build_header()
        self._build_ticket_setup()
        self._build_toolbar()
        self._build_banner()
        self._build_stats()
        self._build_main()
        self._build_console()

        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.run_var.trace_add("write", lambda *_: self._reload_tickets())
        self._refresh(force=True)
        self.root.after(1000, self._tick)

    # --- styling --------------------------------------------------------------
    def _init_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=BG, foreground=INK, font=("Segoe UI", 10))
        style.configure("Card.TFrame", background=CARD)
        style.configure("TLabelframe", background=CARD)
        style.configure("TLabelframe.Label", background=BG, foreground=MUTE)
        style.configure("Treeview", rowheight=26, fieldbackground=CARD, background=CARD)
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        style.configure("Match.Treeview", rowheight=30, fieldbackground=CARD, background=CARD)
        style.map("Treeview", background=[("selected", BLUE)], foreground=[("selected", "white")])

    def _btn(self, parent, text, cmd, bg=CARD, fg=INK, big=False):
        return tk.Button(
            parent, text=text, command=cmd, bg=bg, fg=fg, relief="flat",
            activebackground=bg, activeforeground=fg, cursor="hand2",
            font=("Segoe UI", 11 if big else 10, "bold"),
            width=16, padx=6, pady=8 if big else 6, bd=0,
            highlightthickness=1, highlightbackground="#d2d9e0")

    # --- header (workbook + status pill) --------------------------------------
    def _build_header(self):
        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", padx=14, pady=(12, 4))
        self.wb_label = tk.Label(head, text="", bg=BG, fg=INK, font=("Segoe UI", 12, "bold"))
        self.wb_label.pack(side="left")
        self.status = tk.Label(head, text="  🟢  Ready  ", bg="#e3f4e6", fg=GREEN,
                               font=("Segoe UI", 11, "bold"), padx=10, pady=4)
        self.status.pack(side="right")

        # Seconds between tickets — min/max editable before each run
        self.max_interval_var = tk.StringVar(value=str(config.MAX_INTERVAL_SECONDS))
        tk.Entry(head, textvariable=self.max_interval_var, width=5, justify="right",
                 font=("Segoe UI", 11, "bold"), relief="flat",
                 highlightthickness=1, highlightbackground="#d2d9e0").pack(side="right", padx=(4, 0))
        tk.Label(head, text="to", bg=BG, fg=MUTE,
                 font=("Segoe UI", 10)).pack(side="right", padx=4)
        self.min_interval_var = tk.StringVar(value=str(config.MIN_INTERVAL_SECONDS))
        tk.Entry(head, textvariable=self.min_interval_var, width=5, justify="right",
                 font=("Segoe UI", 11, "bold"), relief="flat",
                 highlightthickness=1, highlightbackground="#d2d9e0").pack(side="right", padx=(4, 0))
        tk.Label(head, text="Seconds between tickets:", bg=BG, fg=MUTE,
                 font=("Segoe UI", 10)).pack(side="right", padx=(16, 4))
        self._set_wb_label()

    # --- ticket type + stake rule (one row under the header) -----------------
    def _build_ticket_setup(self):
        row = tk.Frame(self.root, bg=CARD, highlightthickness=1, highlightbackground="#e1e6eb")
        row.pack(fill="x", padx=14, pady=(4, 4))
        lbl = dict(bg=CARD, fg=MUTE, font=("Segoe UI", 10))
        entry = dict(font=("Segoe UI", 11, "bold"), relief="flat",
                     highlightthickness=1, highlightbackground="#d2d9e0")

        tk.Label(row, text="Matches per ticket:", **lbl).pack(side="left", padx=(12, 6), pady=8)
        self.type_var = tk.IntVar(value=self.settings["match_type"])
        self._shown_type = self.type_var.get()
        self.type_buttons = []
        for n in config.MATCH_TYPES:
            b = tk.Radiobutton(row, text=str(n), value=n, variable=self.type_var,
                               command=self._type_changed, bg=CARD, fg=INK,
                               activebackground=CARD, selectcolor=CARD,
                               font=("Segoe UI", 11, "bold"), cursor="hand2")
            b.pack(side="left", padx=(0, 4))
            self.type_buttons.append(b)

        rule = self.settings["rules"][str(self._shown_type)]
        self.fixed_on = tk.BooleanVar(value=rule["mode"] == "fixed")
        self.fixed_cb = tk.Checkbutton(row, text="Fixed stake", variable=self.fixed_on,
                                       command=self._stake_mode_changed, bg=CARD, fg=INK,
                                       activebackground=CARD, selectcolor=CARD,
                                       font=("Segoe UI", 10, "bold"), cursor="hand2")
        self.fixed_cb.pack(side="left", padx=(28, 4))
        self.fixed_var = tk.StringVar(value=str(rule["fixed"]))
        self.fixed_entry = tk.Entry(row, textvariable=self.fixed_var, width=8, justify="right", **entry)
        self.fixed_entry.pack(side="left")

        tk.Label(row, text="Formula:", **lbl).pack(side="left", padx=(24, 4))
        self.formula_var = tk.StringVar(value=rule["formula"])
        self.formula_entry = tk.Entry(row, textvariable=self.formula_var, width=20, **entry)
        self.formula_entry.pack(side="left")
        self.formula_check = tk.Label(row, text="", bg=CARD, font=("Segoe UI", 10, "bold"))
        self.formula_check.pack(side="left", padx=(6, 0))
        self.formula_var.trace_add("write", lambda *_: self._check_formula())
        tk.Label(row, text="I12 = Product,  ODDS = live odds,  rounded up to 5",
                 **lbl).pack(side="left", padx=(12, 12))
        self._stake_mode_changed()

    def _type_changed(self):
        """Keep what was typed for the old type, then show the new type's own stake rule."""
        self._store_rule(self._shown_type)
        self._shown_type = self.type_var.get()
        rule = self.settings["rules"][str(self._shown_type)]
        self.fixed_on.set(rule["mode"] == "fixed")
        self.fixed_var.set(str(rule["fixed"]))
        self.formula_var.set(rule["formula"])
        self._stake_mode_changed()

    def _stake_mode_changed(self):
        fixed = self.fixed_on.get()
        self.fixed_entry.config(state="normal" if fixed else "disabled")
        self.formula_entry.config(state="disabled" if fixed else "normal")
        self._check_formula()

    def _check_formula(self):
        if self.fixed_on.get():
            self.formula_check.config(text="")
            return
        try:
            stake_rules.formula_variables(self.formula_var.get())
            self.formula_check.config(text="✓", fg=GREEN)
        except stake_rules.StakeError:
            self.formula_check.config(text="✗ not valid", fg=RED)

    def _rule_from_fields(self):
        return {"mode": "fixed" if self.fixed_on.get() else "formula",
                "fixed": self.fixed_var.get().strip(),
                "formula": self.formula_var.get().strip()}

    def _store_rule(self, match_type):
        rule = self._rule_from_fields()
        saved = self.settings["rules"][str(match_type)]
        saved["mode"] = rule["mode"]
        saved["fixed"] = rule["fixed"] or saved["fixed"]
        saved["formula"] = rule["formula"] or saved["formula"]

    def _save_settings(self):
        self._store_rule(self.type_var.get())
        self.settings["match_type"] = self.type_var.get()
        try:
            stake_rules.save_settings(SETTINGS, self.settings)
        except OSError as e:
            self._log_console(f"[could not save settings: {e}]\n")

    def _set_status(self, running, what=""):
        if running:
            self.status.config(text=f"  🟡  Running {what}…  ", bg="#fbf1d6", fg=AMBER)
        else:
            self.status.config(text="  🟢  Ready  ", bg="#e3f4e6", fg=GREEN)

    def _set_wb_label(self):
        exists = os.path.exists(self.excel_path)
        self.wb_label.config(text=f"📄  {os.path.basename(self.excel_path)}",
                             fg=INK if exists else RED)

    # --- toolbar (buttons + run selector) -------------------------------------
    def _build_toolbar(self):
        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", padx=14, pady=(4, 8))
        self._btn(bar, "📂  Choose File", self._choose_excel).pack(side="left")
        self.btn_live = self._btn(bar, "🚀  Generate", lambda: self._run(live=True),
                                  bg=GREEN, fg="white", big=True)
        self.btn_live.pack(side="left", padx=(8, 0))
        self.btn_resume = self._btn(bar, "▶  Resume", self._resume, bg=BLUE, fg="white", big=True)
        self.btn_resume.config(state="disabled")
        self.btn_resume.pack(side="left", padx=(8, 0))
        self.btn_stop = self._btn(bar, "⏹  Stop", self._stop, fg=RED)
        self.btn_stop.config(state="disabled")
        self.btn_stop.pack(side="left", padx=(8, 0))
        self._btn(bar, "🔄  Refresh", lambda: self._refresh(force=True)).pack(side="left", padx=(8, 0))
        self._btn(bar, "📋  Logs", self._open_logs).pack(side="left", padx=(8, 0))
        self._btn(bar, "📄  QR to PDF", self._export_pdf).pack(side="left", padx=(8, 0))

        self.run_var = tk.StringVar(value="All runs")
        self.run_combo = ttk.Combobox(bar, textvariable=self.run_var, width=30, state="readonly")
        self.run_combo.pack(side="right")
        self.run_combo.bind("<<ComboboxSelected>>", lambda _e: setattr(self, "_user_selected_run", True))
        tk.Label(bar, text="Run:", bg=BG, fg=MUTE).pack(side="right", padx=(0, 6))

    # --- last-run banner ------------------------------------------------------
    def _build_banner(self):
        self.banner = tk.Label(self.root, text="", bg=BG, fg=INK, anchor="w",
                               font=("Segoe UI", 10, "bold"), padx=14, pady=6)
        # packed on demand in _show_run_summary so it takes no space until a run ends

    def _show_run_summary(self):
        # Use the ticket rows as the source of truth for how many tickets were
        # created — this is accurate even for resumed sessions where two separate
        # log "summary" events exist (one per bot.run() call) but all tickets
        # share the same run_id and are already loaded in self._ticket_rows.
        created_count = len(self._ticket_rows)

        s = _summarize_last_run(_read_log_events())
        failed     = s["failed"]     if s else []
        reasons    = s["reasons"]    if s else {}
        unresolved = s["unresolved"] if s else []
        already    = s["already"]    if s else []
        why = "  •  ".join(f"{r} ({n})" for r, n in reasons.most_common(3))
        skipped_note = (f"\n\nAlready created earlier, skipped (never made twice): {len(already)}"
                        f"\n   sheets {', '.join(map(str, already))}" if already else "")

        if failed:
            self.banner.config(
                text=f"⚠  Last run: {created_count} created, {len(failed)} not created"
                     + (f"  —  {why}" if why else ""),
                bg="#fdecea", fg=RED)
            msg = f"Tickets created: {created_count}"
            msg += f"\nNot created: {len(failed)}  (sheets {', '.join(map(str, failed))})"
            if reasons:
                msg += "\n\nWhy:\n" + "\n".join(f"   • {r} — {n} match(es)"
                                                 for r, n in reasons.most_common())
            if unresolved:
                shown, extra = unresolved[:20], len(unresolved) - 20
                msg += "\n\nUnresolved matches:\n" + "\n".join(
                    f"   • Sheet {u['sheet']}: {u['match_id'] or '(no Match ID)'} "
                    f"[{u['tournament'] or '(no tournament)'}] — {u['reason']}"
                    for u in shown)
                if extra > 0:
                    msg += f"\n   … and {extra} more (see 📋 Logs for the full list)."
            else:
                msg += "\n\nOpen 📋 Logs for the full detail."
            messagebox.showwarning("Run finished — some tickets not created", msg + skipped_note)
        else:
            self.banner.config(text=f"✅  Last run: all {created_count} tickets created",
                               bg="#e3f4e6", fg=GREEN)
            messagebox.showinfo("Run finished",
                                f"All {created_count} tickets created successfully.{skipped_note}")
        self.banner.pack(fill="x", before=self._stats_row, pady=(0, 4))

    # --- stats row ------------------------------------------------------------
    def _build_stats(self):
        row = tk.Frame(self.root, bg=BG)
        row.pack(fill="x", padx=14, pady=(0, 8))
        self._stats_row = row
        self.stat = {}
        for key, title, color in [("tickets", "Tickets", INK), ("odds", "Average Odds", BLUE),
                                   ("success", "Success", GREEN), ("failed", "Failed", RED)]:
            card = tk.Frame(row, bg=CARD, highlightthickness=1, highlightbackground="#e1e6eb")
            card.pack(side="left", expand=True, fill="x", padx=(0, 10))
            tk.Label(card, text=title.upper(), bg=CARD, fg=MUTE,
                     font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=12, pady=(8, 0))
            val = tk.Label(card, text="—", bg=CARD, fg=color, font=("Segoe UI", 20, "bold"))
            val.pack(anchor="w", padx=12, pady=(0, 8))
            self.stat[key] = val

    # --- main area (tickets left, detail right) -------------------------------
    def _build_main(self):
        main = tk.Frame(self.root, bg=BG)
        main.pack(fill="both", expand=True, padx=14)

        # left: ticket list
        left = tk.Frame(main, bg=CARD, highlightthickness=1, highlightbackground="#e1e6eb")
        left.pack(side="left", fill="both", expand=True)
        tk.Label(left, text="TICKETS", bg=CARD, fg=MUTE,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=10, pady=(8, 2))
        wrap = tk.Frame(left, bg=CARD)
        wrap.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        cols = ("sheet", "voucher", "stake", "odds", "time")
        self.tickets = ttk.Treeview(wrap, columns=cols, show="headings", height=12)
        for c, t, w in [("sheet", "#", 64), ("voucher", "Voucher", 180), ("stake", "Stake", 64),
                        ("odds", "Odds", 90), ("time", "Generated", 190)]:
            self.tickets.heading(c, text=t)
            self.tickets.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tickets.yview)
        self.tickets.configure(yscrollcommand=sb.set)
        self.tickets.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tickets.bind("<<TreeviewSelect>>", self._show_ticket)

        # right: selected-ticket detail
        right = tk.Frame(main, bg=CARD, width=380, highlightthickness=1, highlightbackground="#e1e6eb")
        right.pack(side="right", fill="y", padx=(12, 0))
        right.pack_propagate(False)
        self.detail_title = tk.Label(right, text="Select a ticket", bg=CARD, fg=INK,
                                     font=("Segoe UI", 14, "bold"))
        self.detail_title.pack(anchor="w", padx=14, pady=(12, 6))

        kv = tk.Frame(right, bg=CARD)
        kv.pack(fill="x", padx=14)
        self.kv = {}
        for key, label in [("voucher", "Voucher"), ("stake", "Stake"),
                           ("odds", "Combined Odds"), ("time", "Generated")]:
            r = tk.Frame(kv, bg=CARD)
            r.pack(fill="x", pady=1)
            tk.Label(r, text=label, bg=CARD, fg=MUTE, width=13, anchor="w",
                     font=("Segoe UI", 9)).pack(side="left")
            v = tk.Label(r, text="—", bg=CARD, fg=INK, anchor="w", font=("Segoe UI", 10, "bold"))
            v.pack(side="left", fill="x")
            self.kv[key] = v

        self.qr_label = tk.Label(right, bg=CARD, text="")
        self.qr_label.pack(pady=(6, 4))
        tk.Label(right, text="MATCHES", bg=CARD, fg=MUTE,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=14)
        mwrap = tk.Frame(right, bg=CARD)
        mwrap.pack(fill="both", expand=True, padx=10, pady=(2, 10))
        self.sel = ttk.Treeview(mwrap, columns=("match", "side", "odds"),
                                show="headings", height=6, style="Match.Treeview")
        for c, t, w in [("match", "Match", 210), ("side", "Side", 55), ("odds", "Odds", 55)]:
            self.sel.heading(c, text=t)
            self.sel.column(c, width=w, anchor="w")
        msb = ttk.Scrollbar(mwrap, orient="vertical", command=self.sel.yview)
        self.sel.configure(yscrollcommand=msb.set)
        self.sel.pack(side="left", fill="both", expand=True)
        msb.pack(side="right", fill="y")

    # --- collapsible activity console -----------------------------------------
    def _build_console(self):
        self.console_open = True
        self.toggle = tk.Button(self.root, text="▼  Activity", command=self._toggle_console,
                                bg=BG, fg=INK, relief="flat", anchor="w", cursor="hand2",
                                font=("Segoe UI", 9, "bold"), bd=0)
        self.toggle.pack(fill="x", padx=14)
        self.console_frame = tk.Frame(self.root, bg=BG)
        self.console_frame.pack(fill="x", padx=14, pady=(0, 10))
        self.console = tk.Text(self.console_frame, height=5, wrap="none", bd=0,
                               bg="#1e1e1e", fg="#d4d4d4", font=("Consolas", 9))
        self.console.pack(fill="x")

    def _toggle_console(self):
        self.console_open = not self.console_open
        if self.console_open:
            self.console_frame.pack(fill="x", padx=14, pady=(0, 10))
            self.toggle.config(text="▼  Activity")
        else:
            self.console_frame.pack_forget()
            self.toggle.config(text="▲  Activity  (hidden)")

    # --- excel picker ---------------------------------------------------------
    def _choose_excel(self):
        path = filedialog.askopenfilename(
            title="Choose ticket workbook",
            filetypes=[("Excel", "*.xlsm *.xlsx"), ("All files", "*.*")])
        if path:
            self.excel_path = path
            self._set_wb_label()

    # --- export QRs to PDF ----------------------------------------------------
    def _export_pdf(self):
        """One A4 page per ticket in the current view: QR + voucher/stake/odds."""
        rows = list(self._ticket_rows)
        if not rows:
            messagebox.showinfo("Nothing to export", "No tickets in the current view.")
            return
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            messagebox.showerror("PDF export", "Pillow is required for PDF export.")
            return

        default = f"tickets_{(rows[0].get('run_id') or 'export').replace(':', '').replace('T', '_').replace('Z', '')}.pdf"
        out = filedialog.asksaveasfilename(
            title="Save tickets as PDF", defaultextension=".pdf",
            initialfile=default, filetypes=[("PDF", "*.pdf")])
        if not out:
            return

        A4 = (1240, 1754)  # ~150 dpi
        try:
            font_big = ImageFont.truetype("arial.ttf", 46)
            font = ImageFont.truetype("arial.ttf", 32)
        except OSError:
            font_big = font = ImageFont.load_default()

        pages, missing = [], 0
        for t in rows:
            page = Image.new("RGB", A4, "white")
            d = ImageDraw.Draw(page)
            y = 90
            d.text((90, y), f"Ticket #{_ticket_name(t)}", fill="black", font=font_big)
            y += 90
            for label, key in [("Voucher", "voucher"), ("Stake", "stake"),
                               ("Combined Odds", "combined_odds")]:
                d.text((90, y), f"{label}: {t.get(key, '-')}", fill="black", font=font)
                y += 50
            d.text((90, y), f"Generated: {fmt_ts(t.get('timestamp', ''))}", fill="black", font=font)
            y += 70

            qr = t.get("qr", "")
            qr_path = qr if os.path.isabs(qr) else os.path.join(HERE, os.path.normpath(qr))
            if os.path.exists(qr_path):
                img = Image.open(qr_path).convert("RGB").resize((620, 620), Image.NEAREST)
                page.paste(img, (90, y))
                y += 660
            else:
                missing += 1
                d.text((90, y), "(QR image not found)", fill="red", font=font)
                y += 60

            for s in t.get("selections", []):
                if y > A4[1] - 90:
                    break
                line = f"{s.get('match')}  -  {s.get('side')}  @ {s.get('odds')}"
                d.text((90, y), line[:70], fill="black", font=font)
                y += 44
            pages.append(page)

        try:
            pages[0].save(out, save_all=True, append_images=pages[1:])
        except OSError as e:
            messagebox.showerror("PDF export failed", str(e))
            return
        note = f"\n({missing} QR image(s) missing)" if missing else ""
        messagebox.showinfo("PDF saved", f"{len(pages)} ticket(s) written to:\n{out}{note}")

    # --- logs window ----------------------------------------------------------
    def _open_logs(self):
        if getattr(self, "_logs_win", None) and self._logs_win.winfo_exists():
            self._logs_win.lift()
            return
        win = tk.Toplevel(self.root)
        self._logs_win = win
        win.title("Activity Log — logs/bot.jsonl")
        win.geometry("900x560")
        win.configure(bg=BG)

        top = tk.Frame(win, bg=BG)
        top.pack(fill="x", padx=10, pady=8)
        tk.Label(top, text="Show level ≥", bg=BG, fg=MUTE).pack(side="left")
        self._log_level = tk.StringVar(value="INFO")
        ttk.Combobox(top, textvariable=self._log_level, values=["INFO", "WARNING", "ERROR"],
                     width=10, state="readonly").pack(side="left", padx=6)
        self._log_level.trace_add("write", lambda *_: self._reload_logs())

        wrap = tk.Frame(win, bg=CARD)
        wrap.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.logs = ttk.Treeview(wrap, columns=("ts", "level", "msg"), show="headings")
        for c, t, w in [("ts", "Time", 170), ("level", "Level", 90), ("msg", "Message", 600)]:
            self.logs.heading(c, text=t)
            self.logs.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.logs.yview)
        self.logs.configure(yscrollcommand=sb.set)
        self.logs.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for lvl, color in LEVEL_COLOR.items():
            self.logs.tag_configure(lvl, foreground=color)

        self._reload_logs()
        self._poll_logs()

    def _reload_logs(self):
        if not (getattr(self, "_logs_win", None) and self._logs_win.winfo_exists()):
            return
        order = {"INFO": 0, "WARNING": 1, "ERROR": 2, "CRITICAL": 2}
        floor = order.get(self._log_level.get(), 0)
        self.logs.delete(*self.logs.get_children())
        for e in _read_log_events():
            lvl = e.get("level", "INFO")
            if order.get(lvl, 0) < floor:
                continue
            self.logs.insert("", "end", values=(fmt_ts(e.get("ts", "")), lvl, e.get("msg", "")),
                             tags=(lvl,) if lvl in LEVEL_COLOR else ())
        self.logs.yview_moveto(1.0)

    def _poll_logs(self):
        if getattr(self, "_logs_win", None) and self._logs_win.winfo_exists():
            self._reload_logs()
            self.root.after(1500, self._poll_logs)

    # --- running the bot (in-process, so it works as a frozen .exe) -----------
    def _run(self, live):
        if self.worker and self.worker.is_alive():
            return
        if self._pending_sheets and not messagebox.askyesno(
            "Discard paused run?",
            f"A paused run has {len(self._pending_sheets)} sheet(s) remaining: "
            f"{', '.join(self._pending_sheets)}.\n\n"
            "Starting Generate now will abandon them — those sheets won't be "
            "retried unless you re-run the whole workbook.\n\n"
            "Click Resume instead to continue where it left off.\n\n"
            "Start a new run anyway?"):
            return
        self._pending_sheets = []  # confirmed: abandon the paused run
        self._set_buttons(idle=True)
        if not os.path.exists(self.excel_path):
            messagebox.showerror("No workbook", f"Excel file not found:\n{self.excel_path}")
            return
        match_type = self.type_var.get()
        rule = self._rule_from_fields()
        try:
            stake_rules.check_rule(rule)
        except stake_rules.StakeError as e:
            messagebox.showerror("Stake not valid", f"{match_type}-match tickets:\n\n{e}")
            return
        try:
            lo = int(str(self.min_interval_var.get()).strip())
            hi = int(str(self.max_interval_var.get()).strip())
            if lo < 1 or hi < lo:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid interval",
                                 "'Seconds between tickets' must be two positive integers\n"
                                 "with min ≤ max.")
            return
        self._min_interval = lo
        self._max_interval = hi
        # Validate every requested sheet synchronously so the user sees one
        # grouped popup and no API call can start with an invalid workbook.
        try:
            tickets, validation_errors, products = excel_reader.load_tickets_and_products(
                self.excel_path, sheets=None, match_count=match_type)
            stakes, stake_errors = stake_rules.preview_stakes(rule, products, list(tickets))
        except Exception as e:
            messagebox.showerror("Workbook validation failed", str(e))
            return
        validation_errors = list(validation_errors) + [(s, [e]) for s, e in stake_errors.items()]
        if validation_errors:
            lines = [f"Ticket type selected: {match_type} matches per ticket.",
                     "Fix all of the following before running:\n"]
            for sheet, errors in validation_errors:
                lines.append(f"Sheet {sheet}:")
                lines.extend(f"  • {error}" for error in errors)
                lines.append("")
            messagebox.showerror("Excel validation failed", "\n".join(lines))
            return
        # Never make a ticket twice: finish any ticket cut off last time, then
        # leave out every sheet whose combination was already made (any run,
        # any day, even after closing the robot). The bot checks again itself.
        already = []
        if live:
            try:
                bot.recover_interrupted()
                index = ticket_ledger.created_index()
                already = [s for s in tickets if ticket_ledger.find_created(picks=tickets[s], index=index)]
            except ticket_ledger.LedgerError as e:
                messagebox.showerror("Ticket history problem", str(e))
                return
            if already and len(already) == len(tickets):
                messagebox.showinfo(
                    "Nothing to create",
                    f"All {len(tickets)} tickets in this workbook were already created:\n"
                    f"{ticket_ledger.compress_labels(match_type, already)}\n\n"
                    "They are never made twice, so there is nothing left to do.")
                return
            for s in already:
                stakes.pop(s, None)
        # Start with a clean current-run view. Historical records remain on disk
        # and are still available through the Run selector after completion.
        self._previous_latest_run_id = (_read_tickets()[0].get("run_id")
                                        if _read_tickets() else None)
        self._current_run_view = True
        self._current_run_id = None
        self._ticket_rows = []
        self.tickets.delete(*self.tickets.get_children())
        self._update_stats(None)
        self._clear_ticket_detail()
        self._set_status(True, "LIVE" if live else "preview")
        self._save_settings()
        preview = []
        if already:
            preview.append(f"Already created, will be SKIPPED ({len(already)}):\n"
                           f"   {ticket_ledger.compress_labels(match_type, already)}\n")
            preview.append(f"To create now ({len(stakes)}):")
        for sheet, stake in list(stakes.items())[:PREVIEW_LINES]:
            shown = stake if stake is not None else "from the live odds"
            preview.append(f"   Ticket {bot.ticket_label(match_type, sheet)}:  stake {shown}")
        if len(stakes) > PREVIEW_LINES:
            preview.append(f"   … and {len(stakes) - PREVIEW_LINES} more")
        if live and not messagebox.askyesno(
            "Confirm LIVE run",
            f"This will place REAL bet drafts from:\n{os.path.basename(self.excel_path)}\n\n"
            f"Ticket type: {match_type} matches  ({len(stakes)} tickets)\n"
            f"Stake: {stake_rules.describe(rule)}\n\n" + "\n".join(preview) +
            "\n\nContinue?"):
            self._set_status(False)   # cancelled: don't leave the pill on "Running LIVE…"
            self._current_run_view = False
            self._current_run_id = None
            self._refresh(force=True)
            return
        self._match_type = match_type
        self._stake_rule = rule
        if not self.console_open:
            self._toggle_console()
        self._log_pos = _file_size(LOG_FILE)  # console shows only this run's log lines
        self.console.delete("1.0", "end")
        self._log_console(f"--- {'LIVE run' if live else 'dry run'} started ---\n")
        self._set_buttons(running=True)
        self._set_status(True, "LIVE" if live else "preview")
        self._worker_error = None
        self._stop_event.clear()
        self._current_run_done_sheets = []   # reset done-sheet tracker for this run
        self._pending_sheets = []            # clear any previous resume state
        # Generate a single run_id for this session — shared with resume so all
        # sheets appear under one entry in the Run selector.
        import time as _time
        self._session_run_id = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())
        self._session_has_run = True
        self._user_selected_run = False  # a fresh run supersedes any manual browse choice
        self.worker = threading.Thread(target=self._run_worker, args=(live,), daemon=True)
        self.worker.start()

    def _run_worker(self, live):
        try:
            bot.run(
                excel=self.excel_path,
                dry_run=not live,
                cancel=self._stop_event.is_set,
                match_type=getattr(self, "_match_type", None),
                stake_rule=getattr(self, "_stake_rule", None),
                min_interval=getattr(self, "_min_interval", None),
                max_interval=getattr(self, "_max_interval", None),
                on_ticket_done=self._current_run_done_sheets.append,
                run_id=getattr(self, "_session_run_id", None),
            )
        except Exception as e:  # surfaced on the UI thread by _tick
            import logging
            logging.getLogger("bot").error("run failed: %s", e, exc_info=True)
            self._worker_error = str(e)

    def _stop(self):
        if self.worker and self.worker.is_alive():
            self._stop_event.set()
            self.btn_stop.config(state="disabled")
            self._set_status(True, "stopping")

    def _set_buttons(self, idle=False, running=False, paused=False):
        """Update all action buttons to reflect the current state.

        idle    — no run active, ready to Generate
        running — run in progress, only Stop enabled
        paused  — stopped mid-run: Resume continues it, but Generate is also
                  enabled so the user can instead discard it and start over
                  from sheet 1 (that click goes through _run()'s own confirm
                  dialog, so nothing is abandoned silently).
        """
        self.btn_live.config(state="normal" if (idle or paused) else "disabled")
        self.btn_resume.config(state="normal" if paused else "disabled")
        self.btn_stop.config(state="normal" if running else "disabled")

    def _log_console(self, text):
        self.console.insert("end", text)
        self.console.see("end")

    def _tail_console(self):
        """Append new log lines (level + message) as the run writes them."""
        size = _file_size(LOG_FILE)
        if size < self._log_pos:      # rotated / truncated
            self._log_pos = 0
        if size == self._log_pos:
            return
        try:
            with open(LOG_FILE, encoding="utf-8") as f:
                f.seek(self._log_pos)
                for line in f:
                    try:
                        e = json.loads(line)
                        self._log_console(f"{e.get('level', 'INFO'):7} {e.get('msg', '')}\n")
                    except ValueError:
                        pass
                self._log_pos = f.tell()
        except OSError:
            pass

    # --- refresh loop ---------------------------------------------------------
    def _tick(self):
        self._tail_console()
        if self.worker and not self.worker.is_alive() and not self._run_done_handled:
            self._run_done_handled = True
            self.worker = None
            self._current_run_view = False
            self._current_run_id = None
            self._refresh(force=True)
            # Detect paused state: stopped by user before all sheets finished
            if self._stop_event.is_set() and os.path.exists(self.excel_path):
                all_sheets = excel_reader.get_available_ticket_sheets(self.excel_path)
                pending = [s for s in all_sheets if s not in self._current_run_done_sheets]
                if pending:
                    self._pending_sheets = pending
                    self._resume_excel = self.excel_path
                    self._set_buttons(paused=True)
                    self._set_status(False)
                    self._log_console(
                        f"\n[paused — {len(pending)} sheet(s) remaining: "
                        f"{', '.join(pending)}]\n"
                    )
                    # ↓ MUST reschedule even on early return — otherwise the tick
                    #   loop dies and Resume completion is never detected.
                    self.root.after(1000, self._tick)
                    return
            # All sheets done (or not stopped by user)
            self._pending_sheets = []
            self._set_buttons(idle=True)
            self._set_status(False)
            self._log_console("\n[run finished]\n")
            if self._worker_error:
                messagebox.showerror("Run failed", self._worker_error)
            else:
                self._show_run_summary()
        elif self.worker and self.worker.is_alive():
            self._run_done_handled = False
        self._refresh()
        self.root.after(1000, self._tick)

    # --- resume (continue after a mid-run Stop) --------------------------------
    def _resume(self):
        """Restart bot.run() for the sheets not yet completed in the last run."""
        if not self._pending_sheets or (self.worker and self.worker.is_alive()):
            return
        self._session_has_run = True
        self._set_buttons(running=True)
        self._set_status(True, "LIVE (resume)")
        self._stop_event.clear()
        self._worker_error = None
        self._log_console(
            f"\n--- resuming sheets {', '.join(self._pending_sheets)} ---\n"
        )
        self._run_done_handled = True  # reset so _tick handles the new run's end
        self.worker = threading.Thread(target=self._resume_worker, daemon=True)
        self.worker.start()

    def _resume_worker(self):
        try:
            bot.run(
                excel=self._resume_excel,
                dry_run=False,
                sheets=list(self._pending_sheets),
                cancel=self._stop_event.is_set,
                match_type=getattr(self, "_match_type", None),   # same type + stake as the paused run
                stake_rule=getattr(self, "_stake_rule", None),
                min_interval=getattr(self, "_min_interval", None),
                max_interval=getattr(self, "_max_interval", None),
                on_ticket_done=self._current_run_done_sheets.append,
                run_id=getattr(self, "_session_run_id", None),  # same id = all sheets in one run
                # The paused ticket's trailing wait was cut short by Stop — apply
                # the same anti-fingerprinting gap before this resumed batch's
                # first ticket too, so pacing stays continuous across the pause.
                wait_before_first=True,
            )
        except Exception as e:
            import logging
            logging.getLogger("bot").error("resume failed: %s", e, exc_info=True)
            self._worker_error = str(e)

    def _on_close(self):
        """Closing during a run is safe (nothing is ever made twice), but say so first."""
        if self.worker and self.worker.is_alive() and not messagebox.askyesno(
                "Tickets are being created",
                "A run is in progress. Close the robot anyway?\n\n"
                "Nothing will be repeated: next time, Generate skips every ticket "
                "already made and continues with the rest."):
            return
        self._stop_event.set()
        self.root.destroy()

    def _changed(self, path):
        try:
            m = os.path.getmtime(path)
        except OSError:
            m = None
        if self._mtimes.get(path) != m:
            self._mtimes[path] = m
            return True
        return False

    def _refresh(self, force=False):
        if force or self._changed(TICKETS):
            self._reload_tickets()

    def _reload_tickets(self):
        rows = _read_tickets()  # newest first
        if self._current_run_view:
            # The bot creates one run_id for the whole batch. Once the first
            # ticket is written, lock the live view to that run only.
            if self._current_run_id is None:
                new_run = next((r.get("run_id") for r in rows
                                if r.get("run_id") != self._previous_latest_run_id), None)
                self._current_run_id = new_run
            if self._current_run_id is None:
                self._ticket_rows = []
                self.tickets.delete(*self.tickets.get_children())
                self._update_stats(None)
                return
            rows = [r for r in rows if r.get("run_id") == self._current_run_id]
        runs = []
        for r in rows:
            rid = r.get("run_id") or "(earlier)"
            if rid not in runs:
                runs.append(rid)

        # Human-friendly, STABLE labels mapped back to run_id.
        self._run_labels = {"All runs": None}
        values = ["All runs"]
        for rid in runs:
            n = sum(1 for r in rows if (r.get("run_id") or "(earlier)") == rid)
            when = fmt_ts(rid) if rid != "(earlier)" else "(earlier)"
            label = f"{when}  |  {n} ticket{'s' if n != 1 else ''}"
            self._run_labels[label] = rid
            values.append(label)
        self.run_combo["values"] = values

        latest = values[1] if runs else "All runs"
        if latest != self._last_latest:
            self._last_latest = latest
            self.run_var.set(latest)  # re-enters via trace
            return
        if self.run_var.get() not in values:
            self.run_var.set("All runs")

        sel_rid = self._run_labels.get(self.run_var.get())  # None => All runs
        # Don't show a historical run just because it's the newest on disk —
        # only once this session has actually run something, or the user has
        # deliberately picked a run from the dropdown.
        show_empty = not self._session_has_run and not self._user_selected_run
        self._ticket_rows = [] if show_empty else [
            r for r in rows if sel_rid is None or (r.get("run_id") or "(earlier)") == sel_rid
        ]
        self.tickets.delete(*self.tickets.get_children())
        for i, t in enumerate(self._ticket_rows):
            self.tickets.insert("", "end", iid=str(i), values=(
                _ticket_name(t), t.get("voucher"), t.get("stake"),
                t.get("combined_odds"), fmt_ts(t.get("timestamp", ""))))
        self._update_stats(None if show_empty else sel_rid)
        if show_empty:
            self._clear_ticket_detail()  # cold start: no stale ticket shown on the right either

    def _update_stats(self, sel_rid):
        rows = self._ticket_rows
        n = len(rows)
        odds = [r["combined_odds"] for r in rows if isinstance(r.get("combined_odds"), (int, float))]
        avg = f"{sum(odds) / len(odds):.0f}" if odds else "—"
        self.stat["tickets"].config(text=str(n))
        self.stat["odds"].config(text=avg)
        self.stat["success"].config(text=str(n))

        # "Failed" is only knowable once a run has finished — while tickets are
        # still being generated the remaining sheets haven't failed, they just
        # haven't run yet. Read the real count from the run's summary event.
        running = bool(self.worker and self.worker.is_alive())
        if running or sel_rid is None:
            self.stat["failed"].config(text="—")
            return
        summary = _summarize_last_run(_read_log_events())
        self.stat["failed"].config(text=str(len(summary["failed"])) if summary else "—")

    # --- ticket detail --------------------------------------------------------
    def _clear_ticket_detail(self):
        """Reset the right-hand detail panel to its empty state.

        _show_ticket() populates kv/qr_label/sel from a selected row; a fresh
        Generate (or the cold-start empty view) must clear those too, not just
        the title, or a stale ticket's voucher/QR/matches keeps showing.
        """
        self.detail_title.config(text="Select a ticket")
        for key in self.kv:
            self.kv[key].config(text="—")
        self._qr_img = None
        self.qr_label.config(image="", text="")
        self.sel.delete(*self.sel.get_children())

    def _show_ticket(self, _event):
        s = self.tickets.selection()
        if not s:
            return
        t = self._ticket_rows[int(s[0])]
        self.detail_title.config(text=f"Ticket #{_ticket_name(t)}")
        self.kv["voucher"].config(text=str(t.get("voucher", "—")))
        self.kv["stake"].config(text=str(t.get("stake", "—")))
        self.kv["odds"].config(text=str(t.get("combined_odds", "—")))
        self.kv["time"].config(text=fmt_ts(t.get("timestamp", "")))

        qr = t.get("qr", "")
        qr_path = qr if os.path.isabs(qr) else os.path.join(HERE, os.path.normpath(qr))
        try:
            img = tk.PhotoImage(file=qr_path)
            factor = max(1, -(-img.width() // 200))  # shrink so it fits ~200px wide
            self._qr_img = img.subsample(factor) if factor > 1 else img
            self.qr_label.config(image=self._qr_img, text="")
        except tk.TclError:
            self.qr_label.config(image="", text=f"(QR not found)\n{qr}", fg=MUTE)

        self.sel.delete(*self.sel.get_children())
        for x in t.get("selections", []):
            self.sel.insert("", "end", values=(x.get("match"), x.get("side"), x.get("odds")))


def _selftest():
    """`InbetComboTicketBot.exe --selftest` (used by the Windows build): open the
    window, check the bundled pieces work, close, and write the result to
    selftest.txt next to the exe. Exit code 0 = ok."""
    try:
        root = tk.Tk()
        Dashboard(root)
        root.update()
        root.destroy()
        assert stake_rules.compute_stake(stake_rules.default_rule(), i12=74.95670826) == 275
        bot.generate_qr("SELFTEST", os.path.join(HERE, "logs", "selftest_qr.png"))
        result, code = "selftest ok", 0
    except Exception as e:  # noqa: BLE001 - report anything
        result, code = f"selftest FAILED: {e!r}", 1
    with open(os.path.join(HERE, "selftest.txt"), "w", encoding="utf-8") as f:
        f.write(result + "\n")
    sys.exit(code)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    root = tk.Tk()
    Dashboard(root)
    root.mainloop()
