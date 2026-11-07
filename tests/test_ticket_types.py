"""
Ticket types (5..10 matches), stake rules and QR names.

Run:  python -m pytest -q tests

Builds its own workbook in the client's layout (header row 1, matches from row
2, Product in I12) and runs the full bot with the site's API replaced by fakes,
so nothing here touches the network.
"""

import datetime
import json
import math
import os
import random
import sys

import openpyxl
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot  # noqa: E402
import common  # noqa: E402
import config  # noqa: E402
import excel_reader  # noqa: E402
import stake_rules  # noqa: E402

HEADER = ["Match ID", "Day", "Time", "Match", "Home Odds", "Draw Odds", "Away Odds", "Tournament", "Min Odd"]
TOURNAMENT_ID = 555


def build_workbook(path, sheets):
    """sheets = {name: match_count}; returns {name: [rows]} with the picks written."""
    rnd = random.Random(7)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    written, code = {}, 30000
    for name, n in sheets.items():
        ws = wb.create_sheet(name)
        ws.append(HEADER)
        rows, mins = [], []
        for _ in range(n):
            code += 1
            h, d, a = (round(rnd.uniform(1.5, 4), 2), round(rnd.uniform(3, 4), 2),
                       round(rnd.uniform(1.5, 4), 2))
            pick = rnd.choice([h, d, a])
            mins.append(pick)
            row = [f"#{code}", "Sunday, 08.11.2026", datetime.time(20, 45),
                   f"Home{code} - Away{code}", h, d, a, "Europa League", pick]
            ws.append(row)
            rows.append(row)
        ws["H12"] = "Product:"
        ws["I12"] = math.prod(mins)
        written[name] = rows
    wb.save(path)
    return written


# --- reading + validation ----------------------------------------------------
@pytest.mark.parametrize("n", config.MATCH_TYPES)
def test_each_type_reads_only_its_own_sheet(tmp_path, n):
    path = tmp_path / "wb.xlsx"
    build_workbook(path, {str(t): t for t in config.MATCH_TYPES})
    tickets, skipped, products = excel_reader.load_tickets_and_products(str(path), match_count=n)
    assert list(tickets) == [str(n)]
    assert len(tickets[str(n)]) == n
    assert {s for s, _ in skipped} == {str(t) for t in config.MATCH_TYPES if t != n}
    assert products[str(n)] == pytest.approx(math.prod(p["min_odd"] for p in tickets[str(n)]))


def test_bigger_sheet_under_smaller_type_is_flagged(tmp_path):
    path = tmp_path / "wb.xlsx"
    build_workbook(path, {"1": 8})
    _, skipped = excel_reader.load_all_tickets(str(path), match_count=5)
    errors = dict(skipped)["1"]
    assert any("row 7" in e and "more than 5 matches" in e for e in errors), errors


def test_smaller_sheet_under_bigger_type_is_flagged(tmp_path):
    path = tmp_path / "wb.xlsx"
    build_workbook(path, {"1": 5})
    _, skipped = excel_reader.load_all_tickets(str(path), match_count=8)
    assert "Sheet must contain exactly 8 matches; found 5." in dict(skipped)["1"]


def test_unknown_type_is_refused(tmp_path):
    path = tmp_path / "wb.xlsx"
    build_workbook(path, {"1": 5})
    with pytest.raises(ValueError):
        excel_reader.load_all_tickets(str(path), match_count=3)


# --- stake rules -------------------------------------------------------------
CLIENT_I12 = {"5": 74.95670826, "6": 140.84824532624998, "7": 210.92817704363998, "8": 554.414846938445}


def test_client_formula_on_client_numbers():
    rule = {"mode": "formula", "formula": "20000/(I12-1)"}
    stakes, problems = stake_rules.preview_stakes(rule, CLIENT_I12, list(CLIENT_I12))
    assert stakes == {"5": 275, "6": 145, "7": 100, "8": 40}
    assert problems == {}


