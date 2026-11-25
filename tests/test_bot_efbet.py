"""
The efbet ticket generator end to end, against a fake efbet that answers like
the real one (recorded 25 Nov 2026, tests/fixtures/efbet): the matches with
their markets, the bonus rules, the booking and the booking read back.
"""

import contextlib
import copy
import itertools
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import openpyxl
import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "ticket_efbet"))

import barcode_efbet  # noqa: E402
import bot_efbet as bot  # noqa: E402
import swarm_efbet as swarm  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures", "efbet")
HEADER = ["Match ID", "Day", "Time", "Match", "Home Odds", "Draw Odds", "Away Odds", "Tournament", "Min Odd"]
ORDER = (31500243, 31500242, 31500254, 31500257, 31500253)
FIXED_2 = {"mode": "fixed", "fixed": 2}


def load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)


class FakeEfbet:
    """efbet's swarm server as the robot sees it (swarm_efbet's functions)."""

    def __init__(self):
        future = datetime.now(timezone.utc) + timedelta(days=3)
        self.games = load("games_reply.json")["data"]["data"]["game"]
        for i, g in enumerate(self.games.values()):
            g["start_ts"] = int((future + timedelta(hours=i)).timestamp())
        self.rules = load("bonus_rules_reply.json")["data"]["details"]
        self.config = load("session_reply.json")["data"]["partner_config"]
        self.bookings = {}
        self.booked = []
        self.numbers = itertools.count(3535002)
        self.tamper = None            # change the booking efbet "stores"
        self.refuse = None            # efbet's answer to create_bet_book_v2 instead of a booking
        self.opened = 0

    @contextlib.contextmanager
    def connection(self, log=print):
        self.opened += 1
        yield self

    def get_games(self, ids):
        return {str(i): copy.deepcopy(self.games[str(i)]) for i in ids if str(i) in self.games}

    def selection(self, sid):
        for gid, g in self.games.items():
            for mid, m in g["market"].items():
                if str(sid) in m["event"]:
                    return int(gid), int(mid)
        raise KeyError(sid)

    def create_booking(self, params):
        self.booked.append(params)
        if self.refuse is not None:
            return self.refuse
        number = next(self.numbers)
        self.bookings[number] = copy.deepcopy(params)
        return {"result_text": None, "result": 0,
                "details": {"Id": number, "PartnerId": 18762167, "Data": json.dumps(params["bets"])}}

    def get_booking(self, number):
        bet = copy.deepcopy(self.bookings[int(number)]["bets"][0])
        sels = []
        for s in bet["Selections"]:
            gid, mid = self.selection(s["SelectionId"])
            sels.append({"sport_id": 1, "price": s["Price"], "selection_id": s["SelectionId"], "market_id": mid,
                         "match_id": gid, "competition_id": 541})
        booking = {"bets": [{"bet_type": bet["BetType"], "input_method": 1, "selections": sels,
                             "amount": float(bet["Amount"])}], "id": int(number)}
        if self.tamper:
            self.tamper(booking)
        return {"result": 0, "result_text": None, "details": booking}


@pytest.fixture
def site(monkeypatch, tmp_path):
    fake = FakeEfbet()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(swarm, "connection", fake.connection)
    monkeypatch.setattr(swarm, "get_games", fake.get_games)
    monkeypatch.setattr(swarm, "bonus_rules", lambda: fake.rules)
    monkeypatch.setattr(swarm, "partner_config", lambda: fake.config)
    monkeypatch.setattr(swarm, "create_booking", fake.create_booking)
    monkeypatch.setattr(swarm, "get_booking", fake.get_booking)
    monkeypatch.setattr(bot.time, "sleep", lambda s: None)
    monkeypatch.setattr(bot, "_wait", lambda lo, hi, stop: 0)
    return fake


