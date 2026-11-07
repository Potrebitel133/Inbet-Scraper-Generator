"""
A ticket is never made twice: not after Stop, not after closing and restarting,
not after a crash in the middle of a ticket. Runs the real bot against a fake site.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot  # noqa: E402
import common  # noqa: E402
import config  # noqa: E402
import ticket_ledger  # noqa: E402
from test_ticket_types import FakeSite, build_workbook  # noqa: E402

RULE = {"mode": "formula", "formula": "20000/(I12-1)"}


def run(path, n, **kw):
    return bot.run(excel=str(path), match_type=n, stake_rule=RULE, min_interval=0, max_interval=0, **kw)


def log_labels():
    with open(config.TICKETS_LOG, encoding="utf-8") as f:
        return [t["label"] for t in json.load(f)]


@pytest.fixture
def site6(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rows = build_workbook(tmp_path / "wb.xlsx", {str(i): 6 for i in range(1, 9)})
    site = FakeSite(rows)
    site.install(monkeypatch)
    return tmp_path / "wb.xlsx", site, rows


def test_stop_then_restart_continues_where_it_ended(site6):
    path, site, _ = site6
    made = []

    def stop_after_3():
        return len(made) >= 3

    first = run(path, 6, cancel=stop_after_3, on_ticket_done=made.append)
    assert first["done"] == ["1", "2", "3"]

    second = run(path, 6)                        # a fresh start, as after closing the robot
    assert second["already_done"] == ["1", "2", "3"]
    assert second["done"] == ["4", "5", "6", "7", "8"]
    assert len(site.drafts) == 8                 # 8 sheets -> exactly 8 tickets, none twice
    assert log_labels() == [f"6-{i}" for i in range(1, 9)]

    third = run(path, 6)
    assert third["already_done"] == [str(i) for i in range(1, 9)] and len(site.drafts) == 8


def test_crash_after_voucher_is_finished_not_remade(site6, monkeypatch):
    path, site, _ = site6
    real_qr = bot.generate_qr
    calls = {"n": 0}

    def qr_crashes_on_sheet_2(voucher, out):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("power cut")           # voucher received, QR + log not written
        return real_qr(voucher, out)

    monkeypatch.setattr(bot, "generate_qr", qr_crashes_on_sheet_2)
    first = run(path, 6, sheets=["1", "2"])
    assert first["done"] == ["1"] and first["errored"] == ["2"]
    assert len(site.drafts) == 2 and os.path.exists(config.IN_PROGRESS_FILE)

    monkeypatch.setattr(bot, "generate_qr", real_qr)
    second = run(path, 6, sheets=["1", "2", "3"])
    assert second["already_done"] == ["1", "2"] and second["done"] == ["3"]
    assert len(site.drafts) == 3                 # sheet 2 finished from its note, not remade
    assert log_labels() == ["6-1", "6-2", "6-3"]
    assert not os.path.exists(config.IN_PROGRESS_FILE)
    with open(config.TICKETS_LOG, encoding="utf-8") as f:
        assert all(os.path.exists(t["qr"]) for t in json.load(f))


def test_failed_create_draft_leaves_nothing_behind(site6, monkeypatch):
    path, site, _ = site6

    def boom(payload):
        raise ConnectionError("no answer")

    monkeypatch.setattr(common, "create_draft", boom)
    assert run(path, 6, sheets=["1"])["errored"] == ["1"]
    assert not os.path.exists(config.IN_PROGRESS_FILE)   # no voucher = nothing to scan

    monkeypatch.setattr(common, "create_draft", site.create_draft)
    assert run(path, 6, sheets=["1"])["done"] == ["1"]   # so it is made next time


def test_same_combination_in_two_sheets_or_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rows = build_workbook(tmp_path / "a.xlsx", {"1": 5})
    import shutil
    shutil.copy(tmp_path / "a.xlsx", tmp_path / "copy renamed.xlsx")
    site = FakeSite(rows)
    site.install(monkeypatch)
    assert run(tmp_path / "a.xlsx", 5)["done"] == ["1"]
    assert run(tmp_path / "copy renamed.xlsx", 5)["already_done"] == ["1"]
    assert len(site.drafts) == 1


def test_tickets_made_before_this_update_are_recognised(site6):
    """Old log entries have no Match IDs: matched by match name + side instead."""
    path, site, _ = site6
    run(path, 6, sheets=["1"])
    with open(config.TICKETS_LOG, encoding="utf-8") as f:
        old = json.load(f)
    for t in old:                                  # strip to the previous version's format
        t.pop("label"), t.pop("match_type")
        for s in t["selections"]:
            s.pop("match_id")
            s["outcomeId"] = "x" + str(s["outcomeId"])   # force the name check, not outcomes
    with open(config.TICKETS_LOG, "w", encoding="utf-8") as f:
        json.dump(old, f)
    assert run(path, 6, sheets=["1"])["already_done"] == ["1"]
    assert len(site.drafts) == 1


def test_site_outcomes_catch_a_duplicate_the_sheet_check_missed(site6):
    """Same outcomes on the site under different Match IDs: stopped before create-draft."""
    path, site, _ = site6
    run(path, 6, sheets=["1"])
    with open(config.TICKETS_LOG, encoding="utf-8") as f:
        log = json.load(f)
    for s in log[0]["selections"]:
        s["match_id"] = "#99" + s["match_id"].lstrip("#")
    with open(config.TICKETS_LOG, "w", encoding="utf-8") as f:
        json.dump(log, f)
    result = run(path, 6, sheets=["1"])
    assert result["already_done"] == ["1"] and len(site.drafts) == 1


def test_damaged_history_stops_before_any_api_call(site6, monkeypatch):
    path, _, _ = site6
    with open(config.TICKETS_LOG, "w") as f:
        f.write('[{"voucher": "V1", "selec')
    called = []
    monkeypatch.setattr(common, "fetch_sports", lambda save_to=None: called.append(1))
    with pytest.raises(ticket_ledger.LedgerError):
        run(path, 6)
    assert not called


def test_dry_run_is_not_affected(site6, monkeypatch):
    path, _, _ = site6
    run(path, 6, sheets=["1"])
    monkeypatch.setattr(config, "TOURNAMENTS_FILE", "t.json")
    with open("t.json", "w") as f:
        json.dump([{"type": "tournament", "path": "europa-league", "id": 555}], f)
    games = common.fetch_tournament_games(555)
    with open("games.json", "w") as f:
        json.dump(games, f)
    result = bot.run(excel=str(path), match_type=6, stake_rule=RULE, dry_run=True,
                     offline_games="games.json", sheets=["1"])
    assert result["done"] == ["1"] and result["already_done"] == []


def test_labels_compress():
    assert ticket_ledger.compress_labels(6, ["3", "1", "2", "5", "7", "8"]) == "6-1 to 6-3, 6-5, 6-7 to 6-8"
