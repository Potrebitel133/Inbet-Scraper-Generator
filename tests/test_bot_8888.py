"""
The 8888 ticket generator end to end, against a fake 8888 that answers like the
real one (recorded 15 Nov 2026, tests/fixtures/8888/ticket): match pages, the
saved-betslip request and the ticket page used to read every ticket back.
"""

import copy
import html
import itertools
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import openpyxl
import pytest
import qrcode

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "ticket_8888"))

import bot_8888 as bot  # noqa: E402
import config  # noqa: E402
import scraper_8888 as s8  # noqa: E402
import slip_8888 as slip  # noqa: E402
import stake_rules  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures", "8888", "ticket")
EVENT_IDS = (4156205, 4167326, 3707207, 3708369, 3708367)
HEADER = ["Match ID", "Day", "Time", "Match", "Home Odds", "Draw Odds", "Away Odds", "Tournament", "Min Odd"]
# Sheet 17 of the client's 5-match file
SHEET_17 = [
    ("#4156205", "Олимпия Милано - Цървена Звезда", 1.88, 10, 1.76, "Евролига", 1.88),
    ("#4167326", "Бешикташ - Реал Мадрид", 2.38, 10, 1.46, "Евролига", 2.38),
    ("#3707207", "Славия Прага - Виляреал", 2.75, 3.35, 2.1, "Шампионска Лига", 2.1),
    ("#3708369", "Брюж - Ливърпул", 3.65, 3.95, 1.65, "Шампионска Лига", 1.65),
    ("#3708367", "Спортинг - Манчестър Юнайтед", 2.4, 3.2, 2.45, "Шампионска Лига", 2.45),
]
FIXED_10 = {"mode": "fixed", "fixed": 10}


def load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)


def workbook(path, sheets):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(str(name))
        ws.append(HEADER)
        product = 1.0
        for mid, match, h, d, a, tour, mo in rows:
            ws.append([mid, "Tuesday, 17.11.2026", "21:30", match, h, d, a, tour, mo])
            product *= mo
        ws["H12"], ws["I12"] = "Product:", product
    wb.save(path)
    return str(path)