def row(fake, gid, side="home"):
    g = fake.games[str(gid)]
    ev = {e["type_1"]: e["price"] for m in g["market"].values() if m["display_key"] == "WINNER_EP"
          for e in m["event"].values()}
    mo = {"home": ev["W1"], "draw": ev["X"], "away": ev["W2"]}[side]
    return [gid, "Friday, 27.11.2026", "21:30", f"{g['team1_name'].strip()} - {g['team2_name'].strip()}",
            ev["W1"], ev["X"], ev["W2"], "Germany - Bundesliga", mo]


def workbook(path, sheets):
    """The client's layout: sheets '5-1', '8-3', ... plus his 'Matches ' sheet."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        ws.append(HEADER)
        product = 1.0
        for r in rows:
            ws.append(r)
            product *= r[-1]
        ws["H12"], ws["I12"] = "Product:", product
    ws = wb.create_sheet("Matches ")
    ws.append(HEADER[:8])
    wb.save(path)
    return str(path)


def standard_book(fake, tmp_path):
    one = [row(fake, g) for g in ORDER]
    two = [row(fake, g, "away" if g == 31500243 else "home") for g in ORDER]
    return workbook(tmp_path / "261126Kristi.xlsx", {"5-1": one, "5-2": two, "8-1": one})


def tickets_log():
    with open(bot.FILES_EFBET["TICKETS_LOG"], encoding="utf-8") as f:
        return json.load(f)


def run(path, **kw):
    kw.setdefault("stake_rule", FIXED_2)
    kw.setdefault("match_type", 5)
    return bot.run(excel=path, min_interval=0, max_interval=0, **kw)


# --- the workbook ---------------------------------------------------------------------------
def test_only_the_chosen_types_sheets_are_read(site, tmp_path):
    book = standard_book(site, tmp_path)
    tickets, bad, products = bot.load_tickets_and_products(book, None, 5)
    assert list(tickets) == ["1", "2"] and not bad and products["1"] > 1
    assert bot.available_sheets(book, 5) == ["1", "2"] and bot.available_sheets(book, 8) == ["1"]
    _, bad8, _ = bot.load_tickets_and_products(book, None, 8)
    assert bad8 and "exactly 8 matches" in bad8[0][1][0]
    _, bad6, _ = bot.load_tickets_and_products(book, None, 6)
    assert "no sheets for 6-match tickets" in bad6[0][1][0]


def test_the_clients_own_workbook_layout_validates(site, tmp_path):
    # 261126Kristi.xlsx: Match ID as a number, Time as a time of day, Min Odd and Product as he makes them
    from datetime import time as tod
    rows = [[31462439, "Wednesday, 25.11.2026", tod(19, 45), "SK Slavia Praha - Villarreal", 2.71, 3.84, 2.03,
             "UEFA Champions League", 2.03]] * 5
    book = workbook(tmp_path / "k.xlsx", {"5-9": rows, "10-7": rows})
    tickets, bad, _ = bot.load_tickets_and_products(book, None, 5)
    assert list(tickets) == ["9"] and not bad and tickets["9"][0]["side"] == "away"
    assert bot.ticket_label(5, "9") == "5-9"


# --- tickets ---------------------------------------------------------------------------------
def test_tickets_are_booked_checked_and_saved_with_barcodes(site, tmp_path):
    result = run(standard_book(site, tmp_path))
    assert result["done"] == ["1", "2"] and not result["skipped"] and not result["errored"]
    recorded = load("book_request.json")["params"]
    assert site.booked[0] == recorded                     # exactly what the efbet page sent for 3535002
    werder_away = next(int(e["id"]) for m in site.games["31500243"]["market"].values()
                       if m["display_key"] == "WINNER_EP" for e in m["event"].values() if e["type_1"] == "W2")
    assert site.booked[1]["bets"][0]["Selections"][0] == {"SelectionId": werder_away, "Price": 3.7}
    first, second = tickets_log()
    assert (first["label"], first["voucher"], second["label"], second["voucher"]) == ("5-1", "3535002",
                                                                                       "5-2", "3535003")
    assert first["verified"] and first["stake"] == 2 and first["site"] == "efbet"
    assert first["combined_odds"] == 16.526
    assert first["on_efbet"] == {"total_odds": 16.526, "bonus_percent": 5.0, "possible_win": 33.05, "bonus": 1.55,
                                 "final_payout": 34.6}
    for t in (first, second):
        assert barcode_efbet.decode(Image.open(t["qr"])) == t["voucher"]
        assert os.path.basename(t["qr"]) == f"{t['label']}.png" and "barcodes_efbet" in t["qr"]
    assert not os.path.exists(bot.FILES_EFBET["IN_PROGRESS_FILE"])


def test_never_twice(site, tmp_path):
    book = standard_book(site, tmp_path)
    run(book)
    again = run(book)
    assert again["already_done"] == ["1", "2"] and len(site.booked) == 2


def test_check_only_books_nothing(site, tmp_path):
    result = run(standard_book(site, tmp_path), dry_run=True)
    assert result["done"] == ["1", "2"] and not site.booked
    assert not os.path.exists(bot.FILES_EFBET["TICKETS_LOG"])


def test_a_booking_that_differs_gets_no_barcode(site, tmp_path):
    site.tamper = lambda b: b["bets"][0].update(amount=20.0)
    result = run(standard_book(site, tmp_path))
    assert result["skipped"] == ["1", "2"]
    assert not os.path.exists(bot.FILES_EFBET["TICKETS_LOG"])
    with open(bot.REJECTED_LOG, encoding="utf-8") as f:
        rejected = json.load(f)
    assert len(rejected) == 2 and "stake on efbet is 20.0" in rejected[0]["rejected"][0]
    assert not os.path.isdir(bot.FILES_EFBET["QR_OUTPUT_DIR"])


def test_efbet_refusing_a_booking_skips_the_sheet(site, tmp_path):
    site.refuse = {"result": 2400, "result_text": "Max stake exceeded", "details": None}
    result = run(standard_book(site, tmp_path))
    assert result["skipped"] == ["1", "2"] and not os.path.exists(bot.FILES_EFBET["TICKETS_LOG"])


def test_a_started_match_stops_only_its_ticket(site, tmp_path):
    book = standard_book(site, tmp_path)
    site.games["31500243"]["start_ts"] = int(datetime.now(timezone.utc).timestamp()) - 600
    result = run(book)
    assert result["skipped"] == ["1", "2"] and not site.booked
    site.games["31500243"]["start_ts"] += 7 * 86400
    del site.games["31500253"]
    result = run(book)
    assert result["skipped"] == ["1", "2"]


def test_stake_outside_efbets_limits(site, tmp_path):
    result = run(standard_book(site, tmp_path), stake_rule={"mode": "fixed", "fixed": 5000})
    assert result["skipped"] == ["1", "2"] and not site.booked


def test_a_cut_off_ticket_is_finished_not_made_again(site, tmp_path, monkeypatch):
    book = standard_book(site, tmp_path)
    real = bot.read_back
    calls = {"n": 0}

    def crash_once(code):
        calls["n"] += 1
        if calls["n"] == 1:
            raise KeyboardInterrupt            # the robot closed right after efbet gave the number
        return real(code)

    monkeypatch.setattr(bot, "read_back", crash_once)
    with pytest.raises(KeyboardInterrupt):
        run(book, sheets=["1"])
    assert os.path.exists(bot.FILES_EFBET["IN_PROGRESS_FILE"]) and len(site.booked) == 1
    result = run(book)
    assert len(site.booked) == 2                           # only sheet 2 is booked now
    labels = [t["label"] for t in tickets_log()]
    assert labels == ["5-1", "5-2"] and result["already_done"] == ["1"]


def test_tied_odds_pick_one_side_at_random(site, tmp_path):
    rows = [row(site, g) for g in ORDER]
    rows[0][4] = rows[0][6] = rows[0][8] = 2.5               # home and away both 2.5 in the sheet
    book = workbook(tmp_path / "tie.xlsx", {"5-4": rows})
    result = run(book)
    assert result["done"] == ["4"]
    (t,) = tickets_log()
    assert t["selections"][0]["random_pick"] and t["selections"][0]["side"] in ("home", "away")
