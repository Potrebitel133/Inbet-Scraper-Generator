"""
The ticket window's run list (both robots use dashboard.py). Needs a display;
skipped where there is none.
"""

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

tk = pytest.importorskip("tkinter")


@pytest.fixture
def window(tmp_path, monkeypatch):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    import dashboard
    log = tmp_path / "tickets_log.json"
    log.write_text(json.dumps([{
        "voucher": "QS300332039773", "sheet": "17", "label": "5-17", "stake": 10,
        "combined_odds": 40.98, "qr": "missing.png", "run_id": "2026-11-16T14:57:50Z",
        "timestamp": "2026-11-16T14:57:59Z", "selections": []}]), encoding="utf-8")
    monkeypatch.setattr(dashboard, "TICKETS", str(log))
    cwd = os.getcwd()
    dash = dashboard.Dashboard(root)
    root.update()
    yield root, dash
    root.destroy()
    os.chdir(cwd)


def test_picking_the_run_already_shown_lists_its_tickets(window):
    root, dash = window
    assert dash._ticket_rows == []                       # fresh start: nothing shown yet
    label = dash.run_var.get()
    assert label.endswith("1 ticket")                    # the newest run is preselected
    dash.run_combo.set(label)                            # the client picks that same run
    dash.run_combo.event_generate("<<ComboboxSelected>>")
    root.update()
    assert [t["label"] for t in dash._ticket_rows] == ["5-17"]


def test_window_never_wider_or_taller_than_the_screen(window):
    root, _ = window
    root.update_idletasks()
    assert root.winfo_width() <= root.winfo_screenwidth()
    assert root.winfo_height() <= root.winfo_screenheight()