class Fake8888:
    """Match pages, slip-data and the ticket page, like 8888 answers them."""

    def __init__(self):
        future = datetime.now(timezone.utc) + timedelta(days=3)
        self.events = {}
        for i, eid in enumerate(EVENT_IDS):
            ev = load(f"event_{eid}.json")
            ev["startTime"] = (future + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
            self.events[eid] = ev
        self.slips = {}           # code -> body
        self.posted = []
        self.counter = itertools.count(1)
        self.slip_reply = None     # override the slip-data answer
        self.tamper = None         # change the ticket 8888 "stores"
        self.detail_fails = 0      # ticket page unreachable this many times

    def fetch_json(self, path, params=None):
        eid = int(path.rsplit("/", 1)[1])
        if eid not in self.events:
            raise s8.ScrapeError("not found on 8888 (HTTP 404): the tournament may have been removed")
        return copy.deepcopy(self.events[eid])

    def post_json(self, path, payload):
        assert path == "v2/bet/slip-data"
        self.posted.append(payload)
        if self.slip_reply is not None:
            return self.slip_reply
        code = f"QS{next(self.counter):012d}"
        self.slips[code] = payload
        return {"success": True, "id": code}

    def post_site_form(self, path, fields):
        assert path == "tickets/detail/"
        if self.detail_fails:
            self.detail_fails -= 1
            raise s8.ScrapeError("no connection to 8888 (Timeout)")
        body = self.slips[fields["code"]]
        data = json.loads(body["data"])[0]
        stake = body["betSlip"]["bets"][0]["betAmount"]
        legs = []
        for sid in data["selections"]["ids"]:
            ent = data["selections"]["entities"][sid]
            src = json.loads(ent["source"])
            legs.append({"pick": src["selectionName"], "odds": ent["odds"]["current"],
                         "market": src["marketName"], "teams": ent["event"].replace(" - ", " vs ")})
        legs.reverse()                     # 8888 lists them in its own order
        if self.tamper:
            self.tamper(legs)
        return {"success": "1", "html": render_detail(fields["code"], stake, legs)}


def render_detail(code, stake, legs):
    """The ticket page, in the same markup as 8888's (see detail_response.json)."""
    rows = "".join(
        f'<div class="bet-game-info "> <div class="bet-game-title-box row-info relative"> '
        f'<div class="relative flex"> <div class="bet-game-icon icon-icon_football"></div> '
        f'<div class="bet-game-title">  {leg["pick"]} <span class="green-bgr">ACCA%</span> </div> </div> '
        f'<div class="bet-game-odds"> <span class="odds">{leg["odds"]:.2f}</span> </div> </div> '
        f'<div class="bet-game-type row-info fade-txt">  {html.escape(leg["market"])}  </div> '
        f'<div class="bet-game-desc row-info"> <div class="bet-game-teams">{html.escape(leg["teams"])}</div> '
        f'<div class="bet-game-data fade-txt">25/11/2026, 22:00</div> </div> </div>' for leg in legs)
    total = 1.0
    for leg in legs:
        total *= leg["odds"]
    return (f'<div class="bet-barcode display-none" data-bet-barcode data-barcode="{code}"> {code} </div>'
            f'<div class="bet-sum more-space">  {float(stake):.2f} <span>€</span> </div>'
            f'<div class="bet-type more-space">Колонка</div><div class="bet-desc">{rows}</div>'
            f'<div class="betting-coef-box more-space"><div class="betting-coef-label">Коефициент</div>'
            f'<div class="betting-coef-sum">{total:.2f}</div></div>'
            f'<div class="betting-profit-box more-space"><div class="betting-profit-label">Доп. печалба 7 '
            f'<span class="green-bgr">ACCA%</span></div><div class="betting-profit-sum"> 1.00 <span>€</span></div></div>'
            f'<div class="betting-profit-box more-space"><div class="betting-profit-label">Обща печалба</div>'
            f'<div class="betting-profit-sum">  {float(stake) * total:.2f} <span>€</span> </div></div>')


@pytest.fixture
def site(monkeypatch, tmp_path):
    fake = Fake8888()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(s8, "USE_BROWSER", False)
    for name in ("fetch_json", "post_json", "post_site_form"):
        monkeypatch.setattr(s8, name, getattr(fake, name))
    monkeypatch.setattr(bot.time, "sleep", lambda s: None)
    monkeypatch.setattr(bot, "_wait", lambda lo, hi, stop: 0)
    return fake


def tickets_log():
    with open(bot.FILES_8888["TICKETS_LOG"], encoding="utf-8") as f:
        return json.load(f)


def run(path, **kw):
    kw.setdefault("stake_rule", FIXED_10)
    return bot.run(excel=path, match_type=5, min_interval=0, max_interval=0, **kw)


# --- the normal ticket -------------------------------------------------------------------------
def test_ticket_is_made_checked_and_saved(site, tmp_path):
    book = workbook(tmp_path / "book.xlsx", {17: SHEET_17})
    result = run(book)
    assert result["done"] == ["17"] and not result["errored"] and not result["skipped"]

    (body,) = site.posted
    recorded = load("slip_request.json")["betSlip"]
    assert [s["id"] for s in body["betSlip"]["selections"]] == [s["id"] for s in recorded["selections"]]
    assert body["betSlip"]["bets"][0]["betAmount"] == 10 and body["betSlip"]["betSlipType"] == 2

    (record,) = tickets_log()
    code = record["voucher"]
    assert record["label"] == "5-17" and record["verified"] and record["stake"] == 10
    assert [s["outcomeId"] for s in record["selections"]] == [s["id"] for s in recorded["selections"]]
    assert [s["pick"] for s in record["selections"]] == ["1", "1", "2", "2", "2"]
    assert record["qr"].endswith(os.path.join("", "5-17.png")) and record["qr"].startswith("qr_codes_8888")
    expected = qrcode.make(code).get_image().convert("1")      # the QR holds the code, like 8888's
    from PIL import Image, ImageChops
    assert ImageChops.difference(Image.open(record["qr"]).convert("1"), expected).getbbox() is None
    assert not os.path.exists(bot.FILES_8888["IN_PROGRESS_FILE"])


def test_same_ticket_is_never_made_twice(site, tmp_path):
    book = workbook(tmp_path / "book.xlsx", {17: SHEET_17})
    run(book)
    result = run(book)
    assert result["already_done"] == ["17"] and len(site.posted) == 1
    other = workbook(tmp_path / "other.xlsx", {3: list(reversed(SHEET_17))})   # same picks, other sheet/order
    assert run(other)["already_done"] == ["3"] and len(site.posted) == 1


def test_stake_formula_uses_i12_like_inbet(site, tmp_path):
    book = workbook(tmp_path / "book.xlsx", {17: SHEET_17})
    run(book, stake_rule={"mode": "formula", "formula": "20000/(I12-1)"})
    i12 = 1.88 * 2.38 * 2.1 * 1.65 * 2.45
    assert site.posted[0]["betSlip"]["bets"][0]["betAmount"] == stake_rules.compute_stake(
        {"mode": "formula", "formula": "20000/(I12-1)"}, i12=i12)


# --- equal odds: random side, still never twice -----------------------------------------------------
def test_equal_odds_pick_a_random_side_and_never_repeat(site, tmp_path, monkeypatch):
    rows = list(SHEET_17)
    rows[4] = ("#3708367", "Спортинг - Манчестър Юнайтед", 2.45, 3.2, 2.45, "Шампионска Лига", 2.45)
    book = workbook(tmp_path / "book.xlsx", {9: rows})
    monkeypatch.setattr(bot.random, "choice", lambda seq: seq[0])         # 1 this time
    assert run(book)["done"] == ["9"]
    last = tickets_log()[0]["selections"][4]
    assert last["side"] == "home" and last["random_pick"]
    monkeypatch.setattr(bot.random, "choice", lambda seq: seq[-1])        # 2 next time
    assert run(book)["already_done"] == ["9"] and len(site.posted) == 1


# --- check only ------------------------------------------------------------------------------------------
def test_check_only_creates_nothing(site, tmp_path):
    book = workbook(tmp_path / "book.xlsx", {17: SHEET_17, 2: SHEET_17[:4] + [SHEET_17[4]]})
    result = run(book, dry_run=True)
    assert set(result["done"]) == {"17", "2"} and site.posted == []
    assert not os.path.exists(bot.FILES_8888["TICKETS_LOG"])


# --- problems ---------------------------------------------------------------------------------------------
def test_started_match_skips_only_its_sheet(site, tmp_path):
    site.events[4156205]["isLive"] = True                       # Olimpia Milano has started
    book = workbook(tmp_path / "book.xlsx", {1: SHEET_17[:4], 2: SHEET_17[1:]})   # 4-match tickets
    result = bot.run(excel=book, match_type=4, stake_rule=FIXED_10, min_interval=0, max_interval=0)
    assert result["skipped"] == ["1"] and result["done"] == ["2"] and len(site.posted) == 1
    (record,) = tickets_log()
    assert record["label"] == "4-2" and "4156205" not in json.dumps(site.posted[0])


def test_ticket_that_differs_on_8888_gets_no_qr(site, tmp_path):
    site.tamper = lambda legs: legs[0].update(pick="1" if legs[0]["pick"] == "2" else "2")
    book = workbook(tmp_path / "book.xlsx", {17: SHEET_17})
    result = run(book)
    assert result["skipped"] == ["17"]
    assert not os.path.exists(bot.FILES_8888["TICKETS_LOG"]) and not os.path.exists(bot.FILES_8888["IN_PROGRESS_FILE"])
    with open(bot.REJECTED_LOG, encoding="utf-8") as f:
        (rejected,) = json.load(f)
    assert rejected["rejected"] and not os.path.exists(rejected["qr"])


def test_8888_refusing_the_stake_is_reported(site, tmp_path):
    site.slip_reply = {"success": False, "maxBetStakes": {"x": 500}}
    book = workbook(tmp_path / "book.xlsx", {17: SHEET_17})
    result = run(book)
    assert result["skipped"] == ["17"] and not os.path.exists(bot.FILES_8888["TICKETS_LOG"])


def test_cut_off_ticket_is_checked_and_finished_next_run(site, tmp_path):
    book = workbook(tmp_path / "book.xlsx", {17: SHEET_17})
    site.detail_fails = bot.READ_BACK_TRIES          # code arrives, then the read-back fails
    assert run(book)["errored"] == ["17"]
    assert os.path.exists(bot.FILES_8888["IN_PROGRESS_FILE"]) and not os.path.exists(bot.FILES_8888["TICKETS_LOG"])
    result = run(book)                                # next run: checked on 8888, finished, not made again
    assert result["already_done"] == ["17"] and len(site.posted) == 1
    (record,) = tickets_log()
    assert record["verified"] and os.path.exists(record["qr"]) and "_legs" not in record
    assert not os.path.exists(bot.FILES_8888["IN_PROGRESS_FILE"])


def test_wrong_match_id_is_named(site, tmp_path, caplog):
    rows = list(SHEET_17)
    rows[0] = ("#4156205", "Партизан - Валенсия", 1.9, 10, 1.73, "Евролига", 1.9)
    book = workbook(tmp_path / "book.xlsx", {17: rows})
    with caplog.at_level("WARNING"):
        assert run(book)["skipped"] == ["17"]
    assert any("but the sheet says 'Партизан - Валенсия'" in r.getMessage() for r in caplog.records)


def test_stop_before_the_next_sheet(site, tmp_path):
    book = workbook(tmp_path / "book.xlsx", {17: SHEET_17, 3: list(reversed(SHEET_17[:4])) + [SHEET_17[4]]})
    calls = []

    def cancel():
        calls.append(1)
        return len(site.posted) >= 1
    result = run(book, cancel=cancel)
    assert len(site.posted) == 1 and len(result["done"]) == 1


def test_site_that_cannot_be_opened_is_reported(site, tmp_path, monkeypatch):
    import contextlib

    @contextlib.contextmanager
    def broken(log=print):
        raise s8.ScrapeError("8888punkt.bg did not hand out its key")
        yield
    monkeypatch.setattr(s8, "odds_connection", broken)
    book = workbook(tmp_path / "book.xlsx", {17: SHEET_17})
    result = run(book)
    assert result["errored"] == ["17"] and site.posted == []


def test_files_are_the_8888_robots_own_and_inbet_keeps_its_own():
    before = (config.TICKETS_LOG, config.QR_OUTPUT_DIR, config.SETTINGS_FILE)
    with bot._own_files():
        assert (config.TICKETS_LOG, config.QR_OUTPUT_DIR, config.SETTINGS_FILE, config.LOG_FILE_NAME) == \
               ("tickets_log_8888.json", "qr_codes_8888", "settings_8888.json", "bot_8888.jsonl")
    assert (config.TICKETS_LOG, config.QR_OUTPUT_DIR, config.SETTINGS_FILE) == before == \
           ("tickets_log.json", "qr_codes", "settings.json")