def test_fixed_stake_used_as_typed():
    stakes, _ = stake_rules.preview_stakes({"mode": "fixed", "fixed": "100"}, CLIENT_I12, list(CLIENT_I12))
    assert set(stakes.values()) == {100}


def test_empty_or_one_product_stops_before_run():
    rule = {"mode": "formula", "formula": "20000/(I12-1)"}
    _, problems = stake_rules.preview_stakes(rule, {"a": None, "b": 1.0}, ["a", "b"])
    assert set(problems) == {"a", "b"}


def test_live_odds_formula_waits_for_live_odds():
    rule = {"mode": "formula", "formula": "20000/(ODDS-1)"}
    stakes, problems = stake_rules.preview_stakes(rule, CLIENT_I12, ["5"])
    assert stakes == {"5": None} and problems == {}


@pytest.mark.parametrize("bad", ["", "20000/(I12-1", "__import__('os').system('x')", "I12**2", "open('f')"])
def test_formula_only_accepts_arithmetic(bad):
    with pytest.raises(stake_rules.StakeError):
        stake_rules.check_rule({"mode": "formula", "formula": bad})


def test_settings_round_trip_and_bad_file(tmp_path):
    path = str(tmp_path / "settings.json")
    s = stake_rules.load_settings(path)                      # missing file -> defaults
    assert s["match_type"] == config.DEFAULT_MATCH_TYPE
    s["match_type"] = 8
    s["rules"]["8"] = {"mode": "fixed", "fixed": 100, "formula": "30000/(I12-1)"}
    stake_rules.save_settings(path, s)
    again = stake_rules.load_settings(path)
    assert again["match_type"] == 8 and again["rules"]["8"]["mode"] == "fixed"
    assert again["rules"]["5"]["formula"] == config.DEFAULT_STAKE_FORMULA
    with open(path, "w") as f:
        f.write("{not json")
    assert stake_rules.load_settings(path)["match_type"] == config.DEFAULT_MATCH_TYPE


# --- full live run with the site faked ---------------------------------------
class FakeSite:
    """Stands in for common.* so bot.run goes through the real live code path."""

    def __init__(self, workbook_rows, drift=1.0):
        self.drafts = []
        self.drift = drift          # live odds = sheet odds * drift ("odds moved")
        self.outcomes = {}
        events = {}
        oid = 1000
        for rows in workbook_rows.values():
            for row in rows:
                code = row[0].lstrip("#")
                home, away = row[3].split(" - ")
                outcomes = {}
                for tid, odd, name in ((100001, row[4], home), (100002, row[5], "Draw"),
                                       (100003, row[6], away)):
                    oid += 1
                    o = {"id": oid, "marketTemplateOutcomeId": tid, "odds": odd, "nameEN": name,
                         "isActive": True, "title": name}
                    outcomes[str(oid)] = o
                    self.outcomes[str(oid)] = o
                events[code] = {"shortCode": code, "eventId": int(code), "sportEventId": int(code),
                                "eventTitle": row[3], "tournamentId": TOURNAMENT_ID,
                                "markets": {"m": {"marketTemplateId": 100001, "isMainMarket": True,
                                                  "marketName": "Final Result", "marketId": int(code),
                                                  "outcomes": outcomes}}}
        self.games = {"events": events}

    def install(self, monkeypatch):
        monkeypatch.setattr(common, "fetch_sports", lambda save_to=None: [
            {"type": "tournament", "path": "europa-league", "id": TOURNAMENT_ID}])
        monkeypatch.setattr(common, "fetch_tournament_games", lambda ids, save_to=None: self.games)
        monkeypatch.setattr(common, "fetch_event_markets", lambda event_id: {})
        monkeypatch.setattr(common, "create_bets", self.create_bets)
        monkeypatch.setattr(common, "create_draft", self.create_draft)

    def create_bets(self, outcome_ids):
        outs = [dict(self.outcomes[str(i)], odds=round(self.outcomes[str(i)]["odds"] * self.drift, 2))
                for i in outcome_ids]
        total = math.prod(o["odds"] for o in outs)
        return {"outcomes": outs, "bets": [{"fold": len(outs), "combinationsCount": 1, "totalOdds": total}]}

    def create_draft(self, payload):
        self.drafts.append(payload)
        return {"code": f"V{len(self.drafts):04d}"}


@pytest.mark.parametrize("n", [4, 5, 8, 10])
def test_live_run_end_to_end(tmp_path, monkeypatch, n):
    monkeypatch.chdir(tmp_path)
    rows = build_workbook(tmp_path / "wb.xlsx", {"3": n, "22": n})
    site = FakeSite(rows, drift=1.03)
    site.install(monkeypatch)
    rule = {"mode": "formula", "formula": "20000/(I12-1)"}

    result = bot.run(excel=str(tmp_path / "wb.xlsx"), dry_run=False, match_type=n, stake_rule=rule,
                     min_interval=0, max_interval=0, run_id="2026-11-08T10:00:00Z")

    assert result["done"] == ["3", "22"] and not result["validation_errors"]
    log = json.load(open("tickets_log.json", encoding="utf-8"))
    assert [t["label"] for t in log] == [f"{n}-3", f"{n}-22"]
    for t, payload in zip(log, site.drafts):
        assert os.path.basename(t["qr"]) == f"{t['label']}.png" and os.path.exists(t["qr"])
        bet = payload["bets"][0]
        assert bet["selectedSystem"] == config.FOLD_SYSTEM[n]
        assert len(payload["selections"]) == n == len(bet["selectionRefs"])
        i12 = math.prod(r[8] for r in rows[t["sheet"]])
        assert bet["stake"]["value"] == math.ceil(20000 / (i12 - 1) / 5) * 5   # from I12, not live odds


def test_live_odds_formula_and_fixed_stake(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rows = build_workbook(tmp_path / "wb.xlsx", {"1": 6, "2": 6})
    site = FakeSite(rows, drift=1.05)
    site.install(monkeypatch)
    bot.run(excel=str(tmp_path / "wb.xlsx"), match_type=6, stake_rule={"mode": "formula", "formula": "20000/ODDS"},
            min_interval=0, max_interval=0, sheets=["1"])
    live = site.create_bets([s["outcomeId"] for s in site.drafts[0]["selections"]])["bets"][0]["totalOdds"]
    assert site.drafts[0]["bets"][0]["stake"]["value"] == math.ceil(20000 / live / 5) * 5

    bot.run(excel=str(tmp_path / "wb.xlsx"), match_type=6, stake_rule={"mode": "fixed", "fixed": 100},
            min_interval=0, max_interval=0, sheets=["2"])
    assert site.drafts[1]["bets"][0]["stake"]["value"] == 100


def test_bad_product_stops_before_any_api_call(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    build_workbook(tmp_path / "wb.xlsx", {"1": 5})
    wb = openpyxl.load_workbook(tmp_path / "wb.xlsx")
    wb["1"]["I12"] = None
    wb.save(tmp_path / "wb.xlsx")
    called = []
    monkeypatch.setattr(common, "fetch_sports", lambda save_to=None: called.append(1))
    result = bot.run(excel=str(tmp_path / "wb.xlsx"), match_type=5,
                     stake_rule={"mode": "formula", "formula": "20000/(I12-1)"})
    assert result["validation_errors"] and not called


def test_no_rule_keeps_original_stake(tmp_path, monkeypatch):
    """Called the old way (no type, no rule): 10 matches, ceil(budget / live odds) to 5."""
    monkeypatch.chdir(tmp_path)
    rows = build_workbook(tmp_path / "wb.xlsx", {"1": 10})
    site = FakeSite(rows)
    site.install(monkeypatch)
    bot.run(excel=str(tmp_path / "wb.xlsx"), min_interval=0, max_interval=0)
    live = site.create_bets([s["outcomeId"] for s in site.drafts[0]["selections"]])["bets"][0]["totalOdds"]
    assert site.drafts[0]["bets"][0]["stake"]["value"] == math.ceil(config.STAKE_BUDGET / live / 5) * 5
